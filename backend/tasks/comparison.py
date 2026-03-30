"""Celery tasks for cross-database comparison (MongoDB vs PostgreSQL)."""

import logging

from backend.celery_app import app
from backend.api.redis_client import (
    set_job_running, set_job_progress, set_job_completed, set_job_failed,
)

logger = logging.getLogger(__name__)


@app.task(bind=True, name="falconeye.compare_flights", max_retries=1, default_retry_delay=60)
def run_flight_comparison(self, job_id: str, date: str, flight_numbers: list[str] | None = None) -> dict:
    """Compare Sabre (MongoDB) passenger data against OTP (PostgreSQL) for a date.

    If flight_numbers is provided, only those flights are compared.
    Otherwise, all flights for the date are compared.
    """
    from backend.feeder.comparison import compare_flights_for_date
    from backend.feeder import storage

    set_job_running(job_id)

    try:
        storage.get_db()

        result = compare_flights_for_date(
            date,
            flight_numbers=flight_numbers,
            progress_callback=_make_progress_cb(job_id),
        )

        set_job_completed(job_id, result["flightsCompared"], result)
        return {"jobId": job_id, **result}
    except Exception as exc:
        logger.exception("Comparison job %s failed", job_id)
        set_job_failed(job_id, str(exc))
        raise self.retry(exc=exc)


@app.task(bind=True, name="falconeye.compare_pnr_cross_db", max_retries=1)
def run_pnr_comparison(self, job_id: str, date: str) -> dict:
    """Find PNRs that exist in Sabre but not in OTP, and vice versa."""
    from backend.feeder.aggregations import find_missing_pnrs
    from backend.feeder import storage

    set_job_running(job_id)

    try:
        storage.get_db()
        result = find_missing_pnrs(date)
        set_job_completed(job_id, 1, result)
        return {"jobId": job_id, **result}
    except Exception as exc:
        logger.exception("PNR comparison job %s failed", job_id)
        set_job_failed(job_id, str(exc))
        raise self.retry(exc=exc)


def _make_progress_cb(job_id: str):
    def _on_progress(processed: int, total: int, message: str = ""):
        set_job_progress(job_id, processed, total, message=message)
    return _on_progress
