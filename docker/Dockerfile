# Standalone image. Build context is the repository root (see docker-compose.yml).
FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MDI_MODE=docker \
    OUTPUT_DIR=/output \
    DATA_DIR=/data \
    HA_CONFIG_DIR=/config

WORKDIR /app
COPY my_device_information/app/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt \
 && mkdir -p /output /data \
 && chown 1000:1000 /output /data
COPY my_device_information/app/ /app/

USER 1000:1000
EXPOSE 8099
VOLUME ["/output", "/data"]
HEALTHCHECK --interval=60s --timeout=6s --start-period=10s CMD ["python", "/app/healthcheck.py"]
CMD ["python", "-m", "mdi"]
