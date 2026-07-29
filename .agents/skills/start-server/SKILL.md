---
name: start-server
description: Start the ChemSource AI production VM in Yandex Cloud without deploying code. Use when the user explicitly invokes $start-server or asks to start, turn on, or launch the server or website.
---

# Запуск production-сервера

Из корня репозитория запусти:

```powershell
.\deploy\start-server.cmd
```

Команда идемпотентна: если ВМ уже работает, скрипт только покажет её состояние.
После выполнения сообщи статус, публичный IP и адрес сайта. Эта команда не
обновляет код и не выполняет миграции; для развёртывания используй `$update`.

Явный вызов `$start-server` является разрешением возобновить тарификацию и
запустить ВМ, поэтому дополнительное подтверждение не требуется.
