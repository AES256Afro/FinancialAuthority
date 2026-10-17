# One image: the frontend is built in a first stage and served by the backend.
# Build:  docker build -f docker/Dockerfile -t financialauthority .
# Run:    see deploy/compose/docker-compose.yml, or install from BoxPilot's catalog on BigBox.

# ---- frontend build ----------------------------------------------------------
FROM node:25-alpine@sha256:bdf2cca6fe3dabd014ea60163eca3f0f7015fbd5c7ee1b0e9ccb4ced6eb02ef4 AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- backend -----------------------------------------------------------------
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime
ARG VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FA_DATA_DIR=/data \
    FA_IMPORT_DIR=/imports \
    FA_HOST=0.0.0.0 \
    FA_PORT=8000 \
    FA_OLLAMA_URL=http://host.docker.internal:11434

# tesseract-ocr and poppler-utils read photos and scanned PDFs on this server (M49): free, and nothing leaves it.
RUN apt-get update && apt-get install -y --no-install-recommends curl tesseract-ocr tesseract-ocr-eng poppler-utils \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/app/version.py ./app/version.py
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist /app/frontend/dist

RUN useradd --create-home --uid 1000 app && mkdir -p /data /imports && chown -R app:app /data /imports /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fs http://127.0.0.1:8000/api/health || exit 1

CMD ["/app/backend/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
