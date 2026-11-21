"""Activity feed `categories=flight` must return every stored flight event.

Ingestion stores BOARDING_TIME_CHANGE and JUMPSEAT_CHANGE, and the activity
classifier labels both as flight events. The feed filter's flight entry omits
them, so these tests fail until that map matches the classifier.

The changes collection is mongomock 4.3.0. That stand-in applies the query the
route builds: equality on departureDate, `$in` on changeType, then sort and
limit. No other route filters the activity feed by this category map.
"""

import os
import sys

import mongomock
import pytest

assert mongomock.__version__ == "4.3.0"
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.changes import activity_router  # noqa: E402
from backend.feeder import storage  # noqa: E402
from backend.feeder.converter import convert_flight_status  # noqa: E402
from backend.feeder.differ import detect_changes  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLE_XML = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml",
)
FLIGHT = {
    "airline": "GF",
    "flightNumber": "2006",
    "origin": "LHR",
    "departureDate": "2026-03-19",
}
OTHER_DATE = "2026-03-20"


def _flight_detail_body(xml_text):
    """Parse a FlightDetail SOAP envelope the way SabreClient does."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def _later_flight_xml(xml_text):
    """A second Sabre payload whose flight fields actually changed."""
    updated = xml_text
    replacements = (
        ("<BoardingTime>08:50PM</BoardingTime>",
         "<BoardingTime>09:05PM</BoardingTime>"),
        ("<FlightStatus>OPENCI</FlightStatus>",
         "<FlightStatus>FINAL</FlightStatus>"),
        ("<DepartureGate>B41</DepartureGate>",
         "<DepartureGate>B43</DepartureGate>"),
        ("<DepartureTerminal>2</DepartureTerminal>",
         "<DepartureTerminal>3</DepartureTerminal>"),
        ("<JumpSeat>", '<JumpSeat cockpitInUse="true">'),
    )
    for old, new in replacements:
        assert updated.count(old) == 1
        updated = updated.replace(old, new, 1)
    return updated


def _snapshot(xml_text, snapshot_id):
    body = _flight_detail_body(xml_text)
    data = convert_flight_status(
        body,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["origin"],
        FLIGHT["departureDate"],
    )
    return {"snapshotId": snapshot_id, "data": data}


def _stamp(changes, when):
    for change in changes:
        change["detectedAt"] = when
    return changes


def _passenger(checked_in, boarded):
    return {
        "pnr": "ABC123",
        "lastName": "SMITH",
        "firstName": "JOHN",
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "cabin": "Y",
        "bookingClass": "V",
        "seat": "21A",
        "bagCount": 1,
        "passengerType": "F",
    }


@pytest.fixture
def feed_client():
    """Route the activity feed at a collection filled by the real differ."""
    xml_text = open(SAMPLE_XML, encoding="utf-8").read()
    before = _snapshot(xml_text, "snap-before")
    after = _snapshot(_later_flight_xml(xml_text), "snap-after")

    same_day = detect_changes(before, after, "flight_status", FLIGHT)
    by_type = {change["changeType"]: change for change in same_day}
    assert by_type["BOARDING_TIME_CHANGE"]["newValue"] == "09:05PM"
    assert by_type["BOARDING_TIME_CHANGE"]["field"] == "boarding.time"
    assert "JUMPSEAT_CHANGE" in by_type
    assert "STATUS_CHANGE" in by_type
    assert "GATE_CHANGE" in by_type
    assert "TERMINAL_CHANGE" in by_type

    other_day = detect_changes(
        before,
        after,
        "flight_status",
        {**FLIGHT, "departureDate": OTHER_DATE},
    )
    other_boarding = [
        change for change in other_day
        if change["changeType"] == "BOARDING_TIME_CHANGE"
    ]
    assert len(other_boarding) == 1

    passenger_changes = detect_changes(
        {
            "snapshotId": "pax-before",
            "data": {"passengers": [_passenger(False, False)]},
        },
        {
            "snapshotId": "pax-after",
            "data": {"passengers": [_passenger(True, True)]},
        },
        "passenger_list",
        FLIGHT,
    )
    passenger_types = [change["changeType"] for change in passenger_changes]
    assert passenger_types == ["CHECKED_IN", "BOARDED"]

    _stamp(
        [change for change in same_day
         if change["changeType"] == "BOARDING_TIME_CHANGE"],
        "2099-01-02T00:00:00Z",
    )
    _stamp(
        [change for change in same_day
         if change["changeType"] == "JUMPSEAT_CHANGE"],
        "2099-01-01T00:00:00Z",
    )
    _stamp(other_boarding, "2098-01-01T00:00:00Z")
    _stamp(
        [change for change in same_day
         if change["changeType"] not in (
             "BOARDING_TIME_CHANGE", "JUMPSEAT_CHANGE")],
        "2020-01-01T00:00:00Z",
    )
    _stamp(passenger_changes, "2020-01-01T00:00:01Z")

    mongo = mongomock.MongoClient()
    db = mongo["falconeye"]
    storage.init_db(db)
    stored = storage.store_changes(
        same_day + other_boarding + passenger_changes)
    assert stored == (
        len(same_day) + len(other_boarding) + len(passenger_changes))

    app = FastAPI()
    app.include_router(activity_router)
    app.dependency_overrides.clear()

    from backend.api.routes import changes as changes_module

    original_get_db = changes_module.get_db
    changes_module.get_db = lambda: db
    try:
        yield TestClient(app)
    finally:
        changes_module.get_db = original_get_db
        storage._db = None
        storage._client = None
        storage._owns_connection = False


def _feed(client, **params):
    response = client.get("/activity/feed", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == len(body["events"])
    return body["events"]


def _types(events):
    return {event["eventType"] for event in events}


def test_flight_category_includes_stored_boarding_and_jumpseat_updates(feed_client):
    events = _feed(
        feed_client,
        categories="flight",
        date=FLIGHT["departureDate"],
        limit=50,
    )
    kinds = _types(events)

    assert "STATUS_CHANGE" in kinds
    assert "GATE_CHANGE" in kinds
    assert "TERMINAL_CHANGE" in kinds
    assert "BOARDED" not in kinds
    assert "CHECKED_IN" not in kinds
    assert all(event["departureDate"] == FLIGHT["departureDate"] for event in events)

    boarding = [event for event in events
                if event["eventType"] == "BOARDING_TIME_CHANGE"]
    jumpseat = [event for event in events
                if event["eventType"] == "JUMPSEAT_CHANGE"]
    assert len(boarding) == 1
    assert boarding[0]["category"] == "flight"
    assert boarding[0]["flightNumber"] == FLIGHT["flightNumber"]
    assert "09:05PM" in boarding[0]["description"]
    assert len(jumpseat) == 1
    assert jumpseat[0]["category"] == "flight"


def test_flight_plus_checkin_keeps_boarding_time(feed_client):
    events = _feed(
        feed_client,
        categories="flight,checkin",
        date=FLIGHT["departureDate"],
        limit=50,
    )
    kinds = _types(events)
    assert "CHECKED_IN" in kinds
    assert "BOARDED" not in kinds
    assert "BOARDING_TIME_CHANGE" in kinds
    assert "JUMPSEAT_CHANGE" in kinds


def test_padded_flight_category_keeps_boarding_time(feed_client):
    events = _feed(
        feed_client,
        categories=" flight ",
        date=FLIGHT["departureDate"],
        limit=50,
    )
    assert "BOARDING_TIME_CHANGE" in _types(events)
    assert "JUMPSEAT_CHANGE" in _types(events)


def test_limit_keeps_newest_boarding_and_jumpseat_updates(feed_client):
    events = _feed(
        feed_client,
        categories="flight",
        date=FLIGHT["departureDate"],
        limit=2,
    )
    assert [event["eventType"] for event in events] == [
        "BOARDING_TIME_CHANGE",
        "JUMPSEAT_CHANGE",
    ]
    assert all(event["category"] == "flight" for event in events)


def test_unfiltered_feed_returns_the_stored_boarding_update(feed_client):
    events = _feed(feed_client, date=FLIGHT["departureDate"], limit=50)
    boarding = [event for event in events
                if event["eventType"] == "BOARDING_TIME_CHANGE"]
    assert len(boarding) == 1
    assert boarding[0]["category"] == "flight"
    assert "09:05PM" in boarding[0]["description"]
