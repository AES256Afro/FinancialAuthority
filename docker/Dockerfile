# One image: the frontend is built in a first stage and served by the backend.
# Build:  docker build -f docker/Dockerfile -t financialauthority .
# Run:    see deploy/compose/docker-compose.yml, or install from BoxPilot's catalog on BigBox.

# ---- frontend build ----------------------------------------------------------
FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- backend -----------------------------------------------------------------
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime
ARG VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FA_DATA_DIR=/data \
    FA_IMPORT_DIR=/imports \
    FA_HOST=0.0.0.0 \
    FA_PORT=8000 \
    FA_OLLAMA_URL=http://host.docker.internal:11434

RUN apt-get update && apt-get install -y --no-install-recommends curl \
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
