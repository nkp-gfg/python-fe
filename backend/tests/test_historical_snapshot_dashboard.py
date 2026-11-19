"""Historical snapshot loads for the flight dashboard and passenger tree.

The history tab asks the API for a past snapshot by passing snapshot_sequence.
These tests call that same query on the routes the workbench refetches.

They fail while those routes pass positional arguments into
get_snapshot_data_as_of, which accepts the flight, snapshot type, and sequence
only as keywords. The thread-pool fetch then stores an empty result and both
routes answer 404 Flight not found even though the snapshot is stored.

Keyword callers of the same helper (flight status and the passenger list) are
included so a fix keeps those paths working. Standby, group bookings, and the
reservations route use the same keyword call and are not given their own HTTP
test; the dashboard assertion on the reservation meal covers the reservations
snapshot that the dashboard loads itself.

The in-memory collection implements the find_one/find calls these routes and
get_snapshot_data_as_of make: equality filters, a single $lte comparison, and
a (field, direction) sort. Direction -1 is pymongo DESCENDING (see
pymongo/cursor_shared.py sort()). find_one returns the first sorted match,
which is what pymongo Collection.find_one does by iterating the find cursor.
"""

import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import flights, passengers
from backend.api.snapshot_versioning import get_snapshot_data_as_of

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-04-01"
REQUESTED_SEQUENCE = 5
MATCHING_SEQUENCE = 3

HISTORICAL_GATE = "H12"
OLDER_GATE = "A1"
OTHER_ORIGIN_GATE = "DXB1"
OTHER_DATE_GATE = "DATE1"
LIVE_GATE = "Z9"

HISTORICAL_DEPARTURE_GATE = "G4"
OLDER_DEPARTURE_GATE = "OLD"
LIVE_DEPARTURE_GATE = "Z1"

HISTORICAL_MEAL = "VGML"
OLDER_MEAL = "AVML"
LIVE_MEAL = "KSML"


def _passenger(boarded=True):
    return {
        "pnr": "ABC123",
        "lastName": "SMITH",
        "firstName": "JOHN",
        "cabin": "Y",
        "isRevenue": True,
        "isCheckedIn": True,
        "isBoarded": boarded,
        "passengerType": "F",
    }


def _flight_status_data(gate):
    return {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "status": "BOARDING",
        "gate": gate,
        "legs": [{"city": ORIGIN}, {"city": "LHR"}],
        "aircraft": {"type": "789", "registration": "A9C-HI"},
        "jumpSeat": {"cockpit": 0, "cabin": 0},
    }


def _passenger_list_data(departure_gate, total):
    return {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "departureGate": departure_gate,
        "totalPassengers": total,
        "adultCount": total,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": total,
        "passengers": [_passenger()],
    }


def _reservations_data(meal, gender):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "departureAirport": ORIGIN,
        "departureDate": DATE,
        "reservations": [
            {
                "pnr": "ABC123",
                "passengers": [
                    {
                        "lastName": "SMITH",
                        "firstName": "JOHN",
                        "gender": gender,
                        "specialMeal": meal,
                    }
                ],
            }
        ],
    }


def _snapshot(snapshot_type, sequence, data, origin=ORIGIN, departure_date=DATE):
    return {
        "flightNumber": FLIGHT,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "origin": origin,
        "departureDate": departure_date,
        "capturedAt": f"2026-04-01T0{sequence}:00:00Z",
        "data": data,
    }


