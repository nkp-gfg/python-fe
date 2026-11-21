"""Session-close contract tests.

These tests exercise the Sabre session the API actually opens. They fail
while close_session posts a SessionClose body the checked-in schema rejects
and then drops the local token. They pass once that close matches the
schema and a fault, an HTTP error, or a response with no success indicator
is treated as a failed close that keeps the token.

Run:

    python3 -m pytest backend/tests/test_sabre_session_close.py -q

Not invoked here: the Celery wrapper backend.tasks.ingestion.run_batch_ingestion
and the CLI backend.feeder.runner.main. Both leak through run_feeder, which
these tests call with the same multi-flight payload the batch task passes.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET

# Class attribute is read when the client module is imported.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import passengers as passengers_route
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient, SabreError

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CLOSE_XSD = os.path.join(REPO_ROOT, "sabre_docs", "SessionCloseRQ", "SessionCloseRQ.xsd")
CLOSE_WSDL = os.path.join(REPO_ROOT, "sabre_docs", "SessionCloseRQ", "SessionCloseRQ.wsdl")

XS = "{http://www.w3.org/2001/XMLSchema}"
WSDL = "{http://schemas.xmlsoap.org/wsdl/}"
SOAP_WSDL = "{http://schemas.xmlsoap.org/wsdl/soap/}"
SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"

# Placeholders only. The real credentials are whatever the process env names.
OFFICE_CODE = "TESTPCC"
USERNAME = "test-user"
PASSWORD = "test-password"

SUCCESS_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <SessionCloseRS xmlns="http://www.opentravel.org/OTA/2002/11" version="1" status="Approved">
      <Success/>
    </SessionCloseRS>
  </soap-env:Body>
</soap-env:Envelope>"""

def _fault(message):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <soap-env:Fault>
      <faultcode>soap-env:Client</faultcode>
      <faultstring>{message}</faultstring>
    </soap-env:Fault>
  </soap-env:Body>
</soap-env:Envelope>"""


FAULT_RESPONSE = _fault("SessionCloseRQ rejected")
OTHER_FAULT = _fault("request rejected")

UNCONFIRMED_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <SessionCloseRS xmlns="http://www.opentravel.org/OTA/2002/11" version="1"/>
  </soap-env:Body>
</soap-env:Envelope>"""


def _local(tag):
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _ns(tag):
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def _attr(element, name):
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _find(element, local_name):
    found = []
    for node in element.iter():
        if _local(node.tag) == local_name:
            found.append(node)
    return found


def _child(element, local_name, namespace):
    for node in list(element):
        if _local(node.tag) == local_name and _ns(node.tag) == namespace:
            return node
    return None


def load_close_contract():
    """Read the namespace, required office code, and SOAP action from the files."""
    xsd = ET.parse(CLOSE_XSD).getroot()
    target = xsd.attrib["targetNamespace"]
    form = xsd.attrib.get("elementFormDefault", "unqualified")
    child_ns = target if form == "qualified" else ""

    close_element = None
    for element in xsd.iter(XS + "element"):
        if element.attrib.get("name") == "SessionCloseRQ":
            close_element = element
            break
    if close_element is None:
        raise AssertionError("SessionCloseRQ element missing from SessionCloseRQ.xsd")

    pos = None
    for element in close_element.iter(XS + "element"):
        if element.attrib.get("name") == "POS" and element is not close_element:
            pos = element
            break
    if pos is None:
        raise AssertionError("POS element missing from SessionCloseRQ.xsd")
    pos_required = int(pos.attrib.get("minOccurs", "1")) >= 1

    source = None
    for element in pos.iter(XS + "element"):
        if element.attrib.get("name") == "Source":
            source = element
            break
    if source is None:
        raise AssertionError("Source element missing from SessionCloseRQ.xsd")

    office_attr = None
    for attr in source.iter(XS + "attribute"):
        if attr.attrib.get("use") == "required":
            office_attr = attr.attrib.get("name")
            break
    if not office_attr:
        raise AssertionError("required office attribute missing from SessionCloseRQ.xsd")

    wsdl = ET.parse(CLOSE_WSDL).getroot()
    soap_action = None
    for operation in wsdl.iter(WSDL + "operation"):
        if operation.attrib.get("name") != "SessionCloseRQ":
            continue
        soap_operation = operation.find(SOAP_WSDL + "operation")
        if soap_operation is not None and soap_operation.attrib.get("soapAction"):
            soap_action = soap_operation.attrib["soapAction"]
    if not soap_action:
        raise AssertionError("soapAction missing from SessionCloseRQ.wsdl")

    return {
        "namespace": target,
        "child_namespace": child_ns,
        "pos_required": pos_required,
        "office_attribute": office_attr,
        "soap_action": soap_action,
    }


CONTRACT = load_close_contract()


def _create_response(token):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        "<soap-env:Body>"
        f"<wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>"
        "</soap-env:Body></soap-env:Envelope>"
    )


