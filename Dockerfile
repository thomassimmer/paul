# syntax=docker/dockerfile:1

FROM python:3.12-slim

# WITH_PDF=1 (default) bundles LibreOffice for DOCX -> PDF export, the page fit
# check and the review preview (larger image, ~500 MB). Build without it for a
# much smaller image:
#   WITH_PDF=0 docker compose build
# The fit check then falls back to an estimate, and the preview to plain HTML.
ARG WITH_PDF=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PAUL_DATA_DIR=/app/data

WORKDIR /app

# LibreOffice comes from apt. The package caches live in BuildKit mounts rather
# than in the layer, so a rebuild reuses the downloaded .debs instead of fetching
# them again. Nothing is cleaned up afterwards: the mounts are not part of the
# image, so the lists never end up in it.
#
# Carlito is Calibri's metric-compatible clone (SIL OFL). LibreOffice substitutes
# it automatically, so a template written in Calibri keeps Word's line and page
# breaks. Without it the substitution falls back to DejaVu, which is wider and
# visibly different.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    apt-get update; \
    if [ "$WITH_PDF" = "1" ]; then \
        apt-get install -y --no-install-recommends \
            libreoffice-writer \
            fonts-dejavu-core \
            fonts-crosextra-carlito; \
    fi

# Dependencies first, and from the metadata alone: a stub package and a stub
# README stand in for the real ones so that editing app/ or the README does not
# invalidate this layer. That is the difference between a two-second rebuild and
# reinstalling every wheel. The package is never imported — PYTHONPATH puts /app
# first, and the real one is copied over it at the next step — and nothing reads
# the README at runtime: setuptools only needs it to build the metadata, which is
# why a placeholder is enough to keep this layer stable.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    set -eux; \
    mkdir -p app && touch app/__init__.py; \
    echo "# Paul (Emploi)" > README.md; \
    pip install --upgrade pip; \
    pip install .

# The application itself: the only layer that changes on an edit.
COPY app ./app

EXPOSE 8000

# The web UI has no authentication: it is published on 127.0.0.1 only (see
# docker-compose.yml). Inside the container we listen on all interfaces so the
# port mapping can reach us.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
