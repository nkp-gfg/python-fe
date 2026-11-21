"""Boarding-pass flag changes must be stored and shown.

A later manifest can change only Sabre's BoardingPassFlag. Ingestion still
parses that flag onto the passenger row, but change detection has to record
the issuance and the void, and the activity feed plus passenger and flight
timelines have to surface that record.

These tests fail until that comparison and those feed categories exist.
"""

import copy
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import HTTPException

from backend.api.routes import changes as changes_routes
from backend.feeder.converter import convert_passenger_list
from backend.feeder.runner import _process_api_call
from backend.feeder import storage
from backend.sabre.client import SabreClient


BOARDING_PASS_CHANGE = "BOARDING_PASS_CHANGE"
BOARDING_PASS_CATEGORY = "boardingPass"
ISSUED_DESCRIPTION = "Boarding pass issued"
VOIDED_DESCRIPTION = "Boarding pass voided"


def _passenger_list_xml(passengers, marker="789"):
    """SOAP body in the shape GetPassengerListRS uses for one manifest."""
    blocks = []
    for pax in passengers:
        if "pnr" in pax:
            pnr_xml = (
                f'<PNRLocator nameAssociationID="1">{pax["pnr"]}</PNRLocator>'
            )
        else:
            pnr_xml = ""
        if "boarding_pass" in pax:
            flag_xml = f"<BoardingPassFlag>{pax['boarding_pass']}</BoardingPassFlag>"
        else:
            flag_xml = ""
        blocks.append(
            f"""
            <PassengerInfo>
              <Name_Details>
                <LastName>{pax["last"]}</LastName>
                <FirstName>{pax["first"]}</FirstName>
              </Name_Details>
              {pnr_xml}
              <PassengerID>{pax.get("id", "P1")}</PassengerID>
              <LineNumber>{pax.get("line", "1")}</LineNumber>
              <BookingClass>{pax.get("booking_class", "W")}</BookingClass>
              <Cabin>{pax.get("cabin", "Y")}</Cabin>
              <Seat>{pax.get("seat", "21A")}</Seat>
              <Destination>DMM</Destination>
              {flag_xml}
              <PassengerType>F</PassengerType>
              <BagCount>{pax.get("bags", "0")}</BagCount>
              <CheckIn_Info>
                <CheckInStatus>{pax.get("checked_in", "true")}</CheckInStatus>
              </CheckIn_Info>
              <Boarding_Info>
                <BoardStatus>{pax.get("boarded", "false")}</BoardStatus>
              </Boarding_Info>
            </PassengerInfo>
            """
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2006</Flight>
          <DepartureDate>2026-03-18</DepartureDate>
          <Origin>LHR</Origin>
          <Destination>DMM</Destination>
          <AircraftType>{marker}</AircraftType>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {"".join(blocks)}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_passenger_list(xml_text):
    """Same parse and body extraction SabreClient.get_passenger_list uses."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(
        object.__new__(SabreClient), parsed, "GetPassengerListRS"
    )


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


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
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return copy.deepcopy(doc)
    if any(value == 0 for key, value in projection.items() if key != "_id"):
        projected = copy.deepcopy(doc)
        for key, value in projection.items():
            if value == 0:
                projected.pop(key, None)
        return projected
    projected = {}
    if projection.get("_id", 1) and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key, value in projection.items():
        if key != "_id" and value and key in doc:
            projected[key] = copy.deepcopy(doc[key])
    return projected


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            specs = key
        else:
            specs = [(key, 1 if direction is None else direction)]
        for field, direc in reversed(specs):
            self._docs.sort(
                key=lambda doc, field=field: (
                    _get_path(doc, field) is None,
                    "" if _get_path(doc, field) is None else _get_path(doc, field),
                ),
                reverse=direc < 0,
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
        stored["_id"] = str(uuid.uuid4())
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertManyResult(ids)

    def find(self, query=None, projection=None, sort=None):
        matched = [
            _project(doc, projection)
            for doc in self.docs
            if _match(doc, query or {})
        ]
        cursor = _Cursor(matched)
        if sort:
            cursor.sort(sort)
        return cursor

    def find_one(self, query=None, projection=None, sort=None):
        found = list(self.find(query, projection=projection, sort=sort).limit(1))
        return found[0] if found else None

    def update_one(self, query, update, upsert=False):
        target = next((doc for doc in self.docs if _match(doc, query)), None)
        if target is None:
            if not upsert:
                return
            target = {"_id": str(uuid.uuid4())}
            for key, value in query.items():
                if not isinstance(value, dict):
                    target[key] = copy.deepcopy(value)
            for key, value in update.get("$setOnInsert", {}).items():
                target[key] = copy.deepcopy(value)
            self.docs.append(target)
        for key, value in update.get("$set", {}).items():
            target[key] = copy.deepcopy(value)
        for key, value in update.get("$inc", {}).items():
            target[key] = target.get(key, 0) + value


class _MemoryDB:
    """In-memory stand-in for the collection calls the pipeline and routes make."""

    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        self._collections.setdefault(name, _Collection())
        return self._collections[name]


class BoardingPassChangeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.db = _MemoryDB()
        storage.init_db(self.db)
        self._original_get_db = changes_routes.get_db
        changes_routes.get_db = lambda: self.db

    def tearDown(self):
        changes_routes.get_db = self._original_get_db
        storage.close()

    def _flight(self, flight_number):
        return {
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": "LHR",
            "departureDate": "2026-03-18",
        }

    def _ingest(self, flight, passengers, marker="789"):
        xml_text = _passenger_list_xml(passengers, marker=marker)
        body = _parse_passenger_list(xml_text)
        return _process_api_call(
            "PassengerList",
            "passenger_list",
            flight,
            body,
            xml_text,
            {
                "requestXml": "<GetPassengerListRQ/>",
                "httpStatus": 200,
                "durationMs": 1,
            },
            convert_passenger_list,
            (
                body,
                flight["airline"],
                flight["flightNumber"],
                flight["departureDate"],
                flight["origin"],
            ),
        )

    def _stored_passengers(self, flight):
        snapshot = self.db["snapshots"].find_one(
            {
                "flightNumber": flight["flightNumber"],
                "origin": flight["origin"],
                "departureDate": flight["departureDate"],
                "snapshotType": "passenger_list",
            },
            sort=[("sequenceNumber", -1)],
        )
        self.assertIsNotNone(snapshot)
        return snapshot["data"]["passengers"]

    def test_issued_pass_reaches_activity_feed_and_timelines(self):
        flight = self._flight("2006")
        before = [
            {
                "pnr": "DKVSUM",
                "last": "ALAALI",
                "first": "MOHAMED MR",
                "id": "D31FA8FB0501",
                "line": "3",
                "seat": "21A",
                "bags": "0",
                "checked_in": "true",
                "boarded": "false",
                "boarding_pass": "False",
            },
            {
                "last": "CREW",
                "first": "SAM",
                "id": "STAFF0001",
                "line": "9",
                "seat": "",
                "bags": "1",
                "checked_in": "true",
                "boarded": "false",
                "boarding_pass": "False",
            },
            {
                "pnr": "SEAT01",
                "last": "JONES",
                "first": "MARY",
                "id": "SEAT0001",
                "line": "4",
                "seat": "12A",
                "bags": "0",
                "checked_in": "true",
                "boarded": "false",
                "boarding_pass": "False",
            },
        ]
        after = [
            {**before[0], "boarding_pass": "True"},
            {**before[1], "boarding_pass": "True"},
            {**before[2], "seat": "12C"},
        ]

        first = self._ingest(flight, before)
        second = self._ingest(flight, after)

        stored = {p["lastName"]: p for p in self._stored_passengers(flight)}
        self.assertTrue(stored["ALAALI"]["boardingPassIssued"])
        self.assertTrue(stored["CREW"]["boardingPassIssued"])
        self.assertFalse(stored["JONES"]["boardingPassIssued"])
        self.assertEqual(stored["ALAALI"]["isCheckedIn"], True)
        self.assertEqual(stored["ALAALI"]["isBoarded"], False)
        self.assertEqual(stored["ALAALI"]["bagCount"], 0)
        self.assertFalse(second["isDuplicate"])
        self.assertEqual(second["changesStored"], 3)

        changes = list(
            self.db["changes"].find(
                {"flightNumber": "2006", "passenger.pnr": "DKVSUM"}
            )
        )
        self.assertEqual(
            [c["changeType"] for c in changes], [BOARDING_PASS_CHANGE]
        )
        issued = changes[0]
        self.assertEqual(issued["field"], "boardingPassIssued")
        self.assertEqual(str(issued["oldValue"]), "False")
        self.assertEqual(str(issued["newValue"]), "True")
        self.assertEqual(issued["passenger"]["lastName"], "ALAALI")
        self.assertEqual(issued["passenger"]["firstName"], "MOHAMED MR")

        crew_changes = [
            c
            for c in self.db["changes"].find({"flightNumber": "2006"})
            if c.get("passenger", {}).get("lastName") == "CREW"
        ]
        self.assertEqual(
            [c["changeType"] for c in crew_changes], [BOARDING_PASS_CHANGE]
        )
        self.assertEqual(crew_changes[0]["passenger"]["pnr"], "")
        self.assertEqual(str(crew_changes[0]["oldValue"]), "False")
        self.assertEqual(str(crew_changes[0]["newValue"]), "True")

        feed = changes_routes.get_activity_feed(
            date="2026-03-18", limit=50, categories=None
        )
        issued_feed = [
            event
            for event in feed["events"]
            if event["passenger"]["pnr"] == "DKVSUM"
        ]
        self.assertEqual(len(issued_feed), 1)
        self.assertEqual(issued_feed[0]["eventType"], BOARDING_PASS_CHANGE)
        self.assertEqual(issued_feed[0]["category"], BOARDING_PASS_CATEGORY)
        self.assertEqual(issued_feed[0]["description"], ISSUED_DESCRIPTION)

        filtered = changes_routes.get_activity_feed(
            date="2026-03-18", limit=50, categories=BOARDING_PASS_CATEGORY
        )
        self.assertGreaterEqual(filtered["count"], 1)
        self.assertTrue(
            all(event["eventType"] == BOARDING_PASS_CHANGE for event in filtered["events"])
        )
        self.assertIn(
            "DKVSUM",
            [event["passenger"]["pnr"] for event in filtered["events"]],
        )
        self.assertIn(
            "CREW",
            [event["passenger"]["lastName"] for event in filtered["events"]],
        )

        history = changes_routes.get_passenger_changes(
            "2006", "DKVSUM", date="2026-03-18", limit=100
        )
        self.assertEqual(
            [item["changeType"] for item in history], [BOARDING_PASS_CHANGE]
        )

        timeline = changes_routes.get_passenger_timeline(
            "2006", "DKVSUM", date="2026-03-18", origin="LHR"
        )
        self.assertEqual(timeline["eventCount"], 1)
        self.assertEqual(timeline["events"][0]["changeType"], BOARDING_PASS_CHANGE)
        self.assertEqual(timeline["events"][0]["category"], BOARDING_PASS_CATEGORY)
        self.assertEqual(timeline["events"][0]["description"], ISSUED_DESCRIPTION)
        self.assertEqual(timeline["events"][0]["details"]["field"], "boardingPassIssued")
        self.assertEqual(timeline["events"][0]["details"]["oldValue"], "False")
        self.assertEqual(timeline["events"][0]["details"]["newValue"], "True")

        flight_timeline = changes_routes.get_flight_timeline(
            "2006", origin="LHR", date="2026-03-18"
        )
        pass_events = [
            event
            for event in flight_timeline["events"]
            if event["eventType"] == BOARDING_PASS_CHANGE
        ]
        self.assertEqual(len(pass_events), 2)
        self.assertTrue(
            all(event["category"] == BOARDING_PASS_CATEGORY for event in pass_events)
        )
        self.assertTrue(
            all(event["description"] == ISSUED_DESCRIPTION for event in pass_events)
        )

    def test_voided_pass_reaches_passenger_history(self):
        flight = self._flight("2007")
        passenger = {
            "pnr": "VOID01",
            "last": "HASSAN",
            "first": "ALI MR",
            "id": "VOID0001",
            "line": "2",
            "seat": "4A",
            "bags": "2",
            "checked_in": "true",
            "boarded": "false",
        }
        first = self._ingest(flight, [{**passenger, "boarding_pass": "True"}])
        second = self._ingest(flight, [{**passenger, "boarding_pass": "False"}])

        stored = self._stored_passengers(flight)[0]
        self.assertFalse(stored["boardingPassIssued"])
        self.assertTrue(stored["isCheckedIn"])
        self.assertFalse(stored["isBoarded"])
        self.assertEqual(stored["bagCount"], 2)
        self.assertEqual(first["changesStored"], 0)
        self.assertFalse(second["isDuplicate"])
        self.assertEqual(second["changesStored"], 1)

        timeline = changes_routes.get_passenger_timeline(
            "2007", "VOID01", date="2026-03-18", origin="LHR"
        )
        event = timeline["events"][0]
        self.assertEqual(event["changeType"], BOARDING_PASS_CHANGE)
        self.assertEqual(event["category"], BOARDING_PASS_CATEGORY)
        self.assertEqual(event["description"], VOIDED_DESCRIPTION)
        self.assertEqual(event["details"]["oldValue"], "True")
        self.assertEqual(event["details"]["newValue"], "False")

        feed = changes_routes.get_activity_feed(
            date="2026-03-18", limit=50, categories=BOARDING_PASS_CATEGORY
        )
        self.assertEqual(
            [event["description"] for event in feed["events"]],
            [VOIDED_DESCRIPTION],
        )
        self.assertEqual(feed["events"][0]["passenger"]["pnr"], "VOID01")

    def test_omitted_flag_matches_explicit_false(self):
        flight = self._flight("2008")
        passenger = {
            "pnr": "NONE01",
            "last": "NOOR",
            "first": "SARA",
            "id": "NONE0001",
            "line": "5",
            "seat": "18B",
            "bags": "0",
            "checked_in": "false",
            "boarded": "false",
        }
        self._ingest(flight, [passenger])
        second = self._ingest(flight, [{**passenger, "boarding_pass": "False"}])
        self.assertFalse(self._stored_passengers(flight)[0]["boardingPassIssued"])
        self.assertEqual(second["changesStored"], 0)
        with self.assertRaises(HTTPException) as raised:
            changes_routes.get_passenger_timeline(
                "2008", "NONE01", date="2026-03-18", origin="LHR"
            )
        self.assertEqual(raised.exception.status_code, 404)

    def test_unchanged_flag_stores_no_boarding_pass_event(self):
        flight = self._flight("2009")
        passenger = {
            "pnr": "SAME01",
            "last": "KHALID",
            "first": "OMAR",
            "id": "SAME0001",
            "line": "6",
            "seat": "7C",
            "bags": "1",
            "checked_in": "true",
            "boarded": "true",
            "boarding_pass": "True",
        }
        self._ingest(flight, [passenger], marker="789")
        second = self._ingest(flight, [dict(passenger)], marker="78X")
        self.assertTrue(self._stored_passengers(flight)[0]["boardingPassIssued"])
        self.assertFalse(second["isDuplicate"])
        self.assertEqual(second["changesStored"], 0)


if __name__ == "__main__":
    unittest.main()
