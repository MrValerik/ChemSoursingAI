# Отрисовка страниц браузером. Отдельный образ: здесь исполняется чужой
# JavaScript с сайтов поставщиков, и в контейнере с базой и почтой ему не
# место. Образ Playwright уже содержит Chromium и его системные библиотеки.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
