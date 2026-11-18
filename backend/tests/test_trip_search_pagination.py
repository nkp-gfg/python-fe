"""Trip Search must not persist one incomplete page as the reservation list.

Sabre Trip_SearchRQ 4.5.0 truncates a synchronous response at MaxItemsReturned
(800 in the request template). The response reports that truncation with
ReservationsList @NumberResults (this page) and @TotalResults (matches), and
the user guide says a follow-up page is a new request carrying TokenCriteria.
These tests drive run_feeder, which is the body of POST /flights/ingest and of
the Celery batch task falconeye.ingest_batch. The HTTP stand-in returns the
next page only when the request contains TokenCriteria for that page; repeating
the original search returns the same truncated page.

A correct fix either merges every page into one snapshot, or refuses the
ingest before writing a snapshot, a reservations document, or a
RESERVATION_REMOVED change. Publishing the page and then failing still fails
these tests.
"""

import os

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "https://sabre.invalid")
os.environ.setdefault("SABRE_CPAID", "PLACEHOLDER")
os.environ.setdefault("SABRE_USERNAME", "PLACEHOLDER")
os.environ.setdefault("SABRE_PASSWORD", "PLACEHOLDER")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "PLACEHOLDER")
os.environ.setdefault("SABRE_ORGANIZATION", "PLACEHOLDER")
os.environ.setdefault("SABRE_DOMAIN", "PLACEHOLDER")

import copy
from unittest.mock import patch

import requests

from backend.feeder import storage
from backend.feeder.converter import convert_reservations
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient

SabreClient.API_CALL_DELAY = 0

AIRLINE = "GF"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-03-19"
DEPARTURE_DATETIME = "2026-03-19T08:00:00"
PAGE_TOKEN = "PAGE_TOKEN"
FULL_PNRS = ("AAA111", "BBB222", "CCC333")
# Locators Sabre put on the first page. CCC333 exists only on the next page.
PAGE_PNRS = ("AAA111", "BBB222")


