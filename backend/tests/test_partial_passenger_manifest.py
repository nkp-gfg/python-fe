"""Passenger-list ingestion must not replace a stored manifest with a partial Sabre reply.

These tests exercise ``run_feeder`` against scripted GetPassengerList HTTP responses.
They fail while a failed display-code call is skipped and the first HTTP 200 body is
stored and diffed as the live manifest.
"""

import os
from datetime import datetime, timezone

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["SABRE_BASE_URL"] = "https://sabre.invalid"
os.environ["SABRE_CPAID"] = "placeholder"
os.environ["SABRE_USERNAME"] = "placeholder"
os.environ["SABRE_PASSWORD"] = "placeholder"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder"
os.environ["SABRE_ORGANIZATION"] = "placeholder"
os.environ["SABRE_DOMAIN"] = "placeholder"
os.environ["POSTGRES_URI"] = ""

import mongomock
import pytest
from fastapi import HTTPException
from requests import Response

import backend.api.database as api_database
import backend.feeder.storage as storage
from backend.api.routes.audit import get_flight_audit
from backend.api.routes.changes import get_changes
from backend.api.routes.flights import _dashboard_cache, get_flight_dashboard
from backend.api.routes.passengers import get_passengers
from backend.feeder.runner import run_feeder
from backend.sabre import client as sabre_client

ORIGIN = "BAH"
DEPARTURE_DATE = "2026-06-15"
DEPARTURE_DATETIME = "2026-06-15T09:30:00"

FULL_MANIFEST = [
    {"pnr": "AAA111", "last": "HALE", "first": "NOOR", "pid": "P001", "line": 1},
    {"pnr": "BBB222", "last": "IBRAHIM", "first": "SARA", "pid": "P002", "line": 2},
    {"pnr": "CCC333", "last": "QASIM", "first": "OMAR", "pid": "P003", "line": 3},
]
EXTRA_PASSENGER = {
    "pnr": "DDD444",
    "last": "FAROOQ",
    "first": "LINA",
    "pid": "P004",
    "line": 4,
}
FULL_PNRS = [p["pnr"] for p in FULL_MANIFEST]

SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>"""


def _response(status_code, text):
    resp = Response()
    resp.status_code = status_code
    resp._content = text.encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = "https://sabre.invalid"
    return resp


def _passenger_xml(passengers):
    blocks = []
    for pax in passengers:
        blocks.append(
            f"""        <PassengerInfo>
          <Name_Details>
            <LastName>{pax["last"]}</LastName>
            <FirstName>{pax["first"]}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pax["pnr"]}</PNRLocator>
          <PassengerID>{pax["pid"]}</PassengerID>
          <LineNumber>{pax["line"]}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Destination>DXB</Destination>
          <PassengerType>P</PassengerType>
          <BagCount>0</BagCount>
          <Indicators>
            <Indicator>NotBoarded</Indicator>
            <Indicator>Revenue</Indicator>
          </Indicators>
          <CheckIn_Info>
            <CheckInStatus>false</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
        </PassengerInfo>"""
        )
    joined = "\n".join(blocks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header/>
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>4242</Flight>
          <DepartureDate>{DEPARTURE_DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
{joined}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _call_label(body):
    if ">AE</" in body:
        return "AllEdit"
    if ">RV</" in body:
        return "Booked"
    if ">BP</" in body:
        return "CheckedIn"
    if ">NS</" in body:
        return "NoShowOFL"
    raise AssertionError("GetPassengerList request did not carry a known display code")


class _SabreHttp:
    """Stand-in for the Sabre HTTP endpoint. Parsing stays in SabreClient."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.passenger_requests = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        body = data if isinstance(data, str) else (data or b"").decode("utf-8")
        if action == "SessionCreateRQ":
            return _response(200, SESSION_XML)
        if action == "SessionCloseRQ":
            return _response(200, "<ok/>")
        if action != "GetPassengerListRQ":
            return _response(500, f"{action} failed")
        label = _call_label(body)
        self.passenger_requests.append(label)
        outcome = self.outcomes[label]
        if outcome == "error":
            return _response(500, f"{label} GetPassengerList failed")
        return _response(200, _passenger_xml(outcome))


