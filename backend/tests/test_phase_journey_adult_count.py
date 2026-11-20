"""Phase-journey adult counts must not include children.

An unauthenticated client of GET /flights/{flight_number}/phase-journey
receives each phase's summary.adults next to summary.childCount. adults
must be the passengers who are not children (the same number as adultCount).
A child on the manifest, including a boarded child and a child beside an
adult who is traveling with an infant, must not be counted in adults.

These tests fail while the adults filter matches every passenger. They pass
once that count excludes children.
"""

import copy
import os

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import flights
from backend.feeder.converter import convert_flight_status, convert_passenger_list


class _Cursor:
    """Equality-matched documents, in the order Collection.find produced them.

    pymongo Collection.find (no projection) returns every stored field and
    Cursor.sort orders them. This cursor does only that: it does not rewrite
    passenger flags.
    """

    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda doc: doc.get(key), reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self._docs = []

    def insert(self, doc):
        self._docs.append(copy.deepcopy(doc))

    def find(self, query):
        matched = [
            copy.deepcopy(doc)
            for doc in self._docs
            if all(doc.get(key) == value for key, value in query.items())
        ]
        return _Cursor(matched)

    def find_one(self, query, sort=None):
        matched = list(self.find(query))
        if sort:
            for key, direction in reversed(sort):
                matched.sort(key=lambda doc: doc.get(key), reverse=direction < 0)
        return matched[0] if matched else None


class _MemoryDB:
    """Stand-in for the Mongo database handle get_phase_journey reads.

    Supports the calls on that path only: collection lookup, find(filter)
    with equality on the fields the route puts in the query, find().sort,
    and find_one(..., sort=). Documents are returned unchanged.
    """

    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


def _passenger(
    last_name,
    first_name,
    pnr,
    *,
    child=False,
    with_infant=False,
    checked_in=False,
    boarded=False,
):
    """One GetPassengerListRS PassengerInfo element.

    CHD and INF are the edit codes convert_passenger_list turns into
    isChild and hasInfant.
    """
    codes = []
    if child:
        codes.append("CHD")
    if with_infant:
        codes.append("INF")
    info = {
        "Name_Details": {"LastName": last_name, "FirstName": first_name},
        "PNRLocator": pnr,
        "PassengerType": "F",
        "Cabin": "Y",
        "CheckIn_Info": {
            "CheckInStatus": "true" if checked_in or boarded else "false",
        },
        "Boarding_Info": {"BoardStatus": "true" if boarded else "false"},
        "Indicators": {"Indicator": ["Revenue"]},
    }
    if codes:
        info["EditCodeList"] = {
            "EditCode": codes if len(codes) > 1 else codes[0],
        }
    return info


def _passenger_list(passengers, flight_number, origin, departure_date):
    raw = {
        "PassengerInfoList": {"PassengerInfo": passengers},
        "ItineraryInfo": {"Itinerary": {"Destination": "LHR"}},
    }
    return convert_passenger_list(
        raw, "GF", flight_number, departure_date, origin,
    )


def _flight_status(flight_number, origin, departure_date, status):
    raw = {
        "ItineraryResponseList": {
            "ItineraryInfoResponse": {
                "FlightStatus": status,
                "DepartureGate": "A1",
                "ScheduledDepartureDate": departure_date,
            },
        },
    }
    return convert_flight_status(raw, "GF", flight_number, origin, departure_date)


def _snapshot(flight_number, origin, departure_date, snapshot_type, sequence, data):
    return {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "capturedAt": f"2026-03-19T0{sequence}:00:00Z",
        "data": data,
    }


def _assert_adults_are_not_children(phase):
    summary = phase["summary"]
    children = summary["childCount"]
    assert children > 0, phase
    assert summary["totalPassengers"] > children
    # Both numbers are on the phase payload a client adds together.
    assert phase["passengerSummary"]["childCount"] == children
    assert "adults" in summary
    assert summary["adultCount"] == summary["totalPassengers"] - children
    assert summary["adults"] == summary["adultCount"]
    assert summary["adults"] + children == summary["totalPassengers"]


@pytest.fixture
def client(monkeypatch):
    os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
    db = _MemoryDB()
    monkeypatch.setattr(flights, "get_db", lambda: db)
    with TestClient(app) as test_client:
        yield test_client, db


