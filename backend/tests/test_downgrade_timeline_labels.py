"""Downgraded cabin and booking-class moves must not be labeled upgrades.

The product path under test is the one an operator's History tab reads:

  Sabre passenger fields
    -> convert_passenger_list
    -> store_snapshot (two consecutive passenger_list snapshots)
    -> detect_changes / diff_passenger_list
    -> store_changes
    -> get_passenger_timeline, get_flight_timeline, get_activity_feed

Booking-class direction uses the class order already encoded in this repo:

  rank 2  F
          Cabin F is above cabin J. diff_passenger_list treats Y->J, Y->F,
          and J->F as the only upgrades (backend/feeder/differ.py).
  rank 1  J, C, D, I, R
          Business letters used to tell an upgrade from a downgrade
          (backend/api/routes/flights.py, classMismatch).
  rank 1  Z
          Dashboard BUSINESS_CLASSES (frontend flight-insight-tabs.tsx).
  rank 0  Y, B, H, K, W, V, S, Q, N, O, X, G, M, L, U, T
          Dashboard ECONOMY_CLASSES (same file).

A higher rank is a higher class. Equal ranks, and letters in none of these
sets, are not an upgrade and not a downgrade. Cabin direction stays the
rule already in diff_passenger_list: Y->J, Y->F, and J->F are UPGRADE;
every other cabin change is DOWNGRADE.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import changes as changes_api
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.feeder import storage

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"
AIRLINE = "GF"
FLIGHT_INFO = {
    "flightNumber": FLIGHT,
    "origin": ORIGIN,
    "departureDate": DATE,
}

# Rank table documented in the module docstring.
_RANK = {}
for _letter in ("Y", "B", "H", "K", "W", "V", "S", "Q", "N", "O", "X", "G", "M", "L", "U", "T"):
    _RANK[_letter] = 0
for _letter in ("J", "C", "D", "I", "R", "Z"):
    _RANK[_letter] = 1
_RANK["F"] = 2


def _class_direction(old_class, new_class):
    old_rank = _RANK.get(old_class)
    new_rank = _RANK.get(new_class)
    if old_rank is None or new_rank is None or old_rank == new_rank:
        return None
    return "UPGRADE" if new_rank > old_rank else "DOWNGRADE"


def _passenger(pnr, cabin, booking_class, desired=""):
    body = {
        "PNRLocator": {"#text": pnr},
        "Name_Details": {"LastName": pnr, "FirstName": "A"},
        "Cabin": cabin,
        "BookingClass": booking_class,
        "Seat": "12A",
        "BagCount": "1",
        "PassengerType": "F",
        "CheckIn_Info": {"CheckInStatus": "false"},
        "Boarding_Info": {"BoardStatus": "false"},
    }
    if desired:
        body["DesiredBookingClass"] = desired
    return body


def _raw(passengers):
    return {"PassengerInfoList": {"PassengerInfo": passengers}}


class _InsertResult:
    def __init__(self, ids):
        self.inserted_ids = ids


def _get_path(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _match(doc, query):
    for key, expected in (query or {}).items():
        actual = _get_path(doc, key)
        if isinstance(expected, dict):
            for op, value in expected.items():
                if op == "$in":
                    if actual not in value:
                        return False
                elif op == "$lte":
                    if actual is None or actual > value:
                        return False
                else:
                    raise AssertionError(f"fake mongo does not implement {op}")
        elif actual != expected:
            return False
    return True


def _sort_value(value):
    return (value is None, "" if value is None else value)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=None):
        specs = key if isinstance(key, list) and direction is None else [(key, direction)]
        for field, direc in reversed(specs):
            self._docs.sort(
                key=lambda doc, field=field: _sort_value(_get_path(doc, field)),
                reverse=direc == -1,
            )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


def _project(doc, projection):
    if not projection:
        return doc
    excluding = any(value == 0 for value in projection.values())
    if excluding:
        hidden = {key for key, value in projection.items() if value == 0}
        return {key: value for key, value in doc.items() if key not in hidden}
    kept = {key: doc[key] for key in projection if key in doc}
    if "_id" in doc and projection.get("_id", 1) != 0:
        kept["_id"] = doc["_id"]
    return kept


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(doc)
        return _InsertResult([doc.get("_id")])

    def insert_many(self, docs):
        self.docs.extend(docs)
        return _InsertResult([None] * len(docs))

    def find(self, query=None, projection=None, sort=None, **kwargs):
        matched = [_project(doc, projection) for doc in self.docs if _match(doc, query)]
        cursor = _Cursor(matched)
        if sort:
            cursor.sort(sort)
        return cursor

    def find_one(self, query=None, projection=None, sort=None, **kwargs):
        found = list(self.find(query, projection=projection, sort=sort))
        return found[0] if found else None


class _Database(dict):
    def __getitem__(self, name):
        if name not in self:
            dict.__setitem__(self, name, _Collection())
        return dict.__getitem__(self, name)


def _install_db():
    database = _Database()
    storage.init_db(database)
    changes_api.get_db = lambda: database
    return database


def _ingest_pair(before_passengers, after_passengers):
    """Run the feeder's snapshot, diff, and change-store steps."""
    before = convert_passenger_list(
        _raw(before_passengers), AIRLINE, FLIGHT, DATE, ORIGIN)
    after = convert_passenger_list(
        _raw(after_passengers), AIRLINE, FLIGHT, DATE, ORIGIN)

    storage.store_snapshot(
        "req-before", AIRLINE, FLIGHT, ORIGIN, DATE, "passenger_list", before)
    after_id, _checksum, is_duplicate = storage.store_snapshot(
        "req-after", AIRLINE, FLIGHT, ORIGIN, DATE, "passenger_list", after)
    assert is_duplicate is False

    # Same branch backend/feeder/runner.py takes after a non-duplicate snapshot.
    previous = storage.get_previous_snapshot(FLIGHT, ORIGIN, DATE, "passenger_list")
    assert previous is not None
    detected = detect_changes(
        previous,
        {"snapshotId": after_id, "data": after},
        "passenger_list",
        FLIGHT_INFO,
    )
    stored = storage.store_changes(detected)
    assert stored == len(detected)
    return detected


