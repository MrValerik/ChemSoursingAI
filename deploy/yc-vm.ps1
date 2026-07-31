[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Position = 0)]
    [ValidateSet("status", "start", "stop")]
    [string]$Action = "status",

    [string]$InstanceId = "epdcj7fttoprbgetslm2",

    [switch]$OpenSite,

    [switch]$NonInteractive,

    # Deployment starts the VM before rolling out new code, so it verifies
    # health itself and skips the boot health wait here.
    [switch]$SkipSiteHealthCheck,

    [ValidateRange(30, 1800)]
    [int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($NonInteractive) {
    $ConfirmPreference = "None"
}

function Resolve-YcExecutable {
    $command = Get-Command "yc" -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $userProfile = [Environment]::GetFolderPath("UserProfile")
    $fallback = Join-Path $userProfile "yandex-cloud\bin\yc.exe"
    if (Test-Path -LiteralPath $fallback) {
        return $fallback
    }

    throw "Yandex Cloud CLI is not installed or is missing from PATH."
}

$ycExecutable = Resolve-YcExecutable

function Invoke-Yc {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $ycExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "yc command failed with exit code $LASTEXITCODE."
    }
}

function Invoke-YcJson {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & $ycExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "yc command failed with exit code $LASTEXITCODE."
    }

    try {
        return ($output | ConvertFrom-Json)
    }
    catch {
        throw "Yandex Cloud CLI returned invalid JSON: $($_.Exception.Message)"
    }
}

function Get-Instance {
    return Invoke-YcJson -Arguments @(
        "compute",
        "instance",
        "get",
        $InstanceId,
        "--format",
        "json"
    )
}

function Get-ExternalIp {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Instance
    )

    $interfaces = @($Instance.network_interfaces)
    if ($interfaces.Count -eq 0) {
        return $null
    }

    $primaryAddress = $interfaces[0].primary_v4_address
    if ($null -eq $primaryAddress) {
        return $null
    }

    $nat = $primaryAddress.one_to_one_nat
    if ($null -eq $nat) {
        return $null
    }

    return $nat.address
}

function Show-Instance {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Instance
    )

    $externalIp = Get-ExternalIp -Instance $Instance
    [PSCustomObject]@{
        Name       = $Instance.name
        Id         = $Instance.id
        Zone       = $Instance.zone_id
        Status     = $Instance.status
        ExternalIp = $externalIp
        Site       = if ($externalIp) { "http://$externalIp" } else { $null }
    } | Format-List
}

function Wait-InstanceStatus {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DesiredStatus
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $instance = Get-Instance
        if ($instance.status -eq $DesiredStatus) {
            return $instance
        }

        Write-Host "Current status: $($instance.status). Waiting for $DesiredStatus..."
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    throw "VM did not reach $DesiredStatus within $TimeoutSeconds seconds."
}

function Test-HttpOk {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300)
    }
    catch {
        return $false
    }
}

function Wait-SiteReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExternalIp
    )

    $healthUrl = "http://$ExternalIp/api/health"
    Write-Host ""
    Write-Host "VM is RUNNING. Waiting for the site itself to become ready..."
    Write-Host "(The Docker stack and the local Qwen model need a few minutes after boot.)"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-HttpOk -Url $healthUrl) {
            Write-Host "Site is ready: http://$ExternalIp"
            if (Test-HttpOk -Url "http://$ExternalIp/api/health/llm") {
                Write-Host "Local LLM: ready."
            }
            else {
                Write-Warning (
                    "The site is up, but the local LLM is still loading. " +
                    "Supplier search jobs stay queued until it is ready."
                )
            }
            return $true
        }
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)

    Write-Warning "The VM is RUNNING, but $healthUrl did not answer within $TimeoutSeconds seconds."
    Write-Warning "The application stack did not start. Check on the VM via SSH:"
    Write-Warning "  systemctl is-active docker qwen.service chemsource.service"
    Write-Warning "  sudo systemctl enable --now qwen.service chemsource.service"
    Write-Warning "  docker compose ps"
    Write-Warning "If services are missing, run: sudo bash deploy/install-vm-services.sh"
    return $false
}

$instance = Get-Instance

switch ($Action) {
    "status" {
        Show-Instance -Instance $instance
    }

    "start" {
        if ($instance.status -eq "RUNNING") {
            Write-Host "VM is already running."
            Show-Instance -Instance $instance
        }
        elseif ($PSCmdlet.ShouldProcess(
                "$($instance.name) ($InstanceId)",
                "Start VM; compute billing will resume"
            )) {
            Invoke-Yc -Arguments @("compute", "instance", "start", $InstanceId)
            $instance = Wait-InstanceStatus -DesiredStatus "RUNNING"
            Show-Instance -Instance $instance
        }

        $siteReady = $true
        if ($instance.status -eq "RUNNING" -and -not $SkipSiteHealthCheck) {
            $externalIp = Get-ExternalIp -Instance $instance
            if ($externalIp) {
                $siteReady = Wait-SiteReady -ExternalIp $externalIp
            }
            else {
                Write-Warning "The VM has no public IP address."
                $siteReady = $false
            }
        }

        if ($OpenSite -and $instance.status -eq "RUNNING" -and $siteReady) {
            $externalIp = Get-ExternalIp -Instance $instance
            if ($externalIp) {
                Start-Process "http://$externalIp"
            }
        }

        if (-not $siteReady) {
            # A running VM with a dead site must not look like a success.
            exit 1
        }
    }

    "stop" {
        if ($instance.status -eq "STOPPED") {
            Write-Host "VM is already stopped."
            Show-Instance -Instance $instance
        }
        elseif ($PSCmdlet.ShouldProcess(
                "$($instance.name) ($InstanceId)",
                "Gracefully stop VM; the site and LLM will become unavailable"
            )) {
            Invoke-Yc -Arguments @("compute", "instance", "stop", $InstanceId)
            $instance = Wait-InstanceStatus -DesiredStatus "STOPPED"
            Show-Instance -Instance $instance
        }
    }
}
