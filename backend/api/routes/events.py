"""Server-Sent Events endpoint for real-time job progress.

Replaces client-side polling (3s interval) with push-based updates.
Uses Redis pub/sub to receive events from Celery workers.
"""

import asyncio
import json
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from backend.api.redis_client import subscribe_events, get_job

logger = logging.getLogger(__name__)
router = APIRouter(tags=["events"])

TERMINAL_STATUSES = ("completed", "partial", "failed")

# Every stream holds one Redis pub/sub connection and one blocking reader
# thread, so the number of open streams per process is bounded.
MAX_STREAMS = int(os.environ.get("SSE_MAX_STREAMS", "32"))
MAX_STREAMS_PER_JOB = int(os.environ.get("SSE_MAX_STREAMS_PER_JOB", "4"))

# Idle budget counts loop iterations. A message for another job or a
# malformed payload spends one iteration exactly like a silent second.
JOB_STREAM_IDLE_TICKS = 600
# No per-job stream outlives this many seconds after the job was submitted.
JOB_STREAM_MAX_AGE_S = int(os.environ.get("SSE_JOB_STREAM_MAX_AGE_S", "3600"))
ALL_STREAM_IDLE_TICKS = 3600
ALL_STREAM_MAX_LIFETIME_S = int(os.environ.get("SSE_ALL_STREAM_MAX_LIFETIME_S", "3600"))

_reader_pool = ThreadPoolExecutor(max_workers=MAX_STREAMS, thread_name_prefix="sse-pubsub")
_open_streams = 0
_open_streams_per_job: Counter[str] = Counter()


def _acquire_stream(job_id: str | None = None) -> bool:
    """Reserve a stream slot. Runs on the event loop, so check-and-set is atomic."""
    global _open_streams
    if _open_streams >= MAX_STREAMS:
        return False
    if job_id is not None and _open_streams_per_job[job_id] >= MAX_STREAMS_PER_JOB:
        return False
    _open_streams += 1
    if job_id is not None:
        _open_streams_per_job[job_id] += 1
    return True


def _release_stream(job_id: str | None = None) -> None:
    global _open_streams
    _open_streams -= 1
    if job_id is not None:
        _open_streams_per_job[job_id] -= 1
        if _open_streams_per_job[job_id] <= 0:
            del _open_streams_per_job[job_id]


def _too_many_streams_event() -> dict:
    return {"event": "error", "data": json.dumps({"error": "Too many open event streams"})}


def _job_stream_deadline(job: dict, now: float) -> float:
    """Event-loop time after which the stream for this job must close."""
    remaining = JOB_STREAM_MAX_AGE_S
    submitted = job.get("submittedAt")
    if submitted:
        try:
            submitted_at = datetime.strptime(submitted, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return now
        age = (datetime.now(timezone.utc) - submitted_at).total_seconds()
        remaining = min(remaining, JOB_STREAM_MAX_AGE_S - age)
    return now + remaining


async def _next_message(ps, timeout: float = 1.0):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_reader_pool, _get_message_safe, ps, timeout)


def _decode_message(msg) -> dict | None:
    if msg is None or msg.get("type") != "message":
        return None
    try:
        payload = json.loads(msg["data"])
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


@router.get("/events/jobs/{job_id}")
async def stream_job_events(job_id: str):
    """SSE stream for a specific job's progress updates.

    The client connects once and receives events as they happen:
      - progress: {processed, total, message}
      - completed: {flightsProcessed}
            - partial: {flightsProcessed, failedFlights, error}
      - failed: {error}

        The stream closes automatically when the job completes, partially completes, or fails,
        when the job disappears, after the idle budget is spent, or when the job is older than
        the maximum stream age.
    """
    return EventSourceResponse(
        _job_event_generator(job_id),
        media_type="text/event-stream",
    )


async def _job_event_generator(job_id: str):
    """Async generator that yields SSE events for a job."""
    if not _acquire_stream(job_id):
        yield _too_many_streams_event()
        return
    try:
        async with aclosing(_job_events(job_id)) as events:
            async for event in events:
                yield event
    finally:
        _release_stream(job_id)


async def _job_events(job_id: str):
    # First, check if the job already finished (client may connect late)
    job = get_job(job_id)
    if job is None:
        yield {"event": "error", "data": json.dumps({"error": "Job not found"})}
        return
    if job["status"] in TERMINAL_STATUSES:
        yield {"event": job["status"], "data": json.dumps(job)}
        return

    loop = asyncio.get_running_loop()
    deadline = _job_stream_deadline(job, loop.time())
    if loop.time() >= deadline:
        yield {"event": "error", "data": json.dumps({"error": "Job is too old to stream"})}
        return
    submitted_at = job.get("submittedAt")

    ps = subscribe_events()

    try:
        # Send initial state
        yield {"event": "connected", "data": json.dumps({
            "jobId": job_id, "status": job["status"],
        })}

        idle_ticks = 0
        while idle_ticks < JOB_STREAM_IDLE_TICKS and loop.time() < deadline:
            payload = _decode_message(await _next_message(ps, timeout=1.0))

            if payload is not None and payload.get("jobId") == job_id:
                event_type = payload.get("event", "update")
                yield {"event": event_type, "data": json.dumps(payload.get("data", {}))}

                # Close stream on terminal states
                if event_type in TERMINAL_STATUSES:
                    return
                continue

            idle_ticks += 1
            # Periodic heartbeat to keep connection alive
            if idle_ticks % 15 == 0:
                yield {"event": "heartbeat", "data": ""}

                # Close if the job finished, expired, or was replaced between messages
                current = get_job(job_id)
                if current is None or current.get("submittedAt") != submitted_at:
                    yield {"event": "error", "data": json.dumps({"error": "Job not found"})}
                    return
                if current["status"] in TERMINAL_STATUSES:
                    yield {"event": current["status"], "data": json.dumps(current)}
                    return
    finally:
        ps.unsubscribe()
        ps.close()


def _get_message_safe(ps, timeout: float = 1.0):
    """Blocking get_message with timeout, safe for threading."""
    try:
        return ps.get_message(timeout=timeout)
    except Exception:
        return None


@router.get("/events/jobs")
async def stream_all_job_events(
    job_type: str = Query(default=None, description="Filter by job type"),
):
    """SSE stream for all job events (global dashboard monitor)."""
    return EventSourceResponse(
        _all_events_generator(job_type),
        media_type="text/event-stream",
    )


async def _all_events_generator(job_type: str | None = None):
    """Async generator that yields all job events."""
    if not _acquire_stream():
        yield _too_many_streams_event()
        return
    try:
        async with aclosing(_all_events(job_type)) as events:
            async for event in events:
                yield event
    finally:
        _release_stream()


async def _all_events(job_type: str | None = None):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ALL_STREAM_MAX_LIFETIME_S
    ps = subscribe_events()

    try:
        yield {"event": "connected", "data": json.dumps({"filter": job_type})}

        timeout_count = 0

        while timeout_count < ALL_STREAM_IDLE_TICKS and loop.time() < deadline:
            msg = await _next_message(ps, timeout=1.0)

            if msg is not None and msg.get("type") == "message":
                try:
                    payload = json.loads(msg["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                yield {"event": payload.get("event", "update"), "data": json.dumps(payload)}
                timeout_count = 0  # Reset on activity
            else:
                timeout_count += 1
                if timeout_count % 30 == 0:
                    yield {"event": "heartbeat", "data": ""}
    finally:
        ps.unsubscribe()
        ps.close()