class _InsertResult:
    def __init__(self, inserted_id=None, inserted_ids=None):
        self.inserted_id = inserted_id
        self.inserted_ids = inserted_ids or []


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key_or_spec, direction=None):
        if direction is None:
            spec = list(key_or_spec)
        else:
            spec = [(key_or_spec, direction)]
        for key, way in reversed(spec):
            self._docs.sort(key=lambda doc, field=key: doc.get(field), reverse=way < 0)
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
        stored = copy.deepcopy(doc)
        stored["_id"] = self._next_id
        self._next_id += 1
        self.docs.append(stored)
        return _InsertResult(inserted_id=stored["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertResult(inserted_ids=ids)

    def _matches(self, doc, query):
        for key, expected in (query or {}).items():
            if doc.get(key) != expected:
                return False
        return True

    def find(self, query=None, projection=None, sort=None):
        del projection
        cursor = _Cursor([copy.deepcopy(doc) for doc in self.docs if self._matches(doc, query)])
        if sort:
            cursor.sort(sort)
        return cursor

    def find_one(self, query=None, sort=None, projection=None):
        cursor = self.find(query, projection)
        if sort:
            cursor.sort(sort)
        for doc in cursor:
            return doc
        return None

    def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._matches(doc, query):
                self._apply(doc, update, inserting=False)
                return
        if not upsert:
            return
        created = copy.deepcopy(query)
        self._apply(created, update, inserting=True)
        self.insert_one(created)

    @staticmethod
    def _apply(doc, update, inserting):
        if inserting:
            for key, value in (update.get("$setOnInsert") or {}).items():
                doc[key] = copy.deepcopy(value)
        for key, value in (update.get("$set") or {}).items():
            doc[key] = copy.deepcopy(value)
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = doc.get(key, 0) + value


class _MemoryDb:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


def _envelope(body_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{body_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _reservation_xml(pnr):
    return (
        f'<Reservation Locator="{pnr}">'
        '<stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">'
        f'<stl19:Reservation numberInParty="1" numberOfInfants="0">'
        "<stl19:BookingDetails>"
        f"<stl19:RecordLocator>{pnr}</stl19:RecordLocator>"
        "</stl19:BookingDetails>"
        "</stl19:Reservation>"
        "</stl19:GetReservationRS>"
        "</Reservation>"
    )


def _trip_search_rs(pnrs, number_results, total_results, number_pages, warning=False):
    warning_xml = ""
    if warning:
        warning_xml = (
            "<Warnings>"
            '<Warning ShortText="200006">'
            "Number of results exceeded view limit or requested PNRs limit"
            "</Warning>"
            "</Warnings>"
        )
    reservations = "".join(_reservation_xml(pnr) for pnr in pnrs)
    return _envelope(
        '<Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord" Version="4.5.0">'
        "<Success>Success</Success>"
        f"{warning_xml}"
        f'<ReservationsList NumberResults="{number_results}" '
        f'NumberPages="{number_pages}" TotalResults="{total_results}" '
        f'Token="{PAGE_TOKEN}">'
        f"<Reservations>{reservations}</Reservations>"
        "</ReservationsList>"
        "</Trip_SearchRS>"
    )


def _http_response(xml_text):
    response = requests.Response()
    response.status_code = 200
    response._content = xml_text.encode("utf-8")
    response.encoding = "utf-8"
    response.url = "https://sabre.invalid/PLACEHOLDER"
    return response


def _other_api_body(action):
    if action == "SessionCreateRQ":
        return _envelope(
            "<wsse:BinarySecurityToken "
            'xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
            "PLACEHOLDER_SESSION_TOKEN"
            "</wsse:BinarySecurityToken>"
        )
    if action == "ACS_FlightDetailRQ":
        return _envelope(
            "<ns3:ACS_FlightDetailRS>"
            "<ns3:ItineraryResponseList>"
            "<ns3:ItineraryInfoResponse>"
            "<ns3:FlightStatus>OPEN</ns3:FlightStatus>"
            "</ns3:ItineraryInfoResponse>"
            "</ns3:ItineraryResponseList>"
            "</ns3:ACS_FlightDetailRS>"
        )
    if action == "GetPassengerListRQ":
        return _envelope("<GetPassengerListRS><Success>Success</Success></GetPassengerListRS>")
    if action == "Trip_ReportsRQ":
        return _envelope("<Trip_ReportsRS><Success>Success</Success></Trip_ReportsRS>")
    if action == "VerifyFlightDetailsLLSRQ":
        return _envelope(
            "<VerifyFlightDetailsRS>"
            '<ApplicationResults status="Incomplete"/>'
            "</VerifyFlightDetailsRS>"
        )
    if action == "SessionCloseRQ":
        return _envelope("<SessionCloseRS></SessionCloseRS>")
    raise AssertionError(f"unexpected Sabre action {action}")


class _SabreHttp:
    """Return parsed-shaped SOAP. Page 2 is served only for TokenCriteria."""

    def __init__(self, first_page_xml, second_page_xml):
        self.first_page_xml = first_page_xml
        self.second_page_xml = second_page_xml
        self.trip_search_bodies = []

    def post(self, url, data=None, headers=None, timeout=None):
        del url, timeout
        action = (headers or {}).get("SOAPAction", "")
        body = data.decode("utf-8") if isinstance(data, bytes) else (data or "")
        if action != "Trip_SearchRQ":
            return _http_response(_other_api_body(action))
        self.trip_search_bodies.append(body)
        if f"<Token>{PAGE_TOKEN}</Token>" in body and "<Page>2</Page>" in body:
            return _http_response(self.second_page_xml)
        return _http_response(self.first_page_xml)


def _use_memory_db():
    db = _MemoryDb()
    storage._db = None
    storage._client = None
    storage.init_db(db)
    return db


def _flight(flight_number):
    return {
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": DEPARTURE_DATETIME,
        "flightSequenceNumber": 1,
    }


def _seed_complete_snapshot(pnrs, flight_number):
    """Store one complete reservation snapshot the way a prior ingest would."""
    xml = _trip_search_rs(pnrs, len(pnrs), len(pnrs), 1)
    parsed = SabreClient._parse_xml(xml)
    raw = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    doc = convert_reservations(raw, AIRLINE, flight_number, ORIGIN, DEPARTURE_DATE)
    doc["fetchedAt"] = "2000-01-01T00:00:00Z"
    storage.store_snapshot(
        request_id="seed-request",
        airline=AIRLINE,
        flight_number=flight_number,
        origin=ORIGIN,
        departure_date=DEPARTURE_DATE,
        snapshot_type="reservations",
        data=doc,
    )
    storage.store_reservations(doc)


def _latest_reservations(db, flight_number):
    matches = [
        doc for doc in db["reservations"].docs
        if doc.get("flightNumber") == flight_number
        and doc.get("departureAirport") == ORIGIN
        and doc.get("departureDate") == DEPARTURE_DATE
    ]
    if not matches:
        return None
    matches.sort(key=lambda doc: doc.get("fetchedAt") or "", reverse=True)
    return matches[0]


def _pnrs(doc):
    if not doc:
        return set()
    return {item.get("pnr") for item in doc.get("reservations", [])}


def _removal_old_values(db, flight_number):
    return [
        doc.get("oldValue", "")
        for doc in db["changes"].docs
        if doc.get("flightNumber") == flight_number
        and doc.get("origin") == ORIGIN
        and doc.get("departureDate") == DEPARTURE_DATE
        and doc.get("changeType") == "RESERVATION_REMOVED"
    ]


def _run(flight_number, http):
    with patch("backend.sabre.client.requests.post", http.post):
        try:
            result = run_feeder([_flight(flight_number)])
        except Exception as exc:
            return exc
    return result


def _assert_page_was_not_published(db, flight_number, http, expected_pnrs):
    assert http.trip_search_bodies, "Trip_SearchRQ was never sent"
    assert 'MaxItemsReturned="800"' in http.trip_search_bodies[0]
    latest = _latest_reservations(db, flight_number)
    removals = _removal_old_values(db, flight_number)
    held_back = [
        pnr for pnr in expected_pnrs
        if pnr not in PAGE_PNRS and any(pnr in value for value in removals)
    ]
    assert held_back == [], removals
    if latest is None:
        assert removals == []
        return
    assert _pnrs(latest) == set(expected_pnrs)
    assert latest.get("totalResults") == len(expected_pnrs)
    assert len(latest.get("reservations", [])) == len(expected_pnrs)


def test_incomplete_page_does_not_delete_reservations_missing_from_that_page():
    """A later ingest of a short page must not record the rest as cancelled."""
    db = _use_memory_db()
    flight_number = "8001"
    _seed_complete_snapshot(FULL_PNRS, flight_number)
    http = _SabreHttp(
        first_page_xml=_trip_search_rs(
            PAGE_PNRS, number_results=2, total_results=3, number_pages=2, warning=True,
        ),
        second_page_xml=_trip_search_rs(
            ("CCC333",), number_results=1, total_results=3, number_pages=2,
        ),
    )
    _run(flight_number, http)
    _assert_page_was_not_published(db, flight_number, http, FULL_PNRS)


def test_first_ingest_does_not_store_a_short_page_as_the_full_list():
    """Count mismatch alone, with no warning element, is still an incomplete page."""
    db = _use_memory_db()
    flight_number = "8002"
    http = _SabreHttp(
        first_page_xml=_trip_search_rs(
            PAGE_PNRS, number_results=2, total_results=3, number_pages=2, warning=False,
        ),
        second_page_xml=_trip_search_rs(
            ("CCC333",), number_results=1, total_results=3, number_pages=2,
        ),
    )
    _run(flight_number, http)
    _assert_page_was_not_published(db, flight_number, http, FULL_PNRS)


def test_complete_single_page_is_stored():
    """A response whose total matches the returned records is still ingested."""
    db = _use_memory_db()
    flight_number = "8003"
    _seed_complete_snapshot(PAGE_PNRS, flight_number)
    http = _SabreHttp(
        first_page_xml=_trip_search_rs(
            PAGE_PNRS, number_results=2, total_results=2, number_pages=1,
        ),
        second_page_xml=_trip_search_rs((), number_results=0, total_results=2, number_pages=1),
    )
    result = _run(flight_number, http)
    assert not isinstance(result, Exception)
    reservations = result["results"][0]["apis"]["reservations"]
    assert reservations["status"] == "success"
    latest = _latest_reservations(db, flight_number)
    assert _pnrs(latest) == set(PAGE_PNRS)
    assert _removal_old_values(db, flight_number) == []
