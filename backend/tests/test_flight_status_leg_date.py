"""Leg dates from ACS Flight Detail must survive ingestion.

These tests fail while flight-status conversion drops LegDate. They pass once
each stored leg copies that element's calendar date.

The Sabre HTTP call is not made. The response body is the committed Gulf Air
ACS_FlightDetailRS sample, or a small document in the same shape, parsed with
SabreClient._parse_xml and SabreClient._extract_body — the same two steps
get_flight_status uses after the POST. POST /flights/ingest then runs the real
feeder, which stores the converted document. Status and history reads return
that document.

Run:
    python -m unittest backend.tests.test_flight_status_leg_date

Stored legs use legDate (empty string when the LegInfo element omits LegDate).
A leg must not be filled with the flight's departure date.
"""

import asyncio
import copy
import re
import unittest
import uuid
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from backend.api.routes import flights as flights_routes
from backend.api.routes.ingestion import SabreIngestRequest, ingest_flight
from backend.feeder import runner as runner_mod
from backend.feeder import storage
from backend.feeder.converter import _ensure_list, _strip_ns
from backend.sabre.client import SabreClient, SabreError

_REPO = Path(__file__).resolve().parents[2]
_SAMPLES = _REPO / "sample_data" / "sample_xmlfiles"
_SECURITY_BLOCK = re.compile(
    r"<wsse:Security\b[^>]*>.*?</wsse:Security>",
    re.DOTALL,
)
_LEG_BLOCK = re.compile(r"<LegInfo\b[^>]*>(.*?)</LegInfo>", re.DOTALL)
_LEG_DATE = re.compile(r"<LegDate>([^<]*)</LegDate>")


def _redacted_sample(name):
    """Load a committed ACS response with the session header removed."""
    text = (_SAMPLES / name).read_text(encoding="utf-8")
    redacted = _SECURITY_BLOCK.sub("", text)
    if "BinarySecurityToken" in redacted or "<wsse:Security" in redacted:
        raise AssertionError(
            "sample still contains a session credential after redaction"
        )
    return redacted


def _leg_dates_in_xml(xml):
    dates = []
    for block in _LEG_BLOCK.findall(xml):
        match = _LEG_DATE.search(block)
        dates.append(match.group(1).strip() if match else "")
    return dates


def _parsed_leg_dates(body):
    data = _strip_ns(body)
    legs = _ensure_list((data.get("LegInfoList") or {}).get("LegInfo"))
    dates = []
    for leg in legs:
        if isinstance(leg, dict):
            dates.append(str(leg.get("LegDate") or "").strip())
        else:
            dates.append("")
    return dates


