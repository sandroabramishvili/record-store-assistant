# API image: FastAPI + LangGraph + migrations + seed scripts.
# Same Python as local development (3.14); exact package versions from the lock file.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is cached until the lock file changes.
COPY requirements.lock.txt .
RUN pip install -r requirements.lock.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts

# Don't run as root.
RUN useradd --create-home --uid 1000 store && chown -R store /app
USER store

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# Migrations on every start (idempotent), then ONE uvicorn worker: the "one run per chat"
# guard (app/ai/chat.py) is in-process, so it is correct for exactly one worker.
# On Linux the default event loop works with psycopg; app/loop.py is only needed on Windows.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
