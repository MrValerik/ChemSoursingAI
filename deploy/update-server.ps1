[CmdletBinding()]
param(
    [string]$InstanceId = "epdcj7fttoprbgetslm2",
    [string]$SshUser = "valerik",
    [string]$SshKeyPath = (Join-Path ([Environment]::GetFolderPath("UserProfile")) ".ssh\id_ed25519"),
    [string]$KnownHostsPath = (Join-Path ([Environment]::GetFolderPath("UserProfile")) ".ssh\known_hosts"),
    [string]$RemoteProjectDir = "/home/valerik/ChemSoursingAI",
    [ValidateRange(60, 1800)]
    [int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ($RemoteProjectDir -notmatch "^/[A-Za-z0-9._/-]+$") {
    throw "RemoteProjectDir содержит недопустимые символы."
}

function Resolve-Application {
    param([Parameter(Mandatory = $true)][string]$Name)

    $commands = @(Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue)
    if ($commands.Count -eq 0) {
        throw "Не найдена обязательная команда: $Name"
    }
    return $commands[0].Source
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$FailureMessage = "Внешняя команда завершилась с ошибкой."
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Код завершения: $LASTEXITCODE."
    }
}

function Sync-MainWithOrigin {
    param(
        [Parameter(Mandatory = $true)][string]$GitExecutable,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )

    Invoke-External -Executable $GitExecutable -Arguments @(
        "-C", $RepositoryRoot, "fetch", "origin", "main"
    ) -FailureMessage "Не удалось обновить сведения об origin/main."

    $divergence = (
        & $GitExecutable -C $RepositoryRoot rev-list --left-right --count "origin/main...HEAD"
    ).Trim() -split "\s+"
    if ($LASTEXITCODE -ne 0 -or $divergence.Count -ne 2) {
        throw "Не удалось сравнить локальную ветку main с origin/main."
    }

    $remoteAhead = [int]$divergence[0]
    $localAhead = [int]$divergence[1]
    if ($remoteAhead -eq 0) {
        Write-Host "Синхронизация main: локальных коммитов для отправки — $localAhead."
        return
    }

    Write-Host (
        "В origin/main обнаружено новых коммитов: $remoteAhead. " +
        "Выполняется автоматический rebase локального main."
    )
    & $GitExecutable -C $RepositoryRoot rebase origin/main
    if ($LASTEXITCODE -eq 0) {
        return
    }

    $conflicts = @(
        & $GitExecutable -C $RepositoryRoot diff --name-only --diff-filter=U
    )
    & $GitExecutable -C $RepositoryRoot rebase --abort
    $conflictText = if ($conflicts.Count -gt 0) {
        $conflicts -join ", "
    } else {
        "Git не сообщил имена файлов."
    }
    throw (
        "Автоматическая синхронизация main остановлена из-за конфликтов. " +
        "Rebase отменён, ВМ не запускалась. Конфликтующие файлы: $conflictText"
    )
}

$git = Resolve-Application "git"
$ssh = Resolve-Application "ssh"
$curl = Resolve-Application "curl.exe"

$branch = (& $git -C $repositoryRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
    throw "Развёртывание разрешено только из ветки main. Текущая ветка: $branch"
}

$localChanges = @(& $git -C $repositoryRoot status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось проверить состояние локального репозитория."
}
if ($localChanges.Count -gt 0) {
    throw "В локальном репозитории есть незакоммиченные изменения. Сначала зафиксируйте их."
}

if (-not (Test-Path -LiteralPath $SshKeyPath -PathType Leaf)) {
    throw "SSH-ключ не найден: $SshKeyPath"
}
if (-not (Test-Path -LiteralPath $KnownHostsPath -PathType Leaf)) {
    throw "Файл известных SSH-хостов не найден: $KnownHostsPath"
}

$venvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
} else {
    Resolve-Application "python"
}
$npm = Resolve-Application "npm.cmd"

