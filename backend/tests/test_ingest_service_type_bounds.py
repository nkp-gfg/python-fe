"""Bounds for Sabre ingestion requests.

POST /flights/ingest and POST /flights/ingest/batch must reject a
serviceTypeCode longer than four characters, and must reject an ingest
JSON body larger than 1 MiB, before the feeder runs and before a job
record or Celery message is created.

These tests fail while that input is still accepted and forwarded.
"""

import base64
import json
import os
import sys
from queue import Empty

import anyio
import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.main import app  # noqa: E402
from backend.api.routes import ingestion  # noqa: E402
from backend.api import redis_client  # noqa: E402
from backend.celery_app import app as celery_app  # noqa: E402
from kombu.transport.memory import Channel as MemoryChannel  # noqa: E402

# IATA service types used by this API are a single character. Four is the
# longest value these tests still allow; five must be rejected.
MAX_SERVICE_TYPE_CODE_LENGTH = 4
# A batch of 50 flights with those short fields is far smaller than 1 MiB.
# A larger JSON body must be refused while it is being read.
MAX_INGEST_BODY_BYTES = 1024 * 1024
OVERSIZED_BODY_BYTES = MAX_INGEST_BODY_BYTES + (256 * 1024)

_VALID_IDENTITY = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BAH",
    "departureDate": "2026-11-17",
    "departureDateTime": "2026-11-17T08:00:00",
}


class _MemoryRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def ping(self):
        return True


class _Boundary:
    def __init__(self):
        self.redis = _MemoryRedis()
        self.feeder_payloads = []
        self.body_sizes = []


_boundary = _Boundary()


def _flight(service_type_code=...):
    body = dict(_VALID_IDENTITY)
    if service_type_code is not ...:
        body["serviceTypeCode"] = service_type_code
    return body


def _encode(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _success_result(flights):
    flight = flights[0]
    return {
        "processedFlights": len(flights),
        "results": [
            {
                "success": True,
                "flight": {
                    "airline": flight["airline"],
                    "flightNumber": flight["flightNumber"],
                    "origin": flight["origin"],
                    "departureDate": flight["departureDate"],
                    "departureDateTime": flight["departureDateTime"],
                    "flightSequenceNumber": flight.get("flightSequenceNumber"),
                },
                "apis": {},
            }
        ],
    }


def _spy_run_feeder(flights, progress_callback=None):
    _boundary.feeder_payloads.append(flights)
    return _success_result(flights)


async def _tracking_body(self):
    data = await _original_request_body(self)
    _boundary.body_sizes.append(len(data))
    return data


_original_request_body = Request.body


def _drain_broker():
    for queue in list(MemoryChannel.queues.values()):
        while True:
            try:
                queue.get_nowait()
            except Empty:
                break


def _queued_payloads():
    """Return the raw Celery message bodies currently stored by the broker."""
    payloads = []
    for queue in list(MemoryChannel.queues.values()):
        held = []
        while True:
            try:
                held.append(queue.get_nowait())
            except Empty:
                break
        for item in held:
            body = item.get("body")
            if isinstance(body, str):
                try:
                    raw = base64.b64decode(body, validate=True)
                except Exception:
                    raw = body.encode("utf-8")
            elif isinstance(body, bytes):
                raw = body
            else:
                raw = json.dumps(body).encode("utf-8")
            payloads.append(raw)
            queue.put(item)
    return payloads


def _diagnostics():
    forwarded = []
    for flights in _boundary.feeder_payloads:
        for flight in flights:
            code = flight.get("serviceTypeCode")
            forwarded.append(0 if code is None else len(code))
    queued = [len(payload) for payload in _queued_payloads()]
    return (
        f"feeder_service_type_lengths={forwarded} "
        f"job_keys={list(_boundary.redis.store)} "
        f"queued_message_bytes={queued} "
        f"request_body_bytes={list(_boundary.body_sizes)}"
    )


@pytest.fixture(autouse=True)
def _isolate_ingest(monkeypatch):
    _boundary.redis = _MemoryRedis()
    _boundary.feeder_payloads = []
    _boundary.body_sizes = []
    _drain_broker()

    celery_app.conf.broker_url = "memory://"
    celery_app.conf.result_backend = "cache+memory://"
    celery_app.conf.task_always_eager = False

    monkeypatch.setattr(redis_client, "get_redis", lambda: _boundary.redis)
    monkeypatch.setattr(ingestion, "run_feeder", _spy_run_feeder)
    monkeypatch.setattr(Request, "body", _tracking_body)
    yield
    _drain_broker()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    return TestClient(app)


def _assert_not_forwarded():
    assert _boundary.feeder_payloads == [], _diagnostics()
    assert _boundary.redis.store == {}, _diagnostics()
    assert _queued_payloads() == [], _diagnostics()


def _assert_field_rejected(response):
    assert response.status_code == 422, (
        "a serviceTypeCode longer than "
        f"{MAX_SERVICE_TYPE_CODE_LENGTH} characters was accepted "
        f"({response.status_code}). {_diagnostics()}"
    )
    _assert_not_forwarded()


def _assert_body_rejected(status_code):
    assert status_code == 413, (
        "an ingest body larger than "
        f"{MAX_INGEST_BODY_BYTES} bytes was accepted ({status_code}). "
        f"{_diagnostics()}"
    )
    largest = max(_boundary.body_sizes, default=0)
    assert largest <= MAX_INGEST_BODY_BYTES, (
        "the oversized body was materialized before it was refused. "
        f"{_diagnostics()}"
    )
    _assert_not_forwarded()


def _assert_sync_accepted(response, service_type_code):
    assert response.status_code == 200, response.text
    assert len(_boundary.feeder_payloads) == 1
    assert _boundary.feeder_payloads[0][0].get("serviceTypeCode") == service_type_code
    assert _boundary.redis.store == {}
    assert _queued_payloads() == []


def _assert_batch_accepted(response, flights):
    assert response.status_code == 202, response.text
    assert _boundary.feeder_payloads == []
    assert len(_boundary.redis.store) == 1
    payloads = _queued_payloads()
    assert len(payloads) == 1
    decoded = payloads[0]
    for flight in flights:
        code = flight.get("serviceTypeCode")
        if code is not None:
            assert code.encode("utf-8") in decoded


def test_sync_accepts_documented_service_types(client):
    for code in ("J", "P", "C"):
        _boundary.feeder_payloads = []
        response = client.post("/flights/ingest", json=_flight(code))
        _assert_sync_accepted(response, code)


def test_sync_accepts_omitted_and_null_service_type(client):
    omitted = client.post("/flights/ingest", json=_flight())
    _assert_sync_accepted(omitted, None)

    _boundary.feeder_payloads = []
    explicit_null = client.post("/flights/ingest", json=_flight(None))
    _assert_sync_accepted(explicit_null, None)


def test_batch_accepts_documented_service_types_and_a_full_batch(client):
    flights = [_flight(code) for code in ("J", "P", "C")]
    response = client.post("/flights/ingest/batch", json={"flights": flights})
    _assert_batch_accepted(response, flights)

    _drain_broker()
    _boundary.redis = _MemoryRedis()
    full = [_flight("J") for _ in range(50)]
    raw = _encode({"flights": full})
    assert len(raw) <= MAX_INGEST_BODY_BYTES
    response = client.post(
        "/flights/ingest/batch",
        content=raw,
        headers={"content-type": "application/json"},
    )
    _assert_batch_accepted(response, full)


@pytest.mark.parametrize("endpoint", ["/flights/ingest", "/flights/ingest/batch"])
def test_service_type_longer_than_four_characters_is_rejected(client, endpoint):
    code = "A" * (MAX_SERVICE_TYPE_CODE_LENGTH + 1)
    flight = _flight(code)
    payload = flight if endpoint == "/flights/ingest" else {"flights": [flight]}
    response = client.post(endpoint, json=payload)
    _assert_field_rejected(response)


def test_batch_with_one_oversized_service_type_queues_nothing(client):
    payload = {"flights": [_flight("J"), _flight("A" * (MAX_SERVICE_TYPE_CODE_LENGTH + 1))]}
    response = client.post("/flights/ingest/batch", json=payload)
    _assert_field_rejected(response)


@pytest.mark.parametrize("endpoint", ["/flights/ingest", "/flights/ingest/batch"])
def test_oversized_json_body_is_rejected_before_it_is_buffered(client, endpoint):
    code = "B" * OVERSIZED_BODY_BYTES
    flight = _flight(code)
    payload = flight if endpoint == "/flights/ingest" else {"flights": [_flight("J"), flight]}
    raw = _encode(payload)
    assert len(raw) > MAX_INGEST_BODY_BYTES
    response = client.post(
        endpoint,
        content=raw,
        headers={"content-type": "application/json"},
    )
    _assert_body_rejected(response.status_code)


def _scope(path, headers):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("192.0.2.10", 12345),
        "server": ("testserver", 80),
        "state": {},
    }


