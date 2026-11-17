"""Caller limits for every route that opens a Sabre session.

These tests fail while single-flight ingestion, batch ingestion, schedule
lookup, and live passenger detail accept unlimited anonymous calls. They
pass once those routes authenticate the caller and enforce the budgets
below before any Sabre session is opened and before any ingestion job is
written.

Contract the implementation must satisfy
----------------------------------------
Read these environment variables on every request:

- ``FALCONEYE_API_TOKENS``: comma-separated bearer tokens. The caller id
  is the token. Tests set ``caller-a,caller-b``.
- ``FALCONEYE_SABRE_LIMIT_WINDOW_SECONDS``: budget window. Tests set ``2``.
  Usage older than this window no longer counts. A pause of this many
  seconds plus a quarter-second clears both caller and global counters.
- ``FALCONEYE_SABRE_SESSIONS_PER_CALLER``: tests set ``3``.
- ``FALCONEYE_SABRE_SESSIONS_GLOBAL``: tests set ``4``.
- ``FALCONEYE_FLIGHTS_PER_CALLER``: tests set ``4``.
- ``FALCONEYE_FLIGHTS_GLOBAL``: tests set ``6``.

Authentication is the ``Authorization: Bearer <token>`` header. A missing
header, a non-bearer scheme, or an unknown token is HTTP 401. An admitted
call that would exceed either the caller's remaining budget or the global
remaining budget is HTTP 429. A rejected call must not open a Sabre
session, must not write a Redis ingestion job, and must not consume budget.

Counting:

- ``POST /flights/schedule/lookup`` and
  ``GET /flights/{flight}/passengers/{pnr}/detail`` each take one session
  and zero flights.
- ``POST /flights/ingest`` takes one session and one flight.
- ``POST /flights/ingest/batch`` takes one session and one flight per
  submitted flight. The batch is admitted entirely or not at all. A batch
  that does not fit in the remaining flight budget is 429 and opens no
  session. An admitted batch opens one session for the whole batch.

``SABRE_API_DELAY_SECONDS`` is a pause inside one ``SabreClient`` instance.
It is not a substitute for these budgets. Concurrent requests, including
ones already inside the Sabre call, still have to stay inside the caps.

The operator CLI ``python -m backend.feeder.runner`` is not covered. Its
input file is supplied by the operator, not by an HTTP caller.

The Sabre HTTP transport is replaced with a local stand-in at
``requests.post`` (requests 2.34.2, ``requests/api.py``). Session creation
is the real ``SabreClient.create_session`` path. Batch admission runs the
real Celery task body (``task_always_eager``) so a queued batch that
reaches ``run_feeder`` is visible as a ``SessionCreateRQ``.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Set before importing the app. load_dotenv() does not override these.
os.environ["SABRE_BASE_URL"] = "http://sabre.invalid"
os.environ["SABRE_CPAID"] = "TEST"
os.environ["SABRE_USERNAME"] = "placeholder"
os.environ["SABRE_PASSWORD"] = "placeholder"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "XXX"
os.environ["SABRE_ORGANIZATION"] = "TEST"
os.environ["SABRE_DOMAIN"] = "TEST"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"
os.environ["FALCONEYE_API_TOKENS"] = "caller-a,caller-b"
os.environ["FALCONEYE_SABRE_LIMIT_WINDOW_SECONDS"] = "2"
os.environ["FALCONEYE_SABRE_SESSIONS_PER_CALLER"] = "3"
os.environ["FALCONEYE_SABRE_SESSIONS_GLOBAL"] = "4"
os.environ["FALCONEYE_FLIGHTS_PER_CALLER"] = "4"
os.environ["FALCONEYE_FLIGHTS_GLOBAL"] = "6"

import pytest
import redis
from pymongo import MongoClient
from requests import Response
from starlette.testclient import TestClient

import backend.sabre.client as sabre_client
from backend.celery_app import app as celery_app

WINDOW_SECONDS = 2
SESSIONS_PER_CALLER = 3
SESSIONS_GLOBAL = 4
FLIGHTS_PER_CALLER = 4
FLIGHTS_GLOBAL = 6
CALLER_A = "caller-a"
CALLER_B = "caller-b"

celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True

_lock = threading.Lock()
_sessions = 0
_inflight = 0
_max_inflight = 0

pytestmark = pytest.mark.filterwarnings(
    "ignore:Using `httpx` with `starlette.testclient` is deprecated:DeprecationWarning"
)


def _fake_post(url, data=None, json=None, **kwargs):
    """Stand in for requests.post. Counts SessionCreateRQ and returns a token.

    The SOAP body is ignored so a configured credential never leaves this
    process in an assertion or a log line from the test.
    """
    global _sessions, _inflight, _max_inflight
    action = (kwargs.get("headers") or {}).get("SOAPAction")
    if action == "SessionCreateRQ":
        with _lock:
            _sessions += 1
            _inflight += 1
            _max_inflight = max(_max_inflight, _inflight)
    response = Response()
    response.status_code = 200 if action == "SessionCreateRQ" else 500
    response.url = str(url)
    response.encoding = "utf-8"
    if action == "SessionCreateRQ":
        body = b"<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
    else:
        body = b"stub"
    response._content = body
    response._content_consumed = True
    if action == "SessionCloseRQ":
        with _lock:
            _inflight = max(0, _inflight - 1)
    return response


sabre_client.requests.post = _fake_post


def session_count() -> int:
    with _lock:
        return _sessions


def reset_inflight_gauge() -> None:
    global _inflight, _max_inflight
    with _lock:
        _inflight = 0
        _max_inflight = 0


def max_inflight_sessions() -> int:
    with _lock:
        return _max_inflight


def job_keys() -> set[str]:
    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        return set(client.keys("job:*"))
    finally:
        client.close()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def flight(number: str) -> dict:
    return {
        "airline": "GF",
        "flightNumber": number,
        "origin": "BAH",
        "departureDate": "2026-03-19",
        "departureDateTime": "2026-03-19T08:00:00",
        "flightSequenceNumber": 1,
    }


def flights(start: int, count: int) -> dict:
    return {"flights": [flight(str(start + i)) for i in range(count)]}


SCHEDULE = {
    "airline": "GF",
    "flightNumber": "2006",
    "departureDate": "2026-03-19",
}
DETAIL = "/flights/2006/passengers/ABC123/detail"


def wait_for_new_window() -> None:
    time.sleep(WINDOW_SECONDS + 0.25)


def assert_unchanged(before_sessions: int, before_jobs: set[str]) -> None:
    assert session_count() == before_sessions
    assert job_keys() == before_jobs


@pytest.fixture(scope="module")
def client():
    mongo = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=5000)
    db = mongo["falconeye"]
    db["passenger_list"].delete_many(
        {"flightNumber": "2006", "departureDate": "2026-03-19"}
    )
    db["passenger_list"].insert_one(
        {
            "flightNumber": "2006",
            "departureDate": "2026-03-19",
            "origin": "BAH",
            "fetchedAt": "2026-03-19T00:00:00Z",
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN"}
            ],
        }
    )
    redis.Redis.from_url(os.environ["REDIS_URL"]).flushdb()
    with TestClient(app()) as test_client:
        yield test_client
    mongo.close()


def app():
    from backend.api.main import app as fastapi_app
    return fastapi_app


def test_anonymous_schedule_lookup_does_not_open_a_sabre_session(client):
    before = session_count()
    jobs = job_keys()
    response = client.post("/flights/schedule/lookup", json=SCHEDULE)
    assert response.status_code == 401, _opened(response, before)
    assert_unchanged(before, jobs)


def test_anonymous_sync_ingest_does_not_open_a_sabre_session(client):
    before = session_count()
    jobs = job_keys()
    response = client.post("/flights/ingest", json=flight("2006"))
    assert response.status_code == 401, _opened(response, before)
    assert_unchanged(before, jobs)


def test_anonymous_passenger_detail_does_not_open_a_sabre_session(client):
    before = session_count()
    jobs = job_keys()
    response = client.get(
        DETAIL, params={"origin": "BAH", "date": "2026-03-19", "airline": "GF"}
    )
    assert response.status_code == 401, _opened(response, before)
    assert_unchanged(before, jobs)


def test_anonymous_batch_does_not_queue_sabre_work(client):
    before = session_count()
    jobs = job_keys()
    statuses = []
    for index in range(3):
        response = client.post(
            "/flights/ingest/batch", json=flights(4000 + index * 2, 2)
        )
        statuses.append(response.status_code)
    oversized = client.post("/flights/ingest/batch", json=flights(1000, 50))
    statuses.append(oversized.status_code)
    assert statuses == [401, 401, 401, 401], (
        f"batch statuses {statuses}; Sabre sessions opened: "
        f"{session_count() - before}"
    )
    assert_unchanged(before, jobs)


def test_unknown_or_non_bearer_token_does_not_open_a_sabre_session(client):
    before = session_count()
    jobs = job_keys()
    headers = [
        {"Authorization": "Bearer not-a-caller"},
        {"Authorization": CALLER_A},
    ]
    calls = [
        lambda h: client.post("/flights/schedule/lookup", json=SCHEDULE, headers=h),
        lambda h: client.post("/flights/ingest", json=flight("2006"), headers=h),
        lambda h: client.post("/flights/ingest/batch", json=flights(2100, 1), headers=h),
        lambda h: client.get(
            DETAIL,
            params={"origin": "BAH", "date": "2026-03-19", "airline": "GF"},
            headers=h,
        ),
    ]
    for header in headers:
        for call in calls:
            response = call(header)
            assert response.status_code == 401, _opened(response, before)
            assert_unchanged(before, jobs)


def test_schema_overflow_batch_opens_no_session(client):
    before = session_count()
    jobs = job_keys()
    response = client.post(
        "/flights/ingest/batch",
        json=flights(5000, 51),
        headers=auth(CALLER_A),
    )
    assert response.status_code in (401, 422, 429)
    assert_unchanged(before, jobs)


def test_flight_and_session_budgets(client):
    wait_for_new_window()
    before = session_count()
    jobs = job_keys()

    rejected = client.post(
        "/flights/ingest/batch",
        json=flights(1000, 50),
        headers=auth(CALLER_A),
    )
    assert rejected.status_code == 429, _opened(rejected, before)
    assert_unchanged(before, jobs)

    too_many = client.post(
        "/flights/ingest/batch",
        json=flights(5000, 51),
        headers=auth(CALLER_A),
    )
    assert too_many.status_code == 422, _opened(too_many, before)
    assert_unchanged(before, jobs)

    first = client.post(
        "/flights/schedule/lookup", json=SCHEDULE, headers=auth(CALLER_A)
    )
    assert first.status_code == 502, _opened(first, before)
    assert session_count() == before + 1

    second = client.post(
        "/flights/schedule/lookup", json=SCHEDULE, headers=auth(CALLER_A)
    )
    assert second.status_code == 502, _opened(second, before)
    assert session_count() == before + 2

    admitted = client.post(
        "/flights/ingest/batch",
        json=flights(3000, FLIGHTS_PER_CALLER),
        headers=auth(CALLER_A),
    )
    assert admitted.status_code == 202, _opened(admitted, before)
    assert admitted.json()["flightsQueued"] == FLIGHTS_PER_CALLER
    assert session_count() == before + 3
    assert len(job_keys() - jobs) == 1

    caller_capped = client.post(
        "/flights/schedule/lookup", json=SCHEDULE, headers=auth(CALLER_A)
    )
    assert caller_capped.status_code == 429, _opened(caller_capped, before)
    assert session_count() == before + 3

    caller_flights_capped = client.post(
        "/flights/ingest/batch",
        json=flights(3100, 1),
        headers=auth(CALLER_A),
    )
    assert caller_flights_capped.status_code == 429, _opened(caller_flights_capped, before)
    assert session_count() == before + 3
    jobs_after_caller = job_keys()

    does_not_fit = client.post(
        "/flights/ingest/batch",
        json=flights(3200, 3),
        headers=auth(CALLER_B),
    )
    assert does_not_fit.status_code == 429, _opened(does_not_fit, before)
    assert session_count() == before + 3
    assert job_keys() == jobs_after_caller

    remainder = client.post(
        "/flights/ingest/batch",
        json=flights(3300, FLIGHTS_GLOBAL - FLIGHTS_PER_CALLER),
        headers=auth(CALLER_B),
    )
    assert remainder.status_code == 202, _opened(remainder, before)
    assert session_count() == before + SESSIONS_GLOBAL
    assert len(job_keys() - jobs_after_caller) == 1

    global_capped = client.get(
        DETAIL,
        params={"origin": "BAH", "date": "2026-03-19", "airline": "GF"},
        headers=auth(CALLER_B),
    )
    assert global_capped.status_code == 429, _opened(global_capped, before)
    assert session_count() == before + SESSIONS_GLOBAL

    other_caller_capped = client.post(
        "/flights/schedule/lookup", json=SCHEDULE, headers=auth(CALLER_A)
    )
    assert other_caller_capped.status_code == 429, _opened(other_caller_capped, before)
    assert session_count() == before + SESSIONS_GLOBAL


def test_sync_ingest_counts_as_one_flight_and_detail_does_not(client):
    wait_for_new_window()
    before = session_count()

    ingested = client.post("/flights/ingest", json=flight("2006"), headers=auth(CALLER_A))
    assert ingested.status_code == 409, _opened(ingested, before)
    assert session_count() == before + 1

    detail = client.get(
        DETAIL,
        params={"origin": "BAH", "date": "2026-03-19", "airline": "GF"},
        headers=auth(CALLER_A),
    )
    assert detail.status_code == 502, _opened(detail, before)
    assert session_count() == before + 2

    admitted = client.post(
        "/flights/ingest/batch",
        json=flights(3400, FLIGHTS_PER_CALLER - 1),
        headers=auth(CALLER_A),
    )
    assert admitted.status_code == 202, _opened(admitted, before)
    assert session_count() == before + 3

    overflow = client.post(
        "/flights/ingest/batch",
        json=flights(3500, 1),
        headers=auth(CALLER_A),
    )
    assert overflow.status_code == 429, _opened(overflow, before)
    assert session_count() == before + 3


def test_concurrent_schedule_lookups_share_one_caller_budget(client):
    wait_for_new_window()
    before = session_count()
    reset_inflight_gauge()
    sabre_client.SabreClient.API_CALL_DELAY = 0.2
    try:
        responses = _parallel(
            [
                lambda: client.post(
                    "/flights/schedule/lookup", json=SCHEDULE, headers=auth(CALLER_A)
                )
                for _ in range(8)
            ],
        )
    finally:
        sabre_client.SabreClient.API_CALL_DELAY = 0

    statuses = [response.status_code for response in responses]
    assert statuses.count(502) <= SESSIONS_PER_CALLER
    assert statuses.count(429) == len(statuses) - statuses.count(502)
    assert session_count() - before == statuses.count(502)
    assert session_count() - before <= SESSIONS_PER_CALLER
    assert max_inflight_sessions() <= SESSIONS_PER_CALLER
    assert set(statuses) <= {502, 429}


def test_concurrent_schedule_lookups_share_a_global_budget(client):
    wait_for_new_window()
    before = session_count()
    reset_inflight_gauge()

    def lookup(token):
        return lambda: (
            token,
            client.post(
                "/flights/schedule/lookup", json=SCHEDULE, headers=auth(token)
            ),
        )

    calls = [lookup(CALLER_A) for _ in range(4)] + [lookup(CALLER_B) for _ in range(4)]
    responses = _parallel(calls)
    by_caller = {CALLER_A: [], CALLER_B: []}
    for token, response in responses:
        by_caller[token].append(response.status_code)

    for token, statuses in by_caller.items():
        assert statuses.count(502) <= SESSIONS_PER_CALLER, token
        assert set(statuses) <= {502, 429}
    total_admitted = sum(statuses.count(502) for statuses in by_caller.values())
    assert total_admitted <= SESSIONS_GLOBAL
    assert session_count() - before == total_admitted
    assert max_inflight_sessions() <= SESSIONS_GLOBAL


def test_concurrent_batches_cannot_oversubscribe_flights(client):
    wait_for_new_window()
    before = session_count()
    jobs = job_keys()

    def batch(token, start):
        return lambda: (
            token,
            client.post(
                "/flights/ingest/batch",
                json=flights(start, FLIGHTS_PER_CALLER),
                headers=auth(token),
            ),
        )

    calls = [
        batch(CALLER_A, 6000),
        batch(CALLER_A, 6100),
        batch(CALLER_A, 6200),
        batch(CALLER_B, 6300),
    ]
    results = _parallel(calls)
    admitted = [response for _token, response in results if response.status_code == 202]
    rejected = [response for _token, response in results if response.status_code == 429]
    assert len(admitted) == 1, [response.status_code for _token, response in results]
    assert len(rejected) == len(results) - 1
    assert session_count() - before == 1
    assert len(job_keys() - jobs) == 1


def _parallel(calls):
    barrier = threading.Barrier(len(calls))

    def run(call):
        barrier.wait(timeout=5)
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def _opened(response, before: int) -> str:
    return (
        f"HTTP {response.status_code}; Sabre sessions opened: "
        f"{session_count() - before}"
    )