def _one(changes, pnr, change_type):
    hits = [
        change for change in changes
        if change.get("changeType") == change_type
        and change.get("passenger", {}).get("pnr") == pnr
    ]
    assert len(hits) == 1, f"expected one {change_type} for {pnr}, found {len(hits)}"
    return hits[0]


def _timeline(pnr):
    return changes_api.get_passenger_timeline(FLIGHT, pnr, date=DATE, origin=ORIGIN)


def _event(timeline, change_type):
    hits = [event for event in timeline["events"] if event["changeType"] == change_type]
    assert len(hits) == 1, f"expected one {change_type} event, found {len(hits)}"
    return hits[0]


def _assert_labeled(change, event, direction):
    """Stored metadata, timeline category, and timeline direction agree."""
    stored_direction = (change.get("metadata") or {}).get("direction")
    shown_direction = (event.get("upgradeInfo") or {}).get("direction")
    if direction is None:
        assert stored_direction not in ("UPGRADE", "DOWNGRADE"), change
        assert shown_direction not in ("UPGRADE", "DOWNGRADE"), event
        assert event["category"] not in ("upgrade", "downgrade"), event
        return
    assert stored_direction == direction, change
    assert shown_direction == direction, event
    expected_category = "upgrade" if direction == "UPGRADE" else "downgrade"
    assert event["category"] == expected_category, event


def test_cabin_and_class_moves_keep_their_direction_through_history():
    original_get_db = changes_api.get_db
    original_storage_db = storage._db
    _install_db()
    try:
        _assert_history_labels()
    finally:
        changes_api.get_db = original_get_db
        storage._db = original_storage_db


