"""Flight-level changes must stay visible on the network change chart.

Passenger events are written once per passenger and flight-level events once
per flight. The analytics route returns every type sorted by count. The
network page then draws Change Events Distribution from only the first twelve
rows, so gate, status, terminal, boarding-time, and jump-seat changes drop
off while the headline total still counts them.

These tests fail until that chart surfaces every flight-level type the route
returns (on the chart, in an omitted-type list beside it, or on a flight-level
chart of its own).
"""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

import mongomock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import analytics

# Load the differ module directly. Importing backend.feeder also imports the
# Sabre client, which this test does not call.
_DIFFER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "feeder", "differ.py")
_differ_spec = importlib.util.spec_from_file_location(
    "feeder_differ_under_test", _DIFFER_PATH)
_differ = importlib.util.module_from_spec(_differ_spec)
_differ_spec.loader.exec_module(_differ)
diff_flight_status = _differ.diff_flight_status
diff_passenger_list = _differ.diff_passenger_list
diff_reservations = _differ.diff_reservations

DATE = "2026-03-19"
FLIGHT = {"flightNumber": "2057", "origin": "BAH", "departureDate": DATE}
FLIGHT_LEVEL_TYPES = (
    "GATE_CHANGE",
    "STATUS_CHANGE",
    "TERMINAL_CHANGE",
    "BOARDING_TIME_CHANGE",
    "JUMPSEAT_CHANGE",
)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FRONTEND = os.path.join(REPO_ROOT, "frontend")


def _passenger(pnr, last, first, **overrides):
    doc = {
        "pnr": pnr,
        "lastName": last,
        "firstName": first,
        "isCheckedIn": False,
        "isBoarded": False,
        "cabin": "Y",
        "bookingClass": "Y",
        "seat": "",
        "bagCount": 0,
        "passengerType": "F",
        "priorityCode": "",
        "desiredBookingClass": "",
        "editCodes": [],
    }
    doc.update(overrides)
    return doc


def _changes_from_differ():
    """Build stored change documents the feeder would write for one flight."""
    flight_info = dict(FLIGHT)
    staying = []
    for index in range(20):
        staying.append(_passenger(
            f"PNR{index:03d}", f"LAST{index:03d}", "ALEX",
            desiredBookingClass="J",
        ))
    removed = [
        _passenger("RMV001", "GONE", "ONE"),
        _passenger("RMV002", "GONE", "TWO"),
    ]
    added = [
        _passenger(f"NEW{index:03d}", f"NEWLAST{index:03d}", "SAM")
        for index in range(4)
    ]
    before_pax = {
        "snapshotId": "pax-before",
        "data": {"passengers": staying + removed},
    }
    after_staying = []
    for index, passenger in enumerate(staying):
        # Achieved class matches the desired class, which is then cleared.
        after_staying.append(_passenger(
            passenger["pnr"],
            passenger["lastName"],
            passenger["firstName"],
            isCheckedIn=True,
            isBoarded=True,
            cabin="J",
            bookingClass="J",
            seat=f"{index + 1}A",
            bagCount=1,
            passengerType="M",
            priorityCode="UPG",
            desiredBookingClass="",
            editCodes=["FF", "DOCS"],
        ))
    after_pax = {
        "snapshotId": "pax-after",
        "data": {"passengers": after_staying + added},
    }

    before_res = {
        "snapshotId": "res-before",
        "data": {"reservations": [
            {"pnr": "KEEP1", "numberInParty": 2},
            {"pnr": "KEEP2", "numberInParty": 1},
            {"pnr": "DROP1", "numberInParty": 1},
            {"pnr": "DROP2", "numberInParty": 3},
        ]},
    }
    after_res = {
        "snapshotId": "res-after",
        "data": {"reservations": [
            {"pnr": "KEEP1", "numberInParty": 4},
            {"pnr": "KEEP2", "numberInParty": 3},
            {"pnr": "ADD1", "numberInParty": 1},
            {"pnr": "ADD2", "numberInParty": 2},
            {"pnr": "ADD3", "numberInParty": 1},
        ]},
    }

    before_status = {
        "snapshotId": "sts-before",
        "data": {
            "status": "OPENCI",
            "gate": "A1",
            "terminal": "T1",
            "boarding": {"time": "10:00"},
            "jumpSeat": {"cockpitInUse": False, "cabinInUse": False},
            "passengerCounts": {},
        },
    }
    after_status = {
        "snapshotId": "sts-after",
        "data": {
            "status": "FINAL",
            "gate": "A2",
            "terminal": "T2",
            "boarding": {"time": "10:30"},
            "jumpSeat": {"cockpitInUse": True, "cabinInUse": True},
            "passengerCounts": {},
        },
    }

    return (
        diff_passenger_list(before_pax, after_pax, flight_info)
        + diff_reservations(before_res, after_res, flight_info)
        + diff_flight_status(before_status, after_status, flight_info)
    )


