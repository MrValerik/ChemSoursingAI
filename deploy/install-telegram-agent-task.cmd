@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-telegram-agent-task.ps1" %*
exit /b %ERRORLEVEL%
