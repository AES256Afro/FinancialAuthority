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

# ---- uv, used to install the locked dependencies and never shipped ----------------------------------------------
# Pinned by digest like the bases (Dependabot moves it); it used to be `pip install uv` at build time, the newest
# release from PyPI with no version or hash, left in the image afterwards.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# ---- backend -----------------------------------------------------------------
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime
ARG VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FA_DATA_DIR=/data \
    FA_IMPORT_DIR=/imports \
    FA_HOST=0.0.0.0 \
    FA_PORT=8000 \
    FA_INSTALL=docker \
    FA_OLLAMA_URL=http://host.docker.internal:11434

# tesseract-ocr and poppler-utils read photos and scanned PDFs on this server (M49): free, and nothing leaves it.
# The base is pinned by digest, so its own packages are only as new as the pin: the upgrade takes Debian's security
# fixes released since (0.10.4 shipped without two, liblzma5 among them). curl is the healthcheck's.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends curl tesseract-ocr tesseract-ocr-eng poppler-utils \
 && rm -rf /var/lib/apt/lists/*

# The locked dependencies only, each checked against its hash in uv.lock. The app itself is not installed as a
# package (that would fetch an unlocked build backend from PyPI): it runs from /app/backend, the working directory.
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1 uv sync --frozen --no-dev --no-install-project \
 && rm -rf /tmp/uv-* /app/backend/.venv/.lock
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist

# The app runs as its own user, which owns the data and import folders only: the code and the virtual environment
# stay root-owned and read-only to it, so nothing that runs in the app can change what the app is. Nothing in the
# image is setuid or setgid, and the installer (pip) the app never uses is gone.
RUN useradd --create-home --uid 1000 app && mkdir -p /data /imports && chown -R app:app /data /imports \
 && python -m pip uninstall -y -q pip \
 && find / -xdev -type f -perm /6000 -exec chmod a-s {} +
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fs http://127.0.0.1:8000/api/health || exit 1

CMD ["/app/backend/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
