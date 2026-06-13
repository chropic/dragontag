FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# rsgain ships a prebuilt Debian package built for bookworm (this image's base);
# apt-get install ./file.deb resolves its ffmpeg/libav runtime deps. Pinned for
# reproducible builds. The release asset has no Debian revision suffix.
ARG RSGAIN_VERSION=3.7
RUN apt-get update \
 && apt-get install -y --no-install-recommends libchromaprint-tools ca-certificates curl \
 && curl -fsSL --retry 3 -o /tmp/rsgain.deb \
      "https://github.com/complexlogic/rsgain/releases/download/v${RSGAIN_VERSION}/rsgain_${RSGAIN_VERSION}_amd64.deb" \
 && apt-get install -y --no-install-recommends /tmp/rsgain.deb \
 && rm -f /tmp/rsgain.deb \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 1000 --no-create-home dragontag

WORKDIR /app

COPY pyproject.toml /app/
COPY dragontag /app/dragontag

RUN pip install --upgrade pip && pip install .

VOLUME ["/library", "/drop", "/config"]
EXPOSE 7593
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7593/health || exit 1

ENV DRAGONTAG_LIBRARY_PATH=/library \
    DRAGONTAG_DROP_PATH=/drop \
    DRAGONTAG_CONFIG_PATH=/config

USER dragontag
CMD ["uvicorn", "dragontag.app.main:app", "--host", "0.0.0.0", "--port", "7593"]
