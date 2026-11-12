# Home Assistant add-on image. The app folder is shared with the standalone Docker version.
FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MDI_MODE=addon

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt
COPY app/ /app/

CMD ["python", "-m", "mdi"]