class SessionGateway:
    """Stand-in that allocates a session on create and releases it only for a valid close.

    Validity is the checked-in SessionCloseRQ schema: body element namespace,
    required POS office code, and the SOAP action from SessionCloseRQ.wsdl.
    The office code must be the one captured from that session's create call.
    """

    def __init__(self, mode):
        self.mode = mode
        self.sessions = {}
        self.created = []
        self.close_attempts = []
        self._next = 0
        self._lock = threading.Lock()

    @property
    def open_count(self):
        return len(self.sessions)

    def handle(self, soap_action, xml_text):
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return 500, FAULT_RESPONSE

        if _find(root, "SessionCreateRQ"):
            return self._create(root)
        if _find(root, "SessionCloseRQ") or soap_action in {
            CONTRACT["soap_action"],
            "SessionCloseRQ",
        }:
            return self._close(soap_action, root)
        return 500, OTHER_FAULT

    def _create(self, root):
        office = None
        for node in _find(root, "Source"):
            office = _attr(node, CONTRACT["office_attribute"])
            if office:
                break
        with self._lock:
            self._next += 1
            token = f"TEST-SESSION-TOKEN-{self._next}"
            self.sessions[token] = office
            self.created.append(office)
        return 200, _create_response(token)

    def _close(self, soap_action, root):
        tokens = _find(root, "BinarySecurityToken")
        token = (tokens[0].text or "").strip() if tokens else ""
        problems = self._problems(soap_action, root, token)
        accepted = self.mode == "schema" and not problems
        with self._lock:
            self.close_attempts.append(
                {
                    "soap_action": soap_action,
                    "problems": problems,
                    "accepted": accepted,
                }
            )
            if accepted:
                self.sessions.pop(token, None)

        if accepted:
            return 200, SUCCESS_RESPONSE
        if self.mode == "http_error":
            return 500, FAULT_RESPONSE
        if self.mode == "unconfirmed":
            return 200, UNCONFIRMED_RESPONSE
        return 200, FAULT_RESPONSE

    def _problems(self, soap_action, root, token):
        problems = []
        if soap_action != CONTRACT["soap_action"]:
            problems.append(
                f"soap-action:{soap_action!r}!=contract:{CONTRACT['soap_action']!r}"
            )

        closes = _find(root, "SessionCloseRQ")
        if not closes:
            problems.append("missing-SessionCloseRQ")
            return problems
        close = closes[0]
        if _ns(close.tag) != CONTRACT["namespace"]:
            problems.append(
                f"namespace:{_ns(close.tag)!r}!=contract:{CONTRACT['namespace']!r}"
            )

        child_ns = CONTRACT["child_namespace"]
        pos = _child(close, "POS", child_ns)
        if CONTRACT["pos_required"] and pos is None:
            problems.append("missing-POS")
            pos_for_source = None
        else:
            pos_for_source = pos
        source = _child(pos_for_source, "Source", child_ns) if pos_for_source is not None else None
        if source is None:
            problems.append("missing-Source")
            office = None
        else:
            office = _attr(source, CONTRACT["office_attribute"])
            if not office:
                problems.append(f"missing-{CONTRACT['office_attribute']}")

        expected = self.sessions.get(token)
        if not token or token not in self.sessions:
            problems.append("unknown-session")
        elif office != expected:
            problems.append("office-code-differs-from-session-create")
        return problems


class _GatewayServer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        action = (self.headers.get("SOAPAction") or "").strip()
        if len(action) >= 2 and action[0] == action[-1] == '"':
            action = action[1:-1]
        status, payload = self.server.gateway.handle(action, body)
        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


class RunningGateway:
    def __init__(self, mode):
        self.gateway = SessionGateway(mode)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayServer)
        self.httpd.gateway = self.gateway
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        host, port = self.httpd.server_address
        self.base_url = f"http://{host}:{port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)

    def __getattr__(self, name):
        return getattr(self.gateway, name)


@pytest.fixture
def gateway():
    running = RunningGateway("schema")
    _point_sabre_at(running.base_url)
    yield running
    running.close()


@pytest.fixture
def api(gateway):
    with TestClient(app) as client:
        yield client


def _point_sabre_at(base_url):
    os.environ["SABRE_BASE_URL"] = base_url
    os.environ["SABRE_CPAID"] = "TESTCPA"
    os.environ["SABRE_USERNAME"] = USERNAME
    os.environ["SABRE_PASSWORD"] = PASSWORD
    os.environ["SABRE_PSEUDO_CITY_CODE"] = OFFICE_CODE
    os.environ["SABRE_ORGANIZATION"] = "TESTORG"
    os.environ["SABRE_DOMAIN"] = "TESTDOMAIN"
    SabreClient.API_CALL_DELAY = 0


