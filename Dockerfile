FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    DATABASE_URL=sqlite+aiosqlite:////data/firefox-vrt.db

WORKDIR /app

# Install minimal system deps (Pillow needs libjpeg/zlib at runtime, but the
# wheel ships them; we only need build-tools if a wheel is missing).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY firefox_vrt ./firefox_vrt
RUN pip install --upgrade pip && pip install .

# Persistent data lives on a mount.
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "firefox_vrt.app:app", "--host", "0.0.0.0", "--port", "8000"]
