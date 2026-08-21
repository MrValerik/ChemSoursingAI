@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0login-telegram-agent-codex.ps1" %*
exit /b %ERRORLEVEL%
