"""Spec for informal-cabin process audit on a shared record locator.

These tests fail while cabin-change and class-change events are matched by
record locator alone. They pass once each event is matched by locator plus
the passenger name already stored on the change, with one alert per traveler.

Run:
    python -m unittest backend.tests.test_informal_cabin_locator -v
"""

import copy
import unittest
from unittest.mock import patch

from backend.api.audit_rules import run_audit
from backend.api.routes.audit import get_flight_audit
from backend.feeder import storage
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BAH",
    "departureDate": "2026-04-12",
}


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    """Equality-matched documents, in insertion order until sort() is called.

    The audit route asks MongoDB only for documents whose flightNumber,
    origin, and departureDate equal the request, then drops _id. Those
    documents are the dicts store_changes inserted; nothing else is rewritten.
    """

    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        def sort_key(doc):
            value = doc.get(key, None)
            return (value is None, "" if value is None else value)

        self._docs.sort(key=sort_key, reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self._docs = []
        self._next_id = 1

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", self._next_id)
        self._next_id += 1
        self._docs.append(stored)
        return _InsertOneResult(stored["_id"])

    def insert_many(self, docs):
        return _InsertManyResult([self.insert_one(doc).inserted_id for doc in docs])

    def _matches(self, doc, query):
        return all(doc.get(key) == value for key, value in query.items())

    def _project(self, doc, projection):
        projected = copy.deepcopy(doc)
        if projection and projection.get("_id") == 0:
            projected.pop("_id", None)
        return projected

    def find_one(self, query, sort=None, projection=None):
        matches = [self._project(doc, projection) for doc in self._docs if self._matches(doc, query)]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc, field=field: (
                        doc.get(field) is None,
                        "" if doc.get(field) is None else doc.get(field),
                    ),
                    reverse=direction < 0,
                )
        return matches[0] if matches else None

    def find(self, query, projection=None):
        return _Cursor(
            [self._project(doc, projection) for doc in self._docs if self._matches(doc, query)]
        )


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        self._collections.setdefault(name, _Collection())
        return self._collections[name]


def _passenger(last, first, pnr, cabin, booking_class, line):
    """One PassengerInfo element, in the shape convert_passenger_list reads."""
    return {
        "Name_Details": {"LastName": last, "FirstName": first},
        "PNRLocator": {"@nameAssociationID": line, "#text": pnr},
        "LineNumber": line,
        "BookingClass": booking_class,
        "Cabin": cabin,
        "Seat": "",
        "PassengerType": "F",
        "BagCount": "0",
        "PriorityCode": "",
        "DesiredBookingClass": "",
        "CorpID": "",
        "CheckIn_Info": {"CheckInStatus": "false"},
        "Boarding_Info": {"BoardStatus": "false"},
        "BoardingPassFlag": "false",
    }


def _manifest(passengers):
    raw = {
        "ItineraryInfo": {
            "Itinerary": {"Destination": "LHR"},
            "CabinInfoList": {"CabinInfo": []},
        },
        "PassengerInfoList": {"PassengerInfo": passengers},
    }
    document = convert_passenger_list(
        raw,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["departureDate"],
        FLIGHT["origin"],
    )
    document["fetchedAt"] = "2026-04-12T12:00:00Z"
    return document


def _informal(result):
    rows = []
    for alert in result["alerts"]:
        if alert.get("ruleId") != "INFORMAL_CABIN_MOVE":
            continue
        details = alert.get("details") or {}
        rows.append({
            "passengerName": alert.get("passengerName"),
            "pnr": alert.get("pnr"),
            "severity": alert.get("severity"),
            "currentCabin": details.get("currentCabin"),
            "bookingClass": details.get("bookingClass"),
        })
    return sorted(rows, key=lambda row: (row["pnr"] or "", row["passengerName"] or ""))


class InformalCabinLocatorTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        self._previous_db = storage._db
        self._previous_owns = storage._owns_connection
        storage.init_db(self.db)
        self._get_db = patch("backend.api.routes.audit.get_db", return_value=self.db)
        self._get_db.start()

    def tearDown(self):
        self._get_db.stop()
        storage._db = self._previous_db
        storage._owns_connection = self._previous_owns

    def _audit(self, manifests):
        """Diff consecutive manifests the way ingestion does, then open the audit."""
        flight_info = {
            "airline": FLIGHT["airline"],
            "flightNumber": FLIGHT["flightNumber"],
            "origin": FLIGHT["origin"],
            "departureDate": FLIGHT["departureDate"],
        }
        all_changes = []
        for index in range(len(manifests) - 1):
            changes = detect_changes(
                {"snapshotId": f"snap-{index}", "data": manifests[index]},
                {"snapshotId": f"snap-{index + 1}", "data": manifests[index + 1]},
                "passenger_list",
                flight_info,
            )
            all_changes.extend(changes)
            storage.store_changes(changes)

        storage.store_passenger_list(manifests[-1])
        audited = get_flight_audit(
            FLIGHT["flightNumber"],
            origin=FLIGHT["origin"],
            date=FLIGHT["departureDate"],
        )
        direct = run_audit(manifests[-1], None, all_changes)
        self.assertEqual(_informal(audited), _informal(direct))
        self.assertEqual(audited["flightNumber"], FLIGHT["flightNumber"])
        return all_changes, audited

    def test_class_change_on_shared_locator_does_not_clear_another_travelers_cabin_change(self):
        """A class change by one traveler must not cancel a companion's cabin alert.

        NOOR's cabin moves Y→J and her booking class stays Y. HASSAN, on the
        same locator and later on the manifest, only changes booking class.
        KAMAL, on a different locator, is the control: his cabin-only change
        is reported today.
        """
        before = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "Y", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "2"),
            _passenger("KAMAL", "OMAR", "ZZZ999", "Y", "M", "3"),
        ])
        after = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "J", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "Y", "B", "2"),
            _passenger("KAMAL", "OMAR", "ZZZ999", "J", "M", "3"),
        ])

        changes, audited = self._audit([before, after])
        cabin_names = sorted(
            (c["passenger"]["pnr"], c["passenger"]["lastName"], c["passenger"]["firstName"])
            for c in changes if c["changeType"] == "CABIN_CHANGE"
        )
        class_names = sorted(
            (c["passenger"]["pnr"], c["passenger"]["lastName"], c["passenger"]["firstName"])
            for c in changes if c["changeType"] == "CLASS_CHANGE"
        )
        self.assertEqual(cabin_names, [("ABC123", "NOOR", "ALI"), ("ZZZ999", "KAMAL", "OMAR")])
        self.assertEqual(class_names, [("ABC123", "HASSAN", "SARA")])

        self.assertEqual(_informal(audited), [
            {
                "passengerName": "NOOR/ALI",
                "pnr": "ABC123",
                "severity": "critical",
                "currentCabin": "J",
                "bookingClass": "Y",
            },
            {
                "passengerName": "KAMAL/OMAR",
                "pnr": "ZZZ999",
                "severity": "critical",
                "currentCabin": "J",
                "bookingClass": "M",
            },
        ])

    def test_informal_alert_names_the_traveler_who_moved_when_a_companion_is_later(self):
        """The alert names the traveler on the cabin-change event, not the last row."""
        before = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "Y", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "2"),
        ])
        after = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "J", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "2"),
        ])

        changes, audited = self._audit([before, after])
        cabin = [c for c in changes if c["changeType"] == "CABIN_CHANGE"]
        self.assertEqual(len(cabin), 1)
        self.assertEqual(cabin[0]["passenger"], {
            "pnr": "ABC123",
            "lastName": "NOOR",
            "firstName": "ALI",
        })
        self.assertFalse(any(c["changeType"] == "CLASS_CHANGE" for c in changes))

        self.assertEqual(_informal(audited), [
            {
                "passengerName": "NOOR/ALI",
                "pnr": "ABC123",
                "severity": "critical",
                "currentCabin": "J",
                "bookingClass": "Y",
            },
        ])

    def test_informal_alert_names_the_traveler_who_moved_when_they_are_last(self):
        """Row order must not decide the name. The moved traveler is last here."""
        before = _manifest([
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "1"),
            _passenger("NOOR", "ALI", "ABC123", "Y", "Y", "2"),
        ])
        after = _manifest([
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "1"),
            _passenger("NOOR", "ALI", "ABC123", "J", "Y", "2"),
        ])

        _, audited = self._audit([before, after])
        self.assertEqual(_informal(audited), [
            {
                "passengerName": "NOOR/ALI",
                "pnr": "ABC123",
                "severity": "critical",
                "currentCabin": "J",
                "bookingClass": "Y",
            },
        ])

    def test_each_traveler_on_a_locator_gets_their_own_informal_alert(self):
        """Two cabin changes and no class change are two alerts, not one row."""
        before = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "Y", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "2"),
        ])
        after = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "J", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "F", "Y", "2"),
        ])

        changes, audited = self._audit([before, after])
        self.assertEqual(
            sorted(c["passenger"]["lastName"] for c in changes if c["changeType"] == "CABIN_CHANGE"),
            ["HASSAN", "NOOR"],
        )
        self.assertFalse(any(c["changeType"] == "CLASS_CHANGE" for c in changes))
        self.assertEqual(_informal(audited), [
            {
                "passengerName": "HASSAN/SARA",
                "pnr": "ABC123",
                "severity": "critical",
                "currentCabin": "F",
                "bookingClass": "Y",
            },
            {
                "passengerName": "NOOR/ALI",
                "pnr": "ABC123",
                "severity": "critical",
                "currentCabin": "J",
                "bookingClass": "Y",
            },
        ])

    def test_later_class_change_clears_only_that_traveler(self):
        """Change history is cumulative. A later class change covers one traveler."""
        first = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "Y", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "2"),
        ])
        second = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "J", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "F", "Y", "2"),
        ])
        third = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "J", "D", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "F", "Y", "2"),
        ])

        _, audited = self._audit([first, second, third])
        self.assertEqual(_informal(audited), [
            {
                "passengerName": "HASSAN/SARA",
                "pnr": "ABC123",
                "severity": "critical",
                "currentCabin": "F",
                "bookingClass": "Y",
            },
        ])

    def test_same_traveler_cabin_and_class_change_is_not_informal(self):
        """A traveler whose own booking class changed is a processed move."""
        before = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "Y", "Y", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "2"),
        ])
        after = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "J", "D", "1"),
            _passenger("HASSAN", "SARA", "ABC123", "Y", "Y", "2"),
        ])

        changes, audited = self._audit([before, after])
        noor = [
            c["changeType"] for c in changes
            if c.get("passenger", {}).get("lastName") == "NOOR"
        ]
        self.assertIn("CABIN_CHANGE", noor)
        self.assertIn("CLASS_CHANGE", noor)
        self.assertEqual(_informal(audited), [])

    def test_single_traveler_informal_cabin_move_is_reported(self):
        """A locator with one traveler still reports a cabin change and no class change."""
        before = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "Y", "Y", "1"),
        ])
        after = _manifest([
            _passenger("NOOR", "ALI", "ABC123", "J", "Y", "1"),
        ])

        _, audited = self._audit([before, after])
        self.assertEqual(_informal(audited), [
            {
                "passengerName": "NOOR/ALI",
                "pnr": "ABC123",
                "severity": "critical",
                "currentCabin": "J",
                "bookingClass": "Y",
            },
        ])


if __name__ == "__main__":
    unittest.main()
