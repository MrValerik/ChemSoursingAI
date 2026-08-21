[CmdletBinding()]
param(
    [string]$TaskName = "ChemSourceAI Telegram Agent"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$envPath = Join-Path $repositoryRoot "tools\telegram-agent\.env"
$python = Join-Path $repositoryRoot "tools\telegram-agent\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Set up the agent and fill in tools\telegram-agent\.env first."
}

$powerShell = (Get-Command "powershell.exe" -CommandType Application).Source
$startScript = Join-Path $PSScriptRoot "start-telegram-agent.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $argument -WorkingDirectory $repositoryRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Scheduled task '$TaskName' was installed and started."
