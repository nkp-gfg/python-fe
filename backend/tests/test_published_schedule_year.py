"""Published schedule must be the calendar date Sabre was actually asked for.

Live lookup and ingestion both call Sabre Verify Flight Details. The request
attribute is sws:dateTime, which accepts yyyy-mm-ddThh:mm as well as a
yearless mm-ddThh:mm. These tests drive the HTTP handlers and the feeder with
the real client, template, XML parser, converter, and schedule store. Only the
SOAP socket is replaced, and the replacement returns a schema-valid yearless
VerifyFlightDetailsRS (the response annotation's MM-DDTHH:MM form).

They fail while the client drops the year and the handlers still label that
reply with the caller's year.
"""

import copy
import os
import re
import xml.etree.ElementTree as ET

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "https://sabre.invalid/example")
os.environ.setdefault("SABRE_CPAID", "XX")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "XXXX")
os.environ.setdefault("SABRE_ORGANIZATION", "XX")
os.environ.setdefault("SABRE_DOMAIN", "XX")
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:9/unused")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:9/0")

import requests
import xmltodict
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import ingestion, schedule
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient

SabreClient.API_CALL_DELAY = 0

_XS = {"xs": "http://www.w3.org/2001/XMLSchema"}
_FULL_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$"
)
_REPLY_DEPARTURE = "03-25T08:15"
_REPLY_ARRIVAL = "03-25T14:40"
_AIRCRAFT = "32N"
_ORIGIN_TERMINAL = "1"
_DEST_TERMINAL = "4"
_ORIGIN = "BAH"
_DESTINATION = "LHR"


def _sabre_datetime_pattern():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "sabre_docs",
        "VerifyFlightDetailsLLS2.0.1RQ",
        "sws_common.xsd",
    )
    root = ET.parse(os.path.abspath(path)).getroot()
    for simple in root.findall("xs:simpleType", _XS):
        if simple.get("name") == "dateTime":
            pattern = simple.find("xs:restriction/xs:pattern", _XS)
            return pattern.get("value")
    raise AssertionError("sws:dateTime pattern missing from sws_common.xsd")


def _request_attribute_type():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "sabre_docs",
        "VerifyFlightDetailsLLS2.0.1RQ",
        "VerifyFlightDetailsLLS2.0.1RQ.xsd",
    )
    root = ET.parse(os.path.abspath(path)).getroot()
    for attr in root.iter("{http://www.w3.org/2001/XMLSchema}attribute"):
        if attr.get("name") == "DepartureDateTime":
            return attr.get("type")
    raise AssertionError("DepartureDateTime attribute missing from the request schema")


