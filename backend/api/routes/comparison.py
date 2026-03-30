"""Comparison API endpoints — cross-database flight data analysis.

Provides both synchronous (small) and async (Celery) comparison modes.
"""

import uuid
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.api.redis_client import create_job, get_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/compare", tags=["comparison"])


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

    fn_list = [f.strip() for f in flight_numbers.split(",")
               ] if flight_numbers else None

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

    fn_list = [f.strip() for f in flight_numbers.split(",")
               ] if flight_numbers else None

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

    job_id = str(uuid.uuid4())
    fn_list = [f.strip() for f in flight_numbers.split(",")
               ] if flight_numbers else None

    create_job(job_id, 1, job_type="comparison")
    run_flight_comparison.delay(job_id, date, fn_list)

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

    job_id = str(uuid.uuid4())
    create_job(job_id, 1, job_type="pnr_comparison")
    run_pnr_comparison.delay(job_id, date)

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
