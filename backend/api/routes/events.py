"""Server-Sent Events endpoint for real-time job progress.

Replaces client-side polling (3s interval) with push-based updates.
Uses Redis pub/sub to receive events from Celery workers.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from backend.api.redis_client import subscribe_events, get_job

logger = logging.getLogger(__name__)
router = APIRouter(tags=["events"])


@router.get("/events/jobs/{job_id}")
async def stream_job_events(job_id: str):
    """SSE stream for a specific job's progress updates.

    The client connects once and receives events as they happen:
      - progress: {processed, total, message}
      - completed: {flightsProcessed}
      - failed: {error}

    The stream closes automatically when the job completes or fails.
    """
    return EventSourceResponse(
        _job_event_generator(job_id),
        media_type="text/event-stream",
    )


async def _job_event_generator(job_id: str):
    """Async generator that yields SSE events for a job."""
    # First, check if the job already finished (client may connect late)
    job = get_job(job_id)
    if job is None:
        yield {"event": "error", "data": json.dumps({"error": "Job not found"})}
        return
    if job["status"] in ("completed", "failed"):
        yield {"event": job["status"], "data": json.dumps(job)}
        return

    # Subscribe to Redis pub/sub in a non-blocking way
    ps = subscribe_events()

    try:
        # Send initial state
        yield {"event": "connected", "data": json.dumps({
            "jobId": job_id, "status": job["status"],
        })}

        timeout_count = 0
        max_timeouts = 600  # 10 minutes at 1s intervals

        while timeout_count < max_timeouts:
            # Non-blocking check for messages
            msg = await asyncio.to_thread(_get_message_safe, ps, timeout=1.0)

            if msg is not None and msg.get("type") == "message":
                try:
                    payload = json.loads(msg["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                # Only forward events for our job
                if payload.get("jobId") != job_id:
                    continue

                event_type = payload.get("event", "update")
                yield {"event": event_type, "data": json.dumps(payload.get("data", {}))}

                # Close stream on terminal states
                if event_type in ("completed", "failed"):
                    return
            else:
                timeout_count += 1
                # Periodic heartbeat to keep connection alive
                if timeout_count % 15 == 0:
                    yield {"event": "heartbeat", "data": ""}

                    # Check if job finished between messages
                    current = get_job(job_id)
                    if current and current["status"] in ("completed", "failed"):
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
    ps = subscribe_events()

    try:
        yield {"event": "connected", "data": json.dumps({"filter": job_type})}

        timeout_count = 0
        max_timeouts = 3600  # 1 hour

        while timeout_count < max_timeouts:
            msg = await asyncio.to_thread(_get_message_safe, ps, timeout=1.0)

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
