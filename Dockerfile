FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libchromaprint-tools ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 1000 --no-create-home dragontag

WORKDIR /app

COPY pyproject.toml /app/
COPY dragontag /app/dragontag

RUN pip install --upgrade pip && pip install .

VOLUME ["/library", "/drop", "/config"]
EXPOSE 7593

ENV AIO_LIBRARY_PATH=/library \
    AIO_DROP_PATH=/drop \
    AIO_CONFIG_PATH=/config

USER dragontag
CMD ["uvicorn", "dragontag.app.main:app", "--host", "0.0.0.0", "--port", "7593"]
