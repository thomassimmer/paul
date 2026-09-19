# syntax=docker/dockerfile:1

FROM python:3.12-slim

# WITH_PDF=1 (default) bundles LibreOffice for DOCX -> PDF export and the page
# fit check (larger image, ~500 MB). Build without it for a much smaller image:
#   WITH_PDF=0 docker compose build
# The fit check then falls back to an estimate.
ARG WITH_PDF=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    PAUL_DATA_DIR=/app/data

WORKDIR /app

RUN set -eux; \
    apt-get update; \
    if [ "$WITH_PDF" = "1" ]; then \
        apt-get install -y --no-install-recommends \
            libreoffice-writer \
            fonts-dejavu-core; \
    fi; \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

# The web UI has no authentication: it is published on 127.0.0.1 only (see
# docker-compose.yml). Inside the container we listen on all interfaces so the
# port mapping can reach us.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
