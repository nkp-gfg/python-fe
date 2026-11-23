"""Scheduled timetable changes must be recorded by flight-status detection.

These tests fail while change detection ignores schedule.scheduledDeparture
and schedule.scheduledArrival. They are the spec for that fix.

Run from the repository root:

    python3 -m unittest backend.tests.test_scheduled_timetable_changes -v
"""

import copy
import unittest
from collections import defaultdict

from backend.api.routes import changes as changes_api
from backend.feeder.converter import convert_flight_status
from backend.feeder.runner import _process_api_call
from backend.feeder.storage import DESCENDING, init_db
from backend.sabre.client import SabreClient

FLIGHT_INFO = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}

# Field names and change types the flight-status diff must write.
# Estimated clocks are a different event and must not reuse these.
SCHEDULED_DEPARTURE_FIELD = "schedule.scheduledDeparture"
SCHEDULED_ARRIVAL_FIELD = "schedule.scheduledArrival"
SCHEDULED_DEPARTURE_CHANGE = "SCHEDULED_DEPARTURE_CHANGE"
SCHEDULED_ARRIVAL_CHANGE = "SCHEDULED_ARRIVAL_CHANGE"


def _flight_detail_xml(
    scheduled_departure_time,
    scheduled_arrival_time,
    estimated_departure_time="05:40PM",
    estimated_arrival_time="09:25PM",
    gate="67A",
    status="FINAL",
    scheduled_departure_date="2026-03-19",
):
    """ACS_FlightDetailRS shaped like the Sabre reply the feeder parses.

    ScheduledArrivalDate is omitted on purpose: the converter falls back to
    ArrivalDate, which is how the stored sample reply is shaped.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <Origin>BOM</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <ScheduledDepartureDate>{scheduled_departure_date}</ScheduledDepartureDate>
          <ScheduledDepartureTime>{scheduled_departure_time}</ScheduledDepartureTime>
          <EstimatedDepartureDate>2026-03-19</EstimatedDepartureDate>
          <EstimatedDepartureTime>{estimated_departure_time}</EstimatedDepartureTime>
          <DepartureGate>{gate}</DepartureGate>
          <DepartureTerminal>2</DepartureTerminal>
          <BoardingTime>05:00PM</BoardingTime>
          <ArrivalDate>2026-03-19</ArrivalDate>
          <ArrivalTime>09:25PM</ArrivalTime>
          <ScheduledArrivalTime>{scheduled_arrival_time}</ScheduledArrivalTime>
          <EstimatedArrivalDate>2026-03-19</EstimatedArrivalDate>
          <EstimatedArrivalTime>{estimated_arrival_time}</EstimatedArrivalTime>
          <FlightStatus>{status}</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_flight_detail(xml_text):
    """Same parse the Sabre client uses after ACS_FlightDetailRQ returns."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(SabreClient.__new__(SabreClient), parsed, "ns3:ACS_FlightDetailRS")


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, str):
            spec = [(key_or_list, 1 if direction is None else direction)]
        else:
            spec = list(key_or_list)
        docs = self._docs
        for key, direc in reversed(spec):
            docs = sorted(
                docs,
                key=lambda doc, field=key: (doc.get(field) is None, doc.get(field)),
                reverse=direc == DESCENDING or direc == -1,
            )
        self._docs = docs
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []
        self._next_id = 1

    def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = self._next_id
            self._next_id += 1
        self.docs.append(doc)
        return _InsertResult(doc["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertManyResult(ids)

    def _matches(self, query):
        query = query or {}
        return [doc for doc in self.docs if all(doc.get(key) == value for key, value in query.items())]

    def _project(self, doc, projection):
        if not projection:
            return doc
        projected = {}
        for key, include in projection.items():
            if include and key in doc:
                projected[key] = doc[key]
        if projection.get("_id", 1) and "_id" in doc:
            projected["_id"] = doc["_id"]
        return projected

    def find_one(self, filter=None, projection=None, sort=None, **kwargs):
        found = self._matches(filter)
        if sort:
            found = list(_Cursor(found).sort(sort))
        if not found:
            return None
        return self._project(copy.deepcopy(found[0]), projection)

    def find(self, filter=None, projection=None, sort=None, **kwargs):
        found = [self._project(doc, projection) for doc in self._matches(filter)]
        cursor = _Cursor(found)
        if sort:
            cursor.sort(sort)
        return cursor

    def update_one(self, filter, update, upsert=False):
        found = self._matches(filter)
        if found:
            self._apply_update(found[0], update, on_insert=False)
            return
        if not upsert:
            return
        doc = copy.deepcopy(filter)
        self._apply_update(doc, update, on_insert=True)
        self.insert_one(doc)

    def _apply_update(self, doc, update, on_insert):
        for key, value in update.get("$set", {}).items():
            doc[key] = value
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value
        if on_insert:
            for key, value in update.get("$setOnInsert", {}).items():
                doc[key] = value


class _Database:
    def __init__(self):
        self._collections = defaultdict(_Collection)

    def __getitem__(self, name):
        return self._collections[name]


class ScheduledTimetableChangeTests(unittest.TestCase):
    def setUp(self):
        self.db = _Database()
        init_db(self.db)
        changes_api.get_db = lambda: self.db

    def _ingest(self, xml_text):
        raw = _parse_flight_detail(xml_text)
        return _process_api_call(
            "FlightStatus",
            "flight_status",
            FLIGHT_INFO,
            raw,
            xml_text,
            {
                "requestXml": "<ACS_FlightDetailRQ/>",
                "httpStatus": 200,
                "durationMs": 1,
            },
            convert_flight_status,
            (raw, "GF", "2057", "BOM", "2026-03-19"),
        )

    def _ingest_pair(self, first_xml, second_xml):
        first = self._ingest(first_xml)
        second = self._ingest(second_xml)
        return first, second

    def _latest_flight_status(self):
        """Return the flight-status document stored by the most recent ingest.

        fetchedAt is whole seconds, so two ingests in the same second tie.
        Insertion order is the order store_flight_status ran.
        """
        docs = self.db["flight_status"].docs
        self.assertTrue(docs, "flight status was not stored")
        snapshots = self.db["snapshots"].docs
        latest_snap = max(snapshots, key=lambda doc: doc["sequenceNumber"])
        self.assertIs(latest_snap["data"], docs[-1])
        return docs[-1]

    def _changes(self):
        return list(self.db["changes"].docs)

    def _assert_change(self, field, change_type, old_value, new_value):
        matches = [
            change for change in self._changes()
            if change.get("field") == field and change.get("changeType") == change_type
        ]
        self.assertEqual(
            len(matches),
            1,
            f"expected one {change_type} for {field}, got {self._changes()}",
        )
        self.assertEqual(matches[0]["oldValue"], old_value)
        self.assertEqual(matches[0]["newValue"], new_value)
        self.assertEqual(matches[0]["flightNumber"], "2057")
        self.assertEqual(matches[0]["origin"], "BOM")
        self.assertEqual(matches[0]["departureDate"], "2026-03-19")
        return matches[0]

    def _activity_event_types(self):
        feed = changes_api.get_activity_feed(date="2026-03-19", limit=50, categories=None)
        return [event["eventType"] for event in feed["events"]]

    def test_scheduled_departure_time_change_is_stored_and_recorded(self):
        first_xml = _flight_detail_xml("05:40PM", "08:10PM")
        second_xml = _flight_detail_xml("06:15PM", "08:10PM")
        first, second = self._ingest_pair(first_xml, second_xml)

        self.assertFalse(first["isDuplicate"])
        self.assertFalse(second["isDuplicate"])
        latest = self._latest_flight_status()
        self.assertEqual(latest["schedule"]["scheduledDeparture"], "2026-03-19T06:15PM")
        self.assertEqual(latest["schedule"]["scheduledArrival"], "2026-03-19T08:10PM")
        self.assertEqual(latest["schedule"]["estimatedDeparture"], "2026-03-19T05:40PM")
        self.assertEqual(latest["schedule"]["estimatedArrival"], "2026-03-19T09:25PM")

        self._assert_change(
            SCHEDULED_DEPARTURE_FIELD,
            SCHEDULED_DEPARTURE_CHANGE,
            "2026-03-19T05:40PM",
            "2026-03-19T06:15PM",
        )
        scheduled_fields = [change.get("field") for change in self._changes()]
        self.assertNotIn("schedule.estimatedDeparture", scheduled_fields)
        self.assertNotIn("schedule.estimatedArrival", scheduled_fields)
        self.assertIn(SCHEDULED_DEPARTURE_CHANGE, self._activity_event_types())
        self.assertEqual(second["changesStored"], 1)

    def test_scheduled_arrival_time_change_is_stored_and_recorded(self):
        first_xml = _flight_detail_xml("05:40PM", "08:10PM")
        second_xml = _flight_detail_xml("05:40PM", "09:40PM")
        _, second = self._ingest_pair(first_xml, second_xml)

        latest = self._latest_flight_status()
        self.assertEqual(latest["schedule"]["scheduledArrival"], "2026-03-19T09:40PM")
        self.assertEqual(latest["schedule"]["estimatedArrival"], "2026-03-19T09:25PM")
        self._assert_change(
            SCHEDULED_ARRIVAL_FIELD,
            SCHEDULED_ARRIVAL_CHANGE,
            "2026-03-19T08:10PM",
            "2026-03-19T09:40PM",
        )
        self.assertNotIn(
            "schedule.estimatedArrival",
            [change.get("field") for change in self._changes()],
        )
        self.assertIn(SCHEDULED_ARRIVAL_CHANGE, self._activity_event_types())
        self.assertEqual(second["changesStored"], 1)

    def test_both_scheduled_clocks_are_separate_events(self):
        first_xml = _flight_detail_xml("05:40PM", "08:10PM")
        second_xml = _flight_detail_xml("06:15PM", "09:40PM", estimated_departure_time="07:00PM", estimated_arrival_time="10:05PM")
        _, second = self._ingest_pair(first_xml, second_xml)

        self._assert_change(
            SCHEDULED_DEPARTURE_FIELD,
            SCHEDULED_DEPARTURE_CHANGE,
            "2026-03-19T05:40PM",
            "2026-03-19T06:15PM",
        )
        self._assert_change(
            SCHEDULED_ARRIVAL_FIELD,
            SCHEDULED_ARRIVAL_CHANGE,
            "2026-03-19T08:10PM",
            "2026-03-19T09:40PM",
        )
        # Estimated clocks moved too. They must not be reported as the scheduled event.
        for change in self._changes():
            if change["field"] in (SCHEDULED_DEPARTURE_FIELD, SCHEDULED_ARRIVAL_FIELD):
                self.assertNotIn("07:00PM", change["newValue"])
                self.assertNotIn("10:05PM", change["newValue"])
        self.assertGreaterEqual(second["changesStored"], 2)
        event_types = self._activity_event_types()
        self.assertIn(SCHEDULED_DEPARTURE_CHANGE, event_types)
        self.assertIn(SCHEDULED_ARRIVAL_CHANGE, event_types)

    def test_scheduled_departure_change_survives_alongside_gate_change(self):
        first_xml = _flight_detail_xml("05:40PM", "08:10PM", gate="67A")
        second_xml = _flight_detail_xml("06:15PM", "08:10PM", gate="70B")
        _, second = self._ingest_pair(first_xml, second_xml)

        self._assert_change(
            SCHEDULED_DEPARTURE_FIELD,
            SCHEDULED_DEPARTURE_CHANGE,
            "2026-03-19T05:40PM",
            "2026-03-19T06:15PM",
        )
        self._assert_change("gate", "GATE_CHANGE", "67A", "70B")
        self.assertEqual(second["changesStored"], 2)

    def test_estimated_time_change_is_not_recorded_as_scheduled_timetable(self):
        first_xml = _flight_detail_xml("05:40PM", "08:10PM")
        second_xml = _flight_detail_xml("05:40PM", "08:10PM", estimated_departure_time="07:05PM", estimated_arrival_time="10:20PM")
        self._ingest_pair(first_xml, second_xml)

        latest = self._latest_flight_status()
        self.assertEqual(latest["schedule"]["scheduledDeparture"], "2026-03-19T05:40PM")
        self.assertEqual(latest["schedule"]["estimatedDeparture"], "2026-03-19T07:05PM")
        self.assertEqual(latest["schedule"]["scheduledArrival"], "2026-03-19T08:10PM")
        self.assertEqual(latest["schedule"]["estimatedArrival"], "2026-03-19T10:20PM")
        for change in self._changes():
            self.assertNotIn(change.get("field"), (SCHEDULED_DEPARTURE_FIELD, SCHEDULED_ARRIVAL_FIELD))
            self.assertNotIn(change.get("changeType"), (SCHEDULED_DEPARTURE_CHANGE, SCHEDULED_ARRIVAL_CHANGE))

    def test_identical_published_timetable_emits_no_scheduled_event(self):
        xml_text = _flight_detail_xml("05:40PM", "08:10PM", status="OPENCI")
        moved = _flight_detail_xml("05:40PM", "08:10PM", status="FINAL")
        self._ingest_pair(xml_text, moved)

        self._assert_change("status", "STATUS_CHANGE", "OPENCI", "FINAL")
        for change in self._changes():
            self.assertNotEqual(change.get("field"), SCHEDULED_DEPARTURE_FIELD)
            self.assertNotEqual(change.get("field"), SCHEDULED_ARRIVAL_FIELD)


if __name__ == "__main__":
    unittest.main()
