"""Activity summary must show the counts the flight timeline route computed.

Operators open a flight Activity tab. FlightTimeline loads
GET /flights/{flight}/timeline and renders the Activity Summary from that
response. Change rows produced by the passenger-list differ are counted by
the route. These tests fail while the summary prints 0 for those rows, and
pass once the same numbers are what the summary shows.

Run from the repository root, after installing frontend dependencies:

    cd frontend && npm ci
    cd .. && PYTHONPATH=. python3 backend/tests/test_activity_summary_stats.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.feeder.differ import diff_passenger_list

FLIGHT = {
    "flightNumber": "2057",
    "origin": "BAH",
    "departureDate": "2026-03-19",
}
RENDERER = REPO_ROOT / "frontend" / "tests" / "render-activity-summary.mjs"


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(
            key=lambda doc: doc.get(key) or "",
            reverse=direction < 0,
        )
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    """Just enough of a Mongo collection for get_flight_timeline's finds."""

    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query=None, projection=None, *args, **kwargs):
        if projection is None and args:
            projection = args[0]
        matched = []
        for doc in self._docs:
            if _matches(doc, query or {}):
                copied = dict(doc)
                if isinstance(projection, dict):
                    for field, include in projection.items():
                        if include == 0:
                            copied.pop(field, None)
                matched.append(copied)
        return _Cursor(matched)


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.get(name, _Collection([]))


def _matches(doc, query):
    for key, expected in query.items():
        if doc.get(key) != expected:
            return False
    return True


def _passenger(pnr, last, first, **overrides):
    passenger = {
        "pnr": pnr,
        "lastName": last,
        "firstName": first,
        "isCheckedIn": False,
        "isBoarded": False,
        "cabin": "Y",
        "bookingClass": "V",
        "seat": "21A",
        "bagCount": 0,
        "passengerType": "F",
        "editCodes": [],
    }
    passenger.update(overrides)
    return passenger


def _checked_in_departure_changes():
    """Changes a departure produces when two passengers check in and one boards, upgrades, and moves seats."""
    before = {
        "snapshotId": "snap-before",
        "data": {
            "passengers": [
                _passenger(
                    "ABC123",
                    "SMITH",
                    "JOHN",
                    desiredBookingClass="C",
                ),
                _passenger("XYZ789", "JONES", "MARY", seat="22B"),
            ]
        },
    }
    after = {
        "snapshotId": "snap-after",
        "data": {
            "passengers": [
                _passenger(
                    "ABC123",
                    "SMITH",
                    "JOHN",
                    isCheckedIn=True,
                    isBoarded=True,
                    cabin="J",
                    bookingClass="C",
                    seat="1A",
                ),
                _passenger(
                    "XYZ789",
                    "JONES",
                    "MARY",
                    isCheckedIn=True,
                    seat="22B",
                ),
            ]
        },
    }
    return diff_passenger_list(before, after, FLIGHT)


def _timeline_response(changes):
    snapshots = [
        {
            "flightNumber": FLIGHT["flightNumber"],
            "origin": FLIGHT["origin"],
            "departureDate": FLIGHT["departureDate"],
            "snapshotType": "passenger_list",
            "sequenceNumber": 1,
            "fetchedAt": "2026-03-19T08:00:00Z",
            "data": {"passengers": []},
        }
    ]
    database = _Database(
        {
            "changes": _Collection(changes),
            "snapshots": _Collection(snapshots),
        }
    )
    with patch("backend.api.routes.changes.get_db", return_value=database):
        client = TestClient(app)
        response = client.get(
            f"/flights/{FLIGHT['flightNumber']}/timeline",
            params={"origin": FLIGHT["origin"], "date": FLIGHT["departureDate"]},
        )
    assert response.status_code == 200, response.text
    return response.json()


def _render_summary(timeline):
    if not (REPO_ROOT / "frontend" / "node_modules" / "jiti").exists():
        raise AssertionError(
            "frontend/node_modules is missing. Run `npm ci` in frontend/ before this test."
        )
    payload = {
        "flightNumber": FLIGHT["flightNumber"],
        "origin": FLIGHT["origin"],
        "date": FLIGHT["departureDate"],
        "timeline": timeline,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(payload, handle)
        payload_path = handle.name
    try:
        completed = subprocess.run(
            ["node", str(RENDERER), payload_path],
            cwd=REPO_ROOT / "frontend",
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "NODE_ENV": "test"},
        )
    finally:
        os.unlink(payload_path)
    assert completed.returncode == 0, (
        "Activity timeline failed to render.\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return json.loads(completed.stdout)


def _count(events, event_type):
    return sum(1 for event in events if event.get("eventType") == event_type)


def test_activity_summary_shows_timeline_counts_for_a_departing_flight():
    changes = _checked_in_departure_changes()
    change_types = [change["changeType"] for change in changes]
    assert change_types.count("CHECKED_IN") == 2
    assert change_types.count("BOARDED") == 1
    assert change_types.count("SEAT_CHANGE") == 1
    assert change_types.count("CABIN_CHANGE") == 1
    assert change_types.count("CLASS_CHANGE") == 1
    assert change_types.count("UPGRADE_CONFIRMED") == 1

    timeline = _timeline_response(changes)
    events = timeline["events"]
    stats = timeline["stats"]

    checked_in = _count(events, "CHECKED_IN")
    boarded = _count(events, "BOARDED")
    seat_changes = _count(events, "SEAT_CHANGE")
    upgrades = (
        _count(events, "CABIN_CHANGE")
        + _count(events, "CLASS_CHANGE")
        + _count(events, "UPGRADE_CONFIRMED")
    )
    change_rows = [event for event in events if event.get("eventType") != "SNAPSHOT_TAKEN"]

    assert checked_in == 2
    assert boarded == 1
    assert seat_changes == 1
    assert upgrades == 3
    assert stats["checkedIn"] == checked_in
    assert stats["boarded"] == boarded
    assert stats["seatChanges"] == seat_changes
    assert stats["upgrades"] == upgrades
    assert stats["totalChanges"] == len(change_rows)
    assert any(event.get("eventType") == "SNAPSHOT_TAKEN" for event in events)
    assert any(event.get("description") == "Passenger checked in" for event in events)
    assert any(event.get("description") == "Passenger boarded aircraft" for event in events)

    rendered = _render_summary(timeline)

    assert rendered["showsSummary"], rendered
    assert rendered["showsCheckinEvent"], rendered
    assert rendered["showsBoardingEvent"], rendered
    assert rendered["showsUpgradeEvent"], rendered
    assert rendered["showsSeatEvent"], rendered
    assert not rendered["showsLoading"], rendered
    assert not rendered["showsEmpty"], rendered
    assert any(
        "/flights/2057/timeline" in call["url"] and "origin=BAH" in call["url"] and "date=2026-03-19" in call["url"]
        for call in rendered["requested"]
    ), rendered["requested"]

    expected = {
        "checkins": checked_in,
        "boarded": boarded,
        "upgrades": upgrades,
        "seatChanges": seat_changes,
        "totalEvents": stats["totalChanges"],
    }
    actual = {key: rendered[key] for key in expected}
    assert actual == expected, (
        "Activity summary did not show the counts from GET /flights/2057/timeline. "
        f"expected={expected} actual={actual} route stats={stats}"
    )


if __name__ == "__main__":
    test_activity_summary_shows_timeline_counts_for_a_departing_flight()
    print("PASS: test_activity_summary_shows_timeline_counts_for_a_departing_flight")
