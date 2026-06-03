# Бэкенд ChemSource AI — образ для on-premise развёртывания.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала зависимости — кэш слоёв Docker не инвалидируется при правках кода.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения.
COPY . .

EXPOSE 8000

# Прод-запуск без --reload. Миграции/создание таблиц — на старте приложения.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
