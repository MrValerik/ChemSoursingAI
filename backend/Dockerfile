# Бэкенд ChemSource AI — образ для on-premise развёртывания.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

WORKDIR /app

# OCR сканов паспортов качества: Tesseract с русским, английским и упрощённым
# китайским, poppler для растеризации PDF. Ставится до зависимостей Python,
# чтобы слой кэшировался и не пересобирался при правках кода.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-rus \
        tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# Сначала зависимости — кэш слоёв Docker не инвалидируется при правках кода.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения.
COPY . .

EXPOSE 8000

# Прод-запуск без --reload. Миграции/создание таблиц — на старте приложения.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
