"""StandbyRestriction must survive ACS flight-detail ingestion.

Sabre sends StandbyRestriction on each ACS PassengerCounts block. The feeder
parses that response with SabreClient and stores convert_flight_status. Flight
status, flight history, the flight list, change detection, and the standby
queue are all built from the stored document after _raw is removed.

These tests fail while the restriction is dropped. They pass once each class
in passengerCounts keeps standbyRestriction and a priority code is not
clearable when that class's stored restriction is non-empty.

Not covered here: the React advice that recomputes "clearable" only from
no-shows and standby totals in
frontend/components/dashboard/revenue-tab.tsx,
frontend/components/dashboard/briefing-tab.tsx, and
frontend/components/dashboard/decision-briefing.tsx, and the status-history
badges in frontend/components/dashboard/status-history.tsx.
"""

import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import (  # noqa: E402
    get_flight_status,
    get_flight_status_history,
    list_flights,
)
from backend.api.routes.passengers import get_standby_list  # noqa: E402
from backend.feeder.converter import convert_flight_status  # noqa: E402
from backend.feeder.differ import diff_flight_status  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "sample_data" / "sample_xmlfiles"

# Committed Gulf Air ACS_FlightDetailRS payloads that include the element.
SAMPLE_FLIGHTS = [
    ("ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml", "GF", "2057", "BOM", "2026-03-19"),
    ("ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml", "GF", "2274", "DMM", "2026-03-19"),
    ("ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml", "GF", "2006", "LHR", "2026-03-18"),
    ("ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml", "GF", "2006", "LHR", "2026-03-19"),
]