def _seed_documents():
    return {
        "snapshots": [
            _snapshot("flight_status", 1, _flight_status_data(OLDER_GATE)),
            _snapshot("flight_status", MATCHING_SEQUENCE, _flight_status_data(HISTORICAL_GATE)),
            _snapshot(
                "flight_status",
                4,
                _flight_status_data(OTHER_ORIGIN_GATE),
                origin="DXB",
            ),
            _snapshot(
                "flight_status",
                MATCHING_SEQUENCE,
                _flight_status_data(OTHER_DATE_GATE),
                departure_date="2026-05-01",
            ),
            _snapshot("passenger_list", 1, _passenger_list_data(OLDER_DEPARTURE_GATE, 9)),
            _snapshot(
                "passenger_list",
                MATCHING_SEQUENCE,
                _passenger_list_data(HISTORICAL_DEPARTURE_GATE, 1),
            ),
            _snapshot("reservations", 1, _reservations_data(OLDER_MEAL, "F")),
            _snapshot(
                "reservations",
                MATCHING_SEQUENCE,
                _reservations_data(HISTORICAL_MEAL, "M"),
            ),
        ],
        "flight_status": [
            {
                **_flight_status_data(LIVE_GATE),
                "fetchedAt": "2026-04-01T12:00:00Z",
            }
        ],
        "passenger_list": [
            {
                **_passenger_list_data(LIVE_DEPARTURE_GATE, 40),
                "fetchedAt": "2026-04-01T12:00:00Z",
            }
        ],
        "reservations": [
            {
                **_reservations_data(LIVE_MEAL, "F"),
                "fetchedAt": "2026-04-01T12:00:00Z",
            }
        ],
        "trip_reports": [],
        "flight_schedules": [],
        "changes": [],
        "flights": [
            {
                "flightNumber": FLIGHT,
                "origin": ORIGIN,
                "departureDate": DATE,
                "flightSequenceNumber": 11,
            }
        ],
    }


def _match(document, query):
    if not query:
        return True
    for field, expected in query.items():
        value = document.get(field, None)
        if isinstance(expected, dict):
            if set(expected) != {"$lte"}:
                raise AssertionError(f"unsupported query operator in {expected}")
            if value is None or value > expected["$lte"]:
                return False
        elif value != expected:
            return False
    return True


def _apply_sort(documents, sort):
    ordered = list(documents)
    if not sort:
        return ordered
    for field, direction in reversed(list(sort)):
        if not isinstance(direction, int):
            raise AssertionError(f"unsupported sort direction {direction!r}")
        ordered.sort(key=lambda doc, field=field: doc.get(field), reverse=direction < 0)
    return ordered


def _project(document, projection):
    if not projection:
        return document
    if any(bool(flag) for flag in projection.values()):
        included = {key: document[key] for key in projection if projection[key] and key in document}
        if projection.get("_id", 1) and "_id" in document:
            included["_id"] = document["_id"]
        return included
    return {key: value for key, value in document.items() if key not in projection}


class MemoryCollection:
    def __init__(self, database, name, documents):
        self.database = database
        self.name = name
        self.documents = documents

    def find_one(self, filter=None, projection=None, sort=None, **kwargs):
        if self.name == "snapshots" and self.database.fail_snapshot_reads:
            raise RuntimeError("snapshot store unavailable")
        if sort is None:
            sort = kwargs.get("sort")
        matches = _apply_sort(
            [doc for doc in self.documents if _match(doc, filter or {})],
            sort,
        )
        if not matches:
            return None
        return _project(copy.deepcopy(matches[0]), projection)

    def find(self, filter=None, projection=None, **kwargs):
        if self.name == "snapshots" and self.database.fail_snapshot_reads:
            raise RuntimeError("snapshot store unavailable")
        return [
            _project(copy.deepcopy(doc), projection)
            for doc in self.documents
            if _match(doc, filter or {})
        ]


