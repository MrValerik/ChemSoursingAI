[CmdletBinding()]
param(
    [string]$RepositoryPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepositoryPath)) {
    $RepositoryPath = Join-Path $PSScriptRoot ".."
}
$repositoryRoot = [System.IO.Path]::GetFullPath($RepositoryPath)
$gitCommands = @(Get-Command "git" -CommandType Application -ErrorAction SilentlyContinue)
if ($gitCommands.Count -eq 0) {
    throw "Git не найден в PATH."
}
$git = $gitCommands[0].Source

$branch = (& $git -C $repositoryRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
    throw "Обновление разрешено только для ветки main. Текущая ветка: $branch"
}

$changes = @(
    & $git -C $repositoryRoot status --porcelain --untracked-files=all
)
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось проверить состояние локального репозитория."
}
if ($changes.Count -gt 0) {
    throw (
        "Рабочее дерево не чистое. Зафиксируйте или уберите локальные изменения " +
        "перед обновлением репозитория."
    )
}

& $git -C $repositoryRoot fetch origin main
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось получить сведения об origin/main."
}

$divergence = (
    & $git -C $repositoryRoot rev-list --left-right --count "origin/main...HEAD"
).Trim() -split "\s+"
if ($LASTEXITCODE -ne 0 -or $divergence.Count -ne 2) {
    throw "Не удалось сравнить локальный main с origin/main."
}

$remoteAhead = [int]$divergence[0]
$localAhead = [int]$divergence[1]
if ($remoteAhead -gt 0 -and $localAhead -gt 0) {
    throw (
        "Локальный main и origin/main разошлись. Автоматическое обновление " +
        "остановлено без изменения файлов. Нужно совместно выбрать способ " +
        "разрешения расхождения."
    )
}

$beforeCommit = (& $git -C $repositoryRoot rev-parse HEAD).Trim()
if ($remoteAhead -gt 0) {
    & $git -C $repositoryRoot merge --ff-only origin/main
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось выполнить безопасный fast-forward до origin/main."
    }
}

$afterCommit = (& $git -C $repositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось определить текущий commit."
}

if ($beforeCommit -eq $afterCommit) {
    Write-Host "Репозиторий уже содержит все изменения origin/main."
} else {
    Write-Host "Репозиторий обновлён безопасным fast-forward."
}
if ($localAhead -gt 0) {
    Write-Host "Локальных неопубликованных коммитов: $localAhead. Они не отправлялись."
}
Write-Host "Ветка: main"
Write-Host "Коммит: $afterCommit"
