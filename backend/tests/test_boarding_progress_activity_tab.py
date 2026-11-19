"""Activity and overview boarding card must show stored check-in and boarding totals.

The card is `BoardingProgress`, mounted by the flight workbench on the overview
tab and the activity tab. It loads `GET /flights/{flight}/boarding-progress`
through `fetchBoardingProgress`. These tests build that payload with the real
differ and the real route handler, then render the real component.

They fail while the card reads `checkinProgress.data` / `cumulativeCount`
(and the same fields on `boardingProgress`) because the handler returns
`series` and `count`. They pass once the rendered totals follow the changes
the differ stored.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pymongo.helpers_shared import _index_list

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.routes.changes import get_boarding_progress
from backend.feeder.differ import detect_changes


FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"
FLIGHT_INFO = {
    "flightNumber": FLIGHT,
    "origin": ORIGIN,
    "departureDate": DATE,
}


def _passenger(pnr, last, first, checked_in, boarded):
    return {
        "pnr": pnr,
        "lastName": last,
        "firstName": first,
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
    }


def _matches(document, query):
    """Equality match used by this handler's filters.

    `Collection.find` documents this as: a query `{"hello": "world"}` matches
    documents that have that key and value and may have other keys. The
    boarding-progress handler only builds queries of that shape. Operator
    documents are rejected so a query this double does not understand cannot
    silently drop or keep the wrong changes.
    """
    if not query:
        return True
    for key, expected in query.items():
        if isinstance(expected, dict):
            raise AssertionError(
                f"boarding-progress query uses an operator this test does not apply: {key}={expected!r}"
            )
        if document.get(key) != expected:
            return False
    return True


class _Cursor:
    def __init__(self, documents):
        self._documents = documents

    def sort(self, key_or_list, direction=None):
        keys = _index_list(key_or_list, direction)
        for field, field_direction in reversed(list(keys)):
            if not isinstance(field_direction, int):
                raise AssertionError(f"unsupported sort direction {field_direction!r}")
            reverse = field_direction < 0
            self._documents.sort(
                key=lambda document, field=field: (
                    document.get(field) is None,
                    document.get(field),
                ),
                reverse=reverse,
            )
        return self

    def limit(self, count):
        # find_one asks for limit(-1), which returns a single document.
        if count < 0:
            count = abs(count)
        if count:
            self._documents = self._documents[:count]
        return self

    def __iter__(self):
        return iter(self._documents)


class _Collection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, filter=None, projection=None, *args, **kwargs):
        if args:
            raise AssertionError(f"unexpected find() arguments: {args!r}")
        if projection not in (None, {}):
            raise AssertionError(f"unexpected projection: {projection!r}")
        matched = [deepcopy(document) for document in self.documents if _matches(document, filter)]
        cursor = _Cursor(matched)
        if kwargs.get("sort") is not None:
            cursor.sort(kwargs["sort"])
        return cursor

    def find_one(self, filter=None, *args, **kwargs):
        # Same control flow as pymongo Collection.find_one: find, then limit(-1).
        cursor = self.find(filter, *args, **kwargs)
        for document in cursor.limit(-1):
            return document
        return None


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _stored_changes():
    before_passengers = [
        _passenger("AAA111", "ALI", "SARA", False, False),
        _passenger("BBB222", "KHAN", "OMAR", False, False),
        _passenger("CCC333", "NOOR", "LAYLA", True, False),
        _passenger("DDD444", "SAID", "HASSAN", False, False),
    ]
    after_passengers = [
        _passenger("AAA111", "ALI", "SARA", True, False),
        _passenger("BBB222", "KHAN", "OMAR", True, True),
        _passenger("CCC333", "NOOR", "LAYLA", True, False),
        _passenger("DDD444", "SAID", "HASSAN", False, False),
    ]
    passenger_changes = detect_changes(
        {"snapshotId": "pax-1", "data": {"passengers": before_passengers}},
        {"snapshotId": "pax-2", "data": {"passengers": after_passengers}},
        "passenger_list",
        FLIGHT_INFO,
    )
    status_changes = detect_changes(
        {"snapshotId": "status-1", "data": {"status": "SCHEDULED"}},
        {"snapshotId": "status-2", "data": {"status": "BOARDING"}},
        "flight_status",
        FLIGHT_INFO,
    )
    # A check-in stored for another flight must not leak into this card.
    other_flight = deepcopy(next(change for change in passenger_changes if change["changeType"] == "CHECKED_IN"))
    other_flight["flightNumber"] = "0001"
    other_flight["passenger"] = {"pnr": "ZZZ999", "lastName": "OTHER", "firstName": "FLIGHT"}
    return passenger_changes + status_changes + [other_flight]


def _running_total(progress):
    points = progress.get("series")
    if points is None:
        points = progress.get("data")
    if not points:
        current = progress.get("current")
        if isinstance(current, int) and current > 0:
            return current
        raise AssertionError(f"handler returned no progress points: {progress}")
    last = points[-1]
    if isinstance(last.get("count"), int):
        return last["count"]
    if isinstance(last.get("cumulativeCount"), int):
        return last["cumulativeCount"]
    raise AssertionError(f"progress point has no running total: {last}")


def test_boarding_card_shows_checked_in_and_boarded_totals(monkeypatch, tmp_path):
    changes = _stored_changes()
    checked_in = [
        change for change in changes
        if change["changeType"] == "CHECKED_IN" and change["flightNumber"] == FLIGHT
    ]
    boarded = [
        change for change in changes
        if change["changeType"] == "BOARDED" and change["flightNumber"] == FLIGHT
    ]
    status_changes = [
        change for change in changes
        if change["changeType"] == "STATUS_CHANGE" and change["flightNumber"] == FLIGHT
    ]
    assert len(checked_in) == 2
    assert len(boarded) == 1
    assert len(status_changes) == 1
    assert status_changes[0]["newValue"] == "BOARDING"
    assert status_changes[0]["oldValue"] == "SCHEDULED"

    total_passengers = 4
    database = _Database({
        "changes": _Collection(changes),
        "snapshots": _Collection([
            {
                "flightNumber": FLIGHT,
                "origin": ORIGIN,
                "departureDate": DATE,
                "snapshotType": "passenger_list",
                "sequenceNumber": 1,
                "data": {"totalPassengers": 99, "passengers": []},
            },
            {
                "flightNumber": FLIGHT,
                "origin": ORIGIN,
                "departureDate": DATE,
                "snapshotType": "passenger_list",
                "sequenceNumber": 2,
                "data": {"totalPassengers": total_passengers, "passengers": []},
            },
        ]),
    })
    monkeypatch.setattr("backend.api.routes.changes.get_db", lambda: database)

    response = get_boarding_progress(FLIGHT, origin=ORIGIN, date=DATE)
    payload = json.loads(json.dumps(response))

    assert payload["totalPassengers"] == total_passengers
    assert _running_total(payload["checkinProgress"]) == len(checked_in)
    assert _running_total(payload["boardingProgress"]) == len(boarded)
    assert payload["milestones"], "status change was not returned as a milestone"

    expected = {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "date": DATE,
        "total": total_passengers,
        "checkedIn": len(checked_in),
        "boarded": len(boarded),
        "remaining": total_passengers - len(checked_in),
        "milestoneStatus": status_changes[0]["newValue"],
    }
    payload_path = tmp_path / "boarding-progress.json"
    expected_path = tmp_path / "boarding-progress-expected.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    expected_path.write_text(json.dumps(expected), encoding="utf-8")

    frontend = ROOT / "frontend"
    vitest = frontend / "node_modules" / ".bin" / "vitest"
    if not vitest.exists():
        pytest.fail(
            "vitest is not installed. From frontend/: "
            "npm ci && npm install --no-save vitest@3.2.4 jsdom @testing-library/react @testing-library/dom"
        )

    env = os.environ.copy()
    env["BOARDING_PROGRESS_PAYLOAD"] = str(payload_path)
    env["BOARDING_PROGRESS_EXPECT"] = str(expected_path)
    env["NEXT_PUBLIC_API_URL"] = "http://127.0.0.1:8000"
    completed = subprocess.run(
        [
            str(vitest),
            "run",
            "--config",
            "tests/vitest.boarding-progress.config.ts",
        ],
        cwd=frontend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
    assert completed.returncode == 0, (
        "BoardingProgress rendered the handler payload with the wrong totals.\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
