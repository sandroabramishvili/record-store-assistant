"""FastAPI application: creates the app and plugs in the routers.

Run locally (from record-store-assistant/):  uvicorn app.main:app --reload
Swagger UI: http://localhost:8000/docs
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.ai.vectorstores import close_vector_stores
from app.db import engine
from app.routers import auth, collection, inventory, knowledge, profile, records, reservations, wantlist
from app.services.reservations import ItemNotFound, ReservationError

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup work goes before `yield` (Phase 11: LangGraph checkpointer).
    yield
    # Shutdown: close all pooled DB connections cleanly.
    await close_vector_stores()
    await engine.dispose()


app = FastAPI(title='Record Store Assistant', version='0.1.0', lifespan=lifespan)

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(records.router)
app.include_router(inventory.router)
app.include_router(collection.router)
app.include_router(wantlist.router)
app.include_router(reservations.router)
app.include_router(knowledge.router)


@app.exception_handler(ReservationError)
async def reservation_error_handler(request: Request, exc: ReservationError) -> JSONResponse:
    """One place that turns reservation service errors into HTTP responses.

    Not found → 404; conflict / limit reached / no longer active → 409.
    The message is customer-friendly, so the UI can show `detail` as is.
    """
    code = status.HTTP_404_NOT_FOUND if isinstance(exc, ItemNotFound) else status.HTTP_409_CONFLICT
    return JSONResponse(status_code=code, content={'detail': str(exc), 'error': type(exc).__name__})


@app.get('/health', tags=['meta'])
async def health() -> dict[str, str]:
    """Liveness check for Docker / monitoring. No auth, no DB."""
    return {'status': 'ok'}
