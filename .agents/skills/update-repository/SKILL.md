---
name: update-repository
description: Safely update the local ChemSource AI main branch from origin/main without pushing, deploying, or starting the VM. Use when the user explicitly invokes $update-repository or asks to update, pull, or synchronize the local repository.
---

# Обновление локального репозитория

Из корня репозитория запусти:

```powershell
.\deploy\update-repository.cmd
```

Скрипт принимает только чистую ветку `main`, выполняет `fetch` и безопасный
fast-forward до `origin/main`. Он не создаёт коммиты, не выполняет `push`, не
запускает ВМ и не разворачивает production.

Если ветки разошлись или есть незакоммиченные изменения, остановись и сообщи
причину пользователю. Не применяй rebase, merge commit, stash или удаление
изменений автоматически.

После успеха сообщи текущий commit и наличие локальных неопубликованных
коммитов.