def parse_flight_detail(xml_text):
    """Run the same parse and body extract as SabreClient.get_flight_status."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def passenger_count_blocks(body):
    counts = body.get("PassengerCounts", [])
    if counts is None:
        return []
    if isinstance(counts, list):
        return counts
    return [counts]


def convert_xml(xml_text, airline, flight_number, origin, departure_date):
    body = parse_flight_detail(xml_text)
    doc = convert_flight_status(body, airline, flight_number, origin, departure_date)
    return body, doc


def assert_restriction_copied(test, body, doc):
    """Every class count must keep the StandbyRestriction text Sabre sent."""
    blocks = passenger_count_blocks(body)
    test.assertGreater(len(blocks), 0)
    for block in blocks:
        cls = block.get("@classOfService", "?")
        expected = block.get("StandbyRestriction") or ""
        counts = doc["passengerCounts"][cls]
        test.assertIn("standbyRestriction", counts)
        test.assertEqual(counts["standbyRestriction"], expected)
        test.assertEqual(counts["booked"], int(block.get("Booked") or 0))
        test.assertEqual(counts["available"], int(block.get("Available") or 0))


def queue_passenger(pnr, cabin, priority, line=1):
    return {
        "lastName": "ALI",
        "firstName": "NOOR",
        "pnr": pnr,
        "lineNumber": line,
        "priorityCode": priority,
        "bookingClass": cabin,
        "desiredBookingClass": "J" if priority == "UPG" else "",
        "cabin": cabin,
        "seat": "",
        "destination": "HYD",
        "corpId": "",
        "seniorityDate": "2011-05-01",
        "isCheckedIn": False,
        "boardingPassIssued": False,
        "isRevenue": priority == "UPG",
    }


def passenger_list_doc(flight_number, origin, departure_date, passengers, fetched_at):
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "fetchedAt": fetched_at,
        "passengers": passengers,
        "cabinSummary": [],
    }


def flight_status_doc(flight_number, origin, departure_date, restrictions, fetched_at):
    """Stored flight status whose class counts already include the restriction."""
    counts = {}
    for cls, code in restrictions.items():
        counts[cls] = {
            "authorized": 180 if cls == "Y" else 12,
            "booked": 10,
            "available": 5,
            "thru": 0,
            "local": 10,
            "onBoard": 0,
            "boardingPasses": 0,
            "meals": 0,
            "revenue": 10,
            "nonRevenue": 0,
            "standbyRestriction": code,
        }
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "fetchedAt": fetched_at,
        "status": "OPENCI",
        "passengerCounts": counts,
    }


def snapshot(flight_number, origin, departure_date, snapshot_type, sequence, data):
    return {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "snapshotType": snapshot_type,
        "sequenceNumber": sequence,
        "capturedAt": f"2026-03-19T00:00:0{sequence}Z",
        "data": data,
    }


class MemoryCollection:
    """Just enough of a Mongo collection for the route functions under test."""

    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query=None, *args, **kwargs):
        matches = [doc for doc in self.docs if _matches(doc, query or {})]
        matches = _apply_sort(matches, kwargs.get("sort"))
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query=None, projection=None):
        matches = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query or {})]
        return MemoryCursor(matches)

    def aggregate(self, pipeline, allowDiskUse=False):
        docs = [copy.deepcopy(doc) for doc in self.docs]
        for stage in pipeline:
            kind = next(iter(stage))
            if kind == "$match":
                docs = [doc for doc in docs if _matches(doc, stage["$match"])]
            elif kind == "$sort":
                docs = _apply_sort(docs, list(stage["$sort"].items()))
            elif kind == "$group":
                docs = _group(docs, stage["$group"])
            elif kind == "$project":
                docs = [_project(doc, stage["$project"]) for doc in docs]
            elif not docs:
                continue
            else:
                raise AssertionError(f"unexpected aggregation stage {kind}")
        return docs


class MemoryCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            self.docs = _apply_sort(self.docs, key)
        else:
            self.docs = _apply_sort(self.docs, [(key, direction)])
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection([])
        return self._collections[name]

    def add(self, name, doc):
        self[name].docs.append(copy.deepcopy(doc))


def _matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" not in expected:
                return False
            if actual is None or actual > expected["$lte"]:
                return False
        elif actual != expected:
            return False
    return True


def _apply_sort(docs, sort):
    if not sort:
        return docs
    ordered = list(docs)
    for field, direction in reversed(list(sort)):
        ordered.sort(
            key=lambda doc, field=field: (doc.get(field) is None, doc.get(field)),
            reverse=direction < 0,
        )
    return ordered


def _resolve(expr, doc):
    if isinstance(expr, str):
        if expr == "$$ROOT":
            return doc
        if expr.startswith("$"):
            current = doc
            for part in expr[1:].split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None
            return current
        return expr
    if isinstance(expr, dict) and "$ifNull" in expr:
        inner, default = expr["$ifNull"]
        value = _resolve(inner, doc)
        return default if value is None else value
    if isinstance(expr, dict) and "$first" in expr:
        return _resolve(expr["$first"], doc)
    return expr


def _group(docs, spec):
    groups = {}
    order = []
    for doc in docs:
        gid = {key: _resolve(expr, doc) for key, expr in spec["_id"].items()}
        identity = tuple(gid[key] for key in spec["_id"])
        if identity in groups:
            continue
        grouped = {"_id": gid}
        for field, expr in spec.items():
            if field == "_id":
                continue
            grouped[field] = copy.deepcopy(_resolve(expr, doc))
        groups[identity] = grouped
        order.append(identity)
    return [groups[identity] for identity in order]


def _project(doc, spec):
    projected = copy.deepcopy(doc)
    for path, flag in spec.items():
        if flag != 0:
            continue
        current = projected
        parts = path.split(".")
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if isinstance(current, dict):
            current.pop(parts[-1], None)
    return projected


def use_db(db):
    return patch.multiple(
        "backend.api.routes.flights",
        get_db=lambda: db,
    ), patch.multiple(
        "backend.api.routes.passengers",
        get_db=lambda: db,
    )


class StandbyRestrictionTests(unittest.TestCase):
    def test_committed_flight_details_keep_standby_restriction(self):
        for filename, airline, flight_number, origin, departure_date in SAMPLE_FLIGHTS:
            xml_text = (SAMPLE_DIR / filename).read_text(encoding="utf-8")
            body, doc = convert_xml(xml_text, airline, flight_number, origin, departure_date)
            with self.subTest(filename=filename):
                assert_restriction_copied(self, body, doc)
                self.assertEqual(set(doc["passengerCounts"]), {"Y", "J"})

    def test_single_passenger_count_block_keeps_its_own_code(self):
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS>
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <FlightStatus>OPENCI</FlightStatus>
          <ScheduledDepartureDate>2026-04-02</ScheduledDepartureDate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <PassengerCounts classOfService="S">
        <Booked>4</Booked>
        <Available>1</Available>
        <TotalOnBoard>0</TotalOnBoard>
        <StandbyRestriction>B07</StandbyRestriction>
      </PassengerCounts>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
        body, doc = convert_xml(xml_text, "GF", "3002", "BAH", "2026-04-02")
        assert_restriction_copied(self, body, doc)
        self.assertEqual(doc["passengerCounts"]["S"]["standbyRestriction"], "B07")

    def test_omitted_element_is_stored_as_empty_restriction(self):
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS>
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <FlightStatus>OPENCI</FlightStatus>
          <ScheduledDepartureDate>2026-04-01</ScheduledDepartureDate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <PassengerCounts classOfService="Y">
        <Booked>10</Booked>
        <Available>5</Available>
        <TotalOnBoard>0</TotalOnBoard>
        <StandbyRestriction>Z99</StandbyRestriction>
      </PassengerCounts>
      <PassengerCounts classOfService="J">
        <Booked>1</Booked>
        <Available>3</Available>
        <TotalOnBoard>0</TotalOnBoard>
      </PassengerCounts>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
        body, doc = convert_xml(xml_text, "GF", "3001", "BAH", "2026-04-01")
        assert_restriction_copied(self, body, doc)
        self.assertEqual(doc["passengerCounts"]["Y"]["standbyRestriction"], "Z99")
        self.assertEqual(doc["passengerCounts"]["J"]["standbyRestriction"], "")

    def test_status_history_and_list_publish_the_stored_code(self):
        full_xml = (SAMPLE_DIR / "ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml").read_text(
            encoding="utf-8"
        )
        open_xml = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS>
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <FlightStatus>OPENCI</FlightStatus>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <PassengerCounts classOfService="Y">
        <Booked>20</Booked>
        <Available>40</Available>
        <TotalOnBoard>0</TotalOnBoard>
      </PassengerCounts>
      <PassengerCounts classOfService="J">
        <Booked>2</Booked>
        <Available>8</Available>
        <TotalOnBoard>0</TotalOnBoard>
      </PassengerCounts>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
        _, full = convert_xml(full_xml, "GF", "2274", "DMM", "2026-03-19")
        _, open_flight = convert_xml(open_xml, "GF", "3001", "BAH", "2026-03-19")
        db = MemoryDB()
        db.add("flight_status", full)
        db.add("flight_status", open_flight)

        flights_patch, passengers_patch = use_db(db)
        with flights_patch, passengers_patch:
            status = get_flight_status(
                "2274", origin="DMM", date="2026-03-19", snapshot_sequence=None
            )
            history = get_flight_status_history(
                "2274", origin="DMM", date="2026-03-19", limit=20
            )
            listed = list_flights(date="2026-03-19")

        self.assertNotIn("_raw", status)
        self.assertEqual(status["passengerCounts"]["Y"]["standbyRestriction"], "B10")
        self.assertEqual(status["passengerCounts"]["J"]["standbyRestriction"], "B10")
        self.assertEqual(status["passengerCounts"]["Y"]["available"], 0)
        self.assertEqual(status["passengerCounts"]["Y"]["booked"], 180)

        self.assertEqual(len(history), 1)
        self.assertNotIn("_raw", history[0])
        self.assertEqual(history[0]["passengerCounts"]["Y"]["standbyRestriction"], "B10")
        self.assertEqual(history[0]["passengerCounts"]["J"]["standbyRestriction"], "B10")

        by_flight = {item["flightNumber"]: item for item in listed}
        self.assertEqual(by_flight["2274"]["passengerCounts"]["Y"]["standbyRestriction"], "B10")
        self.assertEqual(by_flight["2274"]["passengerCounts"]["J"]["standbyRestriction"], "B10")
        self.assertEqual(by_flight["2274"]["passengerCounts"]["Y"]["available"], 0)
        self.assertEqual(by_flight["3001"]["passengerCounts"]["Y"]["standbyRestriction"], "")
        self.assertEqual(by_flight["3001"]["passengerCounts"]["J"]["standbyRestriction"], "")
        self.assertNotIn("_raw", by_flight["2274"])

    def test_restriction_change_is_detected_per_class(self):
        flight_info = {
            "flightNumber": "2274",
            "origin": "DMM",
            "departureDate": "2026-03-19",
        }
        before_counts = {
            "Y": {
                "booked": 180,
                "onBoard": 180,
                "boardingPasses": 180,
                "standbyRestriction": "B10",
            },
            "J": {
                "booked": 12,
                "onBoard": 12,
                "boardingPasses": 12,
                "standbyRestriction": "B10",
            },
        }
        after_counts = copy.deepcopy(before_counts)
        after_counts["Y"]["standbyRestriction"] = "B05"
        changes = diff_flight_status(
            {"snapshotId": "before", "data": {"passengerCounts": before_counts}},
            {"snapshotId": "after", "data": {"passengerCounts": after_counts}},
            flight_info,
        )
        restriction_changes = [
            change for change in changes if change.get("field", "").endswith("standbyRestriction")
        ]
        self.assertEqual(
            [(change["field"], change["oldValue"], change["newValue"]) for change in restriction_changes],
            [("passengerCounts.Y.standbyRestriction", "B10", "B05")],
        )

    def test_clearing_a_restriction_is_detected(self):
        flight_info = {
            "flightNumber": "2057",
            "origin": "BOM",
            "departureDate": "2026-03-19",
        }
        before = {"Y": {"booked": 56, "onBoard": 56, "boardingPasses": 56, "standbyRestriction": "B10"}}
        after = {"Y": {"booked": 56, "onBoard": 56, "boardingPasses": 56, "standbyRestriction": ""}}
        changes = diff_flight_status(
            {"snapshotId": "before", "data": {"passengerCounts": before}},
            {"snapshotId": "after", "data": {"passengerCounts": after}},
            flight_info,
        )
        self.assertTrue(
            any(
                change.get("field") == "passengerCounts.Y.standbyRestriction"
                and change.get("oldValue") == "B10"
                and change.get("newValue") == ""
                for change in changes
            )
        )

    def test_queue_uses_the_same_flight_and_cabin_restriction(self):
        """A priority code is clearable only for the class on the requested flight.

        A newer status for the same flight number at another origin must not
        supply the restriction, and a J restriction must not clear or block Y.
        """
        db = MemoryDB()
        db.add(
            "passenger_list",
            passenger_list_doc(
                "2274",
                "DMM",
                "2026-03-19",
                [
                    queue_passenger("DMMY01", "Y", "B01", line=2),
                    queue_passenger("DMMJ01", "J", "UPG", line=1),
                ],
                "2026-03-19T10:00:00Z",
            ),
        )
        db.add(
            "passenger_list",
            passenger_list_doc(
                "2274",
                "BAH",
                "2026-03-19",
                [queue_passenger("BAHY01", "Y", "B01")],
                "2026-03-19T12:00:00Z",
            ),
        )
        db.add(
            "flight_status",
            flight_status_doc(
                "2274",
                "DMM",
                "2026-03-19",
                {"Y": "B10", "J": ""},
                "2026-03-19T10:00:00Z",
            ),
        )
        db.add(
            "flight_status",
            flight_status_doc(
                "2274",
                "BAH",
                "2026-03-19",
                {"Y": "", "J": ""},
                "2026-03-19T12:00:00Z",
            ),
        )

        flights_patch, passengers_patch = use_db(db)
        with flights_patch, passengers_patch:
            body = get_standby_list(
                "2274", origin="DMM", date="2026-03-19", snapshot_sequence=None
            )

        self.assertEqual(body["standbyRestrictions"], {"Y": "B10", "J": ""})
        standby = {pax["pnr"]: pax for pax in body["standby"]["passengers"]}
        upgrade = {pax["pnr"]: pax for pax in body["upgrade"]["passengers"]}
        self.assertEqual(set(standby), {"DMMY01"})
        self.assertEqual(set(upgrade), {"DMMJ01"})
        self.assertEqual(standby["DMMY01"]["priorityCode"], "B01")
        self.assertFalse(standby["DMMY01"]["clearable"])
        self.assertEqual(standby["DMMY01"]["standbyRestriction"], "B10")
        self.assertTrue(upgrade["DMMJ01"]["clearable"])
        self.assertEqual(upgrade["DMMJ01"]["standbyRestriction"], "")

    def test_ingested_full_flight_does_not_treat_priority_as_clearable(self):
        xml_text = (SAMPLE_DIR / "ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml").read_text(
            encoding="utf-8"
        )
        _, status = convert_xml(xml_text, "GF", "2274", "DMM", "2026-03-19")
        db = MemoryDB()
        db.add("flight_status", status)
        db.add(
            "passenger_list",
            passenger_list_doc(
                "2274",
                "DMM",
                "2026-03-19",
                [
                    queue_passenger("YFULL", "Y", "B02", line=4),
                    queue_passenger("JFULL", "J", "UPG", line=3),
                ],
                "2026-03-19T13:54:54Z",
            ),
        )

        flights_patch, passengers_patch = use_db(db)
        with flights_patch, passengers_patch:
            body = get_standby_list(
                "2274", origin="DMM", date="2026-03-19", snapshot_sequence=None
            )

        queued = body["standby"]["passengers"] + body["upgrade"]["passengers"]
        self.assertEqual({pax["pnr"] for pax in queued}, {"YFULL", "JFULL"})
        for pax in queued:
            self.assertEqual(pax["standbyRestriction"], "B10")
            self.assertFalse(pax["clearable"])
        self.assertEqual(body["standbyRestrictions"], {"Y": "B10", "J": "B10"})

    def test_historical_queue_uses_the_restriction_as_of_that_sequence(self):
        limited = passenger_list_doc(
            "2274",
            "DMM",
            "2026-03-19",
            [queue_passenger("NEW2", "Y", "B01")],
            "2026-03-19T11:00:00Z",
        )
        later_status = flight_status_doc(
            "2274", "DMM", "2026-03-19", {"Y": "", "J": ""}, "2026-03-19T12:00:00Z"
        )
        as_of_status = flight_status_doc(
            "2274", "DMM", "2026-03-19", {"Y": "B10", "J": "B10"}, "2026-03-19T09:00:00Z"
        )
        db = MemoryDB()
        # Latest legacy status has no restriction. The sequence-2 view must not use it.
        db.add("flight_status", later_status)
        db.add("passenger_list", limited)
        db.add("snapshots", snapshot("2274", "DMM", "2026-03-19", "passenger_list", 1, {
            "passengers": [queue_passenger("OLD1", "Y", "B01")],
            "cabinSummary": [],
            "origin": "DMM",
            "departureDate": "2026-03-19",
        }))
        db.add("snapshots", snapshot(
            "2274", "DMM", "2026-03-19", "passenger_list", 2, limited
        ))
        db.add("snapshots", snapshot(
            "2274", "DMM", "2026-03-19", "flight_status", 1, as_of_status
        ))
        db.add("snapshots", snapshot(
            "2274", "DMM", "2026-03-19", "flight_status", 4, later_status
        ))

        flights_patch, passengers_patch = use_db(db)
        with flights_patch, passengers_patch:
            body = get_standby_list(
                "2274", origin="DMM", date="2026-03-19", snapshot_sequence=2
            )

        self.assertEqual([pax["pnr"] for pax in body["standby"]["passengers"]], ["NEW2"])
        self.assertEqual(body["standbyRestrictions"], {"Y": "B10", "J": "B10"})
        self.assertFalse(body["standby"]["passengers"][0]["clearable"])
        self.assertEqual(body["standby"]["passengers"][0]["standbyRestriction"], "B10")

    def test_unrestricted_priority_code_stays_clearable(self):
        db = MemoryDB()
        db.add(
            "passenger_list",
            passenger_list_doc(
                "2006",
                "LHR",
                "2026-03-18",
                [queue_passenger("OPEN1", "Y", "B03")],
                "2026-03-18T08:00:00Z",
            ),
        )
        db.add(
            "flight_status",
            flight_status_doc(
                "2006", "LHR", "2026-03-18", {"Y": "", "J": ""}, "2026-03-18T08:00:00Z"
            ),
        )

        flights_patch, passengers_patch = use_db(db)
        with flights_patch, passengers_patch:
            body = get_standby_list(
                "2006", origin="LHR", date="2026-03-18", snapshot_sequence=None
            )

        pax = body["standby"]["passengers"][0]
        self.assertEqual(pax["pnr"], "OPEN1")
        self.assertEqual(pax["priorityCode"], "B03")
        self.assertTrue(pax["clearable"])
        self.assertEqual(pax["standbyRestriction"], "")
        self.assertEqual(body["standbyRestrictions"], {"Y": "", "J": ""})


if __name__ == "__main__":
    unittest.main()
