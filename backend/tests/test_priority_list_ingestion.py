"""Priority-list passengers must survive ingestion into the standby and upgrade queues.

Sabre GetPassengerListRQ returns the passengers that match the requested display
codes (OR = any code). The guide defines those codes separately:

- RV: revenue passengers
- XRV: non-revenue passengers
- BP: boarding pass issued
- BT: checked bags
- AE: air extras
- P: unaccommodated priority-list passengers, and it cannot be combined with
  any other code; SortSequence and ClassOfService are not supported
- PALL: every priority-list passenger, with the same restrictions as P

The local SOAP stand-in applies only those rules. It is not a live Sabre call.
"""

import copy
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

# Placeholders only. Set before backend imports so a local .env cannot replace them.
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "placeholder-pcc"
os.environ["SABRE_ORGANIZATION"] = "placeholder-org"
os.environ["SABRE_DOMAIN"] = "placeholder-domain"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:9/unused"
os.environ.pop("POSTGRES_URI", None)

import unittest

from fastapi.testclient import TestClient

from backend.api.database import get_db as api_get_db
import backend.api.database as api_database
import backend.api.routes.flights as flights_route
import backend.feeder.storage as feeder_storage
from backend.api.main import app


FLIGHT = {
    "airline": "GF",
    "flightNumber": "4401",
    "origin": "BAH",
    "departureDate": "2026-04-02",
    "departureDateTime": "2026-04-02T09:15:00",
}

# Guide appendix: a code matches passengers with that characteristic.
# PALL is every priority-list passenger. P is the unaccommodated subset.
# Sample payload 4.2.a includes seated priority passengers that sample 4.2.b
# (display P) omits, so a seat means the passenger is already accommodated.
PASSENGERS = [
    {
        "last": "PALLONLY",
        "first": "NINA",
        "pnr": "PAL001",
        "id": "PAX-PALL",
        "line": 1,
        "priority": "B01",
        "type": "S",
        "seniority": "1988-01-15",
        "seat": "12A",
        "revenue": False,
        "non_revenue": False,
        "boarding_pass": False,
        "bags": 0,
        "air_extras": False,
        "priority_list": True,
    },
    {
        "last": "HIGHWAIT",
        "first": "PRIYA",
        "pnr": "HGH001",
        "id": "PAX-HIGH",
        "line": 2,
        "priority": "B01",
        "type": "S",
        "seniority": "1990-06-01",
        "seat": "",
        "revenue": False,
        "non_revenue": False,
        "boarding_pass": False,
        "bags": 0,
        "air_extras": False,
        "priority_list": True,
    },
    {
        "last": "UPGONLY",
        "first": "LEILA",
        "pnr": "UPG001",
        "id": "PAX-UPG",
        "line": 1,
        "priority": "UPG",
        "type": "F",
        "desired": "J",
        "seat": "",
        "revenue": False,
        "non_revenue": False,
        "boarding_pass": False,
        "bags": 0,
        "air_extras": False,
        "priority_list": True,
    },
    {
        "last": "LOWSTANDBY",
        "first": "OMAR",
        "pnr": "LOW001",
        "id": "PAX-LOW",
        "line": 4,
        "priority": "B02",
        "type": "S",
        "seniority": "2015-03-01",
        "seat": "",
        "revenue": False,
        "non_revenue": True,
        "boarding_pass": False,
        "bags": 0,
        "air_extras": False,
        "priority_list": True,
    },
    {
        "last": "UPGREV",
        "first": "SAMI",
        "pnr": "UPR001",
        "id": "PAX-UPREV",
        "line": 2,
        "priority": "UPG",
        "type": "F",
        "desired": "J",
        "seat": "",
        "revenue": True,
        "non_revenue": False,
        "boarding_pass": False,
        "bags": 0,
        "air_extras": False,
        "priority_list": True,
    },
]


