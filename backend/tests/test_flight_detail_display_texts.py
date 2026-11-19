"""Oversale and cancelled-itinerary text must survive flight ingestion.

ACS_FlightDetailRQ allows one Display/Type per call (sabre_docs/
ACS_FlightDetailRQ_v3-0-0/ACS_FlightDetailRQRS_v3-0-0.xsd). Type R is general
remarks, type O is oversale text, and type U is cancelled-itinerary text.
Flight ingestion has to ask for O and U as their own calls, keep each text
with its display type, leave the type R remark out of codeshare data, and
return both warnings from the flight status and dashboard payloads the UI
reads.

These tests fail while that gap is present.

MongoDB must be listening at mongodb://127.0.0.1:27017 and Redis at
redis://127.0.0.1:6379/0. The tests set those URLs themselves.

Run:
    PYTHONPATH=. pytest backend/tests/test_flight_detail_display_texts.py -q
"""

import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

import xmltodict
from fastapi import HTTPException

from backend.api.routes.flights import (
    _dashboard_cache,
    get_flight_dashboard,
    get_flight_status,
    get_flight_status_history,
)
from backend.api.routes.ingestion import SabreIngestRequest, ingest_flight
from backend.feeder.runner import main, run_feeder
from backend.tasks.ingestion import run_batch_ingestion

GENERAL_REMARK = "GENERAL REMARK CREW BRIEF AT GATE"
OVERSALE_TEXT = "OVERSALE SEEK VOLUNTEERS BEFORE BOARDING"
CANCELLED_TEXT = "CANCELLED ITINERARY DO NOT BOARD ORIGINAL SEGMENTS"
CODESHARE_PARTNER = "WY1234"

# Response-level free text returned only for the matching Display/Type.
# The XSD names each enumeration as that text, and a captured type R response
# (sample_data/sample_xmlfiles/ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml)
# does not carry oversale or cancelled-itinerary sentences.
_DISPLAY_TEXT = {
    "R": GENERAL_REMARK,
    "O": OVERSALE_TEXT,
    "U": CANCELLED_TEXT,
    "F": "CHECKIN MESSAGE COUNTERS 12 TO 14",
    "B": "BAGGAGE TEXT TRANSFER TO BELT 3",
    "P": "PRICING TEXT YIELD CAP APPLIED",
}


def _local(key):
    return key.split(":")[-1] if isinstance(key, str) else ""


def _find_named(obj, name):
    """Depth-first search for an element, matching namespace prefixes loosely."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _local(key) == name:
                return value
            found = _find_named(value, name)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_named(item, name)
            if found is not None:
                return found
    return None


def _text_of(value):
    if isinstance(value, dict):
        return str(value.get("#text", "")).strip()
    if value is None:
        return ""
    return str(value).strip()


def _display_types(display):
    """Return every Type value under Display. One element is the schema shape."""
    if display is None:
        return []
    nodes = display if isinstance(display, list) else [display]
    types = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        raw = None
        for key, value in node.items():
            if _local(key) == "Type":
                raw = value
                break
        if isinstance(raw, list):
            types.extend(_text_of(item) for item in raw)
        elif raw is not None:
            types.append(_text_of(raw))
    return [item for item in types if item]


def _flight_detail_response(display_type):
    """ACS_FlightDetailRS.

    Display text is the response-level FreeTextInfoList (XSD sequence on
    ACS_FlightDetailRS). The itinerary FreeTextInfoList stays the operational
    block: a single seat-config line, as in the captured type R sample, plus a
    two-line codeshare entry. It does not change with the requested type.
    """
    display_text = _DISPLAY_TEXT.get(display_type, "")
    display_block = ""
    if display_text:
        display_block = f"""
      <FreeTextInfoList>
        <ns2:FreeTextInfo>
          <ns2:TextLine>
            <ns2:Text>{escape(display_text)}</ns2:Text>
          </ns2:TextLine>
        </ns2:FreeTextInfo>
      </FreeTextInfoList>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2006</Flight>
          <Origin>LHR</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <DepartureTime>09:50PM</DepartureTime>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>09:50PM</ScheduledDepartureTime>
          <DepartureGate>B41</DepartureGate>
          <FlightStatus>OPENCI</FlightStatus>
          <FreeTextInfoList>
            <ns2:FreeTextInfo>
              <ns2:TextLine>
                <ns2:Text>26/256</ns2:Text>
              </ns2:TextLine>
            </ns2:FreeTextInfo>
            <ns2:FreeTextInfo>
              <ns2:TextLine>
                <ns2:Text>OPERATED BY</ns2:Text>
                <ns2:Text>{CODESHARE_PARTNER}</ns2:Text>
              </ns2:TextLine>
            </ns2:FreeTextInfo>
          </FreeTextInfoList>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <TotalPlan>0</TotalPlan>{display_block}
      <Result messageId="test" timeStamp="2026-03-19T09:09:47.021Z">
        <ns2:Status>Success</ns2:Status>
        <ns2:CompletionStatus>Complete</ns2:CompletionStatus>
        <ns2:System>ACS-BSO</ns2:System>
      </Result>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>"""


_SESSION_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body><SessionCreateRS/></soap-env:Body>
</soap-env:Envelope>"""


