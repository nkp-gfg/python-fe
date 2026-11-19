"""Passenger history must follow one traveler on a shared record locator.

These tests fail while history is loaded by record locator alone. A booking
locator is shared by every traveler on it, and the manifest stores those
travelers as separate people (PNR + last name + first name). Opening history
for one of them must not return the others' events or the first manifest
row's cabin, seat, check-in, or boarded flags.

The tests call the same FastAPI routes the History tab and the passenger
detail sheet call. Names are passed as ``last_name`` and ``first_name`` query
parameters, matching the snake_case parameters already used by these routes.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import mongomock
import pytest

from backend.api.routes import changes, passengers

FLIGHT = "072"
ORIGIN = "BAH"
DATE = "2026-04-01"
LOCATOR = "SHARE1"


def _passenger(last, first, seat, cabin, booking_class, checked_in, boarded, bags):
    return {
        "pnr": LOCATOR,
        "lastName": last,
        "firstName": first,
        "cabin": cabin,
        "bookingClass": booking_class,
        "seat": seat,
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "bagCount": bags,
    }


AHMED_ALI = _passenger("ALI", "AHMED", "12A", "J", "C", True, True, 2)
SARA_ALI = _passenger("ALI", "SARA", "14C", "Y", "V", False, False, 0)
AHMED_HASSAN = _passenger("HASSAN", "AHMED", "2F", "F", "F", True, False, 1)


def _change(change_type, detected_at, passenger, field=None, old=None, new=None, metadata=None):
    doc = {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "changeType": change_type,
        "detectedAt": detected_at,
        "passenger": {
            "pnr": passenger["pnr"],
            "lastName": passenger["lastName"],
            "firstName": passenger["firstName"],
        },
    }
    if field is not None:
        doc["field"] = field
    if old is not None:
        doc["oldValue"] = old
    if new is not None:
        doc["newValue"] = new
    if metadata is not None:
        doc["metadata"] = metadata
    return doc


def _seed(db):
    # An older manifest lists SARA first and in a different seat. The latest
    # manifest lists Ahmed Ali first, which is the row a locator-only lookup
    # returns.
    db["snapshots"].insert_many([
        {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "snapshotType": "passenger_list",
            "sequenceNumber": 1,
            "data": {"passengers": [
                {**SARA_ALI, "seat": "99Z", "isBoarded": True},
                AHMED_ALI,
            ]},
        },
        {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "snapshotType": "passenger_list",
            "sequenceNumber": 4,
            "data": {"passengers": [AHMED_ALI, SARA_ALI, AHMED_HASSAN]},
        },
        {
            "flightNumber": FLIGHT,
            "origin": "DOH",
            "departureDate": DATE,
            "snapshotType": "passenger_list",
            "sequenceNumber": 9,
            "data": {"passengers": [{**SARA_ALI, "seat": "1A", "isBoarded": True}]},
        },
    ])
    db["passenger_list"].insert_many([
        {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "fetchedAt": "2026-04-01T07:00:00Z",
            "passengers": [SARA_ALI, AHMED_ALI],
        },
        {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "fetchedAt": "2026-04-01T11:00:00Z",
            "passengers": [AHMED_ALI, SARA_ALI, AHMED_HASSAN],
        },
    ])
    db["changes"].insert_many([
        _change(
            "PASSENGER_ADDED", "2026-04-01T08:00:00Z", AHMED_ALI,
            field="passenger", new="cabin=J class=C",
            metadata={"originalCabin": "J", "originalClass": "C"},
        ),
        _change(
            "PASSENGER_ADDED", "2026-04-01T08:05:00Z", SARA_ALI,
            field="passenger", new="cabin=Y class=V",
            metadata={"originalCabin": "Y", "originalClass": "V"},
        ),
        _change(
            "PASSENGER_ADDED", "2026-04-01T08:06:00Z", AHMED_HASSAN,
            field="passenger", new="cabin=F class=F",
            metadata={"originalCabin": "F", "originalClass": "F"},
        ),
        _change(
            "CHECKED_IN", "2026-04-01T09:00:00Z", AHMED_ALI,
            field="isCheckedIn", old="False", new="True",
        ),
        _change(
            "SEAT_CHANGE", "2026-04-01T09:10:00Z", SARA_ALI,
            field="seat", old="", new="14C",
        ),
        _change(
            "CHECKED_IN", "2026-04-01T09:20:00Z", AHMED_HASSAN,
            field="isCheckedIn", old="False", new="True",
        ),
        _change(
            "SEAT_CHANGE", "2026-04-01T09:30:00Z", AHMED_ALI,
            field="seat", old="10A", new="12A",
        ),
        _change(
            "BOARDED", "2026-04-01T10:00:00Z", AHMED_ALI,
            field="isBoarded", old="False", new="True",
        ),
        # Same person on a different flight must not leak into this departure.
        {
            "flightNumber": "099",
            "origin": ORIGIN,
            "departureDate": DATE,
            "changeType": "BOARDED",
            "detectedAt": "2026-04-01T10:05:00Z",
            "passenger": {"pnr": LOCATOR, "lastName": "ALI", "firstName": "SARA"},
            "field": "isBoarded",
            "oldValue": "False",
            "newValue": "True",
        },
    ])


class _Sabre:
    """Stand-in for SabreClient that records who the route asked for.

    get_passenger_detail builds the GetPassengerDataRQ name from the manifest
    before this method runs. The returned body echoes that name so the real
    converter produces the traveler the route selected.
    """

    calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_passenger_data(self, airline, flight_number, departure_date, origin,
                           last_name, first_name=None, pnr=None):
        self.calls.append({
            "airline": airline,
            "flight_number": flight_number,
            "departure_date": departure_date,
            "origin": origin,
            "last_name": last_name,
            "first_name": first_name,
            "pnr": pnr,
        })
        body = {
            "@Status": "OK",
            "PassengerDataResponseList": {
                "PassengerDataResponse": {
                    "LastName": last_name,
                    "FirstName": first_name or "",
                    "PNRLocator": pnr or "",
                }
            },
        }
        return body, "<xml/>", {}


@pytest.fixture
def api(monkeypatch):
    db = mongomock.MongoClient()["falconeye"]
    _seed(db)
    monkeypatch.setattr(changes, "get_db", lambda: db)
    monkeypatch.setattr(passengers, "get_db", lambda: db)
    _Sabre.calls = []
    monkeypatch.setattr(passengers, "SabreClient", _Sabre)
    app = FastAPI()
    # Same registration order as backend.api.main.
    app.include_router(passengers.router)
    app.include_router(changes.router)
    return TestClient(app)


def _timeline(api, last_name=None, first_name=None, pnr=LOCATOR):
    params = {"origin": ORIGIN, "date": DATE}
    if last_name is not None:
        params["last_name"] = last_name
    if first_name is not None:
        params["first_name"] = first_name
    return api.get(f"/flights/{FLIGHT}/passengers/{pnr}/timeline", params=params)


def _assert_named(event, last, first):
    passenger = event.get("passenger") or {}
    assert passenger.get("lastName") == last
    assert passenger.get("firstName") == first


def test_timeline_for_second_traveler_is_not_the_first_manifest_row(api):
    response = _timeline(api, "ALI", "SARA")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["passenger"]["lastName"] == "ALI"
    assert body["passenger"]["firstName"] == "SARA"
    state = body["currentState"]
    assert state["seat"] == "14C"
    assert state["cabin"] == "Y"
    assert state["bookingClass"] == "V"
    assert state["isCheckedIn"] is False
    assert state["isBoarded"] is False
    assert state["bagCount"] == 0
    assert body["originalBooking"]["cabin"] == "Y"
    assert body["originalBooking"]["bookingClass"] == "V"

    assert body["eventCount"] == 2
    assert [event["changeType"] for event in body["events"]] == [
        "PASSENGER_ADDED", "SEAT_CHANGE",
    ]
    for event in body["events"]:
        _assert_named(event, "ALI", "SARA")
    rendered = " ".join(event.get("description", "") for event in body["events"])
    assert "12A" not in rendered
    assert "Boarded" not in rendered
    assert "checked in" not in rendered.lower()


def test_timeline_for_checked_in_traveler_does_not_use_the_boarded_namesake(api):
    """Ahmed Hassan checked in and did not board. Ahmed Ali, listed first, did."""
    response = _timeline(api, "HASSAN", "AHMED")
    assert response.status_code == 200, response.text
    body = response.json()
    state = body["currentState"]
    assert state["seat"] == "2F"
    assert state["cabin"] == "F"
    assert state["bookingClass"] == "F"
    assert state["isCheckedIn"] is True
    assert state["isBoarded"] is False
    assert state["bagCount"] == 1
    assert body["originalBooking"]["cabin"] == "F"
    types = [event["changeType"] for event in body["events"]]
    assert types == ["PASSENGER_ADDED", "CHECKED_IN"]
    assert "BOARDED" not in types
    for event in body["events"]:
        _assert_named(event, "HASSAN", "AHMED")


def test_timeline_for_the_first_traveler_excludes_the_other_people(api):
    response = _timeline(api, "ALI", "AHMED")
    assert response.status_code == 200, response.text
    body = response.json()
    state = body["currentState"]
    assert state["seat"] == "12A"
    assert state["isBoarded"] is True
    assert state["isCheckedIn"] is True
    assert body["originalBooking"]["cabin"] == "J"
    types = [event["changeType"] for event in body["events"]]
    assert types == ["PASSENGER_ADDED", "CHECKED_IN", "SEAT_CHANGE", "BOARDED"]
    for event in body["events"]:
        _assert_named(event, "ALI", "AHMED")
        assert "14C" not in (event.get("description") or "")
        assert "2F" not in (event.get("description") or "")


def test_timeline_requires_both_names(api):
    missing = [
        _timeline(api),
        _timeline(api, last_name="ALI"),
        _timeline(api, first_name="SARA"),
        _timeline(api, last_name="", first_name="SARA"),
        _timeline(api, last_name="ALI", first_name=""),
    ]
    for response in missing:
        assert response.status_code in (400, 422), response.text
        body = response.json()
        assert "currentState" not in body or body.get("currentState") in (None, {})
        assert not body.get("events")


def test_timeline_unknown_traveler_on_a_shared_locator_is_not_someone_else(api):
    response = _timeline(api, "ALI", "NOBODY")
    assert response.status_code == 404, response.text
    body = response.json()
    assert body.get("currentState") in (None, {})
    assert not body.get("events")


def test_passenger_changes_follow_the_named_traveler(api):
    response = api.get(
        f"/flights/{FLIGHT}/changes/passenger/{LOCATOR}",
        params={"date": DATE, "last_name": "ALI", "first_name": "SARA"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["changeType"] for row in body] == ["SEAT_CHANGE", "PASSENGER_ADDED"]
    for row in body:
        assert row["passenger"]["lastName"] == "ALI"
        assert row["passenger"]["firstName"] == "SARA"

    refused = api.get(f"/flights/{FLIGHT}/changes/passenger/{LOCATOR}", params={"date": DATE})
    assert refused.status_code in (400, 422), refused.text
    assert not isinstance(refused.json(), list)


def test_passenger_detail_asks_sabre_for_the_named_traveler_not_the_first_row(api):
    response = api.get(
        f"/flights/{FLIGHT}/passengers/{LOCATOR}/detail",
        params={
            "origin": ORIGIN,
            "date": DATE,
            "airline": "GF",
            "last_name": "ALI",
            "first_name": "SARA",
        },
    )
    assert response.status_code == 200, response.text
    assert _Sabre.calls, "detail route never called Sabre"
    call = _Sabre.calls[-1]
    assert call["last_name"] == "ALI"
    assert call["first_name"] == "SARA"
    assert call["pnr"] == LOCATOR
    shown = response.json()["passengers"][0]
    assert shown["lastName"] == "ALI"
    assert shown["firstName"] == "SARA"

    refused = api.get(
        f"/flights/{FLIGHT}/passengers/{LOCATOR}/detail",
        params={"origin": ORIGIN, "date": DATE, "airline": "GF"},
    )
    assert refused.status_code in (400, 422), refused.text
    assert not refused.json().get("passengers")

