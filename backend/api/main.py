"""
FalconEye — FastAPI Application

Run with:
    uvicorn backend.api.main:app --reload
"""

from backend.api.routes import flights, passengers, reservations, changes, ingestion
from backend.api.database import get_db, close_db
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env before importing routes (they need MONGODB_URI)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify MongoDB connection
    get_db()
    yield
    # Shutdown: close connection
    close_db()


app = FastAPI(
    title="FalconEye API",
    description="Flight Operations Data Platform — Gulf Air",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(flights.router)
app.include_router(ingestion.router)
app.include_router(passengers.router)
app.include_router(reservations.router)
app.include_router(changes.router)


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
    except Exception as e:
        return {"status": "unhealthy", "database": str(e)}
