"""Comparison API endpoints — cross-database flight data analysis.

Provides both synchronous (small) and async (Celery) comparison modes.
"""

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.api.auth import require_api_token
from backend.api.redis_client import (
    COMPARISON_JOB_TTL,
    COMPARISON_JOB_TYPES,
    JobLimitExceeded,
    create_job_within_limit,
    get_job,
    set_job_failed,
)

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/compare",
    tags=["comparison"],
    dependencies=[Depends(require_api_token)],
)

MAX_OPEN_COMPARISON_JOBS = 4
MAX_FLIGHT_COUNT = 20
MAX_FLIGHT_LIST_CHARS = 256


def _parse_flight_numbers(raw: str | None) -> list[str] | None:
    """Split a comma-separated flight list, rejecting oversized input with 422."""
    if not raw:
        return None
    if len(raw) > MAX_FLIGHT_LIST_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"flight_numbers must be at most {MAX_FLIGHT_LIST_CHARS} characters",
        )
    numbers = [f.strip() for f in raw.split(",") if f.strip()]
    if len(numbers) > MAX_FLIGHT_COUNT:
        raise HTTPException(
            status_code=422,
            detail=f"flight_numbers may list at most {MAX_FLIGHT_COUNT} flights",
        )
    return numbers or None


async def _admit_comparison_job(job_type: str) -> str:
    job_id = str(uuid.uuid4())
    try:
        await run_in_threadpool(
            create_job_within_limit,
            job_id,
            1,
            job_type,
            counted_types=COMPARISON_JOB_TYPES,
            limit=MAX_OPEN_COMPARISON_JOBS,
        )
    except JobLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "60"})
    return job_id


def _enqueue(task, job_id: str, *args) -> None:
    try:
        task.apply_async(args=(job_id, *args), expires=COMPARISON_JOB_TTL)
    except Exception:
        set_job_failed(job_id, "Failed to enqueue comparison job")
        raise


# ── Response models ───────────────────────────────────────────────────────

class ComparisonJobAccepted(BaseModel):
    jobId: str
    status: str
    message: str
    pollUrl: str
    sseUrl: str


# ── Synchronous endpoints (small result sets) ────────────────────────────

@router.get("/flights")
async def compare_flights_sync(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    flight_numbers: str = Query(
        default=None, description="Comma-separated flight numbers"),
):
    """Synchronous Sabre vs OTP flight comparison (fast, for small sets)."""
    from backend.feeder.comparison import compare_flights_for_date

    fn_list = _parse_flight_numbers(flight_numbers)

    result = await run_in_threadpool(compare_flights_for_date, date, fn_list)
    return result


@router.get("/passengers")
async def compare_passengers_sync(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    flight_numbers: str = Query(
        default=None, description="Comma-separated flight numbers"),
):
    """Passenger-level aggregation across flights using Polars."""
    from backend.feeder.comparison import compare_passengers_across_flights

    fn_list = _parse_flight_numbers(flight_numbers)

    result = await run_in_threadpool(compare_passengers_across_flights, date, fn_list)
    return result


@router.get("/pnr-mismatches")
async def compare_pnr_mismatches(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """Find PNRs in passenger list but not reservations, and vice versa.

    Uses MongoDB $lookup + $setDifference aggregation (server-side).
    """
    from backend.feeder.aggregations import find_missing_pnrs

    result = await run_in_threadpool(find_missing_pnrs, date)
    return result


@router.get("/status-distribution")
async def passenger_status_distribution(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """Passenger status distribution by cabin across all flights (server-side agg)."""
    from backend.feeder.aggregations import passenger_status_distribution as agg_dist

    result = await run_in_threadpool(agg_dist, date)
    return result


@router.get("/change-summary")
async def change_type_summary(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """Summary of all change types detected for a date."""
    from backend.feeder.aggregations import change_type_summary as agg_changes

    result = await run_in_threadpool(agg_changes, date)
    return result


# ── Async endpoints (large jobs via Celery) ───────────────────────────────

@router.post("/flights/async", response_model=ComparisonJobAccepted, status_code=202)
async def compare_flights_async(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    flight_numbers: str = Query(default=None),
):
    """Submit a large flight comparison job to Celery."""
    from backend.tasks.comparison import run_flight_comparison

    fn_list = _parse_flight_numbers(flight_numbers)
    job_id = await _admit_comparison_job("comparison")
    _enqueue(run_flight_comparison, job_id, date, fn_list)

    return {
        "jobId": job_id,
        "status": "accepted",
        "message": f"Flight comparison job submitted for {date}",
        "pollUrl": f"/flights/ingest/jobs/{job_id}",
        "sseUrl": f"/events/jobs/{job_id}",
    }


@router.post("/pnr-mismatches/async", response_model=ComparisonJobAccepted, status_code=202)
async def compare_pnr_async(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """Submit a PNR cross-database comparison to Celery."""
    from backend.tasks.comparison import run_pnr_comparison

    job_id = await _admit_comparison_job("pnr_comparison")
    _enqueue(run_pnr_comparison, job_id, date)

    return {
        "jobId": job_id,
        "status": "accepted",
        "message": f"PNR comparison job submitted for {date}",
        "pollUrl": f"/flights/ingest/jobs/{job_id}",
        "sseUrl": f"/events/jobs/{job_id}",
    }


# ── Job status (shared with ingestion) ────────────────────────────────────

@router.get("/jobs/{job_id}")
async def get_comparison_job(job_id: str):
    """Get the status of a comparison job."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
