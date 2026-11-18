"""Redis connection for FalconEye — job store, caching, and pub/sub."""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import redis

logger = logging.getLogger(__name__)

_pool: redis.ConnectionPool | None = None

# Key prefixes
JOB_PREFIX = "job:"
CACHE_PREFIX = "cache:"
SSE_CHANNEL = "falconeye:events"

LOCK_PREFIX = "lock:"

# TTLs (seconds)
JOB_TTL = 24 * 3600        # Jobs expire after 24h
COMPARISON_JOB_TTL = 3600   # Comparison jobs expire after 1h
CACHE_TTL_SHORT = 30        # Dashboard cache: 30s
CACHE_TTL_MEDIUM = 300      # Flight list cache: 5min

COMPARISON_JOB_TYPES = frozenset({"comparison", "pnr_comparison"})
OPEN_JOB_STATUSES = frozenset({"accepted", "running"})

_JOB_TTL_BY_TYPE = {job_type: COMPARISON_JOB_TTL for job_type in COMPARISON_JOB_TYPES}

_ADMISSION_LOCK_TTL = 10
_ADMISSION_LOCK_WAIT = 2.0


class JobLimitExceeded(Exception):
    """Raised when a job would exceed its open-job cap."""


def _job_ttl(job_type: str | None) -> int:
    return _JOB_TTL_BY_TYPE.get(job_type or "", JOB_TTL)


def _get_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        _pool = redis.ConnectionPool.from_url(url, decode_responses=True)
        logger.info("Redis connection pool created (%s)", url)
    return _pool


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_get_pool())


def close_redis():
    global _pool
    if _pool is not None:
        _pool.disconnect()
        _pool = None
        logger.info("Redis connection pool closed")


# ── Job Store ─────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_job(job_id: str, flights_queued: int, job_type: str = "ingestion") -> dict:
    """Create a new job record in Redis."""
    job = {
        "jobId": job_id,
        "jobType": job_type,
        "status": "accepted",
        "flightsQueued": flights_queued,
        "flightsProcessed": 0,
        "submittedAt": _utc_now(),
        "startedAt": None,
        "completedAt": None,
        "results": None,
        "error": None,
        "progress": None,
    }
    r = get_redis()
    r.set(f"{JOB_PREFIX}{job_id}", json.dumps(job, default=str), ex=_job_ttl(job_type))
    return job


def count_open_jobs(job_types: frozenset[str]) -> int:
    """Count job records of the given types that are still accepted or running."""
    r = get_redis()
    open_jobs = 0
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match=f"{JOB_PREFIX}*", count=200)
        for key in keys:
            raw = r.get(key)
            if raw is None:
                continue
            try:
                job = json.loads(raw)
            except ValueError:
                continue
            if job.get("jobType") in job_types and job.get("status") in OPEN_JOB_STATUSES:
                open_jobs += 1
        if cursor == 0:
            break
    return open_jobs


def create_job_within_limit(
    job_id: str,
    flights_queued: int,
    job_type: str,
    *,
    counted_types: frozenset[str],
    limit: int,
) -> dict:
    """Create a job record only if fewer than ``limit`` jobs of ``counted_types`` are open.

    The count and the write happen under one Redis lock so concurrent requests,
    including ones served by other API processes, cannot both take the last slot.
    Blocks for up to ``_ADMISSION_LOCK_WAIT`` seconds; call it off the event loop.
    """
    r = get_redis()
    lock_key = f"{LOCK_PREFIX}jobs:{'+'.join(sorted(counted_types))}"
    token = uuid.uuid4().hex
    deadline = time.monotonic() + _ADMISSION_LOCK_WAIT
    while not r.set(lock_key, token, nx=True, ex=_ADMISSION_LOCK_TTL):
        if time.monotonic() >= deadline:
            raise JobLimitExceeded("Job admission is busy; retry shortly")
        time.sleep(0.05)
    try:
        if count_open_jobs(counted_types) >= limit:
            raise JobLimitExceeded(f"At most {limit} jobs of this kind may be open at once")
        return create_job(job_id, flights_queued, job_type=job_type)
    finally:
        if r.get(lock_key) == token:
            r.delete(lock_key)


def get_job(job_id: str) -> dict | None:
    """Retrieve a job by ID."""
    r = get_redis()
    raw = r.get(f"{JOB_PREFIX}{job_id}")
    if raw is None:
        return None
    return json.loads(raw)


