"""Check-in rule from Sabre flight detail must survive ingestion and the flight APIs.

Sabre ACS_FlightDetailRS carries CheckinRuleNumber on the itinerary. Ingestion
parses that SOAP body, converts it, and stores the flight-status document.
Callers then read the latest document from the flight-status and dashboard
routes, including the historical snapshot of the same document.

The normalized field on every one of those documents is ``checkinRuleNumber``.
Its value is the itinerary element's text. The field has to remain after the
routes drop ``_raw``.

These tests fail while the converter leaves the element unread.

Not covered: the dashboard route with ``snapshot_sequence`` calls
``get_snapshot_data_as_of`` positionally, and that helper only accepts those
arguments by keyword, so the historical dashboard errors before it can build
a flight-status object. ``diff_flight_status`` also never compares a check-in
rule. The flight list and data-audit responses do not return this document.
"""

import copy
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api.routes import flights  # noqa: E402
from backend.feeder.converter import convert_flight_status  # noqa: E402
from backend.feeder.runner import (  # noqa: E402
    _process_api_call,
    _validate_live_flight_payload,
)
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLE_DIR = ROOT / "sample_data" / "sample_xmlfiles"
COMMITTED_RESPONSES = (
    "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml",
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml",
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml",
    "ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml",
)
_TOKEN_TAG = re.compile(
    r"<wsse:BinarySecurityToken\b[^>]*>.*?</wsse:BinarySecurityToken>",
    re.DOTALL,
)


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction):
        reverse = direction in (-1, "desc", "descending")
        self._docs.sort(key=lambda doc: _sort_value(doc.get(key)), reverse=reverse)
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"id-{len(self.docs)}")
        doc.setdefault("_id", stored["_id"])
        self.docs.append(stored)

        class _Result:
            inserted_id = stored["_id"]

        return _Result()

    def find_one(self, query, projection=None, sort=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        if sort:
            matches = _sorted(matches, sort)
        return matches[0] if matches else None

    def find(self, query=None, projection=None, sort=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query or {})]
        if sort:
            matches = _sorted(matches, sort)
        return _Cursor(matches)

    def update_one(self, query, update, upsert=False):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        inserted = False
        if not matches:
            if not upsert:
                return
            doc = {}
            self.docs.append(doc)
            inserted = True
        else:
            doc = matches[0]
        if inserted:
            for key, value in update.get("$setOnInsert", {}).items():
                doc[key] = copy.deepcopy(value)
        for key, value in update.get("$set", {}).items():
            doc[key] = copy.deepcopy(value)
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection())


def _sort_value(value):
    if value is None:
        return (0, "")
    return (1, value)


def _sorted(docs, sort):
    ordered = list(docs)
    for key, direction in reversed(list(sort)):
        reverse = direction in (-1, "desc", "descending")
        ordered.sort(key=lambda doc: _sort_value(doc.get(key)), reverse=reverse)
    return ordered


def _matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$lte" not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _redact_session_token(xml_text):
    """Replace a captured session token before the response is parsed."""
    redacted, count = _TOKEN_TAG.subn(
        "<wsse:BinarySecurityToken>placeholder</wsse:BinarySecurityToken>",
        xml_text,
        count=1,
    )
    if count != 1:
        raise AssertionError("flight-detail sample is missing its security-token element")
    if "BinarySecurityToken>placeholder<" not in redacted:
        raise AssertionError("security-token element was not replaced")
    return redacted


