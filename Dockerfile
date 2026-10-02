FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts

# Run as an unprivileged user. SQLite lives in /data - mount a volume so it survives
# container rebuilds (the volume inherits this ownership).
RUN useradd --system --uid 10001 --create-home pika \
    && mkdir -p /data \
    && chown -R pika:pika /data /app
ENV DB_PATH=/data/assistant.db
VOLUME /data
USER pika

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"
# Binds 0.0.0.0 *inside* the container; docker-compose publishes it on the host's
# loopback only. Put a tunnel/reverse proxy in front for the public HTTPS URL.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
