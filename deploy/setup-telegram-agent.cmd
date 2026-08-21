@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-telegram-agent.ps1" %*
exit /b %ERRORLEVEL%
