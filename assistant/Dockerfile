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
        "ruff==0.7.0" \
        "pytest>=7.1.2" \
        "pytest-asyncio>=0.23"

FROM python:3.11-slim AS runtime

RUN groupadd --system --gid 1000 assistant \
    && useradd --system --uid 1000 --gid assistant --no-create-home assistant

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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
        ruff \
        pytest \
        pytest-asyncio \
    && rm -rf /wheels

COPY --chown=assistant:assistant backend/ /app/backend/
COPY --chown=assistant:assistant cli/ /app/cli/
COPY --chown=assistant:assistant pyproject.toml /app/

RUN mkdir -p /app/data && chown -R assistant:assistant /app

USER assistant

ENV DATABASE_PATH=/app/data/assistant.db \
    BACKEND_HOST=127.0.0.1 \
    BACKEND_PORT=8000 \
    OLLAMA_URL=http://host.docker.internal:11434 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "assistant.backend.main:app", "--host", "127.0.0.1", "--port", "8000"]

