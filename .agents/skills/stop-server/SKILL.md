---
name: stop-server
description: Gracefully stop the ChemSource AI production VM in Yandex Cloud. Use when the user explicitly invokes $stop-server or asks to stop, turn off, or shut down the server or website.
---

# Остановка production-сервера

Из корня репозитория запусти:

```powershell
.\deploy\stop-server.cmd
```

Команда идемпотентна: если ВМ уже остановлена, скрипт только покажет её
состояние. После выполнения сообщи подтверждённый статус `STOPPED`.

Явный вызов `$stop-server` является разрешением штатно остановить ВМ и сделать
сайт и локальную ИИ недоступными, поэтому дополнительное подтверждение не
требуется.