class _SabreHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        action = self.headers.get("SOAPAction", "")
        parsed = xmltodict.parse(body)
        if action == "ACS_FlightDetailRQ":
            request = _find_named(parsed, "ACS_FlightDetailRQ") or {}
            info = _find_named(request, "FlightInfo") or {}
            types = _display_types(_find_named(request, "Display"))
            self.server.flight_details.append({
                "airline": _text_of(_find_named(info, "Airline")),
                "flight": _text_of(_find_named(info, "Flight")),
                "origin": _text_of(_find_named(info, "Origin")),
                "departureDate": _text_of(_find_named(info, "DepartureDate")),
                "displayTypes": types,
            })
            # One type per call. A body with several types selects none of them.
            selected = types[0] if len(types) == 1 else ""
            self._send(200, _flight_detail_response(selected))
            return
        if action in {"SessionCreateRQ", "SessionCloseRQ"}:
            self._send(200, _SESSION_RESPONSE)
            return
        self._send(500, "<error>unsupported action</error>")

    def _send(self, status, payload):
        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _SabreServer:
    def __init__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SabreHandler)
        self._httpd.flight_details = []
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        port = self._httpd.server_address[1]
        os.environ["SABRE_BASE_URL"] = f"http://127.0.0.1:{port}"
        os.environ["SABRE_CPAID"] = "GF"
        return self

    def __exit__(self, exc_type, exc, tb):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def flight_details(self):
        return self._httpd.flight_details


def _flight(number):
    return {
        "airline": "GF",
        "flightNumber": number,
        "origin": "LHR",
        "departureDate": "2026-03-19",
        "departureDateTime": "2026-03-19T21:50:00",
        "flightSequenceNumber": 424242,
    }


def _texts_by_type(remarks):
    found = {}
    assert isinstance(remarks, list)
    for entry in remarks:
        assert isinstance(entry, dict)
        found.setdefault(entry.get("type"), []).append(entry.get("text"))
    return found


def _assert_requested(server, flight):
    calls = server.flight_details
    assert calls, "ingestion made no ACS_FlightDetailRQ call"
    for call in calls:
        assert call["airline"] == flight["airline"]
        assert call["flight"] == flight["flightNumber"]
        assert call["origin"] == flight["origin"]
        assert call["departureDate"] == flight["departureDate"]
        assert len(call["displayTypes"]) == 1
    requested = {call["displayTypes"][0] for call in calls}
    assert {"R", "O", "U"} <= requested


def _assert_shown(flight):
    _dashboard_cache.clear()
    number = flight["flightNumber"]
    origin = flight["origin"]
    date = flight["departureDate"]
    status = get_flight_status(number, origin=origin, date=date)
    history = get_flight_status_history(number, origin=origin, date=date, limit=20)
    dashboard = get_flight_dashboard(number, origin=origin, date=date)

    for document in (status, history[0], dashboard["flightStatus"]):
        by_type = _texts_by_type(document.get("remarks"))
        assert GENERAL_REMARK in by_type.get("R", [])
        assert OVERSALE_TEXT in by_type.get("O", [])
        assert CANCELLED_TEXT in by_type.get("U", [])
        codeshare = document.get("codeshareInfo") or []
        assert CODESHARE_PARTNER in codeshare
        for warning in (GENERAL_REMARK, OVERSALE_TEXT, CANCELLED_TEXT):
            assert warning not in codeshare

    shown = dashboard.get("freeTextRemarks") or []
    assert OVERSALE_TEXT in shown
    assert CANCELLED_TEXT in shown
    dash_codeshare = dashboard.get("codeshareInfo") or []
    assert CODESHARE_PARTNER in dash_codeshare
    for warning in (GENERAL_REMARK, OVERSALE_TEXT, CANCELLED_TEXT):
        assert warning not in dash_codeshare


def test_run_feeder_stores_oversale_and_cancelled_itinerary_text():
    flight = _flight("9101")
    with _SabreServer() as server:
        result = run_feeder([flight])
        assert result["results"][0]["apis"]["flightStatus"]["status"] == "success"
        _assert_requested(server, flight)
    _assert_shown(flight)


def test_sync_ingest_stores_oversale_and_cancelled_itinerary_text():
    flight = _flight("9102")
    payload = SabreIngestRequest.model_validate(flight)
    with _SabreServer() as server:
        try:
            import asyncio
            asyncio.run(ingest_flight(payload))
        except HTTPException:
            # Later Sabre actions are declined by the stand-in. Flight status
            # is persisted before those calls, which is the path under test.
            pass
        _assert_requested(server, flight)
    _assert_shown(flight)


def test_batch_ingestion_stores_oversale_and_cancelled_itinerary_text():
    flight = _flight("9103")
    with _SabreServer() as server:
        run_batch_ingestion.run(str(uuid.uuid4()), [flight])
        _assert_requested(server, flight)
    _assert_shown(flight)


def test_cli_runner_stores_oversale_and_cancelled_itinerary_text(tmp_path):
    flight = _flight("9104")
    input_path = tmp_path / "flights.json"
    input_path.write_text(json.dumps({"flights": [flight]}), encoding="utf-8")
    argv = sys.argv
    sys.argv = ["backend.feeder.runner", str(input_path)]
    try:
        with _SabreServer() as server:
            main()
            _assert_requested(server, flight)
    finally:
        sys.argv = argv
    _assert_shown(flight)
