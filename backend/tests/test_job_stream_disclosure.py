"""Security spec for the global job stream and stored job errors.

These tests fail while the current behavior is in place. They pass once an
anonymous caller can no longer learn another caller's job id, flight key, or
Sabre fault text from GET /events/jobs, and once the job status routes and the
per-job event stream return a generic failure reason instead of that text.

The Sabre endpoint in this file is a local HTTP server. SabreClient, the
feeder, the Celery task functions, Redis pub/sub, and the FastAPI routes all
run. The server never records request bodies.

Not covered here, because those routes return the Sabre fault only to the
client that sent the request and do not publish it on the job channel:

- POST /flights/ingest
- GET /flights/{flight_number}/passengers/{pnr}/detail
- the schedule lookup route in backend/api/routes/schedule.py

Comparison progress events use the same publish path as ingestion progress.
They are not driven through compare_flights_for_date, which needs live
MongoDB and Postgres. The ingestion progress assertion covers that publish.
"""

import asyncio
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=200")
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "placeholder-cpaid")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "placeholder-pcc")
os.environ.setdefault("SABRE_ORGANIZATION", "placeholder-org")
os.environ.setdefault("SABRE_DOMAIN", "placeholder-domain")

import httpx
import pytest
import redis
from starlette.testclient import TestClient

from backend.api.main import app
from backend.api.redis_client import close_redis, create_job, get_job
from backend.api.routes.events import _all_events_generator
from backend.feeder import storage
from backend.sabre.client import SabreError
from backend.tasks.comparison import run_flight_comparison, run_pnr_comparison
from backend.tasks.ingestion import run_batch_ingestion

SESSION_MARKER = "SESSION-FRAGMENT-QUX"
PASSENGER_MARKER = "PAX-SURNAME-QUX"
FLIGHT_KEY = "GF9057"
COMPARISON_MARKER = "'MONGODB_URI'"
FAULT_TEMPLATE = (
    '<?xml version="1.0"?>'
    '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soap-env:Body><soap-env:Fault><faultstring>{markers}</faultstring>"
    "</soap-env:Fault></soap-env:Body></soap-env:Envelope>"
)
SESSION_OK = (
    "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
)
FLIGHT = {
    "airline": "GF",
    "flightNumber": "9057",
    "origin": "BAH",
    "departureDate": "2026-04-01",
    "departureDateTime": "2026-04-01T10:00:00",
}
JOB_STATUS_PATHS = ("/flights/ingest/jobs/{job_id}", "/compare/jobs/{job_id}")


class _SabreHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        action = self.headers.get("SOAPAction", "")
        if action == "SessionCreateRQ" and self.server.session_ok:
            body = SESSION_OK.encode()
            status = 200
        else:
            body = self.server.fault_body.encode()
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class SabreFaultServer:
    def __init__(self, session_ok: bool, markers: str):
        self.session_ok = session_ok
        self.fault_body = FAULT_TEMPLATE.format(markers=markers)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SabreHandler)
        self._httpd.session_ok = session_ok
        self._httpd.fault_body = self.fault_body
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        host, port = self._httpd.server_address
        os.environ["SABRE_BASE_URL"] = f"http://{host}:{port}"
        return self

    def __exit__(self, exc_type, exc, tb):
        self._httpd.shutdown()
        self._httpd.server_close()
        return False


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        client.timeout = httpx.Timeout(30.0, read=4.0)
        yield client
    close_redis()


@pytest.fixture(autouse=True)
def _empty_redis():
    client = redis.Redis.from_url(os.environ["REDIS_URL"])
    client.flushdb()
    yield
    client.flushdb()


def _patch_storage(monkeypatch):
    monkeypatch.setattr(storage, "get_db", lambda: object())
    monkeypatch.setattr(storage, "ensure_indexes", lambda: None)


def _run_batch(job_id: str, payloads: list[dict], expected: type[Exception]):
    create_job(job_id, len(payloads), job_type="ingestion")
    try:
        run_batch_ingestion(job_id, payloads)
    except expected:
        return
    raise AssertionError(f"batch ingestion completed without {expected.__name__}")


