@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0yc-vm.ps1" %*
exit /b %ERRORLEVEL%