def _departure_datetime_sent(request_xml):
    """Return the FlightSegment DepartureDateTime the client actually posted."""
    parsed = xmltodict.parse(request_xml)
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "@DepartureDateTime" in node:
                found.append(node["@DepartureDateTime"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(parsed)
    assert len(found) == 1, found
    return found[0]


def _http_response(status, text):
    response = requests.Response()
    response.status_code = status
    response._content = text.encode("utf-8")
    response.url = os.environ["SABRE_BASE_URL"]
    response.headers["Content-Type"] = "text/xml"
    return response


def _session_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>"""


def _schedule_xml(flight_number):
    """Yearless VerifyFlightDetailsRS in the shape the response schema describes.

    ArrivalDateTime and DepartureDateTime are documented as MM-DDTHH:MM.
    This body contains no calendar year, so a stored year cannot have come
    from the reply.
    """
    assert "2026" not in _REPLY_DEPARTURE and "2027" not in _REPLY_DEPARTURE
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <v2:VerifyFlightDetailsRS xmlns:v2="http://webservices.sabre.com/sabreXML/2011/10" Version="2.0.1">
      <stl:ApplicationResults xmlns:stl="http://services.sabre.com/STL/v01" status="Complete"/>
      <v2:OriginDestinationOptions>
        <v2:OriginDestinationOption OriginTimeZone="AST" DestinationTimeZone="GMT">
          <v2:FlightSegment DepartureDateTime="{_REPLY_DEPARTURE}" ArrivalDateTime="{_REPLY_ARRIVAL}" AirMilesFlown="3200" ElapsedTime="0625" AccumulatedElapsedTime="0625">
            <v2:DestinationLocation LocationCode="{_DESTINATION}" Terminal="{_DEST_TERMINAL}"/>
            <v2:Equipment AirEquipType="{_AIRCRAFT}"/>
            <v2:MarketingAirline Code="GF" FlightNumber="{flight_number}"/>
            <v2:Meal Code="M"/>
            <v2:OriginLocation LocationCode="{_ORIGIN}" Terminal="{_ORIGIN_TERMINAL}"/>
          </v2:FlightSegment>
        </v2:OriginDestinationOption>
      </v2:OriginDestinationOptions>
    </v2:VerifyFlightDetailsRS>
  </soap-env:Body>
</soap-env:Envelope>"""


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _MemoryCollection:
    """Stand-in for the two MongoDB operations the schedule path uses.

    insert_one snapshots the mapping the way BSON encoding does inside
    pymongo's insert, so a later pop on the caller's dict does not change
    the stored document. find_one applies an equality filter and sort, then
    returns a copy the way a decoded find result does.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, document):
        stored = copy.deepcopy(document)
        stored.setdefault("_id", object())
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query=None, *args, **kwargs):
        matches = []
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in (query or {}).items()):
                matches.append(doc)
        sort = kwargs.get("sort") or []
        for key, direction in reversed(sort):
            matches.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        if not matches:
            return None
        return copy.deepcopy(matches[0])


class _MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _MemoryCollection()
        return self._collections[name]


class _SabreTransport:
    def __init__(self, flight_number):
        self.flight_number = flight_number
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None, **kwargs):
        action = (headers or {}).get("SOAPAction", "")
        body = data.decode("utf-8") if isinstance(data, bytes) else data
        self.calls.append(action)
        if action == "SessionCreateRQ":
            return _http_response(200, _session_xml())
        if action == "VerifyFlightDetailsLLSRQ":
            self.verify_request_xml = body
            return _http_response(200, _schedule_xml(self.flight_number))
        if action == "SessionCloseRQ":
            return _http_response(200, _session_xml())
        return _http_response(500, "unused")


def _client(monkeypatch, transport, db):
    monkeypatch.setattr(schedule, "get_db", lambda: db)
    monkeypatch.setattr("backend.feeder.storage.get_db", lambda: db)
    monkeypatch.setattr("backend.sabre.client.requests.post", transport.post)
    app = FastAPI()
    app.include_router(schedule.router)
    app.include_router(ingestion.router)
    return TestClient(app)


def _assert_schema_allows_year_and_yearless():
    pattern = re.compile(_sabre_datetime_pattern())
    assert _request_attribute_type() == "sws:dateTime"
    for value in ("2027-03-25T00:00", "2027-03-25T14:35", "03-25T00:00", _REPLY_DEPARTURE):
        assert pattern.fullmatch(value), value


def _assert_published(body, departure_date):
    assert body["success"] is True
    assert body["departureDate"] == departure_date
    assert body["aircraftType"] == _AIRCRAFT
    assert body["originTerminal"] == _ORIGIN_TERMINAL
    assert body["destinationTerminal"] == _DEST_TERMINAL
    assert body["origin"] == _ORIGIN
    assert body["destination"] == _DESTINATION
    assert body["scheduledDeparture"] == _REPLY_DEPARTURE
    assert body["scheduledArrival"] == _REPLY_ARRIVAL
    assert departure_date not in body["scheduledDeparture"]


def _assert_asked_for(request_xml, departure_date, time_prefix):
    assert 'LocationCode=""' in request_xml
    sent = _departure_datetime_sent(request_xml)
    match = _FULL_DATETIME.fullmatch(sent)
    assert match, (
        f"VerifyFlightDetails DepartureDateTime was {sent!r}; "
        f"sws:dateTime allows {departure_date}T{time_prefix}"
    )
    assert match.group(1) == departure_date
    assert sent.startswith(f"{departure_date}T{time_prefix}")
    return sent


def test_schema_accepts_the_year_the_client_can_send():
    _assert_schema_allows_year_and_yearless()


def test_lookup_publishes_only_the_year_sent_to_sabre(monkeypatch):
    _assert_schema_allows_year_and_yearless()
    db = _MemoryDB()
    transport = _SabreTransport("4418")
    client = _client(monkeypatch, transport, db)

    response = client.post(
        "/flights/schedule/lookup",
        json={
            "airline": "GF",
            "flightNumber": "4418",
            "departureDate": "2027-03-25",
        },
    )
    assert response.status_code == 200, response.text
    _assert_published(response.json(), "2027-03-25")
    stored = client.get("/flights/4418/schedule", params={"date": "2027-03-25"})
    assert stored.status_code == 200, stored.text
    _assert_published(stored.json(), "2027-03-25")
    assert "VerifyFlightDetailsLLSRQ" in transport.calls
    _assert_asked_for(transport.verify_request_xml, "2027-03-25", "00:00")


def test_lookup_keeps_distinct_calendar_years_distinct(monkeypatch):
    db = _MemoryDB()
    transport = _SabreTransport("4418")
    client = _client(monkeypatch, transport, db)
    sent = {}
    for year in ("2026", "2027"):
        departure_date = f"{year}-03-25"
        response = client.post(
            "/flights/schedule/lookup",
            json={
                "airline": "GF",
                "flightNumber": "4418",
                "departureDate": departure_date,
            },
        )
        assert response.status_code == 200, response.text
        _assert_published(response.json(), departure_date)
        stored = client.get(
            "/flights/4418/schedule", params={"date": departure_date})
        assert stored.status_code == 200, stored.text
        _assert_published(stored.json(), departure_date)
        sent[year] = _assert_asked_for(
            transport.verify_request_xml, departure_date, "00:00")

    assert sent["2026"] != sent["2027"]


def test_ingestion_sends_the_known_local_departure_time(monkeypatch):
    _assert_schema_allows_year_and_yearless()
    db = _MemoryDB()
    transport = _SabreTransport("4419")
    client = _client(monkeypatch, transport, db)

    response = client.post(
        "/flights/ingest",
        json={
            "airline": "GF",
            "flightNumber": "4419",
            "origin": "BAH",
            "departureDate": "2027-03-25",
            "departureDateTime": "2027-03-25T14:35:00",
            "flightSequenceNumber": 4419,
        },
    )
    assert response.status_code != 401
    assert response.status_code != 403
    stored = client.get("/flights/4419/schedule", params={"date": "2027-03-25"})
    assert stored.status_code == 200, stored.text
    _assert_published(stored.json(), "2027-03-25")
    _assert_asked_for(transport.verify_request_xml, "2027-03-25", "14:35")


def test_batch_payload_reaches_sabre_with_the_local_time(monkeypatch):
    """Celery's task calls run_feeder with this same dumped payload."""
    db = _MemoryDB()
    transport = _SabreTransport("4420")
    monkeypatch.setattr("backend.feeder.storage.get_db", lambda: db)
    monkeypatch.setattr("backend.sabre.client.requests.post", transport.post)

    payload = ingestion.SabreBatchRequest(
        flights=[
            ingestion.SabreIngestRequest(
                airline="GF",
                flightNumber="4420",
                origin="BAH",
                departureDate="2027-03-25",
                departureDateTime="2027-03-25T14:35:00",
                flightSequenceNumber=4420,
            )
        ]
    )
    dumped = [flight.model_dump(mode="json") for flight in payload.flights]
    run_feeder(dumped)

    stored = db["flight_schedules"].find_one(
        {"flightNumber": "4420", "departureDate": "2027-03-25"},
        sort=[("fetchedAt", -1)],
    )
    assert stored is not None
    _assert_published(stored, "2027-03-25")
    _assert_asked_for(transport.verify_request_xml, "2027-03-25", "14:35")
