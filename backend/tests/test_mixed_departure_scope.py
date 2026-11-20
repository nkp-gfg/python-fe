"""Departure identity must be required on flight reads.

The same flight number is stored once per airport and date. Reads that
treat origin and departure date as optional load the newest document of
each collection independently, so one response can show one sector's gate
beside another sector's manifest.

These tests fail while that behavior is present. They pass once every
listed read rejects a request that omits origin or departure date, and
applies both predicates to every collection it loads.

Run:
    PYTHONPATH=. python3 -m pytest backend/tests/test_mixed_departure_scope.py -q
"""

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo import MongoClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import (  # noqa: E402
    audit,
    changes,
    data_audit,
    flights,
    passengers,
    reservations,
    schedule,
)
from backend.sabre.client import SabreError  # noqa: E402

FLIGHT = "2057"
BAH_DATE = "2026-04-02"
LHR_DATE = "2026-04-01"
MARCH_DATE = "2026-03-01"
MONGO_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
DB_NAME = "falconeye_mixed_departure_scope"

# Markers that belong to a different sector than the one a scoped read
# of BAH / 2026-04-02 is allowed to return.
OTHER_SECTOR = ("A12", "Z9", "C3", "KHALID", "VGML", "788")

ROUTES = [
    f"/flights/{FLIGHT}/dashboard",
    f"/flights/{FLIGHT}/tree",
    f"/flights/{FLIGHT}/phase-journey",
    f"/flights/{FLIGHT}/status",
    f"/flights/{FLIGHT}/status/history",
    f"/flights/{FLIGHT}/passengers",
    f"/flights/{FLIGHT}/passengers/summary",
    f"/flights/{FLIGHT}/passengers/standby-list",
    f"/flights/{FLIGHT}/passengers/groups",
    f"/flights/{FLIGHT}/passengers/BAH123",
    f"/flights/{FLIGHT}/reservations",
    f"/flights/{FLIGHT}/reservations/BAH123",
    f"/flights/{FLIGHT}/audit",
    f"/flights/{FLIGHT}/changes",
    f"/flights/{FLIGHT}/changes/summary",
    f"/flights/{FLIGHT}/snapshots",
    f"/flights/{FLIGHT}/changes/passenger/BAH123",
    f"/flights/{FLIGHT}/passengers/BAH123/timeline",
    f"/flights/{FLIGHT}/timeline",
    f"/flights/{FLIGHT}/boarding-progress",
    f"/flights/{FLIGHT}/schedule",
    f"/data-audit/{FLIGHT}/compare",
    f"/data-audit/{FLIGHT}/passengers",
]

INCOMPLETE = [
    {},
    {"origin": "BAH"},
    {"date": BAH_DATE},
]


def _passenger(last, first, pnr, *, boarded):
    return {
        "pnr": pnr,
        "lastName": last,
        "firstName": first,
        "isCheckedIn": True,
        "isBoarded": boarded,
        "isRevenue": True,
        "cabin": "Y",
        "bookingClass": "Y",
        "seat": "12A",
        "passengerType": "ADT",
        "lineNumber": 1,
    }


def _status(origin, date, fetched_at, gate):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": origin,
        "destination": "DOH",
        "departureDate": date,
        "fetchedAt": fetched_at,
        "_ingestTimestamp": fetched_at,
        "status": "OPENCI",
        "gate": gate,
        "boarding": {"indicator": "BDG"},
        "aircraft": {"type": "32N"},
        "jumpSeat": {"cockpit": 0, "cabin": 0},
        "legs": [{"city": "DOH"}],
    }


def _manifest(origin, date, fetched_at, gate, people, total):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": origin,
        "destination": "DOH",
        "departureDate": date,
        "departureGate": gate,
        "fetchedAt": fetched_at,
        "totalPassengers": total,
        "adultCount": total,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": total,
        "passengers": people,
        "cabinSummary": [],
    }


def _reservation(airport, date, fetched_at, *, party, nationality, meal, gender):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "departureAirport": airport,
        "origin": airport,
        "departureDate": date,
        "fetchedAt": fetched_at,
        "totalResults": 1,
        "reservations": [
            {
                "pnr": "BAH123",
                "numberInParty": party,
                "passengers": [
                    {
                        "lastName": "ALI",
                        "firstName": "SARA",
                        "nationality": nationality,
                        "specialMeal": meal,
                        "gender": gender,
                    }
                ],
            }
        ],
    }