def _flight(flight_number):
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": DEPARTURE_DATETIME,
    }


def _bind_db():
    db = mongomock.MongoClient()["falconeye"]
    storage.init_db(db)
    api_database._db = db
    _dashboard_cache.clear()
    return db


def _wait_for_new_timestamp(previous):
    while datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") == previous:
        # fetchedAt is second-resolution; the next ingest must sort strictly later.
        import time
        time.sleep(0.05)


def _ingest(flight_number, outcomes):
    transport = _SabreHttp(outcomes)
    original = sabre_client.requests.post
    sabre_client.requests.post = transport
    try:
        result = run_feeder([_flight(flight_number)])
    finally:
        sabre_client.requests.post = original
    return result, transport


def _seed_full_manifest(flight_number):
    full = {
        "Booked": FULL_MANIFEST,
        "CheckedIn": FULL_MANIFEST,
        "NoShowOFL": FULL_MANIFEST,
        "AllEdit": FULL_MANIFEST,
    }
    result, transport = _ingest(flight_number, full)
    assert result["results"][0]["apis"]["passengerList"]["status"] == "success"
    assert transport.passenger_requests == [
        "Booked", "CheckedIn", "NoShowOFL", "AllEdit"]
    seeded = get_passengers(
        flight_number, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None)
    assert [p["pnr"] for p in seeded["passengers"]] == FULL_PNRS
    _wait_for_new_timestamp(seeded["fetchedAt"])
    return seeded


def _assert_manifest_kept(db, flight_number, seeded, failed_call):
    live = get_passengers(
        flight_number, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None)
    assert [p["pnr"] for p in live["passengers"]] == FULL_PNRS

    dashboard = get_flight_dashboard(
        flight_number, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None)
    assert dashboard["overview"]["manifestRecords"] == len(FULL_PNRS)
    assert dashboard["passengerSummary"]["totalPassengers"] == len(FULL_PNRS)
    assert dashboard["changeSummary"].get("PASSENGER_REMOVED", 0) == 0

    audit = get_flight_audit(flight_number, origin=ORIGIN, date=DEPARTURE_DATE)
    assert audit["fetchedAt"] == seeded["fetchedAt"]

    with pytest.raises(HTTPException) as removed:
        get_changes(
            flight_number,
            origin=ORIGIN,
            date=DEPARTURE_DATE,
            change_type="PASSENGER_REMOVED",
            limit=100,
        )
    assert removed.value.status_code == 404

    stored = list(db["passenger_list"].find({"flightNumber": flight_number}))
    assert len(stored) == 1
    assert [p["pnr"] for p in stored[0]["passengers"]] == FULL_PNRS

    snapshots = list(db["snapshots"].find(
        {"flightNumber": flight_number, "snapshotType": "passenger_list"}))
    assert len(snapshots) == 1
    assert [p["pnr"] for p in snapshots[0]["data"]["passengers"]] == FULL_PNRS

    flight = db["flights"].find_one({"flightNumber": flight_number})
    assert flight["summary"]["totalPax"] == len(FULL_PNRS)


def _assert_call_recorded(result, call_name):
    passenger_list = result["results"][0]["apis"]["passengerList"]
    assert passenger_list["status"] == "error"
    recorded = [
        call for call in passenger_list.get("calls", [])
        if call.get("call") == call_name
    ]
    assert recorded and recorded[0]["status"] == "error"


def test_booked_failure_and_air_extras_success_keeps_manifest():
    """Booked RV/XRV fails and AE returns one traveler. That subset must not become the flight."""
    db = _bind_db()
    flight_number = "4242"
    seeded = _seed_full_manifest(flight_number)

    result, transport = _ingest(flight_number, {
        "Booked": "error",
        "CheckedIn": "error",
        "NoShowOFL": "error",
        "AllEdit": [FULL_MANIFEST[0]],
    })

    assert transport.passenger_requests == [
        "Booked", "CheckedIn", "NoShowOFL", "AllEdit"]
    _assert_manifest_kept(db, flight_number, seeded, "Booked")
    _assert_call_recorded(result, "Booked")