def _assert_history_labels():
    before = [
        _passenger("DWNCAB", "J", "D"),
        _passenger("UPGCAB", "Y", "V"),
        _passenger("DWNF", "J", "F"),
        _passenger("UPGJ", "Y", "V"),
        _passenger("SAME", "Y", "V"),
        _passenger("UNK", "Y", "9"),
        _passenger("ZDOWN", "J", "Z"),
        _passenger("IDOWN", "J", "I"),
        _passenger("CONF", "Y", "V", desired="D"),
    ]
    after = [
        _passenger("DWNCAB", "Y", "V"),
        _passenger("UPGCAB", "J", "D"),
        _passenger("DWNF", "J", "D"),
        _passenger("UPGJ", "Y", "J"),
        _passenger("SAME", "Y", "W"),
        _passenger("UNK", "Y", "8"),
        _passenger("ZDOWN", "J", "V"),
        _passenger("IDOWN", "J", "V"),
        _passenger("CONF", "Y", "D"),
    ]
    detected = _ingest_pair(before, after)

    expectations = [
        ("DWNCAB", "CABIN_CHANGE", "DOWNGRADE"),
        ("DWNCAB", "CLASS_CHANGE", _class_direction("D", "V")),
        ("UPGCAB", "CABIN_CHANGE", "UPGRADE"),
        ("UPGCAB", "CLASS_CHANGE", _class_direction("V", "D")),
        ("DWNF", "CLASS_CHANGE", _class_direction("F", "D")),
        ("UPGJ", "CLASS_CHANGE", _class_direction("V", "J")),
        ("SAME", "CLASS_CHANGE", _class_direction("V", "W")),
        ("UNK", "CLASS_CHANGE", _class_direction("9", "8")),
        ("ZDOWN", "CLASS_CHANGE", _class_direction("Z", "V")),
        ("IDOWN", "CLASS_CHANGE", _class_direction("I", "V")),
        ("CONF", "CLASS_CHANGE", _class_direction("V", "D")),
        ("CONF", "UPGRADE_CONFIRMED", "UPGRADE"),
    ]
    # The rank table itself must encode the cases this finding is about.
    assert _class_direction("D", "V") == "DOWNGRADE"
    assert _class_direction("F", "D") == "DOWNGRADE"
    assert _class_direction("Z", "V") == "DOWNGRADE"
    assert _class_direction("I", "V") == "DOWNGRADE"
    assert _class_direction("V", "D") == "UPGRADE"
    assert _class_direction("V", "J") == "UPGRADE"
    assert _class_direction("V", "W") is None
    assert _class_direction("9", "8") is None

    for pnr, change_type, direction in expectations:
        change = _one(detected, pnr, change_type)
        if change_type == "UPGRADE_CONFIRMED":
            # Confirmed achievement of the desired class is an upgrade.
            event = _event(_timeline(pnr), change_type)
            assert event["category"] == "upgrade", event
            continue
        event = _event(_timeline(pnr), change_type)
        _assert_labeled(change, event, direction)

    flight = changes_api.get_flight_timeline(FLIGHT, origin=ORIGIN, date=DATE)
    flight_events = [
        event for event in flight["events"]
        if event["eventType"] in ("CABIN_CHANGE", "CLASS_CHANGE", "UPGRADE_CONFIRMED")
    ]
    by_key = {
        (event["passenger"]["pnr"], event["eventType"]): event
        for event in flight_events
    }
    for pnr, change_type, direction in expectations:
        event = by_key[(pnr, change_type)]
        if change_type == "UPGRADE_CONFIRMED":
            assert event["category"] == "upgrade"
        elif direction == "UPGRADE":
            assert event["category"] == "upgrade"
        elif direction == "DOWNGRADE":
            assert event["category"] == "downgrade", event
        else:
            assert event["category"] not in ("upgrade", "downgrade"), event

    upgrade_like = [
        change for change in detected
        if change["changeType"] in ("CABIN_CHANGE", "CLASS_CHANGE", "UPGRADE_CONFIRMED")
    ]
    expected_upgrades = 0
    for change in upgrade_like:
        if change["changeType"] == "UPGRADE_CONFIRMED":
            expected_upgrades += 1
            continue
        direction = (change.get("metadata") or {}).get("direction")
        if direction == "UPGRADE":
            expected_upgrades += 1
    assert flight["stats"]["upgrades"] == expected_upgrades
    assert expected_upgrades == 5

    upgrade_feed = changes_api.get_activity_feed(
        date=DATE, limit=50, categories="upgrade")
    feed_keys = {
        (event["passenger"]["pnr"], event["eventType"])
        for event in upgrade_feed["events"]
    }
    for pnr, change_type, direction in expectations:
        key = (pnr, change_type)
        if direction == "UPGRADE" or change_type == "UPGRADE_CONFIRMED":
            assert key in feed_keys, key
        else:
            assert key not in feed_keys, key

    open_feed = changes_api.get_activity_feed(date=DATE, limit=50, categories=None)
    open_by_key = {
        (event["passenger"]["pnr"], event["eventType"]): event
        for event in open_feed["events"]
    }
    for pnr, change_type, direction in expectations:
        event = open_by_key[(pnr, change_type)]
        if direction == "DOWNGRADE":
            assert event["category"] == "downgrade", event
        elif direction == "UPGRADE" or change_type == "UPGRADE_CONFIRMED":
            assert event["category"] == "upgrade", event


if __name__ == "__main__":
    test_cabin_and_class_moves_keep_their_direction_through_history()
    print("ALL TESTS PASS")
