"""Celery tasks for Sabre ingestion — replaces asyncio.ensure_future pattern."""

import logging

from backend.celery_app import app
from backend.api.redis_client import (
    set_job_running, set_job_progress, set_job_completed, set_job_failed,
    cache_invalidate_pattern,
)

logger = logging.getLogger(__name__)


@app.task(bind=True, name="falconeye.ingest_batch", max_retries=2, default_retry_delay=30)
def run_batch_ingestion(self, job_id: str, payloads: list[dict]) -> dict:
    """Execute the Sabre feeder pipeline for a batch of flights.

    Runs in a Celery worker thread — survives API restarts and supports
    retries, timeouts, and concurrency control.
    """
    from backend.feeder.runner import run_feeder
    from backend.feeder import storage

    set_job_running(job_id)
    total = len(payloads)

    try:
        # Ensure storage layer has a DB connection in the worker process
        storage.get_db()
        storage.ensure_indexes()

        result = run_feeder(
            payloads, progress_callback=_make_progress_cb(job_id, total))

        set_job_completed(
            job_id, result["processedFlights"], result["results"])

        # Invalidate cached flight lists so dashboard picks up new data
        cache_invalidate_pattern("flights:*")

        return {
            "jobId": job_id,
            "processedFlights": result["processedFlights"],
        }
    except Exception as exc:
        logger.exception("Batch ingestion job %s failed", job_id)
        set_job_failed(job_id, str(exc))
        raise self.retry(exc=exc)


def _make_progress_cb(job_id: str, total: int):
    """Return a callback that run_feeder can call after each flight."""
    def _on_progress(index: int, flight_key: str):
        set_job_progress(
            job_id, index, total,
            message=f"Processing {flight_key} ({index}/{total})",
        )
    return _on_progress
