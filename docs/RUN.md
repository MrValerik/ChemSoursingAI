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
docker compose up --build
```

Поднимутся три сервиса: `db` (Postgres + pgvector), `redis`, `backend`.
Бэкенд дождётся готовности БД (healthcheck) и создаст таблицы на старте.

Остановить: `docker compose down` (данные БД сохраняются в томе `pgdata`;
для полной очистки — `docker compose down -v`).

### Подключение локальной LLM

LLM не входит в образ (большой GGUF-файл + GPU). Запустите `llama-server`
(или vLLM) на хосте и укажите адрес:

```bash
LLM_BASE_URL=http://host.docker.internal:8080/v1 LLM_MODEL=qwen3-8b docker compose up
```

Без LLM конвейер извлечения работает на правилах (fallback) — система остаётся
работоспособной.

## 3. Тесты

```bash
cd backend
pytest
```

## Ключевые эндпоинты

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/health` | Проверка живости |
| GET | `/substances/verify?cas=50-78-2` | Верификация вещества по CAS (PubChem) |
| POST | `/rfq` | Создать RFQ (верификация + сохранение) |
| POST | `/rfq/preview` | Сгенерировать текст RFQ без сохранения |
| GET | `/rfq/{id}` · `/rfq` | Карточка RFQ · список |
| POST | `/extraction/quote` | Извлечь котировку из текста (предпросмотр) |
| POST | `/rfq/{id}/extract` | Извлечь и сохранить котировку |
| POST | `/quotations` | Создать котировку вручную |
| GET | `/rfq/{id}/summary` | Сводная сравнительная таблица |
| GET | `/rfq/{id}/quotations` | Котировки по RFQ |

## Переменные окружения

См. `.env.example`. Ключевые: `DATABASE_URL`, `REDIS_URL`, `LLM_BASE_URL`,
`LLM_MODEL`, `PUBCHEM_BASE_URL`.
