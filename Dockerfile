FROM python:3.11-slim-bookworm AS web
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements/ /app/requirements/
RUN pip install --no-cache-dir -r requirements/web.txt && useradd --create-home --uid 10001 app
COPY knowpipe/ /app/knowpipe/
USER app
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "gthread", "--threads", "16", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "knowpipe.web.app:create_app()"]

FROM web AS worker
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless ffmpeg && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements/worker.txt -r requirements/media.txt -r requirements/semantic.txt \
    && mkdir -p /var/lib/knowpipe/recommendations \
    && chown -R app:app /var/lib/knowpipe
ENV SPARK_LOCAL_IP=127.0.0.1 SPARK_MASTER=local[2]
USER app
CMD ["python", "-m", "knowpipe.podcasts.worker"]