def _schedule(origin, date, fetched_at, aircraft, destination):
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": origin,
        "destination": destination,
        "departureDate": date,
        "fetchedAt": fetched_at,
        "success": True,
        "error": None,
        "aircraftType": aircraft,
        "scheduledDeparture": f"{date}T08:00:00",
        "scheduledArrival": f"{date}T12:00:00",
        "elapsedTime": "0400",
        "airMilesFlown": 100,
        "originTerminal": "1",
        "destinationTerminal": "1",
        "originTimeZone": "Asia/Bahrain",
        "destinationTimeZone": "Asia/Qatar",
        "mealCode": "",
        "segments": [],
    }


def _change(origin, date, detected_at, change_type, pnr, last):
    return {
        "flightNumber": FLIGHT,
        "origin": origin,
        "departureDate": date,
        "detectedAt": detected_at,
        "changeType": change_type,
        "passenger": {"pnr": pnr, "lastName": last, "firstName": "SARA"},
        "field": "status",
        "oldValue": "BOOKED",
        "newValue": change_type,
    }


def _snapshot(snapshot_type, origin, date, sequence, data):
    return {
        "flightNumber": FLIGHT,
        "origin": origin,
        "departureDate": date,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "capturedAt": data.get("fetchedAt", ""),
        "fetchedAt": data.get("fetchedAt", ""),
        "data": data,
    }


def _seed(db):
    db["flight_status"].insert_many([
        _status("LHR", LHR_DATE, "2026-04-02T09:00:00Z", "A12"),
        _status("BAH", MARCH_DATE, "2026-04-02T08:00:00Z", "Z9"),
        _status("BAH", BAH_DATE, "2026-04-02T02:00:00Z", "B7"),
    ])
    db["passenger_list"].insert_many([
        _manifest(
            "LHR", LHR_DATE, "2026-04-02T01:00:00Z", "A12",
            [_passenger("SMITH", "JOHN", "LHR123", boarded=False)], 1,
        ),
        _manifest(
            "BAH", MARCH_DATE, "2026-03-01T09:00:00Z", "Z9",
            [_passenger("NOOR", "AMIN", "MAR123", boarded=False)], 1,
        ),
        _manifest(
            "BAH", BAH_DATE, "2026-04-02T03:00:00Z", "B7",
            [_passenger("ALI", "SARA", "BAH123", boarded=True)], 1,
        ),
        _manifest(
            "MCT", BAH_DATE, "2026-04-02T06:00:00Z", "C3",
            [_passenger("KHALID", "OMAR", "BAH123", boarded=False)], 4,
        ),
    ])
    db["reservations"].insert_many([
        _reservation(
            "BAH", MARCH_DATE, "2026-03-01T09:00:00Z",
            party=1, nationality="BH", meal="AVML", gender="F",
        ),
        _reservation(
            "BAH", BAH_DATE, "2026-04-02T04:00:00Z",
            party=1, nationality="BH", meal="AVML", gender="F",
        ),
        _reservation(
            "MCT", BAH_DATE, "2026-04-02T07:00:00Z",
            party=9, nationality="QA", meal="VGML", gender="M",
        ),
    ])
    db["flight_schedules"].insert_many([
        _schedule("BAH", BAH_DATE, "2026-04-02T02:00:00Z", "32N", "DOH"),
        _schedule("MCT", BAH_DATE, "2026-04-02T07:30:00Z", "788", "DXB"),
    ])
    db["changes"].insert_many([
        _change("LHR", LHR_DATE, "2026-04-02T01:00:00Z", "CHECKED_IN", "LHR123", "SMITH"),
        _change("BAH", MARCH_DATE, "2026-03-01T09:00:00Z", "STATUS_CHANGE", "MAR123", "NOOR"),
        _change("BAH", BAH_DATE, "2026-04-02T03:30:00Z", "BOARDED", "BAH123", "ALI"),
        _change("MCT", BAH_DATE, "2026-04-02T06:30:00Z", "CHECKED_IN", "BAH123", "KHALID"),
    ])
    bah_pax = _manifest(
        "BAH", BAH_DATE, "2026-04-02T03:00:00Z", "B7",
        [_passenger("ALI", "SARA", "BAH123", boarded=True)], 1,
    )
    mct_pax = _manifest(
        "MCT", BAH_DATE, "2026-04-02T06:00:00Z", "C3",
        [_passenger("KHALID", "OMAR", "BAH123", boarded=False)], 4,
    )
    lhr_status = _status("LHR", LHR_DATE, "2026-04-02T09:00:00Z", "A12")
    bah_status = _status("BAH", BAH_DATE, "2026-04-02T02:00:00Z", "B7")
    db["snapshots"].insert_many([
        _snapshot("flight_status", "LHR", LHR_DATE, 5, lhr_status),
        _snapshot("flight_status", "BAH", BAH_DATE, 2, bah_status),
        _snapshot("passenger_list", "MCT", BAH_DATE, 4, mct_pax),
        _snapshot("passenger_list", "BAH", BAH_DATE, 3, bah_pax),
        _snapshot(
            "reservations", "MCT", BAH_DATE, 4,
            _reservation(
                "MCT", BAH_DATE, "2026-04-02T07:00:00Z",
                party=9, nationality="QA", meal="VGML", gender="M",
            ),
        ),
        _snapshot(
            "reservations", "BAH", BAH_DATE, 1,
            _reservation(
                "BAH", BAH_DATE, "2026-04-02T04:00:00Z",
                party=1, nationality="BH", meal="AVML", gender="F",
            ),
        ),
    ])


