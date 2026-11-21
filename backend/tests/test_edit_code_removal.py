"""Document and loyalty edit-code removals must be stored and shown.

These tests fail while change detection records a travel-document or loyalty
edit code only when it appears, and drops the codes that disappeared.

They pass once a removal of DOCS, DOCA, DOCV, DCVI, FF, GLD, SLV, BLU, PLT,
or DIA is stored with that code as the old value, and the passenger timeline,
flight timeline, and activity feed each name the code that was removed.
"""

import copy
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import HTTPException

from backend.api.routes import changes as changes_api
from backend.feeder import storage
from backend.feeder.converter import convert_passenger_list
from backend.feeder.runner import _process_api_call
from backend.sabre.client import SabreClient

DOC_CODES = ("DOCS", "DOCA", "DOCV", "DCVI")
LOYALTY_CODES = ("FF", "GLD", "SLV", "BLU", "PLT", "DIA")
REMOVAL_TYPES = {
    "DOCUMENT_REMOVED": DOC_CODES,
    "LOYALTY_STATUS_REMOVED": LOYALTY_CODES,
}
ADDITION_TYPES = {
    "DOCUMENT_ADDED": DOC_CODES,
    "LOYALTY_STATUS_ADDED": LOYALTY_CODES,
}

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2006",
    "origin": "LHR",
    "departureDate": "2026-03-18",
}


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


def _path(doc, key):
    current = doc
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _matches(doc, query):
    for key, expected in (query or {}).items():
        value, present = _path(doc, key)
        if isinstance(expected, dict):
            if "$in" in expected and value not in expected["$in"]:
                return False
            if "$exists" in expected and bool(expected["$exists"]) != present:
                return False
            if "$lte" in expected and (not present or value > expected["$lte"]):
                return False
            if not any(op in expected for op in ("$in", "$exists", "$lte")):
                if value != expected:
                    return False
        elif value != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return doc
    excluded = [key for key, flag in projection.items() if flag == 0]
    if excluded:
        for key in excluded:
            doc.pop(key, None)
        return doc
    included = [key for key, flag in projection.items() if flag]
    kept = {}
    if "_id" in doc:
        kept["_id"] = doc["_id"]
    for key in included:
        if key in doc:
            kept[key] = doc[key]
    return kept


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=None):
        specs = key if isinstance(key, list) else [(key, 1 if direction is None else direction)]
        for field, dirn in reversed(specs):
            self._docs.sort(
                key=lambda doc, field=field: _path(doc, field)[0],
                reverse=int(dirn) < 0,
            )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", str(uuid.uuid4()))
        self.docs.append(stored)
        return _InsertOneResult(stored["_id"])

    def insert_many(self, docs):
        return _InsertManyResult([self.insert_one(doc).inserted_id for doc in docs])

    def find(self, query=None, projection=None, sort=None):
        matched = [_project(copy.deepcopy(doc), projection)
                   for doc in self.docs if _matches(doc, query)]
        cursor = _Cursor(matched)
        if sort:
            cursor.sort(sort)
        return cursor

    def find_one(self, query=None, projection=None, sort=None):
        found = list(self.find(query, projection=projection, sort=sort))
        return found[0] if found else None

    def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _matches(doc, query):
                self._apply(doc, update, inserting=False)
                return
        if not upsert:
            return
        created = {}
        for key, value in query.items():
            if not isinstance(value, dict):
                created[key] = copy.deepcopy(value)
        self._apply(created, update, inserting=True)
        self.insert_one(created)

    @staticmethod
    def _apply(doc, update, inserting):
        for key, value in (update.get("$set") or {}).items():
            doc[key] = copy.deepcopy(value)
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = doc.get(key, 0) + value
        if inserting:
            for key, value in (update.get("$setOnInsert") or {}).items():
                doc[key] = copy.deepcopy(value)


class _MemoryDB:
    """Stores the documents the feeder writes. It does not interpret edit codes."""

    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection())


