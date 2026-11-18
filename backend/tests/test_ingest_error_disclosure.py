"""Feeder failures must not return internal exception text.

These tests exercise the live ingest route and the batch worker the Today
page polls. They fail while the handler copies the feeder exception into the
HTTP body, and they pass when that body is the fixed message below.

Contract once the leak is fixed:
  * POST /flights/ingest returns 502 and {"detail": "Ingestion failed"}
  * a failed batch job's "error" field is exactly "Ingestion failed"

The response must not contain storage host names, driver errors, or the
Sabre endpoint host.

Not covered here, same disclosure class:
  * passenger-list storage errors copied into the 409 detail by
    _extract_ingest_error after the per-flight handler catches them
  * reservations, trip-report, and schedule storage errors, which leave
    run_feeder through the same uncaught path as the flight-status write
    and hit the same route handler
  * comparison tasks storing str(exc) on the shared job record
  * the Today page component itself (it renders job.error and Error.message;
    those strings are what the API tests assert)
"""

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Set before backend imports. load_dotenv does not override existing values.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_CPAID"] = "TEST"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-not-a-secret"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "BAH"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"
os.environ["POSTGRES_URI"] = ""
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"

STORAGE_HOST = "storage-pipeline.invalid"
MONGO_URI = (
    f"mongodb://{STORAGE_HOST}:27017/"
    "?serverSelectionTimeoutMS=400&connectTimeoutMS=200&directConnection=true"
)
os.environ["MONGODB_URI"] = MONGO_URI

SABRE_SESSION_HOST = "sabre-session.invalid"
FIXED_FAILURE = "Ingestion failed"

import pytest
from fastapi.testclient import TestClient

import backend.feeder.storage as feeder_storage
from backend.api.main import app
from backend.api.redis_client import create_job, get_job


VALID_FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BAH",
    "departureDate": "2026-03-19",
    "departureDateTime": "2026-03-19T08:00:00",
}


class _SoapHandler(BaseHTTPRequestHandler):
    """Minimal SOAP peer built from the request markers in sabre templates.

    SessionCreateRQ is recognized by SabreClient.create_session via the
    BinarySecurityToken element. ACS_FlightDetailRQ is recognized by
    SabreClient._extract_body looking for ns3:ACS_FlightDetailRS.
    """

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", "replace")
        if "SessionCreateRQ" in body:
            xml = (
                "<?xml version=\"1.0\"?>"
                "<soap-env:Envelope><soap-env:Body>"
                "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
                "</soap-env:Body></soap-env:Envelope>"
            )
        elif "ACS_FlightDetailRQ" in body:
            xml = (
                "<?xml version=\"1.0\"?>"
                "<soap-env:Envelope><soap-env:Body>"
                "<ns3:ACS_FlightDetailRS></ns3:ACS_FlightDetailRS>"
                "</soap-env:Body></soap-env:Envelope>"
            )
        else:
            xml = (
                "<?xml version=\"1.0\"?>"
                "<soap-env:Envelope><soap-env:Body><ok/></soap-env:Body>"
                "</soap-env:Envelope>"
            )
        payload = xml.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


class _MemoryRedis:
    """In-process stand-in for the get/set/publish calls in redis_client.py."""

    def __init__(self):
        self.store = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def publish(self, channel, message):
        return 1

    def ping(self):
        return True


@pytest.fixture
def client(monkeypatch):
    def _database_down():
        raise RuntimeError("database unavailable")

    # Lifespan imports get_db by name. Skip the 10s startup ping so the
    # request path is the one that opens the feeder client.
    monkeypatch.setattr("backend.api.main.get_db", _database_down)
    feeder_storage.close()
    with TestClient(app) as test_client:
        yield test_client
    feeder_storage.close()


def _soap_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SoapHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _assert_fixed_detail(response):
    assert response.status_code == 502
    assert response.json()["detail"] == FIXED_FAILURE
    assert STORAGE_HOST not in response.text
    assert SABRE_SESSION_HOST not in response.text
    assert "ServerSelectionTimeoutError" not in response.text
    assert "AutoReconnect" not in response.text


def test_sync_ingest_hides_storage_failure_text(client):
    """A valid flight whose Mongo write cannot finish must not echo the driver error."""
    server = _soap_server()
    try:
        os.environ["SABRE_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
        os.environ["MONGODB_URI"] = MONGO_URI
        feeder_storage.close()
        response = client.post("/flights/ingest", json=VALID_FLIGHT)
    finally:
        server.shutdown()
    _assert_fixed_detail(response)


def test_sync_ingest_hides_sabre_session_failure_text(client):
    """A Sabre session error raised before the per-flight handler must not echo the host."""
    os.environ["SABRE_BASE_URL"] = f"http://{SABRE_SESSION_HOST}"
    response = client.post("/flights/ingest", json=VALID_FLIGHT)
    _assert_fixed_detail(response)


def test_batch_job_hides_feeder_exception_text(client, monkeypatch):
    """The job record the Today page polls must not keep the feeder exception string."""
    memory = _MemoryRedis()
    monkeypatch.setattr("backend.api.redis_client.get_redis", lambda: memory)

    server = _soap_server()
    try:
        os.environ["SABRE_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
        os.environ["MONGODB_URI"] = MONGO_URI
        feeder_storage.close()

        from backend.tasks.ingestion import run_batch_ingestion

        job_id = "job-storage-failure"
        create_job(job_id, 1, job_type="ingestion")
        run_batch_ingestion.apply(args=(job_id, [VALID_FLIGHT]))
    finally:
        server.shutdown()

    # Worker path writes the job, then the same GET the Today page polls.
    polled = client.get(f"/flights/ingest/jobs/{job_id}")
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "failed"
    assert body["error"] == FIXED_FAILURE
    assert STORAGE_HOST not in polled.text
    assert "ServerSelectionTimeoutError" not in polled.text
    assert "AutoReconnect" not in polled.text

    stored = get_job(job_id)
    assert stored["error"] == FIXED_FAILURE