def update_job(job_id: str, **fields) -> None:
    """Partially update a job record."""
    job = get_job(job_id)
    if job is None:
        return
    for k, v in fields.items():
        job[k] = v
    r = get_redis()
    r.set(f"{JOB_PREFIX}{job_id}", json.dumps(job, default=str), ex=_job_ttl(job.get("jobType")))


def set_job_running(job_id: str) -> None:
    update_job(job_id, status="running", startedAt=_utc_now())


def set_job_progress(job_id: str, processed: int, total: int, message: str = "") -> None:
    """Update progress and publish SSE event."""
    update_job(
        job_id,
        flightsProcessed=processed,
        progress={"processed": processed, "total": total, "message": message},
    )
    publish_event(job_id, "progress", {
        "processed": processed, "total": total, "message": message,
    })


def _extract_ingestion_error(result: dict[str, Any] | None) -> str | None:
    if not result:
        return None

    apis = result.get("apis") or {}
    for api_name in ("flightStatus", "passengerList", "reservations", "tripReports", "schedule"):
        api_result = apis.get(api_name) or {}
        error = api_result.get("error")
        if error:
            return str(error)

    flight = result.get("flight") or {}
    flight_number = flight.get("flightNumber")
    origin = flight.get("origin")
    departure_date = flight.get("departureDate")
    if flight_number and origin and departure_date:
        return f"Ingestion failed for GF{flight_number} {origin} {departure_date}."
    return None


def set_ingestion_job_finished(job_id: str, processed: int, results: list[dict[str, Any]]) -> None:
    total = len(results)
    failed_results = [
        result for result in results if not result.get("success", False)]
    failed_count = len(failed_results)
    succeeded_count = total - failed_count

    if total == 0:
        status = "failed"
        error = "Ingestion batch produced no results"
    elif failed_count == 0:
        status = "completed"
        error = None
    else:
        first_error = _extract_ingestion_error(
            failed_results[0]) or "Ingestion failed"
        if succeeded_count == 0:
            status = "failed"
            error = first_error if failed_count == 1 else f"All {failed_count} flights failed. First error: {first_error}"
        else:
            status = "partial"
            error = f"{failed_count} of {total} flights failed. First error: {first_error}"

    update_job(
        job_id,
        status=status,
        flightsProcessed=processed,
        results=results,
        completedAt=_utc_now(),
        error=error,
    )
    publish_event(job_id, status, {
        "flightsProcessed": processed,
        "failedFlights": failed_count,
        "error": error,
    })


def set_job_completed(job_id: str, processed: int, results: Any) -> None:
    update_job(
        job_id,
        status="completed",
        flightsProcessed=processed,
        results=results,
        completedAt=_utc_now(),
    )
    publish_event(job_id, "completed", {"flightsProcessed": processed})


def set_job_failed(job_id: str, error: str) -> None:
    update_job(
        job_id,
        status="failed",
        error=error,
        completedAt=_utc_now(),
    )
    publish_event(job_id, "failed", {"error": error})


# ── Cache ─────────────────────────────────────────────────────────────────

def cache_get(key: str) -> Any:
    """Get a cached value (JSON-decoded)."""
    r = get_redis()
    raw = r.get(f"{CACHE_PREFIX}{key}")
    if raw is None:
        return None
    return json.loads(raw)


def cache_set(key: str, value: Any, ttl: int = CACHE_TTL_SHORT) -> None:
    """Set a cached value with TTL."""
    r = get_redis()
    r.set(f"{CACHE_PREFIX}{key}", json.dumps(value, default=str), ex=ttl)


def cache_delete(key: str) -> None:
    """Invalidate a specific cache key."""
    r = get_redis()
    r.delete(f"{CACHE_PREFIX}{key}")


def cache_invalidate_pattern(pattern: str) -> None:
    """Delete all cache keys matching a glob pattern."""
    r = get_redis()
    cursor = 0
    while True:
        cursor, keys = r.scan(
            cursor, match=f"{CACHE_PREFIX}{pattern}", count=100)
        if keys:
            r.delete(*keys)
        if cursor == 0:
            break


# ── Pub/Sub for SSE ───────────────────────────────────────────────────────

def publish_event(job_id: str, event_type: str, data: dict) -> None:
    """Publish a job event to the SSE channel."""
    r = get_redis()
    payload = json.dumps({
        "jobId": job_id,
        "event": event_type,
        "data": data,
        "timestamp": _utc_now(),
    }, default=str)
    r.publish(SSE_CHANNEL, payload)


def subscribe_events() -> redis.client.PubSub:
    """Create a pub/sub subscription for SSE streaming."""
    r = get_redis()
    ps = r.pubsub()
    ps.subscribe(SSE_CHANNEL)
    return ps
