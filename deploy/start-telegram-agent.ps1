[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$agentRoot = Join-Path $repositoryRoot "tools\telegram-agent"
$python = Join-Path $agentRoot ".venv\Scripts\python.exe"
$envPath = Join-Path $agentRoot ".env"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Run .\deploy\setup-telegram-agent.cmd first."
}
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "Missing $envPath. Copy .env.example and fill in the secrets."
}

Push-Location $agentRoot
try {
    & $python -m telegram_agent
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
