"""Spec for Sabre sign-in retries.

SessionCreate allocates a Sabre session as soon as the request is accepted.
``SabreClient._post`` retries that call on ``requests.Timeout`` and
``requests.ConnectionError``. The client keeps only the token parsed from the
last response and ``close_session`` closes only that token, so every earlier
sign-in stays open.

These tests fail while that retry is in place. A fix should:

- send SessionCreate only once when the response is withheld past the read
  timeout or the connection is dropped after the request is accepted
- still retry a later read (ACS_FlightDetailRQ) that is safe to repeat
- close every token whose response was actually returned

The one sign-in whose response never reaches the client cannot be closed,
because the token is not available. The bug is the additional sign-in.

Covered callers, all unauthenticated in ``backend/api/main.py``:

- ``POST /flights/schedule/lookup``
- ``POST /flights/ingest`` (``run_feeder``, also used by the Celery batch task)
- ``GET /flights/{flight}/passengers/{pnr}/detail`` once a stored passenger exists

The batch HTTP handler only enqueues Celery. It is not called here. The worker
enters the same ``run_feeder`` path the ingest test runs.
"""

import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("SABRE_API_DELAY_SECONDS", "0")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1")
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:1")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")

from backend.sabre.client import SabreClient, SabreError  # noqa: E402

CLIENT_TIMEOUT = 0.4
WITHHOLD_SECONDS = 1.2
_TOKEN_RE = re.compile(
    rb"<wsse:BinarySecurityToken[^>]*>([^<]+)</wsse:BinarySecurityToken>",
    re.IGNORECASE,
)


class _Ledger:
    def __init__(self, signin_modes, flight_withholds=0):
        self.signin_modes = list(signin_modes)
        self.flight_withholds = flight_withholds
        self.session_creates = 0
        self.flight_posts = 0
        self.delivered = []
        self.undelivered = []
        self.closed = []
        self.held_seconds = []
        self._lock = threading.Lock()

    def pop_signin_mode(self):
        with self._lock:
            self.session_creates += 1
            if self.signin_modes:
                return self.signin_modes.pop(0)
            return "ok"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        action = self.headers.get("SOAPAction", "")
        ledger = self.server.ledger
        try:
            if action == "SessionCreateRQ":
                self._session_create(ledger)
                return
            if action == "SessionCloseRQ":
                match = _TOKEN_RE.search(body)
                token = match.group(1).decode("ascii") if match else ""
                with ledger._lock:
                    ledger.closed.append(token)
                self._write(200, b"<ok/>")
                return
            if action == "ACS_FlightDetailRQ":
                with ledger._lock:
                    ledger.flight_posts += 1
                    withhold = ledger.flight_withholds > 0
                    if withhold:
                        ledger.flight_withholds -= 1
                if withhold:
                    time.sleep(WITHHOLD_SECONDS)
                self._write(500, b"<unavailable/>")
                return
            self._write(500, b"<unavailable/>")
        finally:
            del body

    def _session_create(self, ledger):
        mode = ledger.pop_signin_mode()
        token = f"opened-{ledger.session_creates}"
        if mode == "ok":
            with ledger._lock:
                ledger.delivered.append(token)
                ledger.held_seconds.append(0.0)
            self._write(200, _session_xml(token))
            return
        if mode == "drop":
            with ledger._lock:
                ledger.undelivered.append(token)
                ledger.held_seconds.append(0.0)
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        started = time.monotonic()
        time.sleep(WITHHOLD_SECONDS)
        held = time.monotonic() - started
        with ledger._lock:
            ledger.undelivered.append(token)
            ledger.held_seconds.append(held)
        self._write(200, _session_xml(token))

    def _write(self, status, payload):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def _session_xml(token):
    xml = (
        '<?xml version="1.0"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        "<soap-env:Header>"
        f"<wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>"
        "</soap-env:Header>"
        "<soap-env:Body><SessionCreateRS/></soap-env:Body>"
        "</soap-env:Envelope>"
    )
    return xml.encode("ascii")


class _SabreStandIn(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, ledger):
        self.ledger = ledger
        super().__init__(("127.0.0.1", 0), _Handler)


@pytest.fixture
def short_timeout():
    """Use a short read timeout so a withheld response fails inside the test.

    The production default is 30 seconds. The retry decision does not depend
    on that value: ``requests`` still raises ``ReadTimeout`` after the body
    has been sent, and tenacity still runs ``_post`` again.
    """
    original = SabreClient._post.__wrapped__.__defaults__
    SabreClient._post.__wrapped__.__defaults__ = (CLIENT_TIMEOUT, None)
    SabreClient.API_CALL_DELAY = 0
    yield
    SabreClient._post.__wrapped__.__defaults__ = original