def _parse_flight_detail(xml):
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, str):
            spec = [(key, 1 if direction is None else direction)]
        else:
            spec = list(key)
        for field, order in reversed(spec):
            self._docs.sort(
                key=lambda doc, field=field: (
                    doc.get(field) is None,
                    doc.get(field),
                ),
                reverse=order < 0,
            )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    """Just enough of a Mongo collection for the flight-status write path.

    Reads return copies so route helpers can pop _id and _raw without changing
    what was stored. Inserts keep the original document, matching pymongo.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = str(uuid.uuid4())
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return SimpleNamespace(inserted_ids=ids)

    def _match(self, doc, query):
        for key, expected in (query or {}).items():
            actual = doc.get(key)
            if isinstance(expected, dict) and any(
                op.startswith("$") for op in expected
            ):
                if "$lte" in expected and not (
                    actual is not None and actual <= expected["$lte"]
                ):
                    return False
                if "$gte" in expected and not (
                    actual is not None and actual >= expected["$gte"]
                ):
                    return False
                if "$eq" in expected and actual != expected["$eq"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find(self, query=None, projection=None, sort=None):
        matched = [
            copy.deepcopy(doc) for doc in self.docs if self._match(doc, query)
        ]
        cursor = _Cursor(matched)
        if sort:
            cursor.sort(sort)
        return cursor

    def find_one(self, query=None, projection=None, sort=None):
        found = list(self.find(query, sort=sort))
        if not found:
            return None
        doc = found[0]
        if not projection:
            return doc
        return {
            key: doc.get(key)
            for key, include in projection.items()
            if include
        }

    def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, query):
                self._apply(doc, update, inserting=False)
                return
        if not upsert:
            return
        created = {}
        for key, value in (query or {}).items():
            if not isinstance(value, dict):
                created[key] = value
        self._apply(created, update, inserting=True)
        self.insert_one(created)

    @staticmethod
    def _apply(doc, update, inserting):
        for key, value in (update.get("$set") or {}).items():
            doc[key] = value
        if inserting:
            for key, value in (update.get("$setOnInsert") or {}).items():
                doc.setdefault(key, value)
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = doc.get(key, 0) + value


class _MemoryDatabase:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection())


class _FlightDetailClient:
    """Stand-in for the SOAP transport only.

    get_flight_status still parses the response with the real client methods.
    Later Sabre calls raise SabreError, which the feeder already handles.
    """

    def __init__(self, xml):
        self._xml = xml

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        parsed = SabreClient._parse_xml(self._xml)
        body = SabreClient._extract_body(
            self, parsed, "ns3:ACS_FlightDetailRS"
        )
        meta = {
            "requestXml": "<ACS_FlightDetailRQ/>",
            "httpStatus": 200,
            "durationMs": 1,
            "sessionToken": None,
            "conversationId": "leg-date-test",
        }
        return body, self._xml, meta

    def get_passenger_list(self, *args, **kwargs):
        raise SabreError("passenger list is outside this flight-status case")

    def get_reservations(self, *args, **kwargs):
        raise SabreError("reservations are outside this flight-status case")

    def get_trip_report(self, *args, **kwargs):
        raise SabreError("trip report is outside this flight-status case")

    def verify_flight_details(self, *args, **kwargs):
        raise SabreError("schedule is outside this flight-status case")


_MULTI_LEG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>3001</Flight>
          <Origin>BAH</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>11:00PM</ScheduledDepartureTime>
          <ArrivalDate>2026-03-20</ArrivalDate>
          <FlightStatus>OPEN</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <LegInfoList>
        <LegInfo>
          <LegCity>BAH</LegCity>
          <LegStatus>OPEN</LegStatus>
          <LegDate>2026-03-19</LegDate>
        </LegInfo>
        <LegInfo>
          <LegCity>DOH</LegCity>
          <LegStatus>OPEN</LegStatus>
          <LegDate>2026-03-20</LegDate>
        </LegInfo>
        <LegInfo>
          <LegCity>MCT</LegCity>
        </LegInfo>
      </LegInfoList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

_SINGLE_LEG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>3002</Flight>
          <Origin>BAH</Origin>
          <DepartureDate>2026-04-02</DepartureDate>
          <FlightStatus>OPEN</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <LegInfoList>
        <LegInfo>
          <LegCity>BAH</LegCity>
          <LegStatus>OPEN</LegStatus>
          <LegDate>2026-04-02</LegDate>
        </LegInfo>
      </LegInfoList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class FlightStatusLegDateTests(unittest.TestCase):
    def setUp(self):
        self.db = _MemoryDatabase()
        storage.init_db(self.db)
        self._flights_get_db = flights_routes.get_db
        flights_routes.get_db = lambda: self.db
        self._sabre_client = runner_mod.SabreClient

    def tearDown(self):
        flights_routes.get_db = self._flights_get_db
        runner_mod.SabreClient = self._sabre_client
        storage._db = None
        storage._owns_connection = False

    def _ingest(self, xml, flight_number, origin, departure):
        runner_mod.SabreClient = lambda: _FlightDetailClient(xml)
        payload = SabreIngestRequest(
            airline="GF",
            flightNumber=flight_number,
            origin=origin,
            departureDate=date.fromisoformat(departure),
            departureDateTime=datetime.fromisoformat(f"{departure}T12:00:00"),
        )
        try:
            asyncio.run(ingest_flight(payload))
        except HTTPException as exc:
            self.assertEqual(exc.status_code, 409)

    def _assert_dates(self, documents, expected):
        self.assertTrue(documents, "flight status was not stored")
        for document in documents:
            legs = document.get("legs") or []
            self.assertEqual(len(legs), len(expected))
            for index, expected_date in enumerate(expected):
                stored = legs[index].get("legDate")
                self.assertEqual(
                    stored,
                    expected_date,
                    f"leg {index} date",
                )

    def _exercise(self, xml, flight_number, origin, departure, expected):
        self.assertEqual(_leg_dates_in_xml(xml), expected)
        body = _parse_flight_detail(xml)
        self.assertEqual(
            _parsed_leg_dates(body),
            expected,
            "parser dropped LegDate before conversion",
        )
        self._ingest(xml, flight_number, origin, departure)

        stored = list(
            self.db["flight_status"].find(
                {
                    "flightNumber": flight_number,
                    "origin": origin,
                    "departureDate": departure,
                }
            )
        )
        self._assert_dates(stored, expected)

        history = flights_routes.get_flight_status_history(
            flight_number, origin=origin, date=departure, limit=20
        )
        self._assert_dates(history, expected)

        latest = flights_routes.get_flight_status(
            flight_number,
            origin=origin,
            date=departure,
            snapshot_sequence=None,
        )
        self._assert_dates([latest], expected)

        as_of = flights_routes.get_flight_status(
            flight_number,
            origin=origin,
            date=departure,
            snapshot_sequence=1,
        )
        self._assert_dates([as_of], expected)
        # The raw archive is not a substitute for the leg field. History strips
        # it, and the snapshot view above must already carry legDate on legs.
        self.assertNotIn("_raw", history[0])

    def test_gf2057_bom_sample_keeps_leg_date(self):
        xml = _redacted_sample("ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml")
        self._exercise(xml, "2057", "BOM", "2026-03-19", ["2026-03-19", ""])

    def test_gf2006_lhr_overnight_does_not_reuse_origin_date(self):
        xml = _redacted_sample("ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml")
        # Departure leg is 18 Mar. The arrival leg has no LegDate. Copying the
        # origin date onto it would place DMM on the LHR operating day.
        self._exercise(xml, "2006", "LHR", "2026-03-18", ["2026-03-18", ""])

    def test_connection_leg_keeps_next_calendar_day(self):
        # Requested departure date is 19 Mar. The DOH sector is the next day.
        self._exercise(
            _MULTI_LEG_XML, "3001", "BAH", "2026-03-19",
            ["2026-03-19", "2026-03-20", ""],
        )

    def test_single_leginfo_element_keeps_leg_date(self):
        # xmltodict returns one LegInfo as a dict, not a list.
        self._exercise(
            _SINGLE_LEG_XML, "3002", "BAH", "2026-04-02", ["2026-04-02"]
        )


if __name__ == "__main__":
    unittest.main()
