FROM mcr.microsoft.com/playwright/python:v1.62.0-noble
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends xvfb xauth && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data && chown pwuser:pwuser /data /app
USER pwuser
CMD ["xvfb-run", "-a", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