def _passenger(last, first, pnr, passenger_id, codes):
    edits = ""
    if codes:
        body = "".join(f"<ns2:EditCode>{code}</ns2:EditCode>" for code in codes)
        edits = f"<EditCodeList>{body}</EditCodeList>"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>{passenger_id}</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>12A</Seat>
          <Destination>BAH</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
          {edits}
        </PassengerInfo>
    """


def _manifest(passengers):
    """SOAP body shaped like a GetPassengerListRS passenger list."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2006</Flight>
          <DepartureDate>2026-03-18</DepartureDate>
          <Origin>LHR</Origin>
          <Destination>BAH</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {''.join(passengers)}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_passenger_list(xml_text):
    """Same parse the Sabre client uses after GetPassengerListRQ returns."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(
        SabreClient.__new__(SabreClient), parsed, "GetPassengerListRS")


def _expected_events(change_type, codes, pnr):
    return {(change_type, code, pnr) for code in codes}


class EditCodeRemovalTests(unittest.TestCase):
    def setUp(self):
        self.db = _MemoryDB()
        storage.init_db(self.db)
        self._original_get_db = changes_api.get_db
        changes_api.get_db = lambda: self.db

    def tearDown(self):
        changes_api.get_db = self._original_get_db
        storage.close()

    def _ingest(self, xml_text):
        raw = _parse_passenger_list(xml_text)
        return _process_api_call(
            "PassengerList",
            "passenger_list",
            FLIGHT,
            raw,
            xml_text,
            {
                "requestXml": "<GetPassengerListRQ/>",
                "httpStatus": 200,
                "durationMs": 1,
            },
            convert_passenger_list,
            (raw, FLIGHT["airline"], FLIGHT["flightNumber"],
             FLIGHT["departureDate"], FLIGHT["origin"]),
        )

    def _manifests(self):
        tracked = ("NASSER", "LAYLA", "HLD001", "PAX1")
        single = ("SOLO", "AMAL", "HLD002", "PAX2")
        unrelated = ("PLAIN", "RAVI", "HLD003", "PAX3")
        steady = ("KEPT", "SARA", "HLD004", "PAX4")
        tracked_codes = ("ET",) + DOC_CODES + LOYALTY_CODES
        before = _manifest([
            _passenger(*tracked, ("ET",)),
            _passenger(*single, ()),
            _passenger(*unrelated, ("ET",)),
            _passenger(*steady, ("DOCS", "FF")),
        ])
        with_codes = _manifest([
            _passenger(*tracked, tracked_codes),
            _passenger(*single, ("DOCS",)),
            _passenger(*unrelated, ("ET",)),
            _passenger(*steady, ("DOCS", "FF")),
        ])
        without_codes = _manifest([
            _passenger(*tracked, ("ET",)),
            _passenger(*single, ()),
            _passenger(*unrelated, ()),
            _passenger(*steady, ("DOCS", "FF")),
        ])
        return before, with_codes, without_codes

    def test_dropped_document_and_loyalty_codes_are_stored_and_shown(self):
        before, with_codes, without_codes = self._manifests()
        self._ingest(before)
        added = self._ingest(with_codes)
        removed = self._ingest(without_codes)

        snapshots = list(self.db["snapshots"].find(
            {"snapshotType": "passenger_list"},
            sort=[("sequenceNumber", 1)],
        ))
        solo = next(
            pax for pax in snapshots[1]["data"]["passengers"]
            if pax["pnr"] == "HLD002"
        )
        self.assertEqual(solo["editCodes"], ["DOCS"])

        stored = list(self.db["changes"].find({}))
        added_events = {
            (change["changeType"], change.get("newValue"), change["passenger"]["pnr"])
            for change in stored
            if change["changeType"] in ADDITION_TYPES
        }
        removed_events = {
            (change["changeType"], change.get("oldValue"), change["passenger"]["pnr"])
            for change in stored
            if change["changeType"] in REMOVAL_TYPES
        }

        expected_added = set()
        expected_removed = set()
        for change_type, codes in ADDITION_TYPES.items():
            expected_added |= _expected_events(change_type, codes, "HLD001")
        expected_added |= _expected_events("DOCUMENT_ADDED", ("DOCS",), "HLD002")
        for change_type, codes in REMOVAL_TYPES.items():
            expected_removed |= _expected_events(change_type, codes, "HLD001")
        expected_removed |= _expected_events("DOCUMENT_REMOVED", ("DOCS",), "HLD002")

        self.assertEqual(added_events, expected_added)
        self.assertEqual(removed_events, expected_removed)
        self.assertEqual(removed["changesStored"], len(expected_removed))
        self.assertEqual(added["changesStored"], len(expected_added))

        for change in stored:
            if change["changeType"] not in REMOVAL_TYPES:
                continue
            self.assertEqual(change["field"], "editCodes")
            self.assertNotIn("newValue", change)
            self.assertNotIn(change["passenger"]["pnr"], {"HLD003", "HLD004"})

        passenger_timeline = changes_api.get_passenger_timeline(
            FLIGHT["flightNumber"], "HLD001",
            date=FLIGHT["departureDate"], origin=FLIGHT["origin"],
        )
        solo_timeline = changes_api.get_passenger_timeline(
            FLIGHT["flightNumber"], "HLD002",
            date=FLIGHT["departureDate"], origin=FLIGHT["origin"],
        )
        flight_timeline = changes_api.get_flight_timeline(
            FLIGHT["flightNumber"],
            origin=FLIGHT["origin"], date=FLIGHT["departureDate"],
        )
        feed = changes_api.get_activity_feed(
            date=FLIGHT["departureDate"], limit=50, categories=None,
        )

        self._assert_surfaces(
            passenger_timeline["events"],
            solo_timeline["events"],
            flight_timeline["events"],
            feed["events"],
        )

    def _assert_surfaces(self, passenger_events, solo_events, flight_events, feed_events):
        passenger_text = {
            (event["changeType"], event["category"], event["description"],
             event["details"].get("oldValue"), event["details"].get("newValue"))
            for event in passenger_events
        }
        for code in DOC_CODES:
            self.assertIn(
                ("DOCUMENT_ADDED", "document", f"Document verified: {code}", None, code),
                passenger_text,
            )
            self.assertIn(
                ("DOCUMENT_REMOVED", "document", f"Document removed: {code}", code, None),
                passenger_text,
            )
        for code in LOYALTY_CODES:
            self.assertIn(
                ("LOYALTY_STATUS_ADDED", "loyalty", f"Loyalty status added: {code}", None, code),
                passenger_text,
            )
            self.assertIn(
                ("LOYALTY_STATUS_REMOVED", "loyalty", f"Loyalty status removed: {code}", code, None),
                passenger_text,
            )

        solo_removed = [
            event for event in solo_events
            if event["changeType"] == "DOCUMENT_REMOVED"
        ]
        self.assertEqual(len(solo_removed), 1)
        self.assertEqual(solo_removed[0]["description"], "Document removed: DOCS")
        self.assertEqual(solo_removed[0]["details"]["oldValue"], "DOCS")
        self.assertEqual(solo_removed[0]["category"], "document")

        flight_descriptions = {event["description"] for event in flight_events}
        feed_descriptions = {event["description"] for event in feed_events}
        for code in DOC_CODES:
            wording = f"Document removed: {code}"
            self.assertIn(wording, flight_descriptions)
            self.assertIn(wording, feed_descriptions)
        for code in LOYALTY_CODES:
            wording = f"Loyalty status removed: {code}"
            self.assertIn(wording, flight_descriptions)
            self.assertIn(wording, feed_descriptions)

        with self.assertRaises(HTTPException) as raised:
            changes_api.get_passenger_timeline(
                FLIGHT["flightNumber"], "HLD003",
                date=FLIGHT["departureDate"], origin=FLIGHT["origin"],
            )
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