def _seed_other_collections(db):
    """Give the other analytics pipelines one document so they can finish.

    Change distribution reads only the changes collection. These documents
    keep the route's earlier aggregations from aborting before that stage.
    """
    db.flights.insert_one({
        "flightNumber": FLIGHT["flightNumber"],
        "airline": "GF",
        "origin": FLIGHT["origin"],
        "departureDate": DATE,
    })
    db.passenger_list.insert_one({
        "flightNumber": FLIGHT["flightNumber"],
        "origin": FLIGHT["origin"],
        "destination": "LHR",
        "departureDate": DATE,
        "fetchedAt": "2026-03-19T12:00:00Z",
        "totalPassengers": 1,
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": 1,
        "cabinSummary": [{"cabin": "Y", "booked": 1, "authorized": 100}],
        "passengers": [{
            "cabin": "Y",
            "bookingClass": "Y",
            "isCheckedIn": False,
            "isBoarded": False,
            "isRevenue": True,
            "isStandby": False,
        }],
    })
    db.reservations.insert_one({
        "flightNumber": FLIGHT["flightNumber"],
        "departureAirport": FLIGHT["origin"],
        "departureDate": DATE,
        "fetchedAt": "2026-03-19T12:00:00Z",
        "reservations": [{
            "pnr": "KEEP1",
            "numberInParty": 1,
            "pointOfSale": {"isoCountry": "BH"},
            "passengers": [{
                "nationality": "BH",
                "frequentFlyerNumber": "",
                "specialMeal": "",
                "wheelchairCode": "",
            }],
            "segments": [{"segmentBookedDate": "2026-03-01"}],
        }],
    })


def test_flight_level_changes_stay_on_the_network_chart():
    changes = _changes_from_differ()
    produced = {}
    for change in changes:
        produced[change["changeType"]] = produced.get(change["changeType"], 0) + 1
    for change_type in FLIGHT_LEVEL_TYPES:
        assert change_type in produced, change_type
    # Passenger-level rows outnumber every flight-level row.
    passenger_floor = min(
        produced[change_type]
        for change_type in (
            "CHECKED_IN", "BOARDED", "SEAT_CHANGE", "BAG_COUNT_CHANGE",
            "CLASS_CHANGE", "CABIN_CHANGE", "DOCUMENT_ADDED",
        )
    )
    assert passenger_floor > max(produced[change_type] for change_type in FLIGHT_LEVEL_TYPES)

    client = mongomock.MongoClient()
    db = client["falconeye"]
    _seed_other_collections(db)
    db.changes.insert_many(changes)
    analytics.get_db = lambda: db

    async def _run_inline(func, *args, **kwargs):
        # mongomock's aggregation is not safe across the route's worker
        # thread. The route still performs its date check, then this calls
        # the same _run_network_analytics the handler passes to the pool.
        return func(*args, **kwargs)

    analytics.run_in_threadpool = _run_inline

    # The route takes no credentials. An unauthenticated caller of
    # GET /analytics/network receives the grouped counts.
    # FastAPI supplies None for omitted query params. Calling the handler
    # directly has to pass those the same way.
    payload = asyncio.run(analytics.get_network_analytics(
        DATE, DATE, origin=None, cabin=None,
        flight_number=None, destination=None,
    ))
    distribution = payload["changeDistribution"]
    ranked = [item["changeType"] for item in distribution]
    assert payload["totalChanges"] == len(changes)
    assert payload["totalChanges"] == sum(item["count"] for item in distribution)
    assert ranked == sorted(ranked, key=lambda change_type: -produced[change_type])
    for change_type in FLIGHT_LEVEL_TYPES:
        assert change_type in ranked
        assert ranked.index(change_type) >= 12, (change_type, ranked)

    fixture = {"network": payload}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(fixture, handle)
        fixture_path = handle.name
    try:
        completed = subprocess.run(
            [
                "node",
                "--import",
                "tsx",
                os.path.join(FRONTEND, "tests", "change-events-distribution-boot.mjs"),
                fixture_path,
            ],
            cwd=FRONTEND,
            check=False,
            timeout=60,
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(fixture_path)
    relevant = "\n".join(
        line for line in (completed.stderr or "").splitlines()
        if (
            "flight-level" in line
            or line.startswith("chart categories")
            or line.startswith("  [")
            or "headline" in line
            or "did not render" in line
        )
    )
    assert completed.returncode == 0, (
        "Change Events Distribution hid flight-level types that the analytics "
        "route returned.\n"
        f"{relevant or completed.stderr}"
    )


if __name__ == "__main__":
    test_flight_level_changes_stay_on_the_network_chart()
    print("PASS: test_flight_level_changes_stay_on_the_network_chart")
