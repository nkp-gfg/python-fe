"""Passenger-list merge must keep passengers from every successful display.

The booked GetPassengerList display is allowed to succeed with no PassengerInfo
element (the element is optional, and an empty list parses as None). Checked-in,
no-show/offload, and all-edit passengers from the later displays still have to
be stored on the flight, and passengers who are still on one of those displays
must not be recorded as removed.

These tests exercise run_feeder. POST /flights/ingest and the Celery batch task
both call that function and do not merge passenger lists themselves.

Run:

    python3 -m pytest backend/tests/test_passenger_list_merge.py -q
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")

from unittest.mock import patch

import pytest

from backend.feeder import storage
from backend.feeder.converter import convert_passenger_list
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient

SabreClient.API_CALL_DELAY = 0

FLIGHT_DATE = "2026-03-19"
ORIGIN = "DMM"
AIRLINE = "GF"

SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>"""


# ── In-memory Mongo, matching the queries storage.py actually issues ──────


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class MemoryCollection:
    """Stores documents already built by the feeder. It does not reshape them."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", object())
        self.docs.append(stored)
        return _InsertOneResult(stored["_id"])

    def insert_many(self, docs):
        return _InsertManyResult([self.insert_one(doc).inserted_id for doc in docs])

    def _matches(self, filt, sort):
        matched = []
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in (filt or {}).items()):
                matched.append(doc)
        if sort:
            for key, direction in reversed(list(sort)):
                matched.sort(key=lambda item, field=key: item.get(field), reverse=direction < 0)
        return matched

    def find_one(self, filt=None, sort=None, projection=None):
        matched = self._matches(filt, sort)
        if not matched:
            return None
        doc = copy.deepcopy(matched[0])
        if not projection:
            return doc
        return {key: doc[key] for key, include in projection.items() if include and key in doc}

    def find(self, filt=None, sort=None):
        return _Cursor([copy.deepcopy(doc) for doc in self._matches(filt, sort)])

    def update_one(self, filt, update, upsert=False):
        matched = self._matches(filt, None)
        if not matched:
            if not upsert:
                return
            doc = {}
            doc.update(copy.deepcopy(update.get("$setOnInsert", {})))
            doc.update(copy.deepcopy(update.get("$set", {})))
            for key, amount in update.get("$inc", {}).items():
                doc[key] = doc.get(key, 0) + amount
            self.insert_one(doc)
            return
        doc = matched[0]
        for key, value in update.get("$set", {}).items():
            doc[key] = copy.deepcopy(value)
        for key, amount in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + amount


class MemoryDatabase:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]


# ── Sabre response fixtures parsed by the real client ─────────────────────


def _passenger_xml(last, first, pnr, passenger_id, line):
    return f"""<PassengerInfo>
      <Name_Details>
        <LastName>{last}</LastName>
        <FirstName>{first}</FirstName>
      </Name_Details>
      <PNRLocator nameAssociationID="{line}">{pnr}</PNRLocator>
      <PassengerID>{passenger_id}</PassengerID>
      <LineNumber>{line}</LineNumber>
      <BookingClass>Y</BookingClass>
      <Cabin>Y</Cabin>
      <Seat>{line}A</Seat>
      <PassengerType>F</PassengerType>
      <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
      <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
    </PassengerInfo>"""


ALI = _passenger_xml("ALI", "SARA", "ALI001", "IDALI", 1)
NOOR = _passenger_xml("NOOR", "OMAR", "NOOR02", "IDNOOR", 2)
EXTRA = _passenger_xml("LENA", "EXTRA", "EXTRA3", "IDEXTRA", 3)
GONE = _passenger_xml("NIA", "GONE", "GONE04", "IDGONE", 4)


def _envelope(list_xml):
    """SOAP envelope in the shape captured from GetPassengerListRS."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>{AIRLINE}</Airline>
          <Flight>2274</Flight>
          <DepartureDate>{FLIGHT_DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>HYD</Destination>
        </Itinerary>
      </ItineraryInfo>
      {list_xml}
      <Result><Status>Complete</Status></Result>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _list_body(passengers_xml, shape):
    if shape == "omit":
        return ""
    if shape == "empty":
        return "<PassengerInfoList/>"
    if shape == "pair":
        return "<PassengerInfoList></PassengerInfoList>"
    if shape == "whitespace":
        return "<PassengerInfoList>\n      </PassengerInfoList>"
    if shape == "passengers":
        return f"<PassengerInfoList>{passengers_xml}</PassengerInfoList>"
    raise AssertionError(shape)


def _display_response(spec):
    """spec is 'error', an empty-list shape, or a passenger XML string."""
    if spec == "error":
        return 500, "display unavailable"
    if spec in {"omit", "empty", "pair", "whitespace"}:
        return 200, _envelope(_list_body("", spec))
    return 200, _envelope(_list_body(spec, "passengers"))


class _HttpResponse:
    """The two response attributes SabreClient._post reads after requests.post."""

    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _display_label(body):
    if "<v4:DisplayCode>AE</v4:DisplayCode>" in body:
        return "AllEdit"
    if "<v4:DisplayCode>NS</v4:DisplayCode>" in body:
        return "NoShowOFL"
    if "<v4:DisplayCode>BP</v4:DisplayCode>" in body:
        return "CheckedIn"
    if "<v4:DisplayCode>RV</v4:DisplayCode>" in body:
        return "Booked"
    raise AssertionError("passenger-list request did not contain a known display code")


def _install_db():
    database = MemoryDatabase()
    storage.init_db(database)
    return database


def _seed_previous(flight_number, passengers_xml):
    client = SabreClient()
    raw = client._extract_body(
        client._parse_xml(_envelope(_list_body(passengers_xml, "passengers"))),
        "GetPassengerListRS",
    )
    converted = convert_passenger_list(raw, AIRLINE, flight_number, FLIGHT_DATE, ORIGIN)
    storage.store_snapshot(
        request_id="seed-request",
        airline=AIRLINE,
        flight_number=flight_number,
        origin=ORIGIN,
        departure_date=FLIGHT_DATE,
        snapshot_type="passenger_list",
        data=converted,
    )


def _run(flight_number, displays):
    """Run one flight through the real feeder. `displays` maps call label to a response spec."""
    labels = []

    def post(url, data=None, headers=None, timeout=None, **kwargs):
        action = (headers or {}).get("SOAPAction")
        if action == "SessionCreateRQ":
            return _HttpResponse(200, SESSION_XML)
        if action == "SessionCloseRQ":
            return _HttpResponse(200, "<ok/>")
        if action != "GetPassengerListRQ":
            return _HttpResponse(500, "not used by this test")
        body = data if isinstance(data, str) else data.decode()
        label = _display_label(body)
        labels.append(label)
        status, text = _display_response(displays[label])
        return _HttpResponse(status, text)

    flight = {
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": FLIGHT_DATE,
        "departureDateTime": f"{FLIGHT_DATE}T16:15:00",
    }
    with patch("backend.sabre.client.requests.post", side_effect=post):
        result = run_feeder([flight])
    return result, labels


def _passenger_ids(raw):
    info = (raw or {}).get("PassengerInfoList")
    if not isinstance(info, dict):
        return set()
    passengers = info.get("PassengerInfo")
    if passengers is None:
        return set()
    if isinstance(passengers, dict):
        passengers = [passengers]
    return {passenger.get("PassengerID") for passenger in passengers}


def _latest_snapshot(database, flight_number):
    snaps = [
        doc for doc in database["snapshots"].docs
        if doc.get("flightNumber") == flight_number and doc.get("snapshotType") == "passenger_list"
    ]
    assert snaps, "passenger snapshot was not stored"
    return max(snaps, key=lambda doc: doc["sequenceNumber"])


def _assert_manifest(database, result, flight_number, expected_ids, removed_pnrs):
    passenger_api = result["results"][0]["apis"]["passengerList"]
    assert passenger_api["status"] == "success", passenger_api.get("error")

    raw_docs = [
        doc for doc in database["sabre_requests"].docs
        if doc.get("apiType") == "PassengerList"
    ]
    assert len(raw_docs) == 1
    assert _passenger_ids(raw_docs[0]["responseJson"]) == expected_ids

    latest = _latest_snapshot(database, flight_number)
    assert latest["sequenceNumber"] >= 2
    stored_ids = {passenger["passengerId"] for passenger in latest["data"]["passengers"]}
    assert stored_ids == expected_ids
    assert _passenger_ids(latest["data"]["_raw"]) == expected_ids

    legacy = [
        doc for doc in database["passenger_list"].docs
        if doc.get("flightNumber") == flight_number
    ]
    assert legacy, "current passenger_list document was not stored"
    legacy_ids = {passenger["passengerId"] for passenger in legacy[-1]["passengers"]}
    assert legacy_ids == expected_ids

    flight_state = [
        doc for doc in database["flights"].docs
        if doc.get("flightNumber") == flight_number
    ]
    assert flight_state, "flight current state was not updated"
    assert flight_state[-1]["summary"]["totalPax"] == len(expected_ids)

    removed = {
        change["passenger"]["pnr"]
        for change in database["changes"].docs
        if change.get("changeType") == "PASSENGER_REMOVED"
        and change.get("flightNumber") == flight_number
    }
    assert removed == removed_pnrs


# ── Cases ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("empty_shape", ["empty", "pair", "whitespace", "omit"])
def test_passengers_on_later_displays_survive_an_empty_booked_list(empty_shape):
    """Booked display succeeds with no PassengerInfo. Later displays must be kept.

    A previous passenger who is still on the checked-in display must not be
    recorded as removed. A previous passenger who is on none of the displays
    is a real removal.
    """
    flight_number = {
        "empty": "3001",
        "pair": "3002",
        "whitespace": "3003",
        "omit": "3004",
    }[empty_shape]
    database = _install_db()
    _seed_previous(flight_number, ALI + GONE)
    result, labels = _run(flight_number, {
        "Booked": empty_shape,
        "CheckedIn": ALI,
        "NoShowOFL": NOOR,
        "AllEdit": EXTRA,
    })
    assert labels == ["Booked", "CheckedIn", "NoShowOFL", "AllEdit"]
    _assert_manifest(
        database,
        result,
        flight_number,
        {"IDALI", "IDNOOR", "IDEXTRA"},
        {"GONE04"},
    )


def test_checked_in_failure_still_merges_noshow_and_all_edit_onto_empty_booked():
    database = _install_db()
    _seed_previous("3010", NOOR + GONE)
    result, labels = _run("3010", {
        "Booked": "empty",
        "CheckedIn": "error",
        "NoShowOFL": NOOR,
        "AllEdit": EXTRA,
    })
    assert labels == ["Booked", "CheckedIn", "NoShowOFL", "AllEdit"]
    _assert_manifest(
        database,
        result,
        "3010",
        {"IDNOOR", "IDEXTRA"},
        {"GONE04"},
    )


def test_empty_checked_in_becomes_base_when_booked_call_fails():
    database = _install_db()
    _seed_previous("3011", NOOR + GONE)
    result, labels = _run("3011", {
        "Booked": "error",
        "CheckedIn": "omit",
        "NoShowOFL": NOOR,
        "AllEdit": EXTRA,
    })
    assert labels == ["Booked", "CheckedIn", "NoShowOFL", "AllEdit"]
    _assert_manifest(
        database,
        result,
        "3011",
        {"IDNOOR", "IDEXTRA"},
        {"GONE04"},
    )


def test_empty_noshow_becomes_base_when_earlier_displays_fail():
    database = _install_db()
    _seed_previous("3012", EXTRA + GONE)
    result, labels = _run("3012", {
        "Booked": "error",
        "CheckedIn": "error",
        "NoShowOFL": "whitespace",
        "AllEdit": EXTRA,
    })
    assert labels == ["Booked", "CheckedIn", "NoShowOFL", "AllEdit"]
    _assert_manifest(
        database,
        result,
        "3012",
        {"IDEXTRA"},
        {"GONE04"},
    )


def test_same_passenger_on_two_later_displays_is_stored_once():
    database = _install_db()
    _seed_previous("3013", GONE)
    result, labels = _run("3013", {
        "Booked": "omit",
        "CheckedIn": ALI,
        "NoShowOFL": ALI,
        "AllEdit": "empty",
    })
    assert labels == ["Booked", "CheckedIn", "NoShowOFL", "AllEdit"]
    _assert_manifest(
        database,
        result,
        "3013",
        {"IDALI"},
        {"GONE04"},
    )


def test_populated_booked_list_keeps_its_passengers_and_later_displays():
    """A display that already has PassengerInfo must still accept later passengers.

    An empty later display must not wipe passengers already merged, and the
    same passenger on two displays is stored once.
    """
    database = _install_db()
    _seed_previous("3014", ALI + GONE)
    result, labels = _run("3014", {
        "Booked": ALI,
        "CheckedIn": ALI + NOOR,
        "NoShowOFL": "empty",
        "AllEdit": EXTRA,
    })
    assert labels == ["Booked", "CheckedIn", "NoShowOFL", "AllEdit"]
    _assert_manifest(
        database,
        result,
        "3014",
        {"IDALI", "IDNOOR", "IDEXTRA"},
        {"GONE04"},
    )
