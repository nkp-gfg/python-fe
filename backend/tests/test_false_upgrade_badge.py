"""Cabin moves must not be drawn as upgrades unless the cabin rank rose.

The passenger History tab compares the cabin stored when the passenger was
first added with the cabin on the latest passenger-list snapshot. These tests
drive that path through the passenger-list converter, the snapshot differ, and
the timeline route, then render the History tab, the flight timeline, and the
activity feed.

Rank follows the only cabin order the differ implements: Y, then J, then F.
Any other letter is unranked. A ranked rise is an upgrade, a ranked drop is a
downgrade, and an unranked letter change is neither.

A class-only change (same cabin, different booking class) is left untested.
The flight-timeline summary count is also left untested: the screen reads
stats.totalUpgrades, and the route returns stats.upgrades, so that number is
not the cabin chip.

Run from the repository root:

    python3 -m pip install 'fastapi>=0.110,<1.0' 'structlog>=24.1,<26.0' 'pymongo>=4.6,<5.0' 'pydantic>=2.0,<3.0'
    npm install --prefix frontend
    npm install --no-save --package-lock=false --prefix frontend esbuild@0.25.5
    python3 backend/tests/test_false_upgrade_badge.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.api.routes import changes as changes_route
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import diff_passenger_list

# Cabin order implemented by diff_passenger_list.
CABIN_RANK = {"Y": 0, "J": 1, "F": 2}

FLIGHT_DATE = "2026-03-19"
ORIGIN = "BAH"


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        def sort_key(doc):
            value = doc.get(key)
            return (value is None, "" if value is None else value)

        self.docs.sort(key=sort_key, reverse=(direction == -1))
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        for doc in self.docs:
            yield deepcopy(doc)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def _get_path(self, doc, key):
        current = doc
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return None, False
            current = current[part]
        return current, True

    def _matches(self, doc, query):
        for key, expected in query.items():
            value, found = self._get_path(doc, key)
            if isinstance(expected, dict) and "$exists" in expected:
                if bool(found) != bool(expected["$exists"]):
                    return False
                continue
            if isinstance(expected, dict) and "$in" in expected:
                if value not in expected["$in"]:
                    return False
                continue
            if not found or value != expected:
                return False
        return True

    def find(self, query, projection=None):
        return FakeCursor([deepcopy(doc) for doc in self.docs if self._matches(doc, query)])

    def find_one(self, query, projection=None, sort=None, **kwargs):
        matched = [doc for doc in self.docs if self._matches(doc, query)]
        if sort:
            for field, direction in reversed(sort):
                matched.sort(
                    key=lambda doc: (
                        doc.get(field) is None,
                        "" if doc.get(field) is None else doc.get(field),
                    ),
                    reverse=(direction == -1),
                )
        if not matched:
            return None
        return deepcopy(matched[0])


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = FakeCollection()
        return dict.__getitem__(self, name)


class Clock:
    def __init__(self):
        self._tick = 0

    def iso(self):
        self._tick += 1
        return f"2026-03-19T12:00:{self._tick:02d}Z"


def _raw_passenger_list(cabin, pnr, booking_class="Y"):
    """Sabre GetPassengerList shape consumed by convert_passenger_list."""
    return {
        "PassengerInfoList": {
            "PassengerInfo": {
                "Name_Details": {"LastName": "SMITH", "FirstName": "JANE"},
                "PNRLocator": {"#text": pnr},
                "BookingClass": booking_class,
                "Cabin": cabin,
                "Seat": "12A",
                "PassengerType": "F",
                "BagCount": "0",
                "LineNumber": "1",
            }
        }
    }


def _convert(cabin, flight_number, pnr):
    return convert_passenger_list(
        _raw_passenger_list(cabin, pnr),
        "GF",
        flight_number,
        FLIGHT_DATE,
        ORIGIN,
    )


def _store_snapshot(db, flight_number, converted, sequence, fetched_at):
    db["snapshots"].docs.append({
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": FLIGHT_DATE,
        "snapshotType": "passenger_list",
        "sequenceNumber": sequence,
        "fetchedAt": fetched_at,
        "data": converted,
    })


def _ingest(flight_number, pnr, cabins, include_initial_add):
    """Replay passenger-list snapshots the feeder would diff.

    include_initial_add mirrors a passenger who appears after tracking has
    already stored a previous manifest. Without it, the passenger is already
    on the first stored snapshot and no PASSENGER_ADDED record is written.
    """
    clock = Clock()
    db = FakeDB()
    flight_info = {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": FLIGHT_DATE,
    }
    manifests = [_convert(cabin, flight_number, pnr) for cabin in cabins]
    sequence = 1
    with patch("backend.feeder.differ._now_iso", clock.iso):
        if include_initial_add:
            empty = {
                "snapshotId": f"{flight_number}-empty",
                "data": {"passengers": []},
            }
            added = manifests[0]
            added_snap = {"snapshotId": f"{flight_number}-1", "data": added}
            db["changes"].docs.extend(diff_passenger_list(empty, added_snap, flight_info))
            _store_snapshot(db, flight_number, added, sequence, clock.iso())
            previous = added_snap
            sequence += 1
            later = manifests[1:]
        else:
            previous = {"snapshotId": f"{flight_number}-1", "data": manifests[0]}
            _store_snapshot(db, flight_number, manifests[0], sequence, clock.iso())
            sequence += 1
            later = manifests[1:]

        for offset, manifest in enumerate(later, start=sequence):
            after = {"snapshotId": f"{flight_number}-{offset}", "data": manifest}
            db["changes"].docs.extend(diff_passenger_list(previous, after, flight_info))
            _store_snapshot(db, flight_number, manifest, offset, clock.iso())
            previous = after

    with patch.object(changes_route, "get_db", return_value=db):
        timeline = changes_route.get_passenger_timeline(
            flight_number, pnr, date=FLIGHT_DATE, origin=ORIGIN,
        )
        badges = changes_route.get_passengers_history_badges(
            flight_number, origin=ORIGIN, date=FLIGHT_DATE,
        )
        flight_timeline = changes_route.get_flight_timeline(
            flight_number, origin=ORIGIN, date=FLIGHT_DATE,
        )
        activity = changes_route.get_activity_feed(
            date=FLIGHT_DATE, limit=50, categories=None,
        )
    return timeline, badges, flight_timeline, activity


_BUNDLE = None


def _bundle_renderer():
    global _BUNDLE
    if _BUNDLE and os.path.exists(_BUNDLE):
        return _BUNDLE
    esbuild_bin = os.path.join(ROOT, "frontend", "node_modules", "esbuild", "package.json")
    if not os.path.exists(esbuild_bin):
        subprocess.run(
            [
                "npm", "install", "--no-save", "--package-lock=false",
                "--prefix", os.path.join(ROOT, "frontend"), "esbuild@0.25.5",
            ],
            check=True,
            cwd=ROOT,
        )
    completed = subprocess.run(
        ["node", os.path.join(ROOT, "frontend", "tests", "bundle-render.mjs")],
        check=True,
        cwd=os.path.join(ROOT, "frontend"),
        capture_output=True,
        text=True,
    )
    _BUNDLE = completed.stdout.strip().splitlines()[-1]
    return _BUNDLE


def _render(timeline=None, flight_timeline=None, activity=None, flight_number="2057", pnr="AB12CD"):
    bundle = _bundle_renderer()
    payload = {}
    if timeline is not None:
        payload["passenger"] = {
            "flightNumber": flight_number,
            "pnr": pnr,
            "origin": ORIGIN,
            "date": FLIGHT_DATE,
            "data": timeline,
        }
    if flight_timeline is not None:
        payload["flight"] = {
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "date": FLIGHT_DATE,
            "data": flight_timeline,
        }
    if activity is not None:
        payload["activity"] = {
            "date": FLIGHT_DATE,
            "limit": 50,
            "data": activity,
        }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(payload, handle)
        payload_path = handle.name
    completed = subprocess.run(
        ["node", bundle, payload_path],
        check=True,
        cwd=os.path.join(ROOT, "frontend"),
        capture_output=True,
        text=True,
    )
    os.unlink(payload_path)
    return json.loads(completed.stdout)


def _arrows(html):
    return {
        "up": "lucide-arrow-up" in html,
        "down": "lucide-arrow-down" in html,
    }


def _assert_ranked_chip(status_html, original, current):
    assert current in status_html, status_html
    arrows = _arrows(status_html)
    original_rank = CABIN_RANK.get(original)
    current_rank = CABIN_RANK.get(current)
    if original_rank is None or current_rank is None or original_rank == current_rank:
        assert arrows == {"up": False, "down": False}, (original, current, arrows)
        assert f"from {original}" not in status_html
        return
    assert original in status_html
    if current_rank > original_rank:
        assert arrows == {"up": True, "down": False}, (original, current, arrows)
    else:
        assert arrows == {"up": False, "down": True}, (original, current, arrows)


def test_downgrade_from_added_cabin_is_not_drawn_as_an_upgrade():
    """Business (J) added, then economy (Y): the History chip must point down."""
    timeline, badges, flight_timeline, activity = _ingest(
        "2057", "JTHENY", ["J", "Y"], include_initial_add=True,
    )
    assert timeline["originalBooking"]["cabin"] == "J"
    assert timeline["currentState"]["cabin"] == "Y"
    cabin_events = [event for event in timeline["events"] if event["changeType"] == "CABIN_CHANGE"]
    assert len(cabin_events) == 1
    assert cabin_events[0]["details"]["oldValue"] == "J"
    assert cabin_events[0]["details"]["newValue"] == "Y"
    assert cabin_events[0]["upgradeInfo"]["direction"] == "DOWNGRADE"

    rendered = _render(timeline, flight_timeline, activity, "2057", "JTHENY")
    status_html = rendered["passenger"]["statusHtml"]
    event_html = rendered["passenger"]["eventHtml"]
    assert "Current Status" in status_html
    assert "Cabin downgrade: J → Y" in event_html
    _assert_ranked_chip(status_html, "J", "Y")
    assert "Upgrade" not in event_html, event_html
    assert _arrows(event_html)["down"] is True

    flight_html = rendered["flight"]["html"]
    assert "Cabin downgrade: J → Y" in flight_html
    assert _arrows(flight_html) == {"up": False, "down": True}, flight_html

    activity_html = rendered["activity"]["html"]
    assert "Cabin downgrade: J → Y" in activity_html
    assert _arrows(activity_html) == {"up": False, "down": True}, activity_html
    assert badges["JTHENY"]["hasUpgrade"] is False


def test_upgrade_from_added_cabin_keeps_the_up_arrow():
    timeline, badges, flight_timeline, activity = _ingest(
        "2058", "YTHENJ", ["Y", "J"], include_initial_add=True,
    )
    assert timeline["originalBooking"]["cabin"] == "Y"
    assert timeline["currentState"]["cabin"] == "J"
    assert badges["YTHENJ"]["hasUpgrade"] is True
    rendered = _render(timeline, flight_timeline, activity, "2058", "YTHENJ")
    _assert_ranked_chip(rendered["passenger"]["statusHtml"], "Y", "J")
    assert _arrows(rendered["passenger"]["eventHtml"])["up"] is True
    assert _arrows(rendered["flight"]["html"]) == {"up": True, "down": False}
    assert _arrows(rendered["activity"]["html"]) == {"up": True, "down": False}


def test_first_to_business_and_business_to_first_follow_rank():
    up_timeline, _, _, _ = _ingest("2059", "YTHENF", ["Y", "F"], include_initial_add=True)
    up_rendered = _render(up_timeline, flight_number="2059", pnr="YTHENF")
    _assert_ranked_chip(up_rendered["passenger"]["statusHtml"], "Y", "F")

    down_timeline, down_badges, _, _ = _ingest(
        "2060", "FTHENJ", ["F", "J"], include_initial_add=True,
    )
    down_rendered = _render(down_timeline, flight_number="2060", pnr="FTHENJ")
    _assert_ranked_chip(down_rendered["passenger"]["statusHtml"], "F", "J")
    assert down_badges["FTHENJ"]["hasUpgrade"] is False


def test_round_trip_above_then_below_original_is_a_downgrade():
    """Added in J, moved to F, then to Y. The chip compares J with Y."""
    timeline, _, _, _ = _ingest(
        "2061", "JFYDOWN", ["J", "F", "Y"], include_initial_add=True,
    )
    assert timeline["originalBooking"]["cabin"] == "J"
    assert timeline["currentState"]["cabin"] == "Y"
    directions = [
        event["upgradeInfo"]["direction"]
        for event in timeline["events"]
        if event["changeType"] == "CABIN_CHANGE"
    ]
    assert directions == ["UPGRADE", "DOWNGRADE"]
    rendered = _render(timeline, flight_number="2061", pnr="JFYDOWN")
    _assert_ranked_chip(rendered["passenger"]["statusHtml"], "J", "Y")


def test_same_cabin_draws_no_arrow():
    timeline, badges, _, _ = _ingest(
        "2062", "STILLJ", ["J", "J"], include_initial_add=True,
    )
    assert timeline["originalBooking"]["cabin"] == "J"
    assert timeline["currentState"]["cabin"] == "J"
    assert badges["STILLJ"]["hasUpgrade"] is False
    rendered = _render(timeline, flight_number="2062", pnr="STILLJ")
    status_html = rendered["passenger"]["statusHtml"]
    assert "J" in status_html
    assert _arrows(status_html) == {"up": False, "down": False}


def test_unranked_cabin_letters_draw_no_arrow():
    timeline, _, _, _ = _ingest(
        "2063", "WTOS", ["W", "S"], include_initial_add=True,
    )
    assert timeline["originalBooking"]["cabin"] == "W"
    assert timeline["currentState"]["cabin"] == "S"
    rendered = _render(timeline, flight_number="2063", pnr="WTOS")
    status_html = rendered["passenger"]["statusHtml"]
    assert "S" in status_html
    _assert_ranked_chip(status_html, "W", "S")


def test_manifest_without_an_add_record_does_not_invent_an_upgrade():
    """A passenger already on the first snapshot has no stored original cabin."""
    timeline, _, _, _ = _ingest(
        "2064", "ALREADY", ["J", "Y"], include_initial_add=False,
    )
    assert timeline["originalBooking"]["cabin"] is None
    assert timeline["currentState"]["cabin"] == "Y"
    assert any(event["changeType"] == "CABIN_CHANGE" for event in timeline["events"])
    rendered = _render(timeline, flight_number="2064", pnr="ALREADY")
    status_html = rendered["passenger"]["statusHtml"]
    assert "Y" in status_html
    assert _arrows(status_html) == {"up": False, "down": False}


def _run_all():
    tests = [
        test_downgrade_from_added_cabin_is_not_drawn_as_an_upgrade,
        test_upgrade_from_added_cabin_keeps_the_up_arrow,
        test_first_to_business_and_business_to_first_follow_rank,
        test_round_trip_above_then_below_original_is_a_downgrade,
        test_same_cabin_draws_no_arrow,
        test_unranked_cabin_letters_draw_no_arrow,
        test_manifest_without_an_add_record_does_not_invent_an_upgrade,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    if failed:
        print(f"{failed} test(s) failed")
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_run_all())
