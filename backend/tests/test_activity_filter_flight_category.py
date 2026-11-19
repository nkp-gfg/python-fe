"""Operational flight changes must not crash the Activity filter.

Builds a timeline the way ingestion does (Sabre flight-status dict →
convert_flight_status → detect_changes → get_flight_timeline) and renders
that payload through the Activity tab filter.

The UI assertion fails while the filter reads a missing category config.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.routes.changes import get_flight_timeline
from backend.feeder.converter import convert_flight_status
from backend.feeder.differ import detect_changes

OPERATIONAL_CHANGE_TYPES = (
    "STATUS_CHANGE",
    "GATE_CHANGE",
    "TERMINAL_CHANGE",
    "BOARDING_TIME_CHANGE",
    "JUMPSEAT_CHANGE",
)


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        self._docs = sorted(
            self._docs,
            key=lambda doc: doc.get(key) or "",
            reverse=direction < 0,
        )
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    """Equality-filter stand-in for the find/sort calls in get_flight_timeline."""

    def __init__(self, docs):
        self.docs = [dict(doc) for doc in docs]

    def find(self, query, projection=None, **_kwargs):
        matched = []
        for doc in self.docs:
            if any(doc.get(key) != value for key, value in query.items()):
                continue
            item = dict(doc)
            if projection:
                for field, include in projection.items():
                    if include == 0:
                        item.pop(field, None)
            matched.append(item)
        return _Cursor(matched)


class _Database(dict):
    def __getitem__(self, name):
        return dict.__getitem__(self, name)


def _raw_flight_status(*, status, gate, terminal, boarding_time, cockpit_in_use, cabin_in_use):
    """Parsed flight-status body in the shape convert_flight_status reads."""
    return {
        "ns2:ItineraryResponseList": {
            "ns2:ItineraryInfoResponse": {
                "ns2:FlightStatus": status,
                "ns2:DepartureGate": gate,
                "ns2:DepartureTerminal": terminal,
                "ns2:BoardingTime": boarding_time,
                "ns2:ScheduledDepartureDate": "2026-03-19",
                "ns2:DepartureDate": "2026-03-19",
            }
        },
        "ns2:LegInfoList": {"ns2:LegInfo": []},
        "ns2:PassengerCounts": [],
        "ns2:JumpSeat": {
            "@cockpitInUse": "true" if cockpit_in_use else "false",
            "@cabinInUse": "true" if cabin_in_use else "false",
            "ns2:Cockpit": "1",
            "ns2:Cabin": "1",
        },
    }


def _timeline_after_two_ingests():
    flight_info = {
        "airline": "GF",
        "flightNumber": "2057",
        "origin": "BAH",
        "departureDate": "2026-03-19",
    }
    before = convert_flight_status(
        _raw_flight_status(
            status="OPENCI",
            gate="A1",
            terminal="1",
            boarding_time="10:00",
            cockpit_in_use=False,
            cabin_in_use=False,
        ),
        flight_info["airline"],
        flight_info["flightNumber"],
        flight_info["origin"],
        flight_info["departureDate"],
    )
    after = convert_flight_status(
        _raw_flight_status(
            status="FINAL",
            gate="B12",
            terminal="2",
            boarding_time="10:40",
            cockpit_in_use=True,
            cabin_in_use=True,
        ),
        flight_info["airline"],
        flight_info["flightNumber"],
        flight_info["origin"],
        flight_info["departureDate"],
    )
    changes = detect_changes(
        {"snapshotId": "snap-before", "data": before},
        {"snapshotId": "snap-after", "data": after},
        "flight_status",
        flight_info,
    )
    found = {change["changeType"] for change in changes}
    missing = [kind for kind in OPERATIONAL_CHANGE_TYPES if kind not in found]
    if missing:
        raise AssertionError(f"detect_changes did not emit {missing}; got {sorted(found)}")

    snapshots = [
        {
            "flightNumber": flight_info["flightNumber"],
            "origin": flight_info["origin"],
            "departureDate": flight_info["departureDate"],
            "snapshotType": "flight_status",
            "sequenceNumber": 1,
            "fetchedAt": "2026-03-19T09:00:00Z",
            "snapshotId": "snap-before",
        },
        {
            "flightNumber": flight_info["flightNumber"],
            "origin": flight_info["origin"],
            "departureDate": flight_info["departureDate"],
            "snapshotType": "flight_status",
            "sequenceNumber": 2,
            "fetchedAt": "2026-03-19T10:00:00Z",
            "snapshotId": "snap-after",
        },
    ]
    database = _Database(
        {
            "changes": _Collection(changes),
            "snapshots": _Collection(snapshots),
        }
    )

    import backend.api.routes.changes as changes_module

    original_get_db = changes_module.get_db
    changes_module.get_db = lambda: database
    try:
        payload = get_flight_timeline(
            flight_info["flightNumber"],
            origin=flight_info["origin"],
            date=flight_info["departureDate"],
        )
    finally:
        changes_module.get_db = original_get_db

    event_types = {event["eventType"] for event in payload["events"]}
    for kind in OPERATIONAL_CHANGE_TYPES:
        if kind not in event_types:
            raise AssertionError(
                f"timeline omitted {kind}; events={sorted(event_types)}"
            )
    categories = sorted({event["category"] for event in payload["events"]})
    print(
        "timeline categories:",
        categories,
        "event types:",
        sorted(event_types),
        flush=True,
    )
    return payload


def test_activity_filter_survives_operational_changes():
    payload = _timeline_after_two_ingests()
    frontend = REPO_ROOT / "frontend"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(payload, handle)
        fixture_path = handle.name

    env = os.environ.copy()
    env["TIMELINE_FIXTURE"] = fixture_path
    completed = subprocess.run(
        [
            "npm",
            "test",
            "--",
            "components/dashboard/flight-timeline.activity-filter.test.tsx",
        ],
        cwd=frontend,
        env=env,
        text=True,
    )
    os.unlink(fixture_path)
    if completed.returncode != 0:
        raise AssertionError(
            "Activity filter test failed for the timeline payload "
            f"(exit {completed.returncode})"
        )


if __name__ == "__main__":
    test_activity_filter_survives_operational_changes()
    print("PASS: test_activity_filter_survives_operational_changes")
