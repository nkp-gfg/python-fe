"""Spec tests for the per-job SSE idle bypass.

These tests fail while the vulnerability is present. A foreign publish on the
shared Redis channel must consume the same 600-iteration idle budget as
silence. Own-job progress must not. The HTTP route is unauthenticated.

`_all_events_generator` is not covered: it resets its idle counter on every
well-formed message on purpose, and API callers cannot publish raw Redis
payloads.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"

from backend.api.redis_client import (  # noqa: E402
    SSE_CHANNEL,
    close_redis,
    create_job,
    get_redis,
    set_job_completed,
    set_job_failed,
    set_job_progress,
    set_ingestion_job_finished,
)
from backend.api.routes.events import _job_event_generator  # noqa: E402
from backend.tasks.comparison import _make_progress_cb as comparison_progress  # noqa: E402
from backend.tasks.ingestion import _make_progress_cb as ingestion_progress  # noqa: E402

# Handler budget is 600 one-second loops (events.py). Foreign traffic must
# spend that budget instead of leaving the stream open.
IDLE_BUDGET = 600
FOREIGN_FLOOD = IDLE_BUDGET + 100
CLOSE_TIMEOUT_S = 15


def setup_module():
    get_redis().flushdb()


def teardown_module():
    get_redis().flushdb()
    close_redis()


def _publish_foreign(other_id: str, count: int) -> None:
    """Publish through every production helper that writes the shared channel."""
    ingest = ingestion_progress(other_id, count)
    compare = comparison_progress(other_id)
    for i in range(count):
        kind = i % 5
        if kind == 0:
            ingest(i + 1, "GF100")
        elif kind == 1:
            compare(i + 1, count, "comparing")
        elif kind == 2:
            set_job_progress(other_id, i + 1, count, message="progress")
        elif kind == 3:
            set_job_failed(other_id, "worker error")
        elif i % 2 == 0:
            set_job_completed(other_id, i + 1, {"flightsCompared": 1})
        else:
            set_ingestion_job_finished(
                other_id,
                1,
                [{"success": False, "apis": {"flightStatus": {"error": "sabre"}}}],
            )


async def _connected(job_id: str):
    agen = _job_event_generator(job_id)
    first = await agen.__anext__()
    assert first["event"] == "connected"
    return agen


def test_foreign_job_events_consume_idle_budget():
    """Other jobs' events must end the stream within the idle budget."""

    async def body():
        get_redis().flushdb()
        create_job("watched", 1)
        create_job("other", 1, job_type="comparison")
        agen = await _connected("watched")
        try:
            _publish_foreign("other", FOREIGN_FLOOD)

            async def drain():
                async for _event in agen:
                    pass

            await asyncio.wait_for(drain(), CLOSE_TIMEOUT_S)
        finally:
            await agen.aclose()

    asyncio.run(body())


def test_own_job_progress_does_not_consume_idle_budget():
    """Progress for the subscribed job is activity and must keep the stream open."""

    async def body():
        get_redis().flushdb()
        create_job("watched", 1)
        agen = await _connected("watched")
        received = 0
        try:
            for i in range(FOREIGN_FLOOD):
                set_job_progress("watched", i + 1, FOREIGN_FLOOD, message="own")

            async def collect():
                nonlocal received
                async for event in agen:
                    if event["event"] == "progress":
                        received += 1
                        if received == FOREIGN_FLOOD:
                            return

            await asyncio.wait_for(collect(), CLOSE_TIMEOUT_S)
        finally:
            await agen.aclose()
        assert received == FOREIGN_FLOOD

    asyncio.run(body())


def test_unauthenticated_route_stays_open_while_other_jobs_publish():
    """GET /events/jobs/{id} has no credential and stays up across foreign publishes."""

    async def body():
        from fastapi import FastAPI

        from backend.api.routes import comparison, events, ingestion

        get_redis().flushdb()
        app = FastAPI()
        app.include_router(events.router)
        app.include_router(ingestion.router)
        app.include_router(comparison.router)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/flights/ingest/batch",
            "raw_path": b"/flights/ingest/batch",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("203.0.113.8", 50000),
            "server": ("test", 80),
            "root_path": "",
        }
        payload = (
            b'{"flights":[{"flightNumber":"100","origin":"BAH",'
            b'"departureDate":"2026-11-18",'
            b'"departureDateTime":"2026-11-18T10:00:00"}]}'
        )
        sent = {"done": False}

        async def receive_post():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        posted = {}

        async def send_post(message):
            if message["type"] == "http.response.start":
                posted["status"] = message["status"]
            elif message["type"] == "http.response.body":
                posted.setdefault("body", bytearray()).extend(message.get("body") or b"")

        await app(scope, receive_post, send_post)
        assert posted["status"] == 202
        import json

        job_id = json.loads(posted["body"])["jobId"]

        body_q = asyncio.Queue()
        started = asyncio.Event()
        disconnect = asyncio.Event()
        meta = {"req": False, "status": None}

        async def receive():
            if not meta["req"]:
                meta["req"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                meta["status"] = message["status"]
                started.set()
            elif message["type"] == "http.response.body":
                await body_q.put(message.get("body") or b"")
                if not message.get("more_body", False):
                    await body_q.put(None)

        sse_scope = dict(scope)
        sse_scope["method"] = "GET"
        sse_scope["path"] = f"/events/jobs/{job_id}"
        sse_scope["raw_path"] = sse_scope["path"].encode()
        sse_scope["headers"] = []
        task = asyncio.create_task(app(sse_scope, receive, send))
        try:
            await asyncio.wait_for(started.wait(), 5)
            assert meta["status"] == 200
            buf = bytearray()
            while b"event: connected" not in buf:
                chunk = await asyncio.wait_for(body_q.get(), 5)
                assert chunk is not None
                buf.extend(chunk)
            assert get_redis().pubsub_numsub(SSE_CHANNEL)[0][1] >= 1
            create_job("other-job", 1, job_type="ingestion")
            _publish_foreign("other-job", FOREIGN_FLOOD)

            async def read_rest():
                while True:
                    chunk = await body_q.get()
                    if chunk is None:
                        return

            await asyncio.wait_for(read_rest(), CLOSE_TIMEOUT_S)
        finally:
            disconnect.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(body())

