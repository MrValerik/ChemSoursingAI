---
name: update
description: Safely deploy the current origin/main of ChemSource AI to the Yandex Cloud production VM. Use when the user explicitly invokes $update or asks to update, deploy, or redeploy the production server.
---

# Обновление production-сервера ChemSource AI

Используй штатный скрипт из корня репозитория:

```powershell
.\deploy\update-server.cmd
```

## Порядок работы

1. Прочитай корневой `AGENTS.md`.
2. Проверь `git status`, текущую ветку и синхронизацию с `origin/main`.
3. Если рабочее дерево не чистое, ветка не `main` или локальный commit не
   совпадает с `origin/main`, остановись и понятно сообщи пользователю, что
   требуется исправить. Не коммить и не отправляй изменения без явного запроса.
4. Запусти `.\deploy\update-server.cmd`.
5. Не заменяй скрипт ручной последовательностью запуска ВМ, SSH, backup,
   миграций, Docker Compose и health-check, пока скрипт доступен.
6. После успешного обновления сообщи полный SHA развёрнутого commit, публичный
   URL, результаты backend и LLM health-check, состояние контейнеров и таймера
   автоостановки.

Команда `$update` является явным разрешением запустить остановленную ВМ для
развёртывания. Отдельно спрашивать об остановке сервера на время этой операции
не нужно.
