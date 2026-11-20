"""Batch ingestion must not open a Sabre session for a sequence MongoDB cannot store.

These tests fail while an out-of-range flightSequenceNumber is accepted and the
batch task retries the feeder. They pass once that input is rejected before any
Sabre call, and once a permanent failure is not run again.

Requires MongoDB at MONGODB_URI (default mongodb://127.0.0.1:27017) and Redis
at redis://127.0.0.1:6379/15. Sabre HTTP is replaced with the SOAP replies the
client parser accepts; session values in those replies are placeholders.

Run from the repository root:

    PYTHONPATH=. pytest backend/tests/test_batch_ingestion_session_replay.py -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["MONGODB_URI"] = os.environ.get(
    "MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "placeholder"
os.environ["SABRE_USERNAME"] = "placeholder"
os.environ["SABRE_PASSWORD"] = "placeholder"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder"
os.environ["SABRE_ORGANIZATION"] = "placeholder"
os.environ["SABRE_DOMAIN"] = "placeholder"

SIGNED_INT64_MAX = 2**63 - 1
# One past either end of the signed 64-bit range BSON can encode.
OVERSIZED_SEQUENCES = [SIGNED_INT64_MAX + 1, -SIGNED_INT64_MAX - 2]

_FLIGHT_STATUS_XML = (
    "<soap-env:Envelope><soap-env:Body>"
    "<ns3:ACS_FlightDetailRS><Result><Status>Complete</Status></Result>"
    "</ns3:ACS_FlightDetailRS></soap-env:Body></soap-env:Envelope>"
)


class _SoapResponse:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


class SabreTransport:
    """Stand-in for the Sabre HTTP endpoint. SabreClient still builds, parses, and stores each call."""

    def __init__(self):
        self.actions = []

    def post(self, url, data=None, headers=None, timeout=None, **kwargs):
        action = (headers or {}).get("SOAPAction", "")
        self.actions.append(action)
        if action == "SessionCreateRQ":
            number = self.actions.count("SessionCreateRQ")
            token = f"placeholder-session-{number}"
            return _SoapResponse(
                f"<wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>"
            )
        if action == "ACS_FlightDetailRQ":
            return _SoapResponse(_FLIGHT_STATUS_XML)
        return _SoapResponse("<ok/>")

    @property
    def session_creates(self):
        return self.actions.count("SessionCreateRQ")


def _flight(number, sequence):
    return {
        "airline": "GF",
        "flightNumber": number,
        "origin": "BAH",
        "departureDate": "2026-03-19",
        "departureDateTime": "2026-03-19T08:00:00",
        "flightSequenceNumber": sequence,
    }


_COLLECTIONS = (
    "sabre_requests", "snapshots", "changes", "flights",
    "flight_status", "passenger_list", "reservations",
    "trip_reports", "flight_schedules",
)


def _reset_mongo_singletons():
    """Drop handles left behind when the API lifespan closes the shared client."""
    import backend.api.database as database
    from backend.feeder import storage

    storage._client = None
    storage._db = None
    storage._owns_connection = False
    database._client = None
    database._db = None


def _drop_collections():
    from backend.feeder import storage

    db = storage.get_db()
    for name in _COLLECTIONS:
        db[name].drop()


@pytest.fixture
def sabre(monkeypatch):
    from backend.celery_app import app as celery_app
    from backend.api.redis_client import get_redis
    import backend.sabre.client as sabre_client

    transport = SabreTransport()
    monkeypatch.setattr(sabre_client.requests, "post", transport.post)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False

    _reset_mongo_singletons()
    get_redis().flushdb()
    _drop_collections()

    yield transport

    _reset_mongo_singletons()
    get_redis().flushdb()
    _drop_collections()


def _archived_tokens():
    from backend.feeder import storage

    tokens = []
    for doc in storage.get_db()["sabre_requests"].find({}):
        token = (doc.get("metadata") or {}).get("sessionToken")
        if token:
            tokens.append(token)
    return tokens


def _failed_events():
    from backend.api.redis_client import get_redis, SSE_CHANNEL

    redis = get_redis()
    pubsub = redis.pubsub()
    pubsub.subscribe(SSE_CHANNEL)
    # The subscribe acknowledgement is not an application event.
    pubsub.get_message(timeout=1)
    return pubsub


def _read_failed_events(pubsub):
    failed = []
    while True:
        message = pubsub.get_message(timeout=0.2)
        if message is None:
            break
        if message.get("type") != "message":
            continue
        payload = json.loads(message["data"])
        if payload.get("event") == "failed":
            failed.append(payload)
    pubsub.close()
    return failed


@pytest.mark.parametrize("sequence", OVERSIZED_SEQUENCES)
@pytest.mark.parametrize("bad_index", [0, 1])
def test_unauthenticated_batch_rejects_oversized_sequence_before_sabre(
    sabre, sequence, bad_index,
):
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.api.redis_client import get_redis

    flights = [_flight("2006", 1001), _flight("2007", 1002)]
    flights[bad_index]["flightSequenceNumber"] = sequence
    redis = get_redis()
    jobs_before = set(redis.scan_iter("job:*"))

    with TestClient(app) as client:
        response = client.post("/flights/ingest/batch", json={"flights": flights})

    assert sabre.session_creates == 0
    assert _archived_tokens() == []
    assert set(redis.scan_iter("job:*")) == jobs_before
    assert response.status_code in (400, 422)


@pytest.mark.parametrize("sequence", OVERSIZED_SEQUENCES)
def test_unauthenticated_sync_ingest_rejects_oversized_sequence_before_sabre(
    sabre, sequence,
):
    from fastapi.testclient import TestClient
    from backend.api.main import app

    with TestClient(app) as client:
        response = client.post("/flights/ingest", json=_flight("2006", sequence))

    assert sabre.session_creates == 0
    assert _archived_tokens() == []
    assert response.status_code in (400, 422)


@pytest.mark.parametrize("sequence", OVERSIZED_SEQUENCES)
def test_run_batch_ingestion_does_not_replay_sabre_after_permanent_failure(
    sabre, sequence,
):
    from backend.api.redis_client import create_job, get_job
    from backend.tasks.ingestion import run_batch_ingestion

    payloads = [
        _flight("2006", sequence),
        _flight("2007", 1002),
    ]
    job_id = "oversized-sequence-job"
    create_job(job_id, len(payloads), job_type="ingestion")
    events = _failed_events()

    run_batch_ingestion.delay(job_id, payloads)

    failed = _read_failed_events(events)
    job = get_job(job_id)
    assert sabre.session_creates <= 1
    assert len(_archived_tokens()) <= 1
    assert len(failed) <= 1
    assert job is not None
    assert job["status"] == "failed"
