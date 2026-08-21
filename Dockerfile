# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.27" \
        "httpx>=0.27" \
        "pydantic>=2.6" \
        "pydantic-settings>=2.2" \
        "rich>=13.7" \
        "numpy>=1.26" \
        "aiosqlite>=0.20" \
        "sqlite-vec>=0.1.0" \
        "faster-whisper>=1.0" \
        "croniter>=2.0" \
        "python-multipart>=0.0.9" \
        "ruff>=0.7.0" \
        "pytest>=8.0" \
        "pytest-asyncio>=0.23" \
        "sqlcipher3>=0.6.0" \
        "cryptography>=42.0"

FROM python:3.11-slim AS runtime

RUN groupadd --system --gid 1000 assistant \
    && useradd --system --uid 1000 --gid assistant --no-create-home assistant

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
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
        faster-whisper \
        croniter \
        python-multipart \
        ruff \
        pytest \
        pytest-asyncio \
        sqlcipher3 \
        cryptography \
    && rm -rf /wheels

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

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "assistant.backend.main:app", "--host", "${BACKEND_HOST}", "--port", "8000"]