def test_phase_journey_snapshots_do_not_count_children_as_adults(client):
    """Snapshot path: first capture of each phase, requested with no credentials.

    The manifest has an adult, an adult traveling with an infant, and a child.
    The boarding capture has that child boarded. A different flight, origin,
    and date sit in the same collection and must not be counted.
    """
    test_client, db = client
    flight = "2057"
    origin = "BAH"
    day = "2026-03-19"

    check_in = _passenger_list(
        [
            _passenger("SMITH", "JOHN", "AAA111", checked_in=True),
            _passenger(
                "JONES", "MARY", "BBB222", with_infant=True, checked_in=True,
            ),
            _passenger("KID", "SAM", "CCC333", child=True),
        ],
        flight, origin, day,
    )
    boarding = _passenger_list(
        [
            _passenger("SMITH", "JOHN", "AAA111", checked_in=True, boarded=True),
            _passenger(
                "JONES", "MARY", "BBB222", with_infant=True, checked_in=True,
            ),
            _passenger("KID", "SAM", "CCC333", child=True, boarded=True),
        ],
        flight, origin, day,
    )
    # The converter, not the test, decides who is a child.
    for manifest in (check_in, boarding):
        flags = [pax["isChild"] for pax in manifest["passengers"]]
        assert flags == [False, False, True]
        assert [pax["hasInfant"] for pax in manifest["passengers"]] == [
            False, True, False,
        ]

    open_status = _flight_status(flight, origin, day, "OPENCI")
    snaps = db["snapshots"]
    snaps.insert(_snapshot(flight, origin, day, "flight_status", 1, open_status))
    snaps.insert(_snapshot(flight, origin, day, "passenger_list", 1, check_in))
    snaps.insert(_snapshot(flight, origin, day, "flight_status", 2, open_status))
    snaps.insert(_snapshot(flight, origin, day, "passenger_list", 2, boarding))
    # Same airport and date, different flight: four children.
    decoy_pax = _passenger_list(
        [
            _passenger("ONE", "A", "D1", child=True),
            _passenger("TWO", "B", "D2", child=True),
            _passenger("THREE", "C", "D3", child=True),
            _passenger("FOUR", "D", "D4", child=True),
        ],
        "9999", origin, day,
    )
    snaps.insert(_snapshot("9999", origin, day, "passenger_list", 1, decoy_pax))
    # Same flight and date, different origin.
    other_origin = _passenger_list(
        [_passenger("OTHER", "KID", "EEE444", child=True)],
        flight, "LHR", day,
    )
    snaps.insert(_snapshot(flight, "LHR", day, "passenger_list", 1, other_origin))
    # Same flight and origin, different date.
    other_day = _passenger_list(
        [_passenger("LATER", "KID", "FFF555", child=True)],
        flight, origin, "2026-03-20",
    )
    snaps.insert(
        _snapshot(flight, origin, "2026-03-20", "passenger_list", 1, other_day),
    )

    response = test_client.get(
        f"/flights/{flight}/phase-journey",
        params={"origin": origin, "date": day},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["flightNumber"] == flight
    assert body["origin"] == origin
    assert body["date"] == day
    phases = {phase["phase"]: phase for phase in body["phases"]}
    assert set(phases) == {"CHECK_IN", "BOARDING"}
    for phase in phases.values():
        _assert_adults_are_not_children(phase)
        assert phase["summary"]["totalPassengers"] == 3
        assert phase["summary"]["childCount"] == 1
        assert phase["summary"]["infantCount"] == 1
        assert phase["summary"]["adults"] == 2


def test_phase_journey_current_manifest_fallback_does_not_count_children_as_adults(client):
    """No passenger_list snapshots: the route uses the latest current manifest.

    An older manifest for the same flight has no children. The newest one
    has two children. A snapshot for a different flight must not be used.
    """
    test_client, db = client
    flight = "3001"
    origin = "DOH"
    day = "2026-04-01"

    older = _passenger_list(
        [
            _passenger("ADULT", "ONE", "G1", checked_in=True),
            _passenger("ADULT", "TWO", "G2", checked_in=True),
        ],
        flight, origin, day,
    )
    older["fetchedAt"] = "2026-04-01T00:00:00Z"
    newer = _passenger_list(
        [
            _passenger("ADULT", "ONE", "G1", checked_in=True),
            _passenger("CHILD", "ONE", "G3", child=True),
            _passenger("CHILD", "TWO", "G4", child=True, checked_in=True),
        ],
        flight, origin, day,
    )
    newer["fetchedAt"] = "2026-04-01T12:00:00Z"
    assert sum(1 for pax in newer["passengers"] if pax["isChild"]) == 2

    status = _flight_status(flight, origin, day, "OPENCI")
    status["fetchedAt"] = "2026-04-01T12:00:00Z"
    db["passenger_list"].insert(older)
    db["passenger_list"].insert(newer)
    db["flight_status"].insert(status)

    decoy = _passenger_list(
        [_passenger("DECOY", "KID", "H1", child=True, checked_in=True)],
        "8888", origin, day,
    )
    db["snapshots"].insert(
        _snapshot("8888", origin, day, "passenger_list", 1, decoy),
    )
    db["passenger_list"].insert(decoy)

    response = test_client.get(
        f"/flights/{flight}/phase-journey",
        params={"origin": origin, "date": day},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["flightNumber"] == flight
    assert len(body["phases"]) == 1
    phase = body["phases"][0]
    assert phase["phase"] == "CHECK_IN"
    _assert_adults_are_not_children(phase)
    assert phase["summary"]["totalPassengers"] == 3
    assert phase["summary"]["childCount"] == 2
    assert phase["summary"]["adults"] == 1
