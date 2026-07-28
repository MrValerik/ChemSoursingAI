# Управление виртуальной машиной Yandex Cloud

## Параметры проекта

| Параметр | Значение |
| --- | --- |
| ВМ | `compute-vm-8-32-100-ssd-1784612980019` |
| ID | `epdcj7fttoprbgetslm2` |
| Зона | `ru-central1-b` |
| Публичный IP | `130.193.53.242` |
| Скрипт | `deploy/yc-vm.ps1` |
| Обновление | `deploy/update-server.cmd` |

Скрипт использует профиль Yandex Cloud CLI текущего пользователя. Токены и
идентификаторы профиля в репозиторий не записываются.

## Проверка состояния

Из корня репозитория:

```powershell
.\deploy\yc-vm.cmd status
```

Команда только читает состояние и не требует подтверждения.

## Запуск

```powershell
.\deploy\yc-vm.cmd start -OpenSite
```

Скрипт:

1. покажет подтверждение начала тарификации вычислительных ресурсов;
2. запустит ВМ;
3. дождётся статуса `RUNNING`;
4. покажет публичный IP и адрес сайта;
5. с `-OpenSite` откроет сайт в браузере.

Если интерактивное подтверждение не требуется:

```powershell
.\deploy\yc-vm.cmd start -Confirm:$false
```

После запуска systemd должен автоматически поднять Docker Compose и Qwen.
Проверка внутри ВМ:

```bash
systemctl is-active qwen.service chemsource.service
docker compose ps
curl http://127.0.0.1/api/health
curl http://127.0.0.1/api/health/llm
```

## Остановка

```powershell
.\deploy\yc-vm.cmd stop
```

Команда запросит подтверждение, выполнит штатную остановку и дождётся статуса
`STOPPED`.

Для неинтерактивного вызова:

```powershell
.\deploy\yc-vm.cmd stop -Confirm:$false
```

## Переопределение ВМ

По умолчанию используется ВМ проекта. Для другой ВМ передайте ID:

```powershell
.\deploy\yc-vm.cmd status -InstanceId "<instance-id>"
```

## Автовыключение и разработка

На сервере установлен таймер выключения после 30 минут без HTTP-активности.
Обычное SSH-соединение активностью не считается. Перед долгой работой только по
SSH временно остановите таймер:

```bash
sudo systemctl stop chemsource-idle-shutdown.timer
```

После работы включите его:

```bash
sudo systemctl start chemsource-idle-shutdown.timer
systemctl is-active chemsource-idle-shutdown.timer
```

Или остановите ВМ скриптом с локального компьютера.

## Типовой цикл разработки

Локально:

```powershell
git pull --ff-only
# изменения через Codex
git push origin main
.\deploy\update-server.cmd
```

`update-server.cmd` является штатным способом развёртывания. Он:

1. требует чистую локальную ветку `main`, совпадающую с `origin/main`;
2. запускает остановленную ВМ и дожидается SSH;
3. запрещает обновление поверх неизвестных изменений на сервере;
4. выполняет fast-forward до переданного commit SHA;
5. создаёт резервную копию PostgreSQL;
6. применяет ещё не применённые `backend/migrations/*.up.sql`;
7. пересобирает Compose-стек и проверяет backend, frontend и локальную ИИ;
8. восстанавливает таймер автоостановки.

Параметры подключения можно переопределить:

```powershell
.\deploy\update-server.cmd `
  -SshUser "valerik" `
  -SshKeyPath "C:\Users\<user>\.ssh\id_ed25519"
```

Для запуска у каждого разработчика должны быть собственный профиль `yc`,
доступ к облаку и собственный SSH-ключ, добавленный на ВМ. Секреты и приватные
ключи в Git не хранятся.

## Команда Codex

Репозиторный skill `.agents/skills/update` доступен всем разработчикам:

- в Codex откройте `/skills` и выберите «Обновить сервер»;
- либо напишите `$update`;
- полный псевдоним `$update-server` выполняет тот же сценарий;
- естественные фразы «обнови сервер» и «разверни на сервере» также должны
  активировать этот workflow.

Описание показывается в списке skills. Устаревшие локальные `/prompts:*` не
используются, потому что они не распространяются через репозиторий.

## Диагностика CLI

```powershell
yc version
yc config list
yc compute instance list
```

Если профиль истёк или недоступен:

```powershell
yc init
```
