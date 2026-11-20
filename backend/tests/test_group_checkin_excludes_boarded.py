"""Group check-in totals must not include passengers who have already boarded.

Sabre leaves CheckInStatus true after BoardStatus becomes true. The flight
board prints a group's boarded count and check-in count side by side, and
each member row already shows Boarded instead of Checked In. The stored
group counter and every API that rebuilds it must count a passenger in
checkedIn only when that passenger is checked in and not boarded.

These tests fail while that double count is still produced.
"""

import copy
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights as flights_routes
from backend.api.routes import passengers as passengers_routes
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

SAMPLE_XML = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2057_BOM_2026-03-19.xml",
)

# One group with one passenger in each state. The middle passenger is the
# overlap the header must not count twice.
MIXED_GROUP_SOAP = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>1001</Flight>
          <DepartureDate>2026-04-01</DepartureDate>
          <Origin>BAH</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details><LastName>WAITING</LastName><FirstName>A</FirstName></Name_Details>
          <PNRLocator>WAIT01</PNRLocator>
          <PassengerID>P-WAIT</PassengerID>
          <LineNumber>1</LineNumber>
          <GroupCode>MX1</GroupCode>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>1A</Seat>
          <CheckIn_Info><CheckInStatus>true</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>ONBOARD</LastName><FirstName>B</FirstName></Name_Details>
          <PNRLocator>WAIT01</PNRLocator>
          <PassengerID>P-BOARD</PassengerID>
          <LineNumber>2</LineNumber>
          <GroupCode>MX1</GroupCode>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>1B</Seat>
          <CheckIn_Info><CheckInStatus>true</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>true</BoardStatus></Boarding_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>BOOKED</LastName><FirstName>C</FirstName></Name_Details>
          <PNRLocator>WAIT01</PNRLocator>
          <PassengerID>P-BOOK</PassengerID>
          <LineNumber>3</LineNumber>
          <GroupCode>MX1</GroupCode>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>1C</Seat>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details><LastName>BOARDEDONLY</LastName><FirstName>D</FirstName></Name_Details>
          <PNRLocator>WAIT01</PNRLocator>
          <PassengerID>P-ANOM</PassengerID>
          <LineNumber>4</LineNumber>
          <GroupCode>MX1</GroupCode>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>1D</Seat>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>true</BoardStatus></Boarding_Info>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):
                return False
            if "$gte" in expected and not (actual is not None and actual >= expected["$gte"]):
                return False
            if set(expected) - {"$lte", "$gte"}:
                return False
        elif actual != expected:
            return False
    return True


class MemoryCollection:
    """Stand-in for a Mongo collection that returns stored documents unchanged."""

    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, projection=None, sort=None):
        matched = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                matched.sort(key=lambda doc: doc.get(field) or 0, reverse=direction < 0)
        if not matched:
            return None
        return copy.deepcopy(matched[0])

    def find(self, query=None, projection=None, sort=None):
        return []


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, MemoryCollection([]))

    def insert(self, name, doc):
        self[name].docs.append(doc)


def parse_passenger_list_body(xml_text):
    """Parse SOAP the same way SabreClient.get_passenger_list does, without the network.

    _extract_body only reads the parsed envelope. Building the client with
    object.__new__ skips __init__, which would read Sabre credentials.
    """
    parsed = SabreClient._parse_xml(xml_text)
    client = object.__new__(SabreClient)
    return client._extract_body(parsed, "GetPassengerListRS")


def load_gf2057_passenger_list():
    with open(SAMPLE_XML, encoding="utf-8") as handle:
        xml_text = handle.read()
    body = parse_passenger_list_body(xml_text)
    return convert_passenger_list(body, "GF", "2057", "2026-03-19", "BOM")


def load_mixed_group():
    body = parse_passenger_list_body(MIXED_GROUP_SOAP)
    return convert_passenger_list(body, "GF", "1001", "2026-04-01", "BAH")