async def _invoke(path, raw, headers):
    chunks = [raw[index:index + 8192] for index in range(0, len(raw), 8192)]
    cursor = {"index": 0}

    async def receive():
        index = cursor["index"]
        if index >= len(chunks):
            return {"type": "http.disconnect"}
        cursor["index"] = index + 1
        return {
            "type": "http.request",
            "body": chunks[index],
            "more_body": cursor["index"] < len(chunks),
        }

    sent = []

    async def send(message):
        sent.append(message)

    await app(_scope(path, headers), receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    return start["status"]


@pytest.mark.parametrize("endpoint", ["/flights/ingest", "/flights/ingest/batch"])
def test_chunked_body_without_content_length_is_rejected(endpoint):
    code = "C" * OVERSIZED_BODY_BYTES
    flight = _flight(code)
    payload = flight if endpoint == "/flights/ingest" else {"flights": [flight]}
    raw = _encode(payload)
    assert len(raw) > MAX_INGEST_BODY_BYTES
    status = anyio.run(
        _invoke,
        endpoint,
        raw,
        [(b"content-type", b"application/json")],
    )
    _assert_body_rejected(status)


@pytest.mark.parametrize("endpoint", ["/flights/ingest", "/flights/ingest/batch"])
def test_body_larger_than_declared_content_length_is_rejected(endpoint):
    """A short Content-Length must not authorize reading the rest of the body."""
    code = "D" * OVERSIZED_BODY_BYTES
    flight = _flight(code)
    payload = flight if endpoint == "/flights/ingest" else {"flights": [flight]}
    raw = _encode(payload)
    assert len(raw) > MAX_INGEST_BODY_BYTES
    status = anyio.run(
        _invoke,
        endpoint,
        raw,
        [
            (b"content-type", b"application/json"),
            (b"content-length", b"4"),
        ],
    )
    _assert_body_rejected(status)
