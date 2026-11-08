FROM python:3.12-slim

LABEL org.opencontainers.image.title="Bissfest" \
      org.opencontainers.image.description="Selbst gehosteter Ernährungs-Tracker: Mit Biss zum Wunschgewicht" \
      org.opencontainers.image.source="https://github.com/Skarhand/bissfest" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/data/abnehm.db BACKUP_DIR=/backup TZ=Europe/Berlin \
    SOURCE_URL=https://github.com/Skarhand/bissfest
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

RUN useradd -r -u 1000 appuser && mkdir -p /data /backup && chown appuser /data /backup
USER appuser
VOLUME /data /backup
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/config')"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