$published = $false
$maxPublishAttempts = 3
for ($publishAttempt = 1; $publishAttempt -le $maxPublishAttempts; $publishAttempt++) {
    Sync-MainWithOrigin -GitExecutable $git -RepositoryRoot $repositoryRoot

    Push-Location (Join-Path $repositoryRoot "backend")
    try {
        Invoke-External -Executable $python -Arguments @(
            "-m", "pytest", "-q"
        ) -FailureMessage "Backend-тесты не прошли."
    } finally {
        Pop-Location
    }

    Push-Location (Join-Path $repositoryRoot "frontend")
    try {
        Invoke-External -Executable $npm -Arguments @(
            "run", "build"
        ) -FailureMessage "Frontend-сборка не прошла."
    } finally {
        Pop-Location
    }

    & $git -C $repositoryRoot push origin HEAD:main
    $pushExitCode = $LASTEXITCODE

    Invoke-External -Executable $git -Arguments @(
        "-C", $repositoryRoot, "fetch", "origin", "main"
    ) -FailureMessage "Не удалось проверить опубликованный origin/main."

    $localCommit = (& $git -C $repositoryRoot rev-parse HEAD).Trim()
    $remoteCommit = (& $git -C $repositoryRoot rev-parse origin/main).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось определить commit после отправки main."
    }
    if ($pushExitCode -eq 0 -and $localCommit -eq $remoteCommit) {
        $published = $true
        break
    }

    $remoteAhead = [int](
        (
            & $git -C $repositoryRoot rev-list --count "HEAD..origin/main"
        ).Trim()
    )
    if ($LASTEXITCODE -ne 0 -or $remoteAhead -eq 0) {
        throw (
            "Не удалось автоматически отправить main в origin. " +
            "ВМ не запускалась. Проверьте доступ к GitHub и правила защиты ветки."
        )
    }
    if ($publishAttempt -lt $maxPublishAttempts) {
        Write-Host (
            "origin/main изменился во время проверок. " +
            "Повторная синхронизация и проверка: попытка $($publishAttempt + 1) " +
            "из $maxPublishAttempts."
        )
    }
}
if (-not $published) {
    throw (
        "origin/main продолжает изменяться. После $maxPublishAttempts попыток " +
        "публикация остановлена, ВМ не запускалась."
    )
}

$vmScript = Join-Path $PSScriptRoot "yc-vm.ps1"
& $vmScript start -InstanceId $InstanceId -TimeoutSeconds $TimeoutSeconds -Confirm:$false
if (-not $?) {
    throw "Не удалось запустить ВМ."
}

$yc = Resolve-Application "yc"
$instanceJson = & $yc compute instance get $InstanceId --format json
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось получить параметры запущенной ВМ."
}
$instance = $instanceJson | ConvertFrom-Json
$externalIp = $instance.network_interfaces[0].primary_v4_address.one_to_one_nat.address
if ([string]::IsNullOrWhiteSpace($externalIp)) {
    throw "У ВМ отсутствует публичный IPv4-адрес."
}

$sshArguments = @(
    "-i", $SshKeyPath,
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=8",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UserKnownHostsFile=$KnownHostsPath"
)
$sshTarget = "$SshUser@$externalIp"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$sshReady = $false
do {
    & $ssh @sshArguments $sshTarget "true" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $sshReady = $true
        break
    }
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $deadline)
if (-not $sshReady) {
    throw "SSH не стал доступен за $TimeoutSeconds секунд."
}

$remoteBootstrap = @'
set -Eeuo pipefail
project_dir="$1"
expected_commit="$2"

server_changes="$(
  git -C "$project_dir" status --porcelain --untracked-files=all |
    grep -vE '^\?\? data/backups/' || true
)"
if [[ -n "$server_changes" ]]; then
  echo "На сервере есть незакоммиченные изменения. Обновление остановлено." >&2
  printf "%s\n" "$server_changes"
  exit 1
fi

git -C "$project_dir" fetch origin main
git -C "$project_dir" checkout main
git -C "$project_dir" pull --ff-only origin main
exec bash "$project_dir/deploy/update-server.sh" "$expected_commit"
'@

$remoteCommand = "bash -s -- '$RemoteProjectDir' '$localCommit'"
$remoteBootstrap | & $ssh @sshArguments $sshTarget $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "Развёртывание на ВМ завершилось с ошибкой."
}

$publicHealth = & $curl --fail --silent --show-error "http://$externalIp/api/health"
if ($LASTEXITCODE -ne 0) {
    throw "Публичный health endpoint недоступен после развёртывания."
}

Write-Host ""
Write-Host "ChemSource AI успешно обновлён."
Write-Host "Коммит: $localCommit"
Write-Host "Сайт: http://$externalIp"
Write-Host "Health: $publicHealth"