@pytest.fixture
def sabre_server(short_timeout):
    holder = {}

    def start(signin_modes, flight_withholds=0):
        ledger = _Ledger(signin_modes, flight_withholds=flight_withholds)
        server = _SabreStandIn(ledger)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        os.environ["SABRE_BASE_URL"] = base
        os.environ["SABRE_CPAID"] = "TEST"
        os.environ["SABRE_USERNAME"] = "placeholder-user"
        os.environ["SABRE_PASSWORD"] = "placeholder-password"
        os.environ["SABRE_PSEUDO_CITY_CODE"] = "TEST"
        os.environ["SABRE_ORGANIZATION"] = "TEST"
        os.environ["SABRE_DOMAIN"] = "TEST"
        holder["server"] = server
        return ledger

    yield start
    server = holder.get("server")
    if server is not None:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def api_client():
    from backend.api import main as main_mod

    class _Redis:
        def ping(self):
            return True

    class _DB:
        def command(self, *args, **kwargs):
            return {"ok": 1}

    main_mod.get_redis = lambda: _Redis()
    main_mod.get_db = lambda: _DB()
    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        yield client


def _assert_signin_not_repeated(ledger):
    assert ledger.held_seconds, "sign-in request never reached the stand-in"
    if ledger.held_seconds[0] != 0.0:
        assert ledger.held_seconds[0] >= CLIENT_TIMEOUT, (
            "stand-in released the first sign-in before the client read timeout"
        )
    assert ledger.undelivered, "first sign-in response was delivered to the client"
    assert ledger.session_creates == 1, (
        "timed-out sign-in was sent again "
        f"({ledger.session_creates} SessionCreate requests, "
        f"{len(ledger.undelivered)} responses never delivered, "
        f"{len(ledger.closed)} tokens closed)"
    )
    assert set(ledger.delivered) <= set(ledger.closed)
    assert set(ledger.undelivered).isdisjoint(ledger.closed)


def test_routes_have_no_auth_dependency():
    from backend.api.main import app

    expected = {
        ("POST", "/flights/schedule/lookup"),
        ("POST", "/flights/ingest"),
        ("GET", "/flights/{flight_number}/passengers/{pnr}/detail"),
    }
    found = set()
    for included in app.routes:
        original = getattr(included, "original_router", None)
        if original is None:
            continue
        include_dependencies = list(included.include_context.dependencies)
        for route in original.routes:
            methods = getattr(route, "methods", None) or set()
            path = getattr(route, "path", None)
            for method in methods:
                if (method, path) in expected:
                    found.add((method, path))
                    assert include_dependencies == []
                    assert list(route.dependencies) == []
                    assert list(route.dependant.dependencies) == []
    assert found == expected


def test_unauthenticated_schedule_lookup_does_not_retry_timed_out_signin(
    api_client, sabre_server
):
    ledger = sabre_server(["withhold", "ok"])
    response = api_client.post(
        "/flights/schedule/lookup",
        json={
            "airline": "GF",
            "flightNumber": "2006",
            "departureDate": "2026-03-19",
        },
    )
    assert response.status_code not in (401, 403)
    _assert_signin_not_repeated(ledger)


def test_unauthenticated_ingest_does_not_retry_two_timed_out_signins(
    api_client, sabre_server
):
    ledger = sabre_server(["withhold", "withhold", "ok"])
    response = api_client.post(
        "/flights/ingest",
        json={
            "airline": "GF",
            "flightNumber": "2006",
            "origin": "BAH",
            "departureDate": "2026-03-19",
            "departureDateTime": "2026-03-19T08:00:00",
        },
    )
    assert response.status_code not in (401, 403)
    _assert_signin_not_repeated(ledger)


def test_unauthenticated_passenger_detail_does_not_retry_dropped_signin(
    api_client, sabre_server, monkeypatch
):
    """The handler reads the stored passenger, then opens a Sabre session.

    The stand-in for that stored document is the only seam. Query values are
    the caller's, and the route has no authentication dependency.
    """

    class _Collection:
        def find_one(self, query, sort=None):
            return {
                "passengers": [
                    {"pnr": "ABC123", "lastName": "ADA", "firstName": "A"}
                ]
            }

    class _DB:
        def __getitem__(self, name):
            return _Collection()

    monkeypatch.setattr(
        "backend.api.routes.passengers.get_db", lambda: _DB()
    )
    ledger = sabre_server(["drop", "ok"])
    response = api_client.get(
        "/flights/2006/passengers/ABC123/detail",
        params={"origin": "BAH", "date": "2026-03-19", "airline": "GF"},
    )
    assert response.status_code not in (401, 403)
    _assert_signin_not_repeated(ledger)


def test_delivered_signin_token_is_closed(api_client, sabre_server):
    ledger = sabre_server(["ok"])
    response = api_client.post(
        "/flights/schedule/lookup",
        json={
            "airline": "GF",
            "flightNumber": "2006",
            "departureDate": "2026-03-19",
        },
    )
    assert response.status_code not in (401, 403)
    assert ledger.session_creates == 1
    assert ledger.undelivered == []
    assert ledger.delivered
    assert set(ledger.delivered) <= set(ledger.closed)


def test_flight_status_read_timeout_is_still_retried(sabre_server):
    ledger = sabre_server(["ok"], flight_withholds=1)
    with SabreClient() as client:
        with pytest.raises((SabreError, requests.Timeout, requests.ConnectionError)):
            client.get_flight_status("GF", "2006", "BAH", "2026-03-19")
    assert ledger.session_creates == 1
    assert ledger.flight_posts == 2
    assert set(ledger.delivered) <= set(ledger.closed)
    assert ledger.undelivered == []
