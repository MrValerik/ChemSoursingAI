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
2. Проверь `git status` и текущую ветку.
3. Если рабочее дерево не чистое или ветка не `main`, остановись и понятно
   сообщи пользователю, что требуется исправить. Не коммить незакоммиченные
   изменения автоматически.
4. Запусти `.\deploy\update-server.cmd`.
5. Разреши скрипту автоматически синхронизировать локальный `main` с
   `origin/main` и выполнить `git push origin main`. При конфликте не разрешай
   запуск ВМ: сообщи конфликтующие файлы и обсуди разрешение конфликта с
   пользователем.
6. Не заменяй скрипт ручной последовательностью запуска ВМ, SSH, backup,
   миграций, Docker Compose и health-check, пока скрипт доступен.
7. После успешного обновления сообщи полный SHA развёрнутого commit, публичный
   URL, результаты backend и LLM health-check, состояние контейнеров и
   подтверждение, что `chemsource-idle-shutdown.timer` отключён и неактивен.

Команда `$update` является явным разрешением отправить чистую ветку `main` в
`origin/main` и запустить остановленную ВМ для развёртывания. Отдельно
спрашивать разрешение на `git push` или запуск ВМ не нужно.

Production-ВМ должна оставаться включённой. Не запускай `$stop-server` и не
включай таймер автоостановки после deployment.