def test_checked_in_subset_does_not_replace_manifest_when_booked_fails():
    db = _bind_db()
    flight_number = "4243"
    seeded = _seed_full_manifest(flight_number)

    result, _transport = _ingest(flight_number, {
        "Booked": "error",
        "CheckedIn": [FULL_MANIFEST[1]],
        "NoShowOFL": "error",
        "AllEdit": "error",
    })

    _assert_manifest_kept(db, flight_number, seeded, "Booked")
    _assert_call_recorded(result, "Booked")


def test_noshow_subset_does_not_replace_manifest_when_booked_fails():
    db = _bind_db()
    flight_number = "4244"
    seeded = _seed_full_manifest(flight_number)

    result, _transport = _ingest(flight_number, {
        "Booked": "error",
        "CheckedIn": "error",
        "NoShowOFL": [FULL_MANIFEST[2]],
        "AllEdit": "error",
    })

    _assert_manifest_kept(db, flight_number, seeded, "Booked")
    _assert_call_recorded(result, "Booked")


def test_checked_in_and_air_extras_merge_does_not_replace_manifest():
    """Two successful subset calls still must not be stored when the booked call failed."""
    db = _bind_db()
    flight_number = "4245"
    seeded = _seed_full_manifest(flight_number)

    result, _transport = _ingest(flight_number, {
        "Booked": "error",
        "CheckedIn": [FULL_MANIFEST[1]],
        "NoShowOFL": "error",
        "AllEdit": [FULL_MANIFEST[0]],
    })

    _assert_manifest_kept(db, flight_number, seeded, "Booked")
    _assert_call_recorded(result, "Booked")


def test_later_call_failure_does_not_commit_partial_merge():
    """Booked succeeds, a later call fails, and a successful middle call must not be diffed in."""
    db = _bind_db()
    flight_number = "4246"
    seeded = _seed_full_manifest(flight_number)

    result, _transport = _ingest(flight_number, {
        "Booked": FULL_MANIFEST,
        "CheckedIn": [EXTRA_PASSENGER],
        "NoShowOFL": "error",
        "AllEdit": [EXTRA_PASSENGER],
    })

    _assert_manifest_kept(db, flight_number, seeded, "NoShowOFL")
    _assert_call_recorded(result, "NoShowOFL")
    with pytest.raises(HTTPException) as added:
        get_changes(
            flight_number,
            origin=ORIGIN,
            date=DEPARTURE_DATE,
            change_type="PASSENGER_ADDED",
            limit=100,
        )
    assert added.value.status_code == 404
    live = get_passengers(
        flight_number, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None)
    assert EXTRA_PASSENGER["pnr"] not in [p["pnr"] for p in live["passengers"]]


def test_complete_passenger_calls_store_the_merged_manifest():
    """When every display-code call succeeds, the de-duplicated union is the live manifest."""
    _bind_db()
    flight_number = "4247"
    _seed_full_manifest(flight_number)

    result, _transport = _ingest(flight_number, {
        "Booked": FULL_MANIFEST,
        "CheckedIn": [FULL_MANIFEST[0]],
        "NoShowOFL": [],
        "AllEdit": [EXTRA_PASSENGER],
    })

    passenger_list = result["results"][0]["apis"]["passengerList"]
    assert passenger_list["status"] == "success"
    assert all(call["status"] == "success" for call in passenger_list["calls"])

    live = get_passengers(
        flight_number, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None)
    assert [p["pnr"] for p in live["passengers"]] == FULL_PNRS + [EXTRA_PASSENGER["pnr"]]

    dashboard = get_flight_dashboard(
        flight_number, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None)
    assert dashboard["overview"]["manifestRecords"] == len(FULL_PNRS) + 1

    added = get_changes(
        flight_number,
        origin=ORIGIN,
        date=DEPARTURE_DATE,
        change_type="PASSENGER_ADDED",
        limit=100,
    )
    assert [change["passenger"]["pnr"] for change in added] == [EXTRA_PASSENGER["pnr"]]