def with_inclusive_stored_counts(manifest):
    """Rewrite stored group counters the way ingestion records them today.

    checkedIn counts every CheckInStatus, including people who already boarded.
    The board must not repeat that stored number.
    """
    stored = copy.deepcopy(manifest)
    for group in stored.get("groupBookings", []):
        members = [
            pax for pax in stored["passengers"]
            if pax.get("groupCode") == group["groupCode"]
        ]
        group["checkedIn"] = sum(1 for pax in members if pax.get("isCheckedIn"))
        group["boarded"] = sum(1 for pax in members if pax.get("isBoarded"))
    return stored


def waiting_checked_in(passengers):
    return sum(1 for pax in passengers if pax.get("isCheckedIn") and not pax.get("isBoarded"))


def boarded_count(passengers):
    return sum(1 for pax in passengers if pax.get("isBoarded"))


def assert_group_header_counts(groups, passengers):
    """checkedIn is people still at check-in; boarded people are only in boarded."""
    by_code = {}
    for pax in passengers:
        code = pax.get("groupCode") or ""
        if code:
            by_code.setdefault(code, []).append(pax)
    assert by_code, "expected at least one group on the manifest"
    overlap_groups = []
    for group in groups:
        members = by_code[group["groupCode"]]
        overlap = [
            pax for pax in members if pax.get("isCheckedIn") and pax.get("isBoarded")
        ]
        if overlap:
            overlap_groups.append(group["groupCode"])
            for pax in overlap:
                assert pax["isCheckedIn"] is True
                assert pax["isBoarded"] is True
        expected_checked_in = waiting_checked_in(members)
        expected_boarded = boarded_count(members)
        assert group["totalMembers"] == len(members)
        assert group["checkedIn"] == expected_checked_in
        assert group["boarded"] == expected_boarded
        assert group["checkedIn"] + group["boarded"] <= group["totalMembers"]
        response_members = group.get("members") or []
        if response_members:
            for pax in overlap:
                row = next(
                    member for member in response_members
                    if member.get("passengerId") == pax.get("passengerId")
                )
                assert row["isBoarded"] is True
                assert row["isCheckedIn"] is True
    assert overlap_groups, "fixture never put the same passenger in both states"


def _client(db):
    app = FastAPI()
    app.include_router(flights_routes.router)
    app.include_router(passengers_routes.router)

    def _db():
        return db

    flights_routes._dashboard_cache.clear()
    return app, _db


@pytest.fixture
def api(monkeypatch):
    db = MemoryDB()
    app, get_db = _client(db)
    monkeypatch.setattr(flights_routes, "get_db", get_db)
    monkeypatch.setattr(passengers_routes, "get_db", get_db)
    return TestClient(app), db


def test_converter_keeps_boarded_passengers_out_of_group_checked_in():
    manifest = load_gf2057_passenger_list()
    al5 = [pax for pax in manifest["passengers"] if pax.get("groupCode") == "AL5"]
    assert al5, "GF2057 sample no longer contains group AL5"
    assert any(pax["isCheckedIn"] and pax["isBoarded"] for pax in al5)
    assert_group_header_counts(manifest["groupBookings"], manifest["passengers"])


def test_mixed_group_counts_only_not_yet_boarded_as_checked_in():
    manifest = load_mixed_group()
    group = next(item for item in manifest["groupBookings"] if item["groupCode"] == "MX1")
    assert group["totalMembers"] == 4
    assert group["checkedIn"] == 1
    assert group["boarded"] == 2
    onboard = next(pax for pax in manifest["passengers"] if pax["passengerId"] == "P-BOARD")
    assert onboard["isCheckedIn"] is True
    assert onboard["isBoarded"] is True


