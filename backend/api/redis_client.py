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
    r.set(f"{JOB_PREFIX}{job_id}", json.dumps(job, default=str), ex=JOB_TTL)


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
