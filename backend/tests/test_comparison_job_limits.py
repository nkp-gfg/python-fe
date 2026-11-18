"""Bounds for async comparison jobs.

These tests are the spec for the missing limits on the comparison routes.
They fail while the vulnerability is present and pass once it is fixed.

Contract the routes must enforce
--------------------------------
POST /compare/flights/async and POST /compare/pnr-mismatches/async:

1. Authentication. On every request, read FALCONEYE_API_TOKEN from the
   environment and require the header

       Authorization: Bearer <that token>

   A missing header, a non-Bearer scheme, or any other token is HTTP 401.
   Do this before validating the flight list, counting jobs, writing Redis,
   or publishing Celery. 401 responses must not create a job record and
   must not publish a task.

2. Flight list bounds (flight route only). Reject HTTP 422 when the raw
   ``flight_numbers`` query value is longer than MAX_FLIGHT_LIST_CHARS, or
   when splitting on commas and stripping each piece produces more than
   MAX_FLIGHT_COUNT non-empty items. Check this before the job cap, and
   before any Redis write or Celery publish. A list at either limit is
   accepted and forwarded whole (no silent truncation). Omitting
   ``flight_numbers`` is allowed and must enqueue the task with ``None``
   so the worker still compares the whole day.

3. Shared job cap. Count Redis job records (the keys ``create_job`` writes)
   whose ``jobType`` is ``comparison`` or ``pnr_comparison`` and whose
   ``status`` is ``accepted`` or ``running``. The two routes share one cap
   of MAX_OPEN_COMPARISON_JOBS. Use the Redis store, not a process-local
   counter. The next request on either route is HTTP 429 and must not
   write a job or publish a task. A job that has left those statuses no
   longer counts.

4. Queue isolation. Publish ``falconeye.compare_flights`` and
   ``falconeye.compare_pnr_cross_db`` to a queue the default worker does
   not consume. ``celery -A backend.celery_app worker`` is started without
   ``-Q``, so it consumes ``app.amqp.queues.consume_from`` (the default
   ``celery`` queue). ``falconeye.ingest_batch`` must remain on that
   default queue. Comparison routing keys must be different from the
   ingestion routing key and must not be in ``consume_from``.

5. Job-record TTL. Every Redis write of a comparison or PNR job record,
   including ``create_job`` and later updates such as ``set_job_running``,
   must set an expiry of at most MAX_COMPARISON_JOB_TTL_SECONDS.

Run from the repository root:

    PYTHONPATH=. pytest backend/tests/test_comparison_job_limits.py -q

Requires the application dependencies plus pytest and httpx.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ["FALCONEYE_API_TOKEN"] = "placeholder-not-a-secret"

from backend.celery_app import app as celery_app  # noqa: E402

celery_app.conf.broker_url = "memory://"
celery_app.conf.result_backend = "cache+memory://"
celery_app._backend_cache = None

import fnmatch  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402
from celery.signals import after_task_publish  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from kombu.transport.memory import Channel  # noqa: E402

import backend.api.main as api_main  # noqa: E402
import backend.api.redis_client as redis_client  # noqa: E402
from backend.api.redis_client import set_job_running  # noqa: E402

MAX_OPEN_COMPARISON_JOBS = 4
MAX_FLIGHT_COUNT = 20
MAX_FLIGHT_LIST_CHARS = 256
MAX_COMPARISON_JOB_TTL_SECONDS = 60 * 60

DATE = "2026-03-19"
FLIGHTS_URL = "/compare/flights/async"
PNR_URL = "/compare/pnr-mismatches/async"
TOKEN = os.environ["FALCONEYE_API_TOKEN"]

_published: list[dict] = []
_redis_holder: dict = {}


def _capture_publish(sender=None, body=None, routing_key=None, **kwargs):
    _published.append({
        "sender": sender,
        "body": body,
        "routing_key": routing_key,
    })


after_task_publish.connect(_capture_publish, dispatch_uid="comparison-job-limit-tests")


class MemoryRedis:
    """In-memory stand-in for the Redis commands the job store uses.

    ``SET key value EX seconds`` stores an integer expiry in seconds
    (redis-py ``Redis.set``, redis/commands/core.py). ``TTL`` reports the
    remaining whole seconds (redis-py ``Redis.ttl``). ``SCAN`` returns
    ``(cursor, keys)`` and ``cursor == 0`` ends the iteration
    (redis-py ``Redis.scan``).
    """

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.sets: list[tuple[str, int | None]] = []

    def ping(self):
        return True

    def set(self, name, value, ex=None, px=None, nx=False, xx=False,
            keepttl=False, get=False, exat=None, pxat=None):
        ttl = None
        if ex is not None:
            ttl = int(ex.total_seconds()) if hasattr(ex, "total_seconds") else int(ex)
        self.sets.append((name, ttl))
        self.store[name] = {"value": value, "ex": ttl}
        return True

    def get(self, name):
        item = self.store.get(name)
        if item is None:
            return None
        return item["value"]

    def ttl(self, name):
        item = self.store.get(name)
        if item is None:
            return -2
        if item["ex"] is None:
            return -1
        return item["ex"]

    def exists(self, *names):
        return sum(1 for name in names if name in self.store)

    def delete(self, *names):
        removed = 0
        for name in names:
            if name in self.store:
                del self.store[name]
                removed += 1
        return removed

    def expire(self, name, seconds):
        item = self.store.get(name)
        if item is None:
            return False
        item["ex"] = int(seconds)
        return True

    def keys(self, pattern="*"):
        return [key for key in self.store if fnmatch.fnmatch(key, pattern)]

    def scan(self, cursor=0, match=None, count=None, _type=None, **kwargs):
        keys = self.keys(match or "*")
        return 0, keys

    def publish(self, channel, message):
        return 0


def _get_redis():
    return _redis_holder["redis"]


redis_client.get_redis = _get_redis
# main.py binds get_redis at import. Point that binding at the same store so
# startup and the route share one job store.
api_main.get_redis = _get_redis


def _mongo_unavailable():
    raise ConnectionError("mongodb disabled in comparison limit tests")


api_main.get_db = _mongo_unavailable


def _auth(token: str | None = TOKEN) -> dict[str, str]:
    if token is None:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _broker_depth() -> int:
    return sum(queue.qsize() for queue in Channel.queues.values())


def _counts() -> tuple[int, int, int]:
    return (len(_redis_holder["redis"].store), len(_published), _broker_depth())


def _task_args(entry: dict):
    body = entry["body"]
    if isinstance(body, tuple):
        return body[0]
    return body["args"]


@pytest.fixture
def client():
    memory = MemoryRedis()
    _redis_holder["redis"] = memory
    _published.clear()
    Channel.queues.clear()
    with TestClient(api_main.app) as test_client:
        yield test_client
    _redis_holder["redis"] = memory


def _post(client, url, *, flight_numbers: str | None = None, token: str | None = TOKEN):
    params = {"date": DATE}
    if flight_numbers is not None:
        params["flight_numbers"] = flight_numbers
    return client.post(url, params=params, headers=_auth(token))


def test_comparison_routes_reject_callers_who_do_not_present_the_token(client):
    before = _counts()
    missing_flights = _post(client, FLIGHTS_URL, token=None)
    missing_pnr = _post(client, PNR_URL, token=None)
    wrong_scheme = client.post(
        FLIGHTS_URL,
        params={"date": DATE},
        headers={"Authorization": f"Token {TOKEN}"},
    )
    wrong_token = _post(client, PNR_URL, token="placeholder-not-the-configured-token")
    oversized = _post(
        client,
        FLIGHTS_URL,
        flight_numbers=",".join(str(i) for i in range(MAX_FLIGHT_COUNT + 5)),
        token=None,
    )

    assert missing_flights.status_code == 401
    assert missing_pnr.status_code == 401
    assert wrong_scheme.status_code == 401
    assert wrong_token.status_code == 401
    assert oversized.status_code == 401
    assert _counts() == before


def test_open_jobs_are_capped_across_flight_and_pnr_routes(client):
    for _ in range(MAX_OPEN_COMPARISON_JOBS):
        accepted = _post(client, FLIGHTS_URL)
        assert accepted.status_code == 202
        assert _task_args(_published[-1])[2] is None
        assert _published[-1]["sender"] == "falconeye.compare_flights"

    before = _counts()
    extra_flight = _post(client, FLIGHTS_URL)
    extra_pnr = _post(client, PNR_URL)

    assert extra_flight.status_code == 429
    assert extra_pnr.status_code == 429
    assert _counts() == before


def test_pnr_jobs_count_against_the_same_cap(client):
    for _ in range(MAX_OPEN_COMPARISON_JOBS):
        accepted = _post(client, PNR_URL)
        assert accepted.status_code == 202
        assert _published[-1]["sender"] == "falconeye.compare_pnr_cross_db"
        assert _task_args(_published[-1])[1] == DATE

    before = _counts()
    extra_flight = _post(client, FLIGHTS_URL)
    extra_pnr = _post(client, PNR_URL)

    assert extra_flight.status_code == 429
    assert extra_pnr.status_code == 429
    assert _counts() == before


def test_a_finished_job_no_longer_consumes_a_cap_slot(client):
    accepted = [
        _post(client, FLIGHTS_URL) for _ in range(MAX_OPEN_COMPARISON_JOBS)
    ]
    assert [response.status_code for response in accepted] == [202] * MAX_OPEN_COMPARISON_JOBS

    finished_id = accepted[0].json()["jobId"]
    redis_client.update_job(finished_id, status="completed")

    another = _post(client, PNR_URL)
    assert another.status_code == 202

    blocked = _post(client, FLIGHTS_URL)
    assert blocked.status_code == 429


def test_flight_list_over_the_count_limit_is_rejected_without_a_job(client):
    numbers = [str(1000 + i) for i in range(MAX_FLIGHT_COUNT)]
    accepted = _post(client, FLIGHTS_URL, flight_numbers=",".join(numbers))
    assert accepted.status_code == 202
    assert _task_args(_published[-1])[1] == DATE
    assert _task_args(_published[-1])[2] == numbers

    before = _counts()
    extra = numbers + ["9999"]
    rejected = _post(client, FLIGHTS_URL, flight_numbers=",".join(extra))
    assert rejected.status_code == 422
    assert _counts() == before


def test_flight_list_over_the_length_limit_is_rejected_without_a_job(client):
    at_limit = "8" * MAX_FLIGHT_LIST_CHARS
    accepted = _post(client, FLIGHTS_URL, flight_numbers=at_limit)
    assert accepted.status_code == 202
    assert _task_args(_published[-1])[2] == [at_limit]

    before = _counts()
    rejected = _post(client, FLIGHTS_URL, flight_numbers="8" * (MAX_FLIGHT_LIST_CHARS + 1))
    assert rejected.status_code == 422
    assert _counts() == before


def test_comparison_job_records_expire_within_an_hour(client):
    flight = _post(client, FLIGHTS_URL)
    pnr = _post(client, PNR_URL)
    assert flight.status_code == 202
    assert pnr.status_code == 202

    memory = _redis_holder["redis"]
    for response in (flight, pnr):
        key = f"job:{response.json()['jobId']}"
        assert memory.store[key]["ex"] <= MAX_COMPARISON_JOB_TTL_SECONDS
        assert 0 < memory.ttl(key) <= MAX_COMPARISON_JOB_TTL_SECONDS

    set_job_running(flight.json()["jobId"])
    set_job_running(pnr.json()["jobId"])
    for response in (flight, pnr):
        key = f"job:{response.json()['jobId']}"
        assert memory.store[key]["ex"] <= MAX_COMPARISON_JOB_TTL_SECONDS
        stored = json.loads(memory.get(key))
        assert stored["status"] == "running"


def test_comparison_tasks_stay_off_the_ingestion_worker_queue(client):
    flight = _post(client, FLIGHTS_URL)
    pnr = _post(client, PNR_URL)
    assert flight.status_code == 202
    assert pnr.status_code == 202

    from backend.tasks.ingestion import run_batch_ingestion

    run_batch_ingestion.delay("ingestion-job", [])

    flight_message = next(
        entry for entry in _published if entry["sender"] == "falconeye.compare_flights")
    pnr_message = next(
        entry for entry in _published if entry["sender"] == "falconeye.compare_pnr_cross_db")
    ingestion_message = next(
        entry for entry in _published if entry["sender"] == "falconeye.ingest_batch")

    assert _task_args(flight_message)[1] == DATE
    assert _task_args(flight_message)[2] is None
    assert _task_args(pnr_message)[1] == DATE

    ingestion_queue = ingestion_message["routing_key"]
    consumed = set(celery_app.amqp.queues.consume_from)
    assert ingestion_queue in consumed

    for entry in (flight_message, pnr_message):
        assert entry["routing_key"] != ingestion_queue
        assert entry["routing_key"] not in consumed
        assert Channel.queues[entry["routing_key"]].qsize() >= 1
