"""Passenger identity on the flight Activity timeline.

Check-in, boarding, seat changes, and upgrades are stored with a nested
passenger (last name, first name, record locator). The Activity tab and the
activity feed still render those events without that identity, and the
check-in, boarding, add, and remove descriptions are generic sentences.

These tests fail until each of those events names the traveler.

Run from the repository root, with the API dependencies importable
(fastapi, pydantic, pymongo, structlog):

    PYTHONPATH=. python3 -m backend.tests.test_timeline_passenger_identity

Reservation add, remove, and party-size changes are not covered. The
reservation differ does not attach a passenger to those records, so the
timeline has no name to show.
"""

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from backend.api.routes import changes as changes_api

_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name, relative):
    """Load a feeder module without importing backend.feeder (that pulls Sabre)."""
    spec = importlib.util.spec_from_file_location(name, _ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


storage = _load_module("falconeye_storage_under_test", "backend/feeder/storage.py")
differ = _load_module("falconeye_differ_under_test", "backend/feeder/differ.py")
detect_changes = differ.detect_changes

FLIGHT = {
    "flightNumber": "2057",
    "origin": "BAH",
    "departureDate": "2026-03-19",
}

# Descriptions that currently say only "Passenger ..." with no identity.
NAMED_DESCRIPTION_TYPES = {
    "CHECKED_IN",
    "BOARDED",
    "PASSENGER_ADDED",
    "PASSENGER_REMOVED",
}


def _pax(pnr, last_name, first_name, **overrides):
    passenger = {
        "pnr": pnr,
        "lastName": last_name,
        "firstName": first_name,
        "isCheckedIn": False,
        "isBoarded": False,
        "cabin": "Y",
        "bookingClass": "V",
        "seat": "21A",
        "bagCount": 0,
        "passengerType": "F",
        "priorityCode": "",
        "editCodes": [],
    }
    passenger.update(overrides)
    return passenger


def _passengers():
    """Before/after manifests. Each traveler changes in exactly one way."""
    removed = _pax("REM001", "FARID", "OMAR")
    added = _pax("ADD001", "HADDAD", "NOOR")
    checkin_before = _pax("CHK001", "QASIM", "HANA")
    checkin_after = _pax("CHK001", "QASIM", "HANA", isCheckedIn=True)
    boarded_before = _pax("BRD001", "SALEH", "RAMI", isCheckedIn=True)
    boarded_after = _pax("BRD001", "SALEH", "RAMI", isCheckedIn=True, isBoarded=True)
    seat_before = _pax("SEA001", "NASSER", "LINA", seat="12A")
    seat_after = _pax("SEA001", "NASSER", "LINA", seat="14C")
    upgrade_before = _pax("UPG001", "KHALID", "SARA", cabin="Y")
    upgrade_after = _pax("UPG001", "KHALID", "SARA", cabin="J")
    downgrade_before = _pax("DWN001", "JABER", "HUDA", cabin="J")
    downgrade_after = _pax("DWN001", "JABER", "HUDA", cabin="Y")
    class_before = _pax("CLS001", "DAOUD", "MAHA", bookingClass="V")
    class_after = _pax("CLS001", "DAOUD", "MAHA", bookingClass="C")
    bag_before = _pax("BAG001", "IBRAHIM", "YUSUF", bagCount=0)
    bag_after = _pax("BAG001", "IBRAHIM", "YUSUF", bagCount=2)
    confirmed_before = _pax(
        "UCF001", "FARES", "DANA", bookingClass="V", desiredBookingClass="J"
    )
    confirmed_after = _pax("UCF001", "FARES", "DANA", bookingClass="J")
    queue_before = _pax("PRI001", "HAMAD", "ZAIN", priorityCode="")
    queue_after = _pax("PRI001", "HAMAD", "ZAIN", priorityCode="UPG")
    loyalty_before = _pax("LOY001", "NAJIB", "RANA", editCodes=[])
    loyalty_after = _pax("LOY001", "NAJIB", "RANA", editCodes=["GLD"])
    document_before = _pax("DOC001", "TALEB", "AMAL", editCodes=[])
    document_after = _pax("DOC001", "TALEB", "AMAL", editCodes=["DOCS"])

    before = [
        removed,
        checkin_before,
        boarded_before,
        seat_before,
        upgrade_before,
        downgrade_before,
        class_before,
        bag_before,
        confirmed_before,
        queue_before,
        loyalty_before,
        document_before,
    ]
    after = [
        added,
        checkin_after,
        boarded_after,
        seat_after,
        upgrade_after,
        downgrade_after,
        class_after,
        bag_after,
        confirmed_after,
        queue_after,
        loyalty_after,
        document_after,
    ]
    return before, after


class _InsertResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        if isinstance(key, list):
            for field, order in reversed(key):
                self._docs.sort(key=lambda doc, field=field: doc.get(field) or "", reverse=order < 0)
        else:
            self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


def _lookup(doc, key):
    current = doc
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _match(doc, query):
    for key, expected in query.items():
        actual, found = _lookup(doc, key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif not found or actual != expected:
            return False
    return True


class _Collection:
    """Enough of a Mongo collection for the timeline and activity routes."""

    def __init__(self):
        self.docs = []

    def insert_many(self, documents):
        inserted_ids = []
        for document in documents:
            stored = copy.deepcopy(document)
            stored["_id"] = f"change-{len(self.docs)}"
            self.docs.append(stored)
            inserted_ids.append(stored["_id"])
        return _InsertResult(inserted_ids)

    def find(self, query=None, projection=None):
        query = query or {}
        matched = [copy.deepcopy(doc) for doc in self.docs if _match(doc, query)]
        return _Cursor(matched)

    def find_one(self, query=None, projection=None, sort=None):
        cursor = self.find(query, projection)
        if sort:
            cursor.sort(sort)
        rows = list(cursor)
        return rows[0] if rows else None


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        collection = self._collections.get(name)
        if collection is None:
            collection = _Collection()
            self._collections[name] = collection
        return collection


def _identity_missing(text, passenger):
    missing = [
        label
        for label, value in (
            ("lastName", passenger["lastName"]),
            ("firstName", passenger["firstName"]),
            ("pnr", passenger["pnr"]),
        )
        if value not in (text or "")
    ]
    return missing


def build_payloads():
    """Diff two manifests, store the changes, and read the live route functions."""
    before_passengers, after_passengers = _passengers()
    before_snap = {"snapshotId": "snap-before", "data": {"passengers": before_passengers}}
    after_snap = {"snapshotId": "snap-after", "data": {"passengers": after_passengers}}
    detected = detect_changes(before_snap, after_snap, "passenger_list", FLIGHT)

    database = _Database()
    original_get_db = changes_api.get_db
    storage.init_db(database)
    changes_api.get_db = lambda: database
    try:
        stored = storage.store_changes(detected)
        if stored != len(detected):
            raise AssertionError(f"store_changes kept {stored} of {len(detected)} changes")

        timeline = changes_api.get_flight_timeline(
            FLIGHT["flightNumber"],
            origin=FLIGHT["origin"],
            date=FLIGHT["departureDate"],
        )
        activity = changes_api.get_activity_feed(
            date=FLIGHT["departureDate"],
            limit=50,
            categories=None,
        )
        passenger_timelines = {}
        expected = []
        for change in detected:
            passenger = change.get("passenger")
            if not passenger:
                continue
            expected.append(
                {
                    "changeType": change["changeType"],
                    "pnr": passenger["pnr"],
                    "lastName": passenger["lastName"],
                    "firstName": passenger["firstName"],
                }
            )
            pnr = passenger["pnr"]
            if pnr not in passenger_timelines:
                passenger_timelines[pnr] = changes_api.get_passenger_timeline(
                    FLIGHT["flightNumber"],
                    pnr,
                    date=FLIGHT["departureDate"],
                    origin=FLIGHT["origin"],
                )
    finally:
        changes_api.get_db = original_get_db
        storage._db = None

    return {
        "timeline": timeline,
        "activity": activity,
        "passengerTimelines": passenger_timelines,
        "expectedPassengers": expected,
    }


class TimelinePassengerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.payload = build_payloads()

    def test_detected_passenger_events_reach_the_timeline(self):
        expected_types = {row["changeType"] for row in self.payload["expectedPassengers"]}
        for required in (
            "CHECKED_IN",
            "BOARDED",
            "PASSENGER_ADDED",
            "PASSENGER_REMOVED",
            "SEAT_CHANGE",
            "CABIN_CHANGE",
            "CLASS_CHANGE",
            "UPGRADE_CONFIRMED",
            "BAG_COUNT_CHANGE",
            "PRIORITY_CHANGE",
            "LOYALTY_STATUS_ADDED",
            "DOCUMENT_ADDED",
        ):
            self.assertIn(required, expected_types, required)
        self.assertGreaterEqual(len(self.payload["expectedPassengers"]), 12)

    def test_checkin_boarding_add_and_remove_descriptions_name_the_passenger(self):
        self._assert_named_descriptions(
            self.payload["timeline"]["events"],
            "flight timeline",
            type_field="eventType",
        )

    def test_activity_feed_descriptions_name_the_passenger(self):
        self._assert_named_descriptions(
            self.payload["activity"]["events"],
            "activity feed",
            type_field="eventType",
        )

    def test_passenger_timeline_descriptions_name_the_passenger(self):
        seen = set()
        for person in self.payload["expectedPassengers"]:
            if person["changeType"] not in NAMED_DESCRIPTION_TYPES:
                continue
            seen.add(person["changeType"])
            body = self.payload["passengerTimelines"][person["pnr"]]
            matched = [
                event
                for event in body["events"]
                if event.get("changeType") == person["changeType"]
            ]
            self.assertTrue(matched, person)
            for event in matched:
                missing = _identity_missing(event.get("description"), person)
                self.assertFalse(
                    missing,
                    f"passenger timeline {person['changeType']} description "
                    f"{event.get('description')!r} is missing {missing}",
                )
        self.assertEqual(seen, NAMED_DESCRIPTION_TYPES)

    def _assert_named_descriptions(self, events, label, type_field):
        by_type = {}
        for event in events:
            change_type = event.get(type_field)
            if change_type in NAMED_DESCRIPTION_TYPES:
                by_type.setdefault(change_type, []).append(event)

        self.assertEqual(set(by_type), NAMED_DESCRIPTION_TYPES, label)
        for change_type, rows in by_type.items():
            for event in rows:
                passenger = event.get("passenger") or {}
                missing = _identity_missing(event.get("description"), passenger)
                self.assertFalse(
                    missing,
                    f"{label} {change_type} description {event.get('description')!r} "
                    f"is missing {missing}; passenger={passenger}",
                )
                for field in ("lastName", "firstName", "pnr"):
                    self.assertTrue(passenger.get(field), f"{label} {change_type} has no {field}")


def main():
    if "--emit" in sys.argv:
        json.dump(build_payloads(), sys.stdout)
        return
    unittest.main()


if __name__ == "__main__":
    main()