class MemoryDatabase:
    def __init__(self, collections, fail_snapshot_reads=False):
        self.fail_snapshot_reads = fail_snapshot_reads
        self.collections = {
            name: MemoryCollection(self, name, docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return self.collections[name]


def _params(snapshot_sequence=None):
    params = {"origin": ORIGIN, "date": DATE}
    if snapshot_sequence is not None:
        params["snapshot_sequence"] = snapshot_sequence
    return params


@pytest.fixture
def database():
    return MemoryDatabase(_seed_documents())


@pytest.fixture
def client(database, monkeypatch):
    monkeypatch.setattr(flights, "get_db", lambda: database)
    monkeypatch.setattr(passengers, "get_db", lambda: database)
    flights._dashboard_cache.clear()
    app = FastAPI()
    app.include_router(flights.router)
    app.include_router(passengers.router)
    return TestClient(app, raise_server_exceptions=False)


def test_keyword_helper_reads_latest_matching_snapshot(database):
    """The helper itself can see the stored snapshot when called as its callers should."""
    loaded = get_snapshot_data_as_of(
        database,
        flight_number=FLIGHT,
        snapshot_type="flight_status",
        snapshot_sequence=REQUESTED_SEQUENCE,
        origin=ORIGIN,
        departure_date=DATE,
    )
    assert loaded["gate"] == HISTORICAL_GATE
    assert loaded["snapshotSequenceNumber"] == MATCHING_SEQUENCE


def test_live_dashboard_without_snapshot_returns_current_board(client):
    response = client.get(f"/flights/{FLIGHT}/dashboard", params=_params())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["flightStatus"]["gate"] == LIVE_GATE
    assert body["departureGate"] == LIVE_DEPARTURE_GATE
    assert body["passengerSummary"]["totalPassengers"] == 40
    assert body["specialRequestsSummary"]["specialMeals"] == {LIVE_MEAL: 1}


def test_dashboard_historical_snapshot_is_not_reported_missing(client):
    response = client.get(
        f"/flights/{FLIGHT}/dashboard",
        params=_params(REQUESTED_SEQUENCE),
    )
    assert response.status_code == 200, response.text
    assert response.json().get("detail") != "Flight not found"
    body = response.json()

    assert body["flightStatus"]["gate"] == HISTORICAL_GATE
    assert body["flightStatus"]["snapshotSequenceNumber"] == MATCHING_SEQUENCE
    assert body["flightStatus"]["gate"] != LIVE_GATE
    assert body["flightStatus"]["gate"] != OLDER_GATE
    assert body["departureGate"] == HISTORICAL_DEPARTURE_GATE
    assert body["passengerSummary"]["totalPassengers"] == 1
    assert body["overview"]["manifestRecords"] == 1
    assert body["analysis"]["totalMale"] == 1
    assert body["specialRequestsSummary"]["specialMeals"] == {HISTORICAL_MEAL: 1}
    assert body["flightSequenceNumber"] == 11


def test_tree_historical_snapshot_is_not_reported_missing(client):
    response = client.get(
        f"/flights/{FLIGHT}/tree",
        params=_params(REQUESTED_SEQUENCE),
    )
    assert response.status_code == 200, response.text
    assert response.json().get("detail") != "Flight not found"
    body = response.json()
    nodes = {node["id"]: node for node in body["nodes"]}
    assert nodes["root"]["value"] == 1
    assert nodes["economy"]["value"] == 1
    assert nodes["root"]["value"] != 40
    assert nodes["root"]["value"] != 9


def _assert_snapshot_failure(response):
    """A snapshot read error must not come back as a missing flight or a board."""
    assert response.status_code >= 400, response.text
    assert "Flight not found" not in response.text
    if not response.headers.get("content-type", "").startswith("application/json"):
        assert response.status_code >= 500
        return
    body = response.json()
    assert body.get("detail") != "Flight not found"
    assert "flightStatus" not in body
    assert "nodes" not in body


def test_snapshot_lookup_failure_is_not_reported_as_missing_flight(database, monkeypatch):
    database.fail_snapshot_reads = True
    monkeypatch.setattr(flights, "get_db", lambda: database)
    flights._dashboard_cache.clear()
    app = FastAPI()
    app.include_router(flights.router)
    client = TestClient(app, raise_server_exceptions=False)

    dashboard = client.get(
        f"/flights/{FLIGHT}/dashboard",
        params=_params(REQUESTED_SEQUENCE),
    )
    _assert_snapshot_failure(dashboard)

    tree = client.get(
        f"/flights/{FLIGHT}/tree",
        params=_params(REQUESTED_SEQUENCE),
    )
    _assert_snapshot_failure(tree)


def test_flight_status_route_returns_historical_snapshot(client):
    response = client.get(
        f"/flights/{FLIGHT}/status",
        params=_params(REQUESTED_SEQUENCE),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == HISTORICAL_GATE
    assert body["snapshotSequenceNumber"] == MATCHING_SEQUENCE


def test_passenger_route_returns_historical_snapshot(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers",
        params=_params(REQUESTED_SEQUENCE),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["departureGate"] == HISTORICAL_DEPARTURE_GATE
    assert body["totalPassengers"] == 1
    assert body["snapshotSequenceNumber"] == MATCHING_SEQUENCE
    assert body["passengers"][0]["nationality"] == ""
