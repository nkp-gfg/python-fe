"""Sabre ingestion trigger endpoints.

Provides three modes of ingestion:
  POST /flights/ingest          — synchronous, single flight
  POST /flights/ingest/batch    — background, multiple flights
  GET  /flights/ingest/jobs/{id} — poll a background job
"""

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend.feeder.runner import run_feeder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/flights", tags=["ingestion"])

# ── In-process job store ──────────────────────────────────────────────────
# Lightweight dict-based store; sufficient for single-process deployments.
# For multi-worker production, swap this for a shared store (Redis / Mongo).

_jobs: dict[str, dict] = {}


def _new_job_id() -> str:
    return str(uuid.uuid4())


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Request / Response models ─────────────────────────────────────────────

class SabreIngestRequest(BaseModel):
    """Payload required to fetch a flight from Sabre and persist it."""

    airline: str = Field(
        default="GF", min_length=2, max_length=2,
        pattern=r"^[A-Z0-9]{2}$",
        description="2-character IATA airline code",
    )
    flightNumber: str = Field(
        ..., min_length=1, max_length=5,
        pattern=r"^\d{1,5}$",
        description="Flight number (digits only)",
    )
    origin: str = Field(
        ..., min_length=3, max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="3-letter IATA departure airport code",
    )
    departureDate: date = Field(..., description="Departure date YYYY-MM-DD")
    departureDateTime: datetime = Field(
        ..., description="Departure timestamp YYYY-MM-DDTHH:MM:SS"
    )


class SabreApiResult(BaseModel):
    """Execution result for one Sabre business API."""

    status: Literal["success", "error"]
    apiType: str | None = None
    snapshotType: str | None = None
    requestId: str | None = None
    snapshotId: str | None = None
    checksum: str | None = None
    isDuplicate: bool | None = None
    changesStored: int | None = None
    httpStatus: int | None = None
    durationMs: int | None = None
    error: str | None = None


class SabreFlightPayload(BaseModel):
    """Normalized flight payload echoed back in the ingestion response."""

    airline: str
    flightNumber: str
    origin: str
    departureDate: str
    departureDateTime: str


class SabreFlightIngestResult(BaseModel):
    """Per-flight ingestion outcome."""

    flight: SabreFlightPayload
    success: bool
    apis: dict[str, SabreApiResult]


class SabreIngestResponse(BaseModel):
    """Top-level response for the single-flight synchronous endpoint."""

    message: str
    processedFlights: int
    result: SabreFlightIngestResult


class SabreBatchRequest(BaseModel):
    """Payload for batch ingestion — accepts multiple flights."""

    flights: list[SabreIngestRequest] = Field(
        ..., min_length=1, max_length=50,
        description="One or more flights to ingest",
    )


class SabreBatchAccepted(BaseModel):
    """Returned immediately when a batch job is accepted."""

    jobId: str
    status: Literal["accepted"]
    flightsQueued: int
    message: str
    pollUrl: str


class SabreJobStatus(BaseModel):
    """Current state of a background ingestion job."""

    jobId: str
    status: Literal["accepted", "running", "completed", "failed"]
    flightsQueued: int
    flightsProcessed: int
    submittedAt: str
    startedAt: str | None = None
    completedAt: str | None = None
    results: list[SabreFlightIngestResult] | None = None
    error: str | None = None


# ── Background runner ─────────────────────────────────────────────────────

async def _run_batch_job(job_id: str, payloads: list[dict]) -> None:
    """Execute the feeder in a threadpool and update the job store."""
    _jobs[job_id]["status"] = "running"
    _jobs[job_id]["startedAt"] = _utc_now()
    try:
        result = await run_in_threadpool(run_feeder, payloads)
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["flightsProcessed"] = result["processedFlights"]
        _jobs[job_id]["results"] = result["results"]
    except Exception as exc:
        logger.exception("Batch job %s failed", job_id)
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(exc)
    finally:
        _jobs[job_id]["completedAt"] = _utc_now()


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/ingest", response_model=SabreIngestResponse)
async def ingest_flight(payload: SabreIngestRequest):
    """Trigger the Sabre SOAP pipeline for one flight (synchronous)."""
    feeder_payload = payload.model_dump(mode="json")
    try:
        result = await run_in_threadpool(run_feeder, [feeder_payload])
    except Exception as exc:
        logger.exception("Ingestion failed for %s%s",
                         payload.airline, payload.flightNumber)
        raise HTTPException(
            status_code=502,
            detail=f"Ingestion pipeline error: {exc}",
        )
    flight_result = result["results"][0] if result["results"] else None

    if flight_result is None:
        raise HTTPException(
            status_code=500, detail="Ingestion completed without a result")

    return {
        "message": "Sabre ingestion completed",
        "processedFlights": result["processedFlights"],
        "result": flight_result,
    }


@router.post(
    "/ingest/batch",
    response_model=SabreBatchAccepted,
    status_code=202,
)
async def ingest_batch(payload: SabreBatchRequest):
    """Accept a batch of flights for background ingestion.

    Returns immediately with a job ID.  Poll GET /flights/ingest/jobs/{jobId}
    for progress and results.
    """
    job_id = _new_job_id()
    payloads = [f.model_dump(mode="json") for f in payload.flights]

    _jobs[job_id] = {
        "jobId": job_id,
        "status": "accepted",
        "flightsQueued": len(payloads),
        "flightsProcessed": 0,
        "submittedAt": _utc_now(),
        "startedAt": None,
        "completedAt": None,
        "results": None,
        "error": None,
    }

    # Fire-and-forget on the running event loop
    asyncio.ensure_future(_run_batch_job(job_id, payloads))

    return {
        "jobId": job_id,
        "status": "accepted",
        "flightsQueued": len(payloads),
        "message": f"Batch ingestion accepted — {len(payloads)} flight(s) queued",
        "pollUrl": f"/flights/ingest/jobs/{job_id}",
    }


@router.get("/ingest/jobs/{job_id}", response_model=SabreJobStatus)
async def get_job_status(job_id: str):
    """Poll the status of a background ingestion job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