def _assert_sessions_released(gateway):
    assert gateway.created, "the call never opened a Sabre session"
    assert gateway.close_attempts, "the call never sent SessionClose"
    refused = [attempt for attempt in gateway.close_attempts if not attempt["accepted"]]
    assert not refused, refused
    assert gateway.open_count == 0


def _close_and_keep_token(gateway):
    client = SabreClient()
    client.create_session()
    token = client._token
    assert gateway.open_count == 1
    try:
        client.close_session()
    except SabreError:
        pass
    assert client._token == token
    assert gateway.open_count == 1
    assert not any(attempt["accepted"] for attempt in gateway.close_attempts)


def test_contract_files_require_office_code_and_ota_action():
    """The files the stand-in enforces are the ones checked in for this service."""
    assert CONTRACT["namespace"] == "http://www.opentravel.org/OTA/2002/11"
    assert CONTRACT["pos_required"] is True
    assert CONTRACT["office_attribute"] == "PseudoCityCode"
    assert CONTRACT["soap_action"] == "OTA"


def test_close_request_satisfies_checked_in_contract(gateway):
    with SabreClient():
        pass
    _assert_sessions_released(gateway)


def test_schedule_lookup_releases_session_without_credentials(api, gateway):
    response = api.post(
        "/flights/schedule/lookup",
        json={
            "airline": "GF",
            "flightNumber": "2006",
            "departureDate": "2026-03-19",
        },
    )
    assert response.status_code not in (401, 403)
    _assert_sessions_released(gateway)


def test_repeated_schedule_lookups_do_not_accumulate_sessions(api, gateway):
    for _ in range(3):
        response = api.post(
            "/flights/schedule/lookup",
            json={
                "airline": "GF",
                "flightNumber": "2006",
                "departureDate": "2026-03-19",
            },
        )
        assert response.status_code not in (401, 403)
    assert len(gateway.created) == 3
    _assert_sessions_released(gateway)


def test_single_flight_ingest_releases_session_without_credentials(api, gateway):
    response = api.post(
        "/flights/ingest",
        json={
            "airline": "GF",
            "flightNumber": "2006",
            "origin": "LHR",
            "departureDate": "2026-03-19",
            "departureDateTime": "2026-03-19T08:00:00",
            "flightSequenceNumber": 1,
        },
    )
    assert response.status_code not in (401, 403)
    _assert_sessions_released(gateway)


def test_feeder_run_for_several_flights_releases_its_one_session(gateway):
    """Same call the batch ingestion task makes: one session for the whole list."""
    flights = [
        {
            "airline": "GF",
            "flightNumber": "2006",
            "origin": "LHR",
            "departureDate": "2026-03-19",
            "departureDateTime": "2026-03-19T08:00:00",
            "flightSequenceNumber": 1,
        },
        {
            "airline": "GF",
            "flightNumber": "2007",
            "origin": "BAH",
            "departureDate": "2026-03-19",
            "departureDateTime": "2026-03-19T12:00:00",
            "flightSequenceNumber": 2,
        },
    ]
    run_feeder(flights)
    assert len(gateway.created) == 1
    _assert_sessions_released(gateway)


def test_passenger_detail_releases_session_without_credentials(api, gateway, monkeypatch):
    passenger = {
        "flightNumber": "2006",
        "origin": "LHR",
        "departureDate": "2026-03-19",
        "passengers": [
            {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN"},
        ],
    }

    class _Collection:
        def find_one(self, *args, **kwargs):
            return passenger

    class _Database:
        def __getitem__(self, name):
            return _Collection()

    monkeypatch.setattr(passengers_route, "get_db", lambda: _Database())
    response = api.get(
        "/flights/2006/passengers/ABC123/detail",
        params={"origin": "LHR", "date": "2026-03-19", "airline": "GF"},
    )
    assert response.status_code not in (401, 403)
    _assert_sessions_released(gateway)


def test_http_error_on_close_keeps_token_and_remote_session():
    running = RunningGateway("http_error")
    _point_sabre_at(running.base_url)
    try:
        _close_and_keep_token(running)
    finally:
        running.close()


def test_http_200_fault_is_not_logged_as_a_successful_close():
    running = RunningGateway("fault")
    _point_sabre_at(running.base_url)
    try:
        with pytest.MonkeyPatch.context() as patch:
            events = []
            logger = __import__("backend.sabre.client", fromlist=["logger"]).logger

            def _info(event, *args, **kwargs):
                events.append(event)

            def _warning(event, *args, **kwargs):
                events.append(event)

            patch.setattr(logger, "info", _info)
            patch.setattr(logger, "warning", _warning)
            _close_and_keep_token(running)
            assert "sabre_session_closed" not in events
    finally:
        running.close()


def test_close_response_without_success_indicator_keeps_the_token():
    running = RunningGateway("unconfirmed")
    _point_sabre_at(running.base_url)
    try:
        _close_and_keep_token(running)
    finally:
        running.close()
