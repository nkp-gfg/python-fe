"""Re-ingest must send the published or ACS departure clock to Trip Search.

The flight board builds SabreIngestRequest.departureDateTime. These tests run
that function from frontend/components/dashboard/flight-workbench.tsx, then the
batch payload through SabreIngestRequest and run_feeder. They fail while a
missing or unparsed schedule clock is replaced with midnight.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "https://sabre.invalid")
os.environ.setdefault("SABRE_CPAID", "XX")
os.environ.setdefault("SABRE_USERNAME", "PLACEHOLDER")
os.environ.setdefault("SABRE_PASSWORD", "PLACEHOLDER")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "PLACEHOLDER")
os.environ.setdefault("SABRE_ORGANIZATION", "PLACEHOLDER")
os.environ.setdefault("SABRE_DOMAIN", "PLACEHOLDER")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.api.routes.flights import list_flights  # noqa: E402
from backend.api.routes.ingestion import SabreIngestRequest  # noqa: E402
from backend.feeder.converter import convert_flight_status, convert_reservations, convert_schedule  # noqa: E402
from backend.feeder.runner import run_feeder  # noqa: E402
from backend.feeder import storage  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

WORKBENCH = REPO / "frontend" / "components" / "dashboard" / "flight-workbench.tsx"
HARNESS = Path(__file__).with_name("board_reingest_time.mjs")

AIRLINE = "GF"
FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-25"
PUBLISHED_CLOCK = "14:30:00"
PRIOR_PNR = "PRIOR1"


class MemoryCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = json.loads(json.dumps(doc, default=str))
        stored["_id"] = f"id-{len(self.docs) + 1}"
        self.docs.append(stored)
        return type("Result", (), {"inserted_id": stored["_id"]})()

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return type("Result", (), {"inserted_ids": ids})()

    def find_one(self, filt, sort=None, projection=None):
        matches = _match(self.docs, filt)
        if sort:
            matches = _sort(matches, sort)
        if not matches:
            return None
        return _project(matches[0], projection)

    def find(self, filt=None, projection=None, sort=None, **kwargs):
        matches = _match(self.docs, filt or {})
        if sort:
            matches = _sort(matches, sort)
        return MemoryCursor([_project(doc, projection) for doc in matches])

    def update_one(self, filt, update, upsert=False):
        matches = _match(self.docs, filt)
        if matches:
            _apply_update(matches[0], update, inserting=False)
            return
        if upsert:
            doc = dict(filt)
            _apply_update(doc, update, inserting=True)
            self.insert_one(doc)

    def aggregate(self, pipeline, allowDiskUse=False):
        docs = list(self.docs)
        grouped = None
        for stage in pipeline:
            if "$match" in stage:
                docs = _match(docs, stage["$match"])
            elif "$sort" in stage:
                spec = [(key, direction) for key, direction in stage["$sort"].items()]
                docs = _sort(docs, spec)
            elif "$group" in stage:
                grouped = []
                seen = {}
                id_spec = stage["$group"]["_id"]
                for doc in docs:
                    key = tuple(_eval(expr, doc) for expr in id_spec.values())
                    if key in seen:
                        continue
                    seen[key] = True
                    ident = {name: _eval(expr, doc) for name, expr in id_spec.items()}
                    copied = json.loads(json.dumps(doc, default=str))
                    grouped.append({"_id": ident, "doc": copied})
                docs = grouped
            elif "$project" in stage:
                for exclusion, enabled in stage["$project"].items():
                    if enabled == 0 and exclusion == "doc._raw":
                        for item in docs:
                            item.get("doc", {}).pop("_raw", None)
            elif "$addFields" in stage:
                continue
            else:
                raise AssertionError(f"aggregation stage not used by list_flights: {stage}")
        return docs


class MemoryCursor:
    def __init__(self, docs):
        self.docs = docs

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        self._collections.setdefault(name, MemoryCollection())
        return self._collections[name]


def _match(docs, filt):
    found = []
    for doc in docs:
        if all(doc.get(key) == value for key, value in (filt or {}).items()):
            found.append(doc)
    return found


def _sort(docs, spec):
    ordered = list(docs)
    for key, direction in reversed(spec):
        ordered.sort(key=lambda doc, key=key: doc.get(key) if doc.get(key) is not None else "", reverse=direction < 0)
    return ordered


def _project(doc, projection):
    if not projection:
        return doc
    included = {key for key, flag in projection.items() if flag}
    if not included:
        return doc
    projected = {"_id": doc.get("_id")}
    for key in included:
        if key in doc:
            projected[key] = doc[key]
    return projected


def _apply_update(doc, update, inserting):
    if inserting:
        for key, value in update.get("$setOnInsert", {}).items():
            doc[key] = value
    for key, value in update.get("$set", {}).items():
        doc[key] = value
    for key, value in update.get("$inc", {}).items():
        doc[key] = doc.get(key, 0) + value


def _eval(expr, doc):
    if isinstance(expr, str) and expr.startswith("$"):
        if expr == "$$ROOT":
            return doc
        return doc.get(expr[1:])
    if isinstance(expr, dict) and "$ifNull" in expr:
        path, default = expr["$ifNull"]
        value = doc.get(path[1:])
        return default if value is None else value
    return expr


def _status_body(date_value, time_value):
    """ACS itinerary shaped like ACS_FlightDetailRS, with optional clock parts."""
    itin = {
        "Airline": AIRLINE,
        "Flight": FLIGHT,
        "Origin": ORIGIN,
    }
    if date_value is not None:
        itin["ScheduledDepartureDate"] = date_value
        itin["DepartureDate"] = date_value
    if time_value is not None:
        itin["ScheduledDepartureTime"] = time_value
        itin["DepartureTime"] = time_value
    return {
        "ItineraryResponseList": {"ItineraryInfoResponse": itin},
        "LegInfoList": {"LegInfo": [{"City": ORIGIN}, {"City": "LHR"}]},
    }


def _schedule_body(departure_datetime):
    """VerifyFlightDetailsRS body. DepartureDateTime is stored unchanged."""
    return {
        "ApplicationResults": {"@status": "Complete"},
        "OriginDestinationOptions": {
            "OriginDestinationOption": {
                "FlightSegment": {
                    "@DepartureDateTime": departure_datetime,
                    "@ArrivalDateTime": "03-25T18:45",
                    "OriginLocation": {"@LocationCode": ORIGIN},
                    "DestinationLocation": {"@LocationCode": "LHR"},
                    "MarketingAirline": {"@Code": AIRLINE, "@FlightNumber": FLIGHT},
                }
            }
        },
    }


def _list_row(status_date, status_time, published_departure):
    status = convert_flight_status(_status_body(status_date, status_time), AIRLINE, FLIGHT, ORIGIN, DATE)
    database = MemoryDB()
    database["flight_status"].insert_one(status)
    if published_departure is not None:
        schedule = convert_schedule(_schedule_body(published_departure), AIRLINE, FLIGHT, DATE)
        assert schedule["scheduledDeparture"] == published_departure
        database["flight_schedules"].insert_one(schedule)
    original = list_flights.__globals__["get_db"]
    list_flights.__globals__["get_db"] = lambda: database
    try:
        rows = list_flights(date=DATE)
    finally:
        list_flights.__globals__["get_db"] = original
    assert len(rows) == 1
    return rows[0]


def _board_decision(row):
    completed = subprocess.run(
        ["node", str(HARNESS), str(WORKBENCH)],
        input=json.dumps(row),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def _expected_local(published):
    """Local departure the board must send for a VerifyFlightDetails clock."""
    if "T" not in published:
        raise AssertionError(published)
    date_part, clock = published.split("T", 1)
    if clock.count(":") == 1:
        clock = f"{clock}:00"
    if len(date_part) == 5:
        date_part = f"{DATE[:4]}-{date_part}"
    return f"{date_part}T{clock}"


class ReingestDepartureTimeTests(unittest.TestCase):
    def test_acs_twelve_hour_clock_is_sent_unchanged_in_24_hour_form(self):
        row = _list_row(DATE, "02:30PM", "03-25T14:30")
        self.assertEqual(row["schedule"]["scheduledDeparture"], f"{DATE}T02:30PM")
        decision = _board_decision(row)
        self.assertEqual(decision["mutateCalls"], 1)
        self.assertEqual(decision["departureDateTime"], f"{DATE}T{PUBLISHED_CLOCK}")

    def test_parsed_acs_midnight_is_kept(self):
        row = _list_row(DATE, "12:00AM", None)
        self.assertEqual(row["schedule"]["scheduledDeparture"], f"{DATE}T12:00AM")
        decision = _board_decision(row)
        self.assertEqual(decision["mutateCalls"], 1)
        self.assertEqual(decision["departureDateTime"], f"{DATE}T00:00:00")

    def test_missing_schedule_string_uses_published_month_day_clock(self):
        self._assert_published_clock(None, None, "03-25T14:30")

    def test_date_only_schedule_uses_published_clock_with_seconds(self):
        self._assert_published_clock(DATE, None, "03-25T14:30:00")

    def test_time_only_schedule_uses_published_year_and_clock(self):
        self._assert_published_clock(None, "02:30PM", "2026-03-25T14:30")

    def test_unparsed_schedule_uses_full_published_datetime(self):
        self._assert_published_clock(DATE, None, "2026-03-25T14:30:00")

    def test_no_parsed_clock_does_not_start_ingest(self):
        row = _list_row(None, None, None)
        self.assertFalse(row["schedule"]["scheduledDeparture"])
        self.assertIsNone(row["publishedSchedule"])
        decision = _board_decision(row)
        self.assertEqual(decision["mutateCalls"], 0)
        self.assertIsNone(decision["payload"])

    def test_trip_search_keeps_prior_booking_when_published_clock_exists(self):
        row = _list_row(DATE, None, "03-25T14:30")
        self.assertEqual(row["schedule"]["scheduledDeparture"], DATE)
        self.assertEqual(row["publishedSchedule"]["scheduledDeparture"], "03-25T14:30")
        decision = _board_decision(row)
        self.assertEqual(decision["mutateCalls"], 1, decision)
        dumped = SabreIngestRequest.model_validate(decision["payload"]).model_dump(mode="json")
        self.assertEqual(dumped["departureDateTime"], f"{DATE}T{PUBLISHED_CLOCK}")

        database = MemoryDB()
        storage.init_db(database)
        captured = []
        original_post = SabreClient._post

        def fake_post(self, action, body, timeout=30, endpoint=None):
            self._last_call_time = 0
            if action == "SessionCreateRQ":
                xml = "<wsse:BinarySecurityToken>PLACEHOLDER</wsse:BinarySecurityToken>"
                return xml, 200, 1, body
            if action == "SessionCloseRQ":
                return "<ok/>", 200, 1, body
            if action == "Trip_SearchRQ":
                captured.append(body)
                clock = _departure_attribute(body)
                locator = PRIOR_PNR if clock == f"{DATE}T{PUBLISHED_CLOCK}" else ""
                return _trip_search_response(locator), 200, 1, body
            return _empty_envelope(), 200, 1, body

        SabreClient._post = fake_post
        try:
            prior = convert_reservations(
                SabreClient._extract_body(
                    None,
                    SabreClient._parse_xml(_trip_search_response(PRIOR_PNR)),
                    "Trip_SearchRS",
                ),
                AIRLINE,
                FLIGHT,
                ORIGIN,
                DATE,
            )
            storage.store_snapshot("prior-request", AIRLINE, FLIGHT, ORIGIN, DATE, "reservations", prior)
            run_feeder([dumped])
        finally:
            SabreClient._post = original_post

        self.assertEqual(len(captured), 1)
        self.assertEqual(_departure_attribute(captured[0]), f"{DATE}T{PUBLISHED_CLOCK}")
        removed = [
            change for change in database["changes"].docs
            if change.get("changeType") == "RESERVATION_REMOVED"
        ]
        self.assertEqual(removed, [])
        stored = database["reservations"].docs[-1]["reservations"]
        self.assertEqual([item["pnr"] for item in stored], [PRIOR_PNR])

    def _assert_published_clock(self, status_date, status_time, published):
        row = _list_row(status_date, status_time, published)
        decision = _board_decision(row)
        self.assertEqual(decision["mutateCalls"], 1, decision)
        self.assertEqual(decision["departureDateTime"], _expected_local(published))


def _departure_attribute(request_xml):
    marker = 'DepartureDateTime Is="'
    start = request_xml.index(marker) + len(marker)
    return request_xml[start:request_xml.index('"', start)]


def _empty_envelope():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body><Unused/></soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _trip_search_response(locator):
    reservation = ""
    total = "0"
    if locator:
        total = "1"
        reservation = (
            "<Reservations>"
            f'<Reservation Locator="{locator}">'
            "<GetReservationRS><Reservation numberInParty=\"1\">"
            "<BookingDetails><CreationTimestamp>2026-03-01T00:00:00</CreationTimestamp></BookingDetails>"
            "<PassengerReservation><Passengers>"
            "<Passenger><LastName>PASSENGER</LastName><FirstName>TEST</FirstName></Passenger>"
            "</Passengers></PassengerReservation>"
            "</Reservation></GetReservationRS>"
            "</Reservation></Reservations>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body><Trip_SearchRS>"
        f'<ReservationsList NumberResults="{total}">{reservation}</ReservationsList>'
        "</Trip_SearchRS></soap-env:Body></soap-env:Envelope>"
    )


if __name__ == "__main__":
    unittest.main()