def test_dashboard_header_for_stored_gf2057_group(api):
    client, db = api
    manifest = load_gf2057_passenger_list()
    db.insert("passenger_list", with_inclusive_stored_counts(manifest))
    response = client.get(
        "/flights/2057/dashboard",
        params={"origin": "BOM", "date": "2026-03-19"},
    )
    assert response.status_code == 200
    body = response.json()
    summary = body["groupBookingSummary"]
    assert summary is not None
    assert_group_header_counts(summary["groups"], manifest["passengers"])
    al5 = next(group for group in summary["groups"] if group["groupCode"] == "AL5")
    assert al5["checkedIn"] == waiting_checked_in(
        [pax for pax in manifest["passengers"] if pax.get("groupCode") == "AL5"]
    )


def test_dashboard_header_corrects_stored_count_when_boarded_without_checkin(api):
    """Subtracting the two stored totals is wrong when someone boarded without check-in."""
    client, db = api
    manifest = load_mixed_group()
    stored = with_inclusive_stored_counts(manifest)
    mx1 = next(group for group in stored["groupBookings"] if group["groupCode"] == "MX1")
    assert mx1["checkedIn"] == 2
    assert mx1["boarded"] == 2
    db.insert("passenger_list", stored)
    response = client.get(
        "/flights/1001/dashboard",
        params={"origin": "BAH", "date": "2026-04-01"},
    )
    assert response.status_code == 200
    summary = response.json()["groupBookingSummary"]
    assert_group_header_counts(summary["groups"], manifest["passengers"])
    group = next(item for item in summary["groups"] if item["groupCode"] == "MX1")
    assert group["checkedIn"] == 1
    assert group["boarded"] == 2


def test_dashboard_recomputes_when_stored_group_summary_is_missing(api):
    client, db = api
    manifest = load_gf2057_passenger_list()
    stored = copy.deepcopy(manifest)
    stored.pop("groupBookings", None)
    db.insert("passenger_list", stored)
    response = client.get(
        "/flights/2057/dashboard",
        params={"origin": "BOM", "date": "2026-03-19"},
    )
    assert response.status_code == 200
    summary = response.json()["groupBookingSummary"]
    assert summary is not None
    assert_group_header_counts(summary["groups"], manifest["passengers"])


def test_group_bookings_endpoint_excludes_boarded_passengers(api):
    client, db = api
    manifest = load_gf2057_passenger_list()
    db.insert("passenger_list", manifest)
    response = client.get(
        "/flights/2057/passengers/groups",
        params={"origin": "BOM", "date": "2026-03-19"},
    )
    assert response.status_code == 200
    assert_group_header_counts(response.json()["groups"], manifest["passengers"])


def test_group_bookings_raw_fallback_excludes_boarded_passengers(api):
    """Older passenger documents have no groupCode and recover groups from _raw."""
    client, db = api
    manifest = load_gf2057_passenger_list()
    stored = copy.deepcopy(manifest)
    for pax in stored["passengers"]:
        pax["groupCode"] = ""
        pax["isGroup"] = False
    db.insert("passenger_list", stored)
    response = client.get(
        "/flights/2057/passengers/groups",
        params={"origin": "BOM", "date": "2026-03-19"},
    )
    assert response.status_code == 200
    assert_group_header_counts(response.json()["groups"], manifest["passengers"])


def test_group_bookings_snapshot_excludes_boarded_passengers(api):
    client, db = api
    manifest = load_mixed_group()
    db.insert("snapshots", {
        "flightNumber": "1001",
        "origin": "BAH",
        "departureDate": "2026-04-01",
        "snapshotType": "passenger_list",
        "sequenceNumber": 2,
        "capturedAt": "2026-04-01T00:00:00Z",
        "data": manifest,
    })
    response = client.get(
        "/flights/1001/passengers/groups",
        params={"origin": "BAH", "date": "2026-04-01", "snapshot_sequence": 4},
    )
    assert response.status_code == 200
    groups = response.json()["groups"]
    assert_group_header_counts(groups, manifest["passengers"])
    group = next(item for item in groups if item["groupCode"] == "MX1")
    assert group["checkedIn"] == 1
    assert group["boarded"] == 2