def _matching_codes(passenger):
    codes = set()
    if passenger["revenue"]:
        codes.add("RV")
    if passenger["non_revenue"]:
        codes.add("XRV")
    if passenger["boarding_pass"]:
        codes.add("BP")
    if passenger["bags"]:
        codes.add("BT")
    if passenger["air_extras"]:
        codes.add("AE")
    unaccommodated = not passenger.get("seat")
    if passenger["priority_list"] and unaccommodated:
        codes.add("P")
    if passenger["priority_list"]:
        codes.add("PALL")
    return codes


def _passenger_xml(passenger):
    seat = (
        f"<Seat>{escape(passenger['seat'])}</Seat>" if passenger.get("seat") else ""
    )
    desired = (
        f"<DesiredBookingClass>{escape(passenger['desired'])}</DesiredBookingClass>"
        if passenger.get("desired")
        else ""
    )
    seniority = (
        f"<SeniorityDate>{escape(passenger['seniority'])}</SeniorityDate>"
        if passenger.get("seniority")
        else ""
    )
    indicators = []
    if passenger["revenue"]:
        indicators.append("Revenue")
    if passenger["non_revenue"]:
        indicators.append("NonRevenue")
    indicator_xml = ""
    if indicators:
        indicator_xml = "<Indicators>" + "".join(
            f"<Indicator>{escape(item)}</Indicator>" for item in indicators
        ) + "</Indicators>"
    return (
        "<PassengerInfo>"
        "<Name_Details>"
        f"<LastName>{escape(passenger['last'])}</LastName>"
        f"<FirstName>{escape(passenger['first'])}</FirstName>"
        "</Name_Details>"
        f"<PNRLocator>{escape(passenger['pnr'])}</PNRLocator>"
        f"<PassengerID>{escape(passenger['id'])}</PassengerID>"
        f"<LineNumber>{passenger['line']}</LineNumber>"
        f"<PriorityCode>{escape(passenger['priority'])}</PriorityCode>"
        "<BookingClass>Y</BookingClass>"
        "<Cabin>Y</Cabin>"
        f"{desired}{seat}"
        "<Destination>DXB</Destination>"
        f"<BoardingPassFlag>{'true' if passenger['boarding_pass'] else 'false'}</BoardingPassFlag>"
        f"<PassengerType>{escape(passenger['type'])}</PassengerType>"
        f"<BagCount>{int(passenger['bags'])}</BagCount>"
        f"{seniority}"
        f"{indicator_xml}"
        "<CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>"
        "<Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>"
        "</PassengerInfo>"
    )


