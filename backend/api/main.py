"""
FalconEye — FastAPI Application

Run with:
    uvicorn backend.api.main:app --reload
"""

from backend.api.routes import flights, passengers, reservations, changes, ingestion, schedule, audit, otp, comparison, events, data_audit, analytics
from backend.api.database import get_db, close_db
from backend.api.postgres import close_pool as close_pg_pool
from backend.api.redis_client import get_redis, close_redis
from backend.feeder import storage as feeder_storage
import logging
import os
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

# Load .env before importing routes (they need MONGODB_URI)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify MongoDB connection
    try:
        db = get_db()
        # Quick ping to verify the connection is actually live
        db.command("ping")
        # Share the API's DB connection with the feeder storage layer
        # so ingestion doesn't create a second MongoClient (avoids DNS timeouts)
        feeder_storage.init_db(db)
        feeder_storage.ensure_indexes()
        logger.info("MongoDB connected successfully")
    except Exception as exc:
        logger.error("MongoDB connection failed during startup: %s", exc)
        # Allow the app to start so health endpoints can report the issue

    # Verify Redis connection
    try:
        r = get_redis()
        r.ping()
        logger.info("Redis connected successfully")
    except Exception as exc:
        logger.warning("Redis connection failed during startup: %s", exc)

    yield
    # Shutdown: close connections
    close_db()
    close_pg_pool()
    close_redis()


app = FastAPI(
    title="FalconEye API",
    description="Flight Operations Data Platform — Gulf Air",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request correlation ID middleware ─────────────────────────────────────


@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    """Attach a unique X-Request-ID to every request/response for tracing."""
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = req_id
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


app.include_router(flights.router)
app.include_router(ingestion.router)
app.include_router(passengers.router)
app.include_router(reservations.router)
app.include_router(changes.router)
app.include_router(changes.activity_router)  # Global activity feed
app.include_router(schedule.router)
app.include_router(audit.router)
app.include_router(otp.router)
app.include_router(comparison.router)
app.include_router(analytics.router)
app.include_router(events.router)
app.include_router(data_audit.router)


# ── Global exception handlers ─────────────────────────────────────────────

@app.exception_handler(PyMongoError)
async def pymongo_exception_handler(request: Request, exc: PyMongoError):
    """Return 503 for all unhandled MongoDB errors instead of a bare 500."""
    logger.exception("Database error on %s %s",
                     request.method, request.url.path)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable — please retry shortly"},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch-all so every error returns a proper JSON response with CORS headers."""
    logger.exception("Unhandled error on %s %s",
                     request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "service": "FalconEye API"}


@app.get("/health/app", tags=["health"])
def health_app():
    """Application health — confirms the API process is running."""
    return {"status": "healthy", "service": "FalconEye API", "version": "1.0.0"}


@app.get("/health/db", tags=["health"])
def health_db():
    """Database health — pings MongoDB and reports connectivity."""
    try:
        get_db().command("ping")
        return {"status": "healthy", "database": "connected"}
    except Exception:
        logger.exception("Database health check failed")
        return {"status": "unhealthy", "database": "unreachable"}
