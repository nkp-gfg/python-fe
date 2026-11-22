"""A flight with status and no passenger list must not be count-verified.

These tests exercise GET /flights/{flight_number}/dashboard through the real
route, the real MongoDB queries, and the real dashboard builder. They fail
while a missing passenger list is reported as dataIntegrity.valid with zero
checks. They pass once that response is invalid and carries a warning that
the manifest was not checked.

A stored manifest is a different case: consistent counts stay valid with the
checks actually run, and inconsistent counts stay invalid.

The flight-board badge is covered by
frontend/tests/missing-manifest-verified-badge.test.mjs, invoked below.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# This suite talks only to a database the test starts locally. Do not inherit
# a configured URI; that value can point at a shared deployment.
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.database import close_db, get_db
from backend.api.routes import flights

COLLECTIONS = (
    "flight_status",
    "passenger_list",
    "reservations",
    "trip_reports",
    "flight_schedules",
    "changes",
    "flights",
    "snapshots",
)

ORIGIN = "BAH"
DATE = "2026-06-01"


def _status_doc(flight_number, status="OPENCI"):
    return {
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DATE,
        "status": status,
        "fetchedAt": "2026-06-01T08:00:00Z",
        "gate": "A1",
        "legs": [{"city": ORIGIN}, {"city": "LHR"}],
        "aircraft": {"type": "320", "registration": "A9C-TE"},
    }


def _passenger(pnr="ABC123"):
    return {
        "pnr": pnr,
        "lastName": "SMITH",
        "firstName": "A",
        "cabin": "Y",
        "isCheckedIn": False,
        "isBoarded": False,
        "isRevenue": True,
        "passengerType": "F",
    }


def _passenger_list(flight_number, passengers, totals):
    return {
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": "2026-06-01T08:05:00Z",
        "passengers": passengers,
        "cabinSummary": [],
        **totals,
    }


@pytest.fixture(scope="module")
def api():
    close_db()
    db = get_db()
    db.command("ping")
    app = FastAPI()
    app.include_router(flights.router)
    with TestClient(app) as client:
        yield client, db
    close_db()


@pytest.fixture(autouse=True)
def _clean(api):
    _, db = api
    for name in COLLECTIONS:
        db[name].delete_many({})
    flights._dashboard_cache.clear()
    yield
    flights._dashboard_cache.clear()


def _get(client, flight_number, **params):
    response = client.get(
        f"/flights/{flight_number}/dashboard",
        params=params or None,
        headers={},
    )
    assert "authorization" not in {key.lower() for key in response.request.headers}
    return response


def _manifest_was_not_checked(warnings):
    for warning in warnings:
        text = warning.lower()
        if "manifest" in text and "not checked" in text:
            return True
    return False


def test_dashboard_route_has_no_credential_dependency():
    """Opening the board does not require an account."""
    matches = [
        route
        for route in flights.router.routes
        if getattr(route, "endpoint", None) is flights.get_flight_dashboard
    ]
    assert len(matches) == 1
    assert matches[0].dependencies == []


def test_unknown_flight_is_not_a_verified_board(api):
    client, _ = api
    response = _get(client, "0000", origin=ORIGIN, date=DATE)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"origin": ORIGIN, "date": DATE},
        {},
    ],
)
def test_status_without_passenger_list_is_not_count_verified(api, params):
    """The attack path: status is stored, the passenger list is not.

    A passenger list for a different flight is stored in the same collection
    so the route has to apply its flight query. The response must not treat
    the missing list as a checked empty flight.
    """
    client, db = api
    db["flight_status"].insert_one(_status_doc("4401"))
    db["passenger_list"].insert_one(
        _passenger_list(
            "9999",
            [_passenger()],
            {
                "totalPassengers": 1,
                "adultCount": 1,
                "childCount": 0,
                "infantCount": 0,
                "totalSouls": 1,
            },
        )
    )

    response = _get(client, "4401", **params)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["flightStatus"]["status"] == "OPENCI"
    assert body["flightStatus"]["flightNumber"] == "4401"
    overview = body["overview"]
    assert overview["manifestRecords"] == 0
    assert overview["totalSouls"] == 0
    assert overview["soulsOnBoard"] == 0

    integrity = body["dataIntegrity"]
    assert integrity["valid"] is False
    assert isinstance(integrity["warnings"], list)
    assert _manifest_was_not_checked(integrity["warnings"])
    assert not (integrity["valid"] is True and integrity["checks"] == 0)


def test_stored_empty_manifest_is_checked_and_can_verify(api):
    """A stored list of zero passengers is checked. That is not the bug."""
    client, db = api
    db["flight_status"].insert_one(_status_doc("4402"))
    db["passenger_list"].insert_one(
        _passenger_list(
            "4402",
            [],
            {
                "totalPassengers": 0,
                "adultCount": 0,
                "childCount": 0,
                "infantCount": 0,
                "totalSouls": 0,
            },
        )
    )

    response = _get(client, "4402", origin=ORIGIN, date=DATE)

    assert response.status_code == 200, response.text
    integrity = response.json()["dataIntegrity"]
    assert integrity["valid"] is True
    assert integrity["checks"] > 0
    assert integrity["warnings"] == []
    assert response.json()["overview"]["manifestRecords"] == 0


def test_stored_manifest_with_inconsistent_counts_stays_invalid(api):
    client, db = api
    db["flight_status"].insert_one(_status_doc("4404", status="FINAL"))
    db["passenger_list"].insert_one(
        _passenger_list(
            "4404",
            [_passenger("XYZ789")],
            {
                "totalPassengers": 2,
                "adultCount": 1,
                "childCount": 0,
                "infantCount": 0,
                "totalSouls": 1,
            },
        )
    )

    response = _get(client, "4404", origin=ORIGIN, date=DATE)

    assert response.status_code == 200, response.text
    integrity = response.json()["dataIntegrity"]
    assert integrity["valid"] is False
    assert integrity["checks"] > 0
    assert integrity["warnings"]


def test_flight_board_verified_badge_requires_completed_checks():
    """Render the real flight header. Verified requires checks > 0 and valid."""
    script = ROOT / "frontend" / "tests" / "missing-manifest-verified-badge.test.mjs"
    completed = subprocess.run(
        ["node", str(script)],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
