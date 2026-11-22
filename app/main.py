"""FastAPI application: creates the app and plugs in the routers.

Run locally (from record-store-assistant/):  uvicorn app.main:app --reload
Swagger UI: http://localhost:8000/docs
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import engine
from app.routers import auth, collection, inventory, profile, records, wantlist

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup work goes before `yield` (Phase 11: LangGraph checkpointer).
    yield
    # Shutdown: close all pooled DB connections cleanly.
    await engine.dispose()


app = FastAPI(title='Record Store Assistant', version='0.1.0', lifespan=lifespan)

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(records.router)
app.include_router(inventory.router)
app.include_router(collection.router)
app.include_router(wantlist.router)


@app.get('/health', tags=['meta'])
async def health() -> dict[str, str]:
    """Liveness check for Docker / monitoring. No auth, no DB."""
    return {'status': 'ok'}
