"""Readable labels for live change types.

The live activity card and the change-velocity panel format change-type
keys before showing them. Change records store uppercase names such as
GATE_CHANGE. These tests drive that name from snapshot diffs through the
dashboard response and into the production React cards.

They fail while those cards insert a space before every capital letter.
A readable label is the change type's words, in order, separated by
spaces, with the underscores removed. GATE_CHANGE may render as
"Gate Change", "GATE CHANGE", or "gate change". It may not render as
"G A T E_ C H A N G E".
"""

import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights as flights_api
from backend.feeder.differ import detect_changes

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RENDER_SCRIPT = os.path.join(REPO_ROOT, "frontend", "tests", "render-change-labels.mjs")


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __iter__(self):
        return iter(self._docs)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self


class _Collection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query=None, projection=None, **kwargs):
        return _Cursor(doc for doc in self.docs if _matches(doc, query or {}))

    def find_one(self, query=None, projection=None, sort=None, **kwargs):
        for doc in self.docs:
            if _matches(doc, query or {}):
                return doc
        return None


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection([]))


def _matches(doc, query):
    for key, expected in query.items():
        if doc.get(key) != expected:
            return False
    return True


def _flight_info(flight_number, origin, departure_date):
    return {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
    }


def _status_snapshot(snapshot_id, **overrides):
    data = {
        "status": "OPEN",
        "gate": "A1",
        "terminal": "1",
        "boarding": {"time": "10:00"},
        "jumpSeat": {"cockpitInUse": False, "cabinInUse": False},
        "passengerCounts": {},
    }
    data.update(overrides)
    return {"snapshotId": snapshot_id, "data": data}


def _passenger(pnr, **overrides):
    passenger = {
        "pnr": pnr,
        "lastName": "SMITH",
        "firstName": "JOHN",
        "isCheckedIn": False,
        "isBoarded": False,
        "cabin": "Y",
        "bookingClass": "V",
        "seat": "21A",
        "bagCount": 1,
        "passengerType": "F",
    }
    passenger.update(overrides)
    return passenger


def _passenger_snapshot(snapshot_id, passengers):
    return {"snapshotId": snapshot_id, "data": {"passengers": passengers}}


def _status_changes():
    """Two flight-status snapshots whose gate, terminal, status, and boarding time differ."""
    info = _flight_info("2057", "BAH", "2026-11-23")
    before = _status_snapshot("snap-before")
    after = _status_snapshot(
        "snap-after",
        status="BOARDING",
        gate="B12",
        terminal="2",
        boarding={"time": "10:40"},
    )
    changes = detect_changes(before, after, "flight_status", info)
    # A change for a different flight must not be counted into this dashboard.
    other = detect_changes(before, after, "flight_status", _flight_info("1999", "LHR", "2026-11-23"))
    return info, changes, other


def _passenger_changes():
    info = _flight_info("2060", "BAH", "2026-11-23")
    before = _passenger_snapshot("pax-before", [_passenger("ABC123")])
    after = _passenger_snapshot(
        "pax-after",
        [
            _passenger("ABC123", isCheckedIn=True, isBoarded=True, seat="22C"),
            _passenger("XYZ789", lastName="JONES", firstName="MARY"),
        ],
    )
    changes = detect_changes(before, after, "passenger_list", info)
    return info, changes


def _dashboard_for(info, changes, extra_changes=()):
    flight_doc = {
        "flightNumber": info["flightNumber"],
        "origin": info["origin"],
        "departureDate": info["departureDate"],
        "status": "OPEN",
        "gate": "B12",
    }
    database = _Database({
        "flight_status": _Collection([flight_doc]),
        "changes": _Collection(list(changes) + list(extra_changes)),
    })
    flights_api._dashboard_cache.clear()
    original_get_db = flights_api.get_db
    flights_api.get_db = lambda: database
    try:
        payload = flights_api.get_flight_dashboard(
            info["flightNumber"],
            origin=info["origin"],
            date=info["departureDate"],
            snapshot_sequence=None,
        )
    finally:
        flights_api.get_db = original_get_db
        flights_api._dashboard_cache.clear()
    insights = flights_api._build_insights([], None, None, None, payload["changeSummary"])
    return payload, insights


def _is_readable_label(change_type, label):
    if "_" in label:
        return False
    tokens = label.split()
    if any(len(token) == 1 for token in tokens):
        return False
    expected = [word.lower() for word in change_type.split("_")]
    return [token.lower() for token in tokens] == expected


def _render(cases):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"cases": cases}, handle)
        payload_path = handle.name
    try:
        completed = subprocess.run(
            ["node", RENDER_SCRIPT, payload_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.unlink(payload_path)
    if completed.returncode != 0:
        raise AssertionError(
            "Rendering the production change labels failed.\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def _assert_labels(change_summary, activity_labels, velocity_labels):
    assert change_summary, "dashboard returned no change types to label"
    assert len(activity_labels) == len(change_summary), activity_labels
    assert len(velocity_labels) == len(change_summary), velocity_labels
    unread = []
    for change_type in change_summary:
        activity = [label for label in activity_labels if _is_readable_label(change_type, label)]
        velocity = [label for label in velocity_labels if _is_readable_label(change_type, label)]
        if activity != [next(iter(activity), None)]:
            unread.append(f"activity {change_type} from {activity_labels}")
        if velocity != [next(iter(velocity), None)]:
            unread.append(f"velocity {change_type} from {velocity_labels}")
    assert not unread, "unreadable change labels:\n" + "\n".join(unread)


def test_flight_status_changes_render_as_words():
    info, changes, other_flight = _status_changes()
    types = [change["changeType"] for change in changes]
    assert types == [
        "STATUS_CHANGE",
        "GATE_CHANGE",
        "TERMINAL_CHANGE",
        "BOARDING_TIME_CHANGE",
    ]
    gate = next(change for change in changes if change["changeType"] == "GATE_CHANGE")
    assert gate["oldValue"] == "A1"
    assert gate["newValue"] == "B12"

    payload, insights = _dashboard_for(info, changes, other_flight)
    summary = payload["changeSummary"]
    assert summary == {
        "STATUS_CHANGE": 1,
        "GATE_CHANGE": 1,
        "TERMINAL_CHANGE": 1,
        "BOARDING_TIME_CHANGE": 1,
    }
    assert insights["changeVelocity"]["changeTypes"] == summary
    assert "1999" not in json.dumps(summary)

    rendered = _render([{
        "name": "flight-status",
        "selection": {
            "flightNumber": info["flightNumber"],
            "origin": info["origin"],
            "date": info["departureDate"],
        },
        "dashboard": payload,
        "insights": insights,
    }])
    case = rendered["cases"][0]
    _assert_labels(summary, case["activity"], case["velocity"])


def test_passenger_changes_render_as_words():
    info, changes = _passenger_changes()
    types = [change["changeType"] for change in changes]
    assert types == ["PASSENGER_ADDED", "CHECKED_IN", "BOARDED", "SEAT_CHANGE"]

    payload, insights = _dashboard_for(info, changes)
    summary = payload["changeSummary"]
    assert summary == {change_type: 1 for change_type in types}
    assert insights["changeVelocity"]["changeTypes"] == summary

    rendered = _render([{
        "name": "passenger-list",
        "selection": {
            "flightNumber": info["flightNumber"],
            "origin": info["origin"],
            "date": info["departureDate"],
        },
        "dashboard": payload,
        "insights": insights,
    }])
    case = rendered["cases"][0]
    _assert_labels(summary, case["activity"], case["velocity"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