def _parse_flight_detail(xml_text):
    """Run the SOAP parse SabreClient.get_flight_status uses after the HTTP call."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "ns3:ACS_FlightDetailRS")


def _itinerary(body):
    itin = body["ItineraryResponseList"]["ItineraryInfoResponse"]
    if isinstance(itin, list):
        return itin[0]
    return itin


def _published(doc):
    """Flight-status fields a caller can see after the routes drop ``_raw``."""
    return {key: value for key, value in doc.items() if key not in {"_raw", "_id"}}


def _ingest(db, xml_text):
    body = _parse_flight_detail(xml_text)
    itin = _itinerary(body)
    airline = itin["Airline"]
    flight_number = str(itin["Flight"])
    origin = itin["Origin"]
    departure_date = itin.get("ScheduledDepartureDate") or itin["DepartureDate"]
    flight_info = {
        "airline": airline,
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
    }
    # Same gate the feeder runs before it stores a flight-detail response.
    _validate_live_flight_payload(flight_info, 1, body)
    _process_api_call(
        "FlightStatus",
        "flight_status",
        flight_info,
        body,
        xml_text,
        {
            "requestXml": "<FlightDetailRequest>placeholder</FlightDetailRequest>",
            "httpStatus": 200,
            "durationMs": 1,
            "sessionToken": "placeholder",
            "conversationId": "placeholder",
        },
        convert_flight_status,
        (body, airline, flight_number, origin, departure_date),
        flight_sequence_number=1,
    )
    return flight_info, itin["CheckinRuleNumber"]


def _client_for(db, monkeypatch):
    monkeypatch.setattr("backend.feeder.storage.get_db", lambda: db)
    monkeypatch.setattr("backend.api.routes.flights.get_db", lambda: db)
    flights._dashboard_cache.clear()
    app = FastAPI()
    app.include_router(flights.router)
    return TestClient(app)


@pytest.fixture
def api(monkeypatch):
    db = _Database()
    client = _client_for(db, monkeypatch)
    return db, client


def _load_committed(name):
    return _redact_session_token((SAMPLE_DIR / name).read_text(encoding="utf-8"))


def _synthetic_response(rule):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>1999</Flight>
          <Origin>BAH</Origin>
          <DepartureDate>2026-03-20</DepartureDate>
          <ScheduledDepartureDate>2026-03-20</ScheduledDepartureDate>
          <ScheduledDepartureTime>10:00AM</ScheduledDepartureTime>
          <DepartureGate>01</DepartureGate>
          <FlightStatus>OPENCI</FlightStatus>
          <CheckinRuleNumber>{rule}</CheckinRuleNumber>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <Result>
        <Status>Success</Status>
        <CompletionStatus>Complete</CompletionStatus>
      </Result>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


@pytest.mark.parametrize("sample_name", COMMITTED_RESPONSES)
def test_committed_flight_detail_checkin_rule_is_stored_and_served(api, sample_name):
    db, client = api
    flight, expected = _ingest(db, _load_committed(sample_name))
    _assert_stored_and_served(db, client, flight, expected)


@pytest.mark.parametrize("rule", ("4A", "17"))
def test_checkin_rule_value_comes_from_the_flight_detail_response(api, rule):
    db, client = api
    flight, expected = _ingest(db, _synthetic_response(rule))
    assert expected == rule
    _assert_stored_and_served(db, client, flight, expected)


def _assert_stored_and_served(db, client, flight, expected):
    params = {
        "origin": flight["origin"],
        "date": flight["departureDate"],
    }
    number = flight["flightNumber"]
    # No Authorization header: these routes have no auth dependency.
    status = client.get(f"/flights/{number}/status", params=params)
    history = client.get(f"/flights/{number}/status/history", params=params)
    dashboard = client.get(f"/flights/{number}/dashboard", params=params)
    status_as_of = client.get(
        f"/flights/{number}/status",
        params={**params, "snapshot_sequence": 1},
    )

    assert status.status_code == 200
    assert history.status_code == 200
    assert dashboard.status_code == 200
    assert status_as_of.status_code == 200

    history_docs = history.json()
    assert history_docs
    surfaces = {
        "flight_status": db["flight_status"].docs[-1],
        "snapshot": db["snapshots"].docs[-1]["data"],
        "status": status.json(),
        "status_history": history_docs[0],
        "dashboard": dashboard.json()["flightStatus"],
        "status_as_of": status_as_of.json(),
    }
    missing = [
        name for name, doc in surfaces.items()
        if _published(doc).get("checkinRuleNumber") != expected
        or not _published(doc).get("status")
    ]
    assert not missing, (
        f"check-in rule {expected!r} missing from {missing}"
    )
