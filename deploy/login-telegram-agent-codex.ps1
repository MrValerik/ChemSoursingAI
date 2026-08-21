[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$agentRoot = Join-Path $repositoryRoot "tools\telegram-agent"
$python = Join-Path $agentRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Run .\deploy\setup-telegram-agent.cmd first."
}

Push-Location $agentRoot
try {
    & $python -m telegram_agent.login
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