def _envelope(body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        "</wsse:Security>"
        "</soap-env:Header>"
        f"<soap-env:Body>{body}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _display_codes(request_xml):
    return re.findall(
        r"<(?:[\w.-]+:)?DisplayCode>([^<]+)</(?:[\w.-]+:)?DisplayCode>",
        request_xml,
    )


def _priority_request_error(codes, request_xml):
    """Return the guide restriction that rejects this priority-list request."""
    if not ({"P", "PALL"} & set(codes)):
        return None
    if set(codes) not in ({"P"}, {"PALL"}):
        return "P and PALL cannot be combined with any other display codes"
    if re.search(r"<(?:[\w.-]+:)?SortSequence\b", request_xml):
        return "SortSequence is not supported on a priority list request"
    if re.search(r"<(?:[\w.-]+:)?ClassOfService\b", request_xml):
        return "ClassOfService is not supported on a priority list request"
    return None


def _passenger_list_response(request_xml):
    codes = _display_codes(request_xml)
    if not codes:
        return 500, "GetPassengerListRQ contained no DisplayCode"
    rejection = _priority_request_error(codes, request_xml)
    if rejection:
        return 500, rejection
    requested = set(codes)
    matched = [
        passenger
        for passenger in PASSENGERS
        if _matching_codes(passenger) & requested
    ]
    body = (
        "<GetPassengerListRS>"
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline><Flight>4401</Flight>"
        "<DepartureDate>2026-04-02</DepartureDate><Origin>BAH</Origin>"
        "</Itinerary></ItineraryInfo>"
        "<PassengerInfoList>"
        + "".join(_passenger_xml(passenger) for passenger in matched)
        + "</PassengerInfoList>"
        "<Result><Status>Success</Status></Result>"
        "</GetPassengerListRS>"
    )
    return 200, _envelope(body)


def _other_response(action):
    if action == "SessionCreateRQ":
        return 200, _envelope("<SessionCreateRS/>")
    if action == "SessionCloseRQ":
        return 200, _envelope("<SessionCloseRS/>")
    if action == "ACS_FlightDetailRQ":
        return 200, _envelope(
            "<ns3:ACS_FlightDetailRS>"
            "<ItineraryResponseList><ItineraryInfoResponse>"
            "<FlightStatus>OPEN</FlightStatus>"
            "<ScheduledDepartureDate>2026-04-02</ScheduledDepartureDate>"
            "</ItineraryInfoResponse></ItineraryResponseList>"
            "</ns3:ACS_FlightDetailRS>"
        )
    if action == "Trip_SearchRQ":
        return 200, _envelope(
            "<Trip_SearchRS>"
            '<ReservationsList NumberResults="0"/>'
            "</Trip_SearchRS>"
        )
    if action == "Trip_ReportsRQ":
        return 200, _envelope(
            "<Trip_ReportsRS><PassengerList PassengerCount=\"0\"/></Trip_ReportsRS>"
        )
    if action == "VerifyFlightDetailsLLSRQ":
        return 200, _envelope(
            "<VerifyFlightDetailsRS>"
            '<ApplicationResults status="Complete"/>'
            "</VerifyFlightDetailsRS>"
        )
    return 500, f"unexpected action {action}"


class _SabreHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request_xml = self.rfile.read(length).decode("utf-8")
        action = self.headers.get("SOAPAction", "")
        self.server.requests.append({"action": action, "body": request_xml})
        if action == "GetPassengerListRQ":
            status, payload = _passenger_list_response(request_xml)
        else:
            status, payload = _other_response(action)
        encoded = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _MemoryCollection:
    def __init__(self):
        self.docs = []
        self._lock = threading.Lock()
        self._next_id = 1

    def insert_one(self, doc):
        with self._lock:
            stored = copy.deepcopy(doc)
            stored["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(stored)
            doc["_id"] = stored["_id"]
            return type("InsertOneResult", (), {"inserted_id": stored["_id"]})()

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return type("InsertManyResult", (), {"inserted_ids": ids})()

    def _matches(self, doc, query):
        for key, expected in (query or {}).items():
            if doc.get(key) != expected:
                return False
        return True

    def _project(self, doc, projection):
        copied = copy.deepcopy(doc)
        if not projection:
            return copied
        included = {key for key, flag in projection.items() if flag}
        if included:
            return {key: copied[key] for key in included if key in copied}
        return copied

    def _sorted(self, docs, sort):
        if not sort:
            return list(docs)
        ordered = list(docs)
        for field, direction in reversed(sort):
            ordered.sort(key=lambda doc, field=field: (doc.get(field) is None, doc.get(field)), reverse=direction < 0)
        return ordered

    def find_one(self, query=None, projection=None, sort=None):
        with self._lock:
            matches = [doc for doc in self.docs if self._matches(doc, query)]
            ordered = self._sorted(matches, sort)
            if not ordered:
                return None
            return self._project(ordered[0], projection)

    def find(self, query=None, projection=None, sort=None, **kwargs):
        with self._lock:
            matches = [
                self._project(doc, projection)
                for doc in self.docs
                if self._matches(doc, query)
            ]
        cursor = _MemoryCursor(matches)
        if sort:
            cursor.sort(sort)
        if kwargs.get("limit") is not None:
            cursor.limit(kwargs["limit"])
        return cursor

    def update_one(self, query, update, upsert=False):
        with self._lock:
            for doc in self.docs:
                if self._matches(doc, query):
                    for key, value in update.get("$set", {}).items():
                        doc[key] = copy.deepcopy(value)
                    for key, value in update.get("$inc", {}).items():
                        doc[key] = doc.get(key, 0) + value
                    return
            if not upsert:
                return
            created = copy.deepcopy(update.get("$setOnInsert", {}))
            for key, value in update.get("$set", {}).items():
                created[key] = copy.deepcopy(value)
            for key, value in update.get("$inc", {}).items():
                created[key] = created.get(key, 0) + value
            created["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(created)

    def index_information(self):
        return {}

    def drop_index(self, name):
        return None

    def create_index(self, keys, **kwargs):
        return kwargs.get("name")


class _MemoryCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, keys):
        if keys:
            ordered = list(self.docs)
            for field, direction in reversed(keys):
                ordered.sort(
                    key=lambda doc, field=field: (doc.get(field) is None, doc.get(field)),
                    reverse=direction < 0,
                )
            self.docs = ordered
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class _MemoryDatabase:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _MemoryCollection()
        return self._collections[name]

    def command(self, name, *args, **kwargs):
        if name == "ping":
            return {"ok": 1}
        raise NotImplementedError(name)


class PriorityListIngestionTests(unittest.TestCase):
    """Ingestion through the unauthenticated route, then the queue readers."""

    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SabreHandler)
        self.httpd.requests = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        os.environ["SABRE_BASE_URL"] = f"http://{host}:{port}"

        self.db = _MemoryDatabase()
        api_database._client = None
        api_database._db = self.db
        feeder_storage._client = None
        feeder_storage._db = None
        feeder_storage._owns_connection = False
        feeder_storage.init_db(self.db)
        flights_route._dashboard_cache.clear()
        self.client = TestClient(app)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        feeder_storage._db = None
        api_database._db = None

    def test_priority_list_only_passengers_reach_manifest_and_queues(self):
        response = self.client.post("/flights/ingest", json=FLIGHT)
        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )

        stored = api_get_db()["passenger_list"].find_one(
            {
                "flightNumber": FLIGHT["flightNumber"],
                "origin": FLIGHT["origin"],
                "departureDate": FLIGHT["departureDate"],
            },
            sort=[("fetchedAt", -1)],
        )
        self.assertIsNotNone(stored)
        stored_names = sorted(
            passenger["lastName"] for passenger in stored["passengers"]
        )
        self.assertEqual(
            stored_names,
            ["HIGHWAIT", "LOWSTANDBY", "PALLONLY", "UPGONLY", "UPGREV"],
            _requested_codes(self.httpd.requests),
        )

        standby = self.client.get(
            "/flights/4401/passengers/standby-list",
            params={"origin": "BAH", "date": "2026-04-02"},
        )
        self.assertEqual(standby.status_code, 200, standby.text)
        standby_body = standby.json()
        self.assertEqual(
            [row["lastName"] for row in standby_body["standby"]["passengers"]],
            ["PALLONLY", "HIGHWAIT", "LOWSTANDBY"],
        )
        self.assertEqual(
            [row["lastName"] for row in standby_body["upgrade"]["passengers"]],
            ["UPGONLY", "UPGREV"],
        )

        dashboard = self.client.get(
            "/flights/4401/dashboard",
            params={"origin": "BAH", "date": "2026-04-02"},
        )
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        summary = dashboard.json()["insights"]["standbyUpgrade"]
        self.assertEqual(summary["standbyTotal"], 3)
        self.assertEqual(summary["upgradeTotal"], 2)
        self.assertEqual(summary["standbyCabins"], {"Y": 3})


def _requested_codes(requests):
    groups = []
    for request in requests:
        if request["action"] != "GetPassengerListRQ":
            continue
        groups.append(",".join(_display_codes(request["body"])))
    return "passenger-list display codes requested: " + " | ".join(groups)


if __name__ == "__main__":
    unittest.main()
