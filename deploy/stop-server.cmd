@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0yc-vm.ps1" stop -NonInteractive %*
exit /b %ERRORLEVEL%
