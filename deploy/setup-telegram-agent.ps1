[CmdletBinding()]
param(
    [string]$PythonPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$agentRoot = Join-Path $repositoryRoot "tools\telegram-agent"
$venvRoot = Join-Path $agentRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$python = $null
if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
    $resolvedPython = Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop
    $python = $resolvedPython.Path
} else {
    $candidates = @(Get-Command "python.exe" -CommandType Application -All -ErrorAction SilentlyContinue)
    foreach ($candidate in $candidates) {
        $candidateVersion = (& $candidate.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null)
        if ($LASTEXITCODE -eq 0 -and [version]$candidateVersion -ge [version]"3.10") {
            $python = $candidate.Source
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($python)) {
    throw "Python 3.10+ was not found. Pass -PythonPath with an explicit python.exe path."
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Telegram agent Python environment."
    }
}

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $agentRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Telegram agent dependencies."
}

$envPath = Join-Path $agentRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $agentRoot ".env.example") -Destination $envPath
    Write-Host "Created local .env: $envPath"
}

Write-Host "Telegram agent is ready. Authenticate Codex if needed:"
Write-Host ".\deploy\login-telegram-agent-codex.cmd"
Write-Host "Then fill in .env and run:"
Write-Host ".\deploy\start-telegram-agent.cmd"
