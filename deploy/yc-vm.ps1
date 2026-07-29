[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Position = 0)]
    [ValidateSet("status", "start", "stop")]
    [string]$Action = "status",

    [string]$InstanceId = "epdcj7fttoprbgetslm2",

    [switch]$OpenSite,

    [switch]$NonInteractive,

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

        if ($OpenSite -and $instance.status -eq "RUNNING") {
            $externalIp = Get-ExternalIp -Instance $instance
            if ($externalIp) {
                Start-Process "http://$externalIp"
            }
            else {
                Write-Warning "The VM has no public IP address."
            }
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