def _app():
    app = FastAPI()
    app.include_router(flights.router)
    app.include_router(passengers.router)
    app.include_router(reservations.router)
    app.include_router(changes.router)
    app.include_router(schedule.router)
    app.include_router(audit.router)
    app.include_router(data_audit.router)
    return app


@pytest.fixture
def db():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    database = client[DB_NAME]
    client.drop_database(DB_NAME)
    _seed(database)
    flights._dashboard_cache.clear()
    yield database
    flights._dashboard_cache.clear()
    client.drop_database(DB_NAME)
    client.close()


@pytest.fixture
def client(db, monkeypatch):
    for module in (
        flights, passengers, reservations, changes, schedule, audit, data_audit,
    ):
        monkeypatch.setattr(module, "get_db", lambda: db)
    with TestClient(_app()) as test_client:
        yield test_client


def _assert_rejected(response):
    assert response.status_code in (400, 422), response.text
    for marker in OTHER_SECTOR:
        assert marker not in response.text


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("params", INCOMPLETE)
def test_flight_read_rejects_missing_departure_identity(client, path, params):
    response = client.get(path, params=params)
    _assert_rejected(response)


def test_dashboard_snapshot_without_departure_is_rejected(client):
    response = client.get(
        f"/flights/{FLIGHT}/dashboard",
        params={"snapshot_sequence": 5},
    )
    _assert_rejected(response)


def test_snapshot_compare_without_departure_is_rejected(client):
    response = client.get(
        f"/flights/{FLIGHT}/snapshots/compare",
        params={"snapshot_sequence": 5},
    )
    _assert_rejected(response)


def test_restore_without_departure_does_not_write(client, db):
    before = {
        name: db[name].count_documents({"flightNumber": FLIGHT})
        for name in ("flight_status", "passenger_list", "reservations")
    }
    response = client.post(f"/flights/{FLIGHT}/snapshots/5/restore")
    after = {
        name: db[name].count_documents({"flightNumber": FLIGHT})
        for name in ("flight_status", "passenger_list", "reservations")
    }
    assert after == before
    _assert_rejected(response)


