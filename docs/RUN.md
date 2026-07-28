# Запуск ChemSource AI

Три способа: быстрый локальный (SQLite, без Docker), полный стек через Docker
Compose (Postgres + Redis) и прогон тестов/демо.

## 1. Быстрый локальный запуск (SQLite, без Docker)

Подходит для разработки и демо ядра без инфраструктуры.

```bash
cd backend
pip install -r requirements.txt

# Вариант А: демонстрация сквозного потока (печатает сводную таблицу)
python scripts/demo.py

# Вариант Б: поднять API на SQLite
#   (Windows PowerShell)
$env:DATABASE_URL="sqlite:///./dev.db"; uvicorn app.main:app --reload
#   (Linux/macOS)
DATABASE_URL=sqlite:///./dev.db uvicorn app.main:app --reload
```

API: <http://localhost:8000> · Swagger UI: <http://localhost:8000/docs>

## 2. Полный стек (Docker Compose: Postgres + Redis + бэкенд)

Боевой режим on-premise. Требуется установленный Docker.

```bash
# из корня репозитория
cp .env.example .env
# Обязательно замените POSTGRES_PASSWORD и AUTH_SECRET_KEY в .env.
docker compose up --build
```

Поднимутся пять сервисов: `db` (Postgres + pgvector), `redis`, `backend`,
`search-worker` и `frontend` (nginx + собранное React-приложение).
`search-worker` последовательно выполняет сохранённые в PostgreSQL задания
поиска, чтобы единственный слот локальной Qwen не получал параллельные запросы.
Бэкенд дождётся готовности БД, а frontend — готовности backend; таблицы
создадутся при старте API.

Остановить: `docker compose down` (данные БД сохраняются в томе `pgdata`;
для полной очистки — `docker compose down -v`).

### Подключение локальной LLM

LLM не входит в Docker-образ: большой GGUF-файл хранится на ВМ, а `llama-server`
работает как отдельная systemd-служба. Для текущей конфигурации используется
Qwen3.5-27B Q4_K_M с частичным переносом слоёв на Tesla T4. Готовый пример
службы находится в `deploy/qwen.service.example`:

```bash
# Выполняется один раз после клонирования проекта на ВМ:
sudo cp deploy/qwen.service.example /etc/systemd/system/qwen.service

sudo systemctl daemon-reload
sudo systemctl enable qwen.service
sudo systemctl restart qwen.service
curl http://127.0.0.1:8000/v1/models

# Из корня проекта:
cp .env.example .env
docker compose up --build -d
curl http://127.0.0.1/api/health/llm
```

`docker-compose.yml` направляет запросы backend-контейнера к хосту через
`host.docker.internal:8000`. Порт 8000 нужен только внутри ВМ: **не открывайте его
во входящих правилах Yandex Cloud**. Для сайта откройте TCP 80 (после подключения
HTTPS также 443). SSH-порт 22 ограничьте вашим IP.

У всех контейнеров задано `restart: unless-stopped`: после перезапуска ВМ Docker
поднимет сайт и базу автоматически, а systemd отдельно запустит Qwen. При штатной
остановке ВМ данные PostgreSQL сохраняются в томе `pgdata`.

Без LLM конвейер извлечения работает на правилах (fallback) — система остаётся
работоспособной.

## 3. Тесты

```bash
cd backend
pytest
```

На каждый pull request и push в `main` workflow `.github/workflows/ci.yml`
автоматически выполняет:

- полный набор backend-тестов;
- production-сборку frontend;
- проверку синтаксиса Linux deployment-скриптов;
- `docker compose config`.

Для командной работы в настройках GitHub рекомендуется защитить `main`:
разрешить изменения только через pull request и сделать jobs `Backend tests`,
`Frontend build` и `Deployment checks` обязательными. Шаблон PR находится в
`.github/pull_request_template.md`.

Production обновляется из чистого и отправленного `main` одной командой с
Windows-машины разработчика:

```powershell
.\deploy\update-server.cmd
```

Скрипт запускает ВМ, создаёт backup PostgreSQL, применяет только новые миграции,
пересобирает контейнеры и проверяет публичный health endpoint. Подробности и
требования к доступам: `docs/YANDEX_VM.md`.

