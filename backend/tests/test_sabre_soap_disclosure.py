"""Client-visible Sabre SOAP bodies must not leave the API.

These tests fail while fault XML and unparsed session responses are copied
into HTTP error details and ingestion job records. They pass once those
payloads carry a fixed message and none of the upstream body.

The local server stands in for Sabre. requests, SabreClient, the feeder, and
the FastAPI routes run as shipped.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

os.environ["SABRE_API_DELAY_SECONDS"] = "0"

# Distinct from any fixed error string the API is allowed to return.
HEAD_MARKER = "UPSTREAM_SOAP_BODY_MARKER"
TAIL_MARKER = "UPSTREAM_SOAP_BODY_TAIL"
SESSION_HEAD = "SESSION_RESPONSE_MATERIAL"
SESSION_TAIL = "SESSION_RESPONSE_TAIL"

FAULT_ACTIONS = (
    "ACS_FlightDetailRQ",
    "GetPassengerListRQ",
    "Trip_SearchRQ",
    "Trip_ReportsRQ",
    "VerifyFlightDetailsLLSRQ",
    "GetPassengerDataRQ",
)


def _fault_body(action: str) -> str:
    # Head marker sits inside the 500 characters the client copies today.
    # Tail marker sits past that window, so copying either slice still fails.
    return (
        "<soap-env:Fault>"
        f"<faultstring>{HEAD_MARKER} {action}</faultstring>"
        "<detail>passenger-record-placeholder</detail>"
        + ("x" * 600)
        + f"<tail>{TAIL_MARKER} {action}</tail>"
        "</soap-env:Fault>"
    )


def _session_ok() -> str:
    return (
        '<?xml version="1.0"?>'
        "<soap-env:Envelope>"
        "<soap-env:Header>"
        "<wsse:Security>"
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        "</wsse:Security>"
        "</soap-env:Header>"
        "<soap-env:Body><SessionCreateRS/></soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _session_unparsed() -> str:
    # HTTP 200 body that does not match the BinarySecurityToken regex.
    # The token text is still in the first 500 characters.
    return (
        '<?xml version="1.0"?>'
        "<soap-env:Envelope><soap-env:Header><Security>"
        f"<BinarySecurityToken>{SESSION_HEAD}</BinarySecurityToken>"
        "</Security></soap-env:Header>"
        + ("y" * 600)
        + f"<tail>{SESSION_TAIL}</tail>"
        "</soap-env:Envelope>"
    )


class _SabreHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            # Request bodies contain the session template. Do not log them.
            self.rfile.read(length)
        action = self.headers.get("SOAPAction", "")
        server = self.server
        server.actions.append(action)  # type: ignore[attr-defined]
        status, body = server.reply(action)  # type: ignore[attr-defined]
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


class SabreStandIn:
    def __init__(self):
        self.actions: list[str] = []
        self.mode = "operation_fault"

        def reply(action: str) -> tuple[int, str]:
            if action == "SessionCreateRQ":
                if self.mode == "session_http_error":
                    return 500, _fault_body(action)
                if self.mode == "session_unparsed":
                    return 200, _session_unparsed()
                return 200, _session_ok()
            if action == "SessionCloseRQ":
                return 200, "<ok/>"
            if self.mode == "operation_fault":
                return 500, _fault_body(action)
            return 500, _fault_body(action)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SabreHandler)
        self._httpd.reply = reply  # type: ignore[attr-defined]
        self._httpd.actions = self.actions  # type: ignore[attr-defined]
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class _MemoryRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def publish(self, channel, payload):
        return 1


class _PassengerDb:
    def __getitem__(self, name):
        return self

    def find_one(self, query, *args, **kwargs):
        return {
            "flightNumber": "2006",
            "departureDate": "2026-03-19",
            "origin": "LHR",
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN"}
            ],
        }


@pytest.fixture(scope="module")
def sabre():
    stand_in = SabreStandIn()
    yield stand_in
    stand_in.close()


@pytest.fixture(scope="module")
def api(sabre):
    os.environ["SABRE_BASE_URL"] = sabre.base_url
    os.environ["SABRE_CPAID"] = "cpaid"
    os.environ["SABRE_USERNAME"] = "placeholder-user"
    os.environ["SABRE_PASSWORD"] = "placeholder-not-a-secret"
    os.environ["SABRE_PSEUDO_CITY_CODE"] = "PCC"
    os.environ["SABRE_ORGANIZATION"] = "ORG"
    os.environ["SABRE_DOMAIN"] = "DOMAIN"
    os.environ.pop("MONGODB_URI", None)

    from backend.sabre.client import SabreClient

    SabreClient.API_CALL_DELAY = 0.0

    import backend.api.main as main_mod
    import backend.api.redis_client as redis_client

    memory = _MemoryRedis()
    redis_client.get_redis = lambda: memory  # type: ignore[assignment]

    def _unavailable(*_args, **_kwargs):
        raise RuntimeError("dependency unavailable")

    main_mod.get_db = _unavailable  # type: ignore[assignment]
    main_mod.get_redis = _unavailable  # type: ignore[assignment]

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        yield client


def _assert_hidden(text: str, *markers: str) -> None:
    leaked = [marker for marker in markers if marker in text]
    actions = [action for action in ("SessionCreateRQ", *FAULT_ACTIONS) if action in text]
    assert not leaked, (
        "upstream SOAP content reached the client: "
        f"{leaked}; actions named in the payload: {actions}"
    )


def _assert_sabre_was_called(sabre: SabreStandIn, action: str, response) -> None:
    assert action in sabre.actions, f"{action} was not called; actions={sabre.actions}"
    assert response.status_code >= 400


SCHEDULE_PAYLOAD = {
    "airline": "GF",
    "flightNumber": "2006",
    "departureDate": "2026-03-19",
}

INGEST_PAYLOAD = {
    "airline": "GF",
    "flightNumber": "2006",
    "origin": "LHR",
    "departureDate": "2026-03-19",
    "departureDateTime": "2026-03-19T08:00:00",
    "flightSequenceNumber": 42,
}


def test_schedule_lookup_hides_fault_body(sabre, api):
    sabre.mode = "operation_fault"
    sabre.actions.clear()
    response = api.post("/flights/schedule/lookup", json=SCHEDULE_PAYLOAD)
    _assert_sabre_was_called(sabre, "VerifyFlightDetailsLLSRQ", response)
    _assert_hidden(response.text, HEAD_MARKER, TAIL_MARKER, SESSION_HEAD, SESSION_TAIL)


def test_passenger_detail_hides_fault_body(sabre, api, monkeypatch):
    import backend.api.routes.passengers as passengers

    monkeypatch.setattr(passengers, "get_db", lambda: _PassengerDb())
    sabre.mode = "operation_fault"
    sabre.actions.clear()
    response = api.get(
        "/flights/2006/passengers/ABC123/detail",
        params={"origin": "LHR", "date": "2026-03-19", "airline": "GF"},
    )
    _assert_sabre_was_called(sabre, "GetPassengerDataRQ", response)
    _assert_hidden(response.text, HEAD_MARKER, TAIL_MARKER, SESSION_HEAD, SESSION_TAIL)


def test_sync_ingest_hides_fault_body(sabre, api):
    sabre.mode = "operation_fault"
    sabre.actions.clear()
    response = api.post("/flights/ingest", json=INGEST_PAYLOAD)
    _assert_sabre_was_called(sabre, "ACS_FlightDetailRQ", response)
    for action in (
        "GetPassengerListRQ",
        "Trip_SearchRQ",
        "Trip_ReportsRQ",
        "VerifyFlightDetailsLLSRQ",
    ):
        assert action in sabre.actions
    _assert_hidden(response.text, HEAD_MARKER, TAIL_MARKER, SESSION_HEAD, SESSION_TAIL)


def test_ingest_job_status_hides_fault_bodies(sabre, api):
    """Job records are what the Today page and the ingestion panel render."""
    from backend.api.redis_client import create_job, set_ingestion_job_finished
    from backend.feeder.runner import run_feeder

    sabre.mode = "operation_fault"
    sabre.actions.clear()
    result = run_feeder([dict(INGEST_PAYLOAD)])
    for action in FAULT_ACTIONS:
        if action == "GetPassengerDataRQ":
            continue
        assert action in sabre.actions

    create_job("job-disclosure", 1)
    set_ingestion_job_finished("job-disclosure", result["processedFlights"], result["results"])
    response = api.get("/flights/ingest/jobs/job-disclosure")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"failed", "partial"}
    assert body.get("error")

    # Late subscribers receive the stored job, including nested call errors
    # that the JSON model drops. See backend/api/routes/events.py.
    with api.stream("GET", "/events/jobs/job-disclosure") as sse:
        assert sse.status_code == 200
        sse_text = b"".join(sse.iter_bytes()).decode("utf-8", errors="replace")

    _assert_hidden(
        response.text + "\n" + sse_text,
        HEAD_MARKER,
        TAIL_MARKER,
        SESSION_HEAD,
        SESSION_TAIL,
    )

    # Fields the Today page and the ingestion panel copy onto the screen,
    # plus every per-API error the job status model returns.
    flight = body["results"][0]
    shown = [body["error"]]
    for name in (
        "flightStatus",
        "passengerList",
        "reservations",
        "tripReports",
        "schedule",
    ):
        error = (flight["apis"].get(name) or {}).get("error")
        assert error is not None, name
        shown.append(error)
    for text in shown:
        _assert_hidden(text, HEAD_MARKER, TAIL_MARKER)


@pytest.mark.parametrize(
    "mode",
    ["session_http_error", "session_unparsed"],
)
def test_schedule_lookup_hides_session_response(sabre, api, mode):
    sabre.mode = mode
    sabre.actions.clear()
    response = api.post("/flights/schedule/lookup", json=SCHEDULE_PAYLOAD)
    _assert_sabre_was_called(sabre, "SessionCreateRQ", response)
    assert "VerifyFlightDetailsLLSRQ" not in sabre.actions
    _assert_hidden(response.text, HEAD_MARKER, TAIL_MARKER, SESSION_HEAD, SESSION_TAIL)


@pytest.mark.parametrize(
    "mode",
    ["session_http_error", "session_unparsed"],
)
def test_passenger_detail_hides_session_response(sabre, api, mode, monkeypatch):
    import backend.api.routes.passengers as passengers

    monkeypatch.setattr(passengers, "get_db", lambda: _PassengerDb())
    sabre.mode = mode
    sabre.actions.clear()
    response = api.get(
        "/flights/2006/passengers/ABC123/detail",
        params={"origin": "LHR", "date": "2026-03-19", "airline": "GF"},
    )
    _assert_sabre_was_called(sabre, "SessionCreateRQ", response)
    assert "GetPassengerDataRQ" not in sabre.actions
    _assert_hidden(response.text, HEAD_MARKER, TAIL_MARKER, SESSION_HEAD, SESSION_TAIL)


@pytest.mark.parametrize(
    "mode",
    ["session_http_error", "session_unparsed"],
)
def test_sync_ingest_hides_session_response(sabre, api, mode):
    sabre.mode = mode
    sabre.actions.clear()
    response = api.post("/flights/ingest", json=INGEST_PAYLOAD)
    _assert_sabre_was_called(sabre, "SessionCreateRQ", response)
    assert "ACS_FlightDetailRQ" not in sabre.actions
    _assert_hidden(response.text, HEAD_MARKER, TAIL_MARKER, SESSION_HEAD, SESSION_TAIL)