def _collect_stream(client: TestClient, path: str, params: dict | None, trigger):
    """Open an event stream, run trigger once it is subscribed, then close it.

    Starlette's test transport blocks until the next SSE chunk and does not
    apply the HTTP read timeout, and this route emits a ping well after the
    job event. The reader therefore runs on its own thread and the response
    is closed after the worker has had time to publish.
    """
    chunks: list[str] = []
    holder: dict = {}
    started = threading.Event()

    def reader():
        try:
            with client.stream("GET", path, params=params) as response:
                holder["response"] = response
                holder["status"] = response.status_code
                started.set()
                if response.status_code != 200:
                    chunks.append(response.text)
                    return
                for piece in response.iter_text():
                    chunks.append(piece)
        except Exception as exc:
            holder["error"] = exc
        finally:
            started.set()

    thread = threading.Thread(target=reader)
    thread.start()
    if not started.wait(20):
        raise TimeoutError("event stream did not start")
    status = holder.get("status")
    if status == 200:
        deadline = time.time() + 5
        while "event: connected" not in "".join(chunks) and time.time() < deadline:
            time.sleep(0.02)
        if "event: connected" not in "".join(chunks):
            response = holder.get("response")
            if response is not None:
                response.close()
            thread.join(5)
            raise TimeoutError("event stream did not subscribe")
        trigger()
        time.sleep(2)
        response = holder.get("response")
        if response is not None:
            response.close()
    else:
        trigger()
    thread.join(10)
    if thread.is_alive():
        raise TimeoutError("event stream reader did not stop")
    error = holder.get("error")
    if error is not None and not chunks:
        raise error
    return status, "".join(chunks)


def _read_limited(client: TestClient, path: str, wait: float = 5) -> tuple[int | None, str]:
    chunks: list[str] = []
    holder: dict = {}

    def reader():
        try:
            with client.stream("GET", path) as response:
                holder["response"] = response
                holder["status"] = response.status_code
                for piece in response.iter_text():
                    chunks.append(piece)
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=reader)
    thread.start()
    thread.join(wait)
    if thread.is_alive():
        response = holder.get("response")
        if response is not None:
            response.close()
        thread.join(5)
    if holder.get("error") is not None and not chunks:
        raise holder["error"]
    return holder.get("status"), "".join(chunks)


def _assert_absent(body: str, needles: tuple[str, ...]):
    for needle in needles:
        assert needle not in body, f"{needle} was disclosed"


def _assert_generic_status(client: TestClient, job_id: str, needles: tuple[str, ...]):
    for template in JOB_STATUS_PATHS:
        path = template.format(job_id=job_id)
        response = client.get(path)
        assert response.status_code == 200, path
        payload = response.json()
        assert payload["jobId"] == job_id
        assert payload["status"] == "failed"
        assert isinstance(payload.get("error"), str) and payload["error"].strip()
        _assert_absent(response.text, needles)


def test_anonymous_global_stream_hides_other_ingestion_session_fault(api, monkeypatch):
    """Attack steps 1-3: an anonymous GET /events/jobs sees a session fault."""
    _patch_storage(monkeypatch)
    job_id = str(uuid.uuid4())
    needles = (job_id, SESSION_MARKER, PASSENGER_MARKER, "SessionCreateRQ returned HTTP")

    def trigger():
        with SabreFaultServer(session_ok=False, markers=f"{SESSION_MARKER} {PASSENGER_MARKER}"):
            _run_batch(job_id, [FLIGHT], SabreError)

    status, body = _collect_stream(api, "/events/jobs", None, trigger)
    stored = get_job(job_id)
    assert stored is not None and stored["status"] == "failed"
    if status == 200:
        assert '"filter": null' in body
    _assert_absent(body, needles)
    _assert_generic_status(api, job_id, needles[1:])


def test_matching_job_type_still_hides_the_job_from_anonymous_clients(api, monkeypatch):
    """job_type=ingestion matches the published job and must not authorize a caller."""
    _patch_storage(monkeypatch)
    job_id = str(uuid.uuid4())
    needles = (job_id, SESSION_MARKER, "SessionCreateRQ returned HTTP")

    def trigger():
        with SabreFaultServer(session_ok=False, markers=SESSION_MARKER):
            _run_batch(job_id, [FLIGHT], SabreError)

    status, body = _collect_stream(
        api, "/events/jobs", {"job_type": "ingestion"}, trigger,
    )
    stored = get_job(job_id)
    assert stored is not None and stored["status"] == "failed"
    if status == 200:
        assert '"filter": "ingestion"' in body
    _assert_absent(body, needles)


def test_job_type_argument_does_not_select_another_callers_events(api, monkeypatch):
    """A comparison subscriber must not receive an ingestion session fault."""
    _patch_storage(monkeypatch)
    job_id = str(uuid.uuid4())
    needles = (job_id, SESSION_MARKER, FLIGHT_KEY)

    def trigger():
        with SabreFaultServer(session_ok=False, markers=SESSION_MARKER):
            _run_batch(job_id, [FLIGHT], SabreError)

    status, body = _collect_stream(
        api, "/events/jobs", {"job_type": "comparison"}, trigger,
    )
    stored = get_job(job_id)
    assert stored is not None and stored["status"] == "failed"
    if status == 200:
        assert '"filter": "comparison"' in body
    _assert_absent(body, needles)