## Ключевые эндпоинты

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/health` | Проверка живости |
| GET | `/health/llm` | Проверка доступности локальной Qwen |
| GET | `/substances/verify?cas=50-78-2` | Верификация вещества по CAS (PubChem) |
| GET/POST | `/substances` | Глобальный справочник веществ · создание карточки |
| GET/PATCH | `/substances/{id}` | Просмотр и изменение экспертных правил вещества |
| GET | `/substances/{id}/history` | История экспертных решений по веществу с авторами |
| POST | `/substances/rfq/{id}/decision` | Подтвердить или отклонить идентификацию ИИ в запросе |
| POST | `/rfq?start_search=true` | Создать закупочный запрос и поставить поиск заданного числа поставщиков по каждой выбранной стране в очередь; разрешены Россия, Китай и Индия |
| POST | `/rfq/preview` | Сгенерировать текст RFQ без сохранения |
| GET | `/rfq/{id}` · `/rfq` | Карточка RFQ · список |
| DELETE | `/rfq/{id}` | Мягко удалить запрос, скрыть его из рабочих списков и остановить активные поиски без уничтожения аудита |
| GET/POST | `/suppliers` | Глобальный реестр компаний с метриками · добавить кандидата |
| POST | `/extraction/quote` | Извлечь котировку из текста (предпросмотр) |
| POST | `/rfq/{id}/extract` | Извлечь и сохранить котировку |
| GET/POST/PATCH | `/prompts` | Библиотека и версии ИИ-промптов |
| POST | `/prompts/preview` | Предпросмотр промпта на локальной Qwen |
| GET/PUT | `/rfq/{id}/ai-settings` | Промпт и инструкции конкретного RFQ |
| POST | `/supplier-search` | ИИ-запрос и поиск кандидатов со ссылками |
| POST | `/supplier-search/jobs?rfq_id={id}` | Поставить полный цикл поиска по России, Китаю или Индии и предварительной квалификации в очередь и сразу получить ID; worker не забирает задания до готовности локальной LLM |
| GET | `/search-runs?rfq_id={id}` | Очередь, история и текущие статусы поисков запроса |
| GET | `/search-runs/{id}?merge_country=true` | Сводка и подробная трассировка выбранного запуска; параметр объединяет уникальных кандидатов, квалификацию и источники всех доступных запусков того же запроса по этой стране |
| POST | `/search-runs/{id}/restart` | Перезапустить ошибочную или зависшую более 30 минут задачу без потери старой трассировки; новый запуск наследует исключения ранее найденных поставщиков |
| POST | `/supplier-search/qualify` | Повторная явная квалификация для совместимости и диагностики; штатная очередь запускает её автоматически |
| POST | `/email/sync` | Загрузить новые ответы из общего IMAP-ящика |
| GET | `/rfq/{id}/communications` | История Email-переписки по RFQ |
| POST | `/communications/{id}/send` | Отправить проверенный дозапрос-черновик |

Для локальной модели 27B оставляйте `LLM_TIMEOUT_S=600`: квалификация пакета
первичных страниц на одной GPU может занимать больше пяти минут. Для надёжного
строгого JSON этап квалификации обрабатывает кандидатов внутренними пакетами по
две компании и объединяет их в один пользовательский результат. Поиск заранее
сохраняет резерв объёмом до трёх целевых списков (не более 60 уникальных
доменов). Недоступная первичная страница не
передаётся ИИ-агенту и автоматически заменяется следующим результатом; при
исчерпании резерва запуск получает статус ошибки, а не ложный статус успешного
завершения. Проверенные результаты автоматически связываются с запросом в
реестре поставщиков как кандидаты.
| POST | `/quotations` | Создать котировку вручную |
| GET | `/rfq/{id}/summary` | Сводная сравнительная таблица |
| GET | `/rfq/{id}/quotations` | Котировки по RFQ |

## Переменные окружения

См. `.env.example`. Ключевые: `DATABASE_URL`, `REDIS_URL`, `LLM_BASE_URL`,
`LLM_MODEL`, `PUBCHEM_BASE_URL`.

## Email: безопасное включение

По умолчанию Compose использует `EMAIL_DELIVERY_MODE=demo` и не отправляет
письма наружу. Для подключения корпоративного ящика заполните в `.env`:

```env
EMAIL_DELIVERY_MODE=live
EMAIL_FROM=procurement@example.com
EMAIL_FROM_NAME=Procurement Department

SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=procurement@example.com
SMTP_PASSWORD=change_me
SMTP_USE_SSL=true
SMTP_STARTTLS=false

IMAP_HOST=imap.example.com
IMAP_PORT=993
IMAP_USER=procurement@example.com
IMAP_PASSWORD=change_me
IMAP_USE_SSL=true
IMAP_FOLDER=INBOX

# Рекомендуется сначала draft, а после приёмки процесса — send.
AUTO_FOLLOWUP_MODE=draft
```

После изменения окружения пересоздайте backend:

```bash
docker compose up -d --build backend frontend
```

Проверьте статус канала в разделе «Настройки». Реальные письма отправляются
только после явного включения `live`. Синхронизация входящих запускается на
вкладке «История» пользователем с ролью руководителя или администратора.