def test_scoped_dashboard_stays_on_one_departure(client):
    response = client.get(
        f"/flights/{FLIGHT}/dashboard",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["flightStatus"]["origin"] == "BAH"
    assert body["flightStatus"]["gate"] == "B7"
    assert body["departureGate"] == "B7"
    assert body["route"]["origin"] == "BAH"
    assert body["route"]["departureDate"] == BAH_DATE
    assert body["specialRequestsSummary"]["specialMeals"] == {"AVML": 1}
    assert body["schedule"]["origin"] == "BAH"
    assert body["schedule"]["aircraftType"] == "32N"
    assert "KHALID" not in response.text
    assert "A12" not in response.text
    assert "VGML" not in response.text


def test_scoped_dashboard_snapshot_stays_on_one_departure(client):
    response = client.get(
        f"/flights/{FLIGHT}/dashboard",
        params={"origin": "BAH", "date": BAH_DATE, "snapshot_sequence": 5},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["flightStatus"]["gate"] == "B7"
    assert body["flightStatus"]["origin"] == "BAH"
    assert body["departureGate"] == "B7"
    assert "KHALID" not in response.text
    assert "A12" not in response.text


def test_scoped_passengers_use_same_reservation(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    passenger = response.json()["passengers"][0]
    assert passenger["lastName"] == "ALI"
    assert passenger["nationality"] == "BH"
    assert passenger["specialMeal"] == "AVML"


def test_passenger_lookup_by_pnr_uses_requested_origin(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers/BAH123",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["origin"] == "BAH"
    assert body["departureDate"] == BAH_DATE
    assert [p["lastName"] for p in body["passengers"]] == ["ALI"]


def test_passenger_detail_selects_requested_origin(client, monkeypatch):
    calls = []

    class _Sabre:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_passenger_data(self, **kwargs):
            calls.append(kwargs)
            raise SabreError("selection recorded")

    monkeypatch.setattr(passengers, "SabreClient", _Sabre)
    response = client.get(
        f"/flights/{FLIGHT}/passengers/BAH123/detail",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert calls, response.text
    assert calls[0]["origin"] == "BAH"
    assert calls[0]["departure_date"] == BAH_DATE
    assert calls[0]["last_name"] == "ALI"
    assert calls[0]["pnr"] == "BAH123"


def test_scoped_reservations_stay_on_requested_airport(client):
    response = client.get(
        f"/flights/{FLIGHT}/reservations",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["departureAirport"] == "BAH"
    assert body["departureDate"] == BAH_DATE
    assert body["reservations"][0]["numberInParty"] == 1


def test_reservation_by_pnr_uses_requested_origin(client):
    response = client.get(
        f"/flights/{FLIGHT}/reservations/BAH123",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["departureAirport"] == "BAH"
    assert body["departureDate"] == BAH_DATE
    assert body["reservations"][0]["numberInParty"] == 1


def test_scoped_audit_does_not_apply_another_airports_party(client):
    response = client.get(
        f"/flights/{FLIGHT}/audit",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    rule_ids = [alert["ruleId"] for alert in response.json().get("alerts", [])]
    assert "PARTY_SIZE_MISMATCH" not in rule_ids


def test_passenger_changes_use_requested_origin(client):
    response = client.get(
        f"/flights/{FLIGHT}/changes/passenger/BAH123",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body
    assert {item["origin"] for item in body} == {"BAH"}
    assert {item["departureDate"] for item in body} == {BAH_DATE}


def test_schedule_read_uses_requested_origin(client):
    response = client.get(
        f"/flights/{FLIGHT}/schedule",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["origin"] == "BAH"
    assert body["departureDate"] == BAH_DATE
    assert body["aircraftType"] == "32N"


def test_flight_list_schedule_matches_row_origin(client):
    response = client.get("/flights", params={"date": BAH_DATE})
    assert response.status_code == 200, response.text
    rows = [row for row in response.json() if row["origin"] == "BAH"]
    assert len(rows) == 1
    assert rows[0]["gate"] == "B7"
    assert rows[0]["publishedSchedule"]["origin"] == "BAH"
    assert rows[0]["publishedSchedule"]["aircraftType"] == "32N"


def test_data_audit_compare_uses_requested_departure(client):
    response = client.get(
        f"/data-audit/{FLIGHT}/compare",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    origin_row = next(row for row in body["rows"] if row["field"] == "Origin")
    assert origin_row["mongoValue"] == "BAH"
    assert "A12" not in response.text


def test_data_audit_passengers_use_requested_departure(client):
    response = client.get(
        f"/data-audit/{FLIGHT}/passengers",
        params={"origin": "BAH", "date": BAH_DATE},
    )
    assert response.status_code == 200, response.text
    boarded = next(
        row for row in response.json()["rows"] if row["field"] == "Boarded"
    )
    assert boarded["mongoValue"] == "1"