def test_job_type_filter_is_applied_to_the_yielded_payload(monkeypatch):
    """The generator itself drops an ingestion fault when asked for comparison.

    Invoking the generator directly still fails if a route-level refusal is
    the only change and the yield path forwards every Redis payload.
    """
    _patch_storage(monkeypatch)
    job_id = str(uuid.uuid4())

    async def _scenario():
        generator = _all_events_generator("comparison")
        try:
            connected = await asyncio.wait_for(generator.__anext__(), timeout=2)
            assert "comparison" in connected["data"]
            with SabreFaultServer(session_ok=False, markers=SESSION_MARKER):
                await asyncio.to_thread(_run_batch, job_id, [FLIGHT], SabreError)
            try:
                yielded = await asyncio.wait_for(generator.__anext__(), timeout=2.5)
            except TimeoutError:
                yielded = None
        finally:
            await generator.aclose()
        return yielded

    yielded = asyncio.run(_scenario())
    stored = get_job(job_id)
    assert stored is not None and stored["status"] == "failed"
    if yielded is not None:
        blob = str(yielded)
        _assert_absent(blob, (job_id, SESSION_MARKER, "SessionCreateRQ returned HTTP"))


def test_caught_sabre_faults_and_flight_keys_stay_off_the_global_stream(api, monkeypatch):
    """Flight-status and later Sabre faults are caught, stored, and published."""
    _patch_storage(monkeypatch)
    job_id = str(uuid.uuid4())
    needles = (
        job_id,
        PASSENGER_MARKER,
        FLIGHT_KEY,
        "ACS_FlightDetailRQ returned HTTP",
        "GetPassengerListRQ returned HTTP",
        "Trip_SearchRQ returned HTTP",
        "Trip_ReportsRQ returned HTTP",
        "VerifyFlightDetailsLLSRQ returned HTTP",
    )

    def trigger():
        with SabreFaultServer(session_ok=True, markers=PASSENGER_MARKER):
            create_job(job_id, 1, job_type="ingestion")
            result = run_batch_ingestion(job_id, [FLIGHT])
            if not isinstance(result, dict):
                raise AssertionError("caught Sabre faults did not finish the batch")

    _status, body = _collect_stream(api, "/events/jobs", {"job_type": "pnr_comparison"}, trigger)
    stored = get_job(job_id)
    assert stored is not None and stored["status"] == "failed"
    _assert_absent(body, needles)
    _assert_generic_status(api, job_id, needles[1:])


def test_finished_job_event_stream_hides_the_stored_sabre_fault(api, monkeypatch):
    """GET /events/jobs/{job_id} replays a finished job without the SOAP body."""
    _patch_storage(monkeypatch)
    job_id = str(uuid.uuid4())
    needles = (SESSION_MARKER, "SessionCreateRQ returned HTTP")
    with SabreFaultServer(session_ok=False, markers=SESSION_MARKER):
        _run_batch(job_id, [FLIGHT], SabreError)

    status_code, body = _read_limited(api, f"/events/jobs/{job_id}")
    assert status_code in (200, 401, 403)
    assert body.strip()
    _assert_absent(body, needles)
    if status_code == 200:
        assert "failed" in body


def test_comparison_and_pnr_failures_are_not_broadcast(api, monkeypatch):
    """Both comparison tasks publish str(exc) on the shared channel."""
    monkeypatch.setattr(storage, "_db", None)
    monkeypatch.setattr(storage, "_client", None)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    flight_job = str(uuid.uuid4())
    pnr_job = str(uuid.uuid4())
    needles = (flight_job, pnr_job, COMPARISON_MARKER)

    def trigger():
        create_job(flight_job, 1, job_type="comparison")
        create_job(pnr_job, 1, job_type="pnr_comparison")
        with pytest.raises(KeyError):
            run_flight_comparison(flight_job, "2026-04-01", None)
        with pytest.raises(KeyError):
            run_pnr_comparison(pnr_job, "2026-04-01")

    _status, body = _collect_stream(api, "/events/jobs", None, trigger)
    assert get_job(flight_job)["status"] == "failed"
    assert get_job(pnr_job)["status"] == "failed"
    _assert_absent(body, needles)
    _assert_generic_status(api, flight_job, (COMPARISON_MARKER,))
    _assert_generic_status(api, pnr_job, (COMPARISON_MARKER,))
