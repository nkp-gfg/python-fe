"""Redis connection for FalconEye — job store, caching, and pub/sub."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis

logger = logging.getLogger(__name__)

_pool: redis.ConnectionPool | None = None

# Key prefixes
JOB_PREFIX = "job:"
CACHE_PREFIX = "cache:"
SSE_CHANNEL = "falconeye:events"

# TTLs (seconds)
JOB_TTL = 24 * 3600        # Jobs expire after 24h
CACHE_TTL_SHORT = 30        # Dashboard cache: 30s
CACHE_TTL_MEDIUM = 300      # Flight list cache: 5min

# Job records and events are readable by anyone holding the job id (and the
# global stream by operators), so they only ever carry these generic reasons.
# Exception and Sabre fault text stays in the server logs.
JOB_FAILED_ERROR = "Job failed. See server logs for details."
API_FAILED_ERROR = "Request failed. See server logs for details."


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
    r.set(f"{JOB_PREFIX}{job_id}", json.dumps(job, default=str), ex=JOB_TTL)
    return job


def _redact_api_result(api_result: Any) -> Any:
    if not isinstance(api_result, dict):
        return api_result
    redacted = dict(api_result)
    if redacted.get("error"):
        redacted["error"] = API_FAILED_ERROR
    calls = redacted.get("calls")
    if isinstance(calls, list):
        redacted["calls"] = [_redact_api_result(call) for call in calls]
    return redacted


def _redact_results(results: Any) -> Any:
    """Replace per-API error text in ingestion results with a generic reason."""
    if not isinstance(results, list):
        return results
    redacted = []
    for result in results:
        if isinstance(result, dict) and isinstance(result.get("apis"), dict):
            result = {
                **result,
                "apis": {
                    name: _redact_api_result(api_result)
                    for name, api_result in result["apis"].items()
                },
            }
        redacted.append(result)
    return redacted


def _job_error(status: str | None, results: Any) -> str | None:
    if status not in ("failed", "partial"):
        return None
    if isinstance(results, list) and results:
        total = len(results)
        failed = sum(
            1 for result in results
            if not (isinstance(result, dict) and result.get("success", False))
        )
        if failed:
            return f"{failed} of {total} flights failed. See server logs for details."
    return JOB_FAILED_ERROR


def _redact_job(job: dict) -> dict:
    job["results"] = _redact_results(job.get("results"))
    job["error"] = _job_error(job.get("status"), job.get("results"))
    return job


def get_job(job_id: str) -> dict | None:
    """Retrieve a job by ID."""
    r = get_redis()
    raw = r.get(f"{JOB_PREFIX}{job_id}")
    if raw is None:
        return None
    return _redact_job(json.loads(raw))


def update_job(job_id: str, **fields) -> dict | None:
    """Partially update a job record and return the stored record."""
    job = get_job(job_id)
    if job is None:
        return None
    for k, v in fields.items():
        job[k] = v
    job = _redact_job(job)
    r = get_redis()
    r.set(f"{JOB_PREFIX}{job_id}", json.dumps(job, default=str), ex=JOB_TTL)
    return job


def _job_type(job: dict | None) -> str | None:
    return job.get("jobType") if job else None


def set_job_running(job_id: str) -> None:
    update_job(job_id, status="running", startedAt=_utc_now())


def set_job_progress(job_id: str, processed: int, total: int, message: str = "") -> None:
    """Update progress and publish SSE event."""
    job = update_job(
        job_id,
        flightsProcessed=processed,
        progress={"processed": processed, "total": total, "message": message},
    )
    publish_event(job_id, "progress", {
        "processed": processed, "total": total, "message": message,
    }, job_type=_job_type(job))


def set_ingestion_job_finished(job_id: str, processed: int, results: list[dict[str, Any]]) -> None:
    total = len(results)
    failed_count = sum(
        1 for result in results if not result.get("success", False))

    if total == 0 or failed_count == total:
        status = "failed"
    elif failed_count == 0:
        status = "completed"
    else:
        status = "partial"

    job = update_job(
        job_id,
        status=status,
        flightsProcessed=processed,
        results=results,
        completedAt=_utc_now(),
    )
    publish_event(job_id, status, {
        "flightsProcessed": processed,
        "failedFlights": failed_count,
        "error": job["error"] if job else _job_error(status, None),
    }, job_type=_job_type(job))


def set_job_completed(job_id: str, processed: int, results: Any) -> None:
    job = update_job(
        job_id,
        status="completed",
        flightsProcessed=processed,
        results=results,
        completedAt=_utc_now(),
    )
    publish_event(job_id, "completed", {"flightsProcessed": processed},
                  job_type=_job_type(job))


def set_job_failed(job_id: str) -> None:
    """Mark a job failed. The cause belongs in the server log, not the job."""
    job = update_job(
        job_id,
        status="failed",
        completedAt=_utc_now(),
    )
    publish_event(job_id, "failed", {"error": JOB_FAILED_ERROR},
                  job_type=_job_type(job))


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

def publish_event(job_id: str, event_type: str, data: dict, job_type: str | None = None) -> None:
    """Publish a job event to the SSE channel."""
    r = get_redis()
    payload = json.dumps({
        "jobId": job_id,
        "jobType": job_type,
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
