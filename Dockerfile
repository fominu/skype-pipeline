FROM python:3.11-slim

# Библиотеки для reportlab
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 libjpeg62-turbo zlib1g && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PYTHONUNBUFFERED=1

# Cloud Run отдаёт переменную PORT — слушаем её
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
