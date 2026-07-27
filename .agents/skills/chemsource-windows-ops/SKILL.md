---
name: chemsource-windows-ops
description: Run, configure, troubleshoot, or document ChemSource AI development on Windows with PowerShell, Python virtual environments, npm, Docker Desktop, Compose, Windows paths, environment variables, ports, file locking, or line endings. Use for local setup, scripts, operational commands, and Windows-specific failures; keep Linux VM and container commands in their native environment.
---

# ChemSource Windows Operations

Distinguish the Windows host, Linux containers, and the Linux Yandex VM before choosing commands.

## Use the correct shell

- Use PowerShell syntax for commands executed on the Windows host.
- Use Bash only inside an explicitly named Linux container, WSL session, or remote VM.
- Keep `deploy/*.sh`, systemd units, and Linux container paths Linux-native.
- Keep `deploy/*.ps1` and `deploy/*.cmd` Windows-native.
- Do not translate commands inside `Dockerfile`, Compose `command`, or container health checks into PowerShell.

## PowerShell conventions

- Use `Set-Location`, `Push-Location`, `Pop-Location`, `Copy-Item`, `Move-Item`, `Remove-Item`, and `Join-Path`.
- Set process environment variables as `$env:NAME = "value"` and avoid printing secrets.
- Prefer `.\.venv\Scripts\python.exe` over virtual-environment activation.
- Quote paths and use `-LiteralPath` for filesystem operations.
- Keep file operations in PowerShell end to end; do not enumerate paths in PowerShell and pass them to `cmd.exe` for deletion or moving.
- Resolve and verify an absolute target before recursive deletion or movement.
- Preserve existing line endings and UTF-8 encoding; avoid repository-wide normalization.
- Do not change the machine-wide execution policy to activate a virtual environment.

## Common workflow

Run from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
npm --prefix .\frontend ci
Copy-Item -LiteralPath .\.env.example -Destination .\.env
docker compose up --build
```

Keep `.env` in safe demo mode and replace placeholder secrets before any exposed deployment. Do not run `docker compose down -v` unless the user explicitly requests deletion of database volumes.

## Troubleshoot

1. Check `Get-Command py, python, npm, docker` and `docker compose version`.
2. Check port ownership with `Get-NetTCPConnection` before changing configured ports.
3. Treat `WinError 32` as a possible unclosed file handle; fix resource lifetime rather than adding retries blindly.
4. Treat path-case assumptions, backslash escaping, and CRLF changes as portability defects.
5. Validate Compose from Windows, but debug service commands in the corresponding Linux container.
