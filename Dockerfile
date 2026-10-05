# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# The package source is needed for the metadata build (`.[dev]`); this is not
# the runtime copy, which happens in the final stage.
COPY assistant/ ./assistant/
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels ".[dev]"

# --- Frontend build stage ---
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

FROM python:3.11-slim AS runtime

RUN groupadd --system --gid 1000 assistant \
    && useradd --system --uid 1000 --gid assistant --no-create-home assistant

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
# Install the built wheels by name. They were resolved from pyproject.toml in the
# builder stage, so this list need not repeat version pins -- it only names what
# to install from /wheels.
RUN pip install --no-cache-dir --no-index --find-links /wheels \
        fastapi \
        "uvicorn[standard]" \
        httpx \
        pydantic \
        pydantic-settings \
        rich \
        numpy \
        aiosqlite \
        sqlite-vec \
        sqlcipher3 \
        cryptography \
        faster-whisper \
        python-multipart \
        pypdf \
        python-docx \
        openpyxl \
        python-pptx \
        icalendar \
        striprtf \
        odfpy \
        xlrd \
        defusedxml \
        reportlab \
        ruff \
        pytest \
        pytest-asyncio \
        sqlalchemy \
        xlwt \
    && rm -rf /wheels

COPY --from=frontend-builder /app/assistant/backend/static /app/assistant/backend/static

COPY --chown=assistant:assistant assistant/ /app/assistant/

RUN mkdir -p /app/data /app/.cache/whisper /home/assistant && chown -R assistant:assistant /app

USER assistant

ENV DATABASE_PATH=/app/data/assistant.db \
    BACKEND_HOST=127.0.0.1 \
    BACKEND_PORT=8000 \
    OLLAMA_URL=http://host.docker.internal:11434 \
    WHISPER_MODEL=base \
    WHISPER_DEVICE=cpu \
    HF_HOME=/app/.cache/huggingface \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8000

# Liveness, not readiness: /healthz does no I/O. The previous probe hit /health,
# which calls Ollama and can block for the client timeout (up to OLLAMA_TIMEOUT,
# 600s) when Ollama is down — so an Ollama outage marked the app unhealthy, and
# with autoheal that meant restarting a container a restart cannot fix.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/healthz || exit 1

CMD ["uvicorn", "assistant.backend.main:app", "--host", "${BACKEND_HOST}", "--port", "8000"]