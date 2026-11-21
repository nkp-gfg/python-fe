"""Passenger-list ingestion must keep air-extra and missing-bag details.

Sabre is asked for four GetPassengerList displays. Travelers who already
appeared are matched by record locator, last name, and passenger id. Details
that exist only on a later display — AEDetailsList on the air-extra (AE)
display, MissingBagList on the checked-in (BP/BT) display — have to be on the
passenger record returned by the passenger API, and each of the four SOAP
response bodies has to be stored in the raw archive.

These tests fail while that merge drops the later elements and while only the
first response body is archived. The batch ingestion task calls the same
``run_feeder`` merge and is not invoked separately.

The live ``GET /flights/{flight}/passengers/{pnr}/detail`` route is a different
operation: one GetPassengerData call, no four-display merge. It is not covered
here.
"""

import asyncio
import copy
import os
from datetime import date, datetime

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://sabre.test")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "HDQ")
os.environ.setdefault("SABRE_ORGANIZATION", "GF")
os.environ.setdefault("SABRE_DOMAIN", "GF")

from unittest.mock import patch

import pytest

from backend.api import database
from backend.api.routes.ingestion import SabreIngestRequest, ingest_flight
from backend.api.routes.passengers import get_passengers
from backend.feeder import storage


BOOKED_TRAVELER = {
    "pnr": "AAAAAA",
    "last": "TRAVELER",
    "first": "ONE",
    "pid": "PAX0001",
    "seat": "12A",
}
CHECKED_IN_ONLY = {
    "pnr": "BBBBBB",
    "last": "BAGONLY",
    "first": "TWO",
    "pid": "PAX0002",
}
NO_SHOW_ONLY = {
    "pnr": "CCCCCC",
    "last": "NOSHOW",
    "first": "THREE",
    "pid": "PAX0003",
}

# Values that appear only on a later display, never on the first copy.
BOOKED_AE_STATUS = "HI"
BOOKED_AE_QTY = "7"
BOOKED_AE_DOC = "0720000000999"
BOOKED_AE_CODE = "BAGS"
BOOKED_BAG_TAG = "GFBAG0001"
CHECKED_IN_AE_DOC = "0720000000777"
CHECKED_IN_AE_QTY = "4"


class MemoryCursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        return MemoryCursor(self._docs[:count])

    def __iter__(self):
        return iter(self._docs)


class MemoryCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = len(self.docs) + 1
        self.docs.append(stored)

        class _Result:
            inserted_id = stored["_id"]

        return _Result()

    def find_one(self, query=None, *args, **kwargs):
        matches = self._matching(query or {})
        sort = kwargs.get("sort")
        if sort:
            for key, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: doc.get(key) if doc.get(key) is not None else "",
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query=None, *args, **kwargs):
        matches = self._matching(query or {})
        sort = kwargs.get("sort")
        if sort:
            for key, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: doc.get(key) if doc.get(key) is not None else "",
                    reverse=direction < 0,
                )
        return MemoryCursor(copy.deepcopy(matches))

    def update_one(self, query, update, upsert=False):
        matches = [doc for doc in self.docs if _query_matches(doc, query or {})]
        if not matches:
            if not upsert:
                return
            doc = {}
            doc.update(copy.deepcopy(update.get("$setOnInsert", {})))
            doc.update(copy.deepcopy(update.get("$set", {})))
            for key, amount in update.get("$inc", {}).items():
                doc[key] = amount
            self.docs.append(doc)
            return
        doc = matches[0]
        for key, value in update.get("$set", {}).items():
            doc[key] = copy.deepcopy(value)
        for key, amount in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + amount

    def _matching(self, query):
        return [doc for doc in self.docs if _query_matches(doc, query)]


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]


def _query_matches(doc, query):
    for key, expected in query.items():
        if doc.get(key) != expected:
            return False
    return True


def _envelope(body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        '<wsse:BinarySecurityToken valueType="String">placeholder-session-token</wsse:BinarySecurityToken>'
        "</wsse:Security>"
        "</soap-env:Header>"
        f"<soap-env:Body>{body}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _passenger(person, seat="", edit_codes=None, air_extra=None, missing_bag=None):
    """One PassengerInfo element shaped like a GetPassengerListRS capture.

    Air-extra children use the STL ``ns2`` prefix seen on the AE display.
    Missing-bag children are in the passenger-list schema and are unprefixed.
    """
    edits = ""
    if edit_codes:
        codes = "".join(f"<ns2:EditCode>{code}</ns2:EditCode>" for code in edit_codes)
        edits = f"<EditCodeList>{codes}</EditCodeList>"
    extra = ""
    if air_extra:
        extra = (
            "<AEDetailsList>"
            "<ns2:AEDetails>"
            f"<ns2:ItemID>{air_extra['item_id']}</ns2:ItemID>"
            f"<ns2:Code>{air_extra['code']}</ns2:Code>"
            f"<ns2:ATPCOGroupCode>{air_extra['code']}</ns2:ATPCOGroupCode>"
            "<ns2:ATPCOSubCode>0CC</ns2:ATPCOSubCode>"
            f"<ns2:StatusCode>{air_extra['status']}</ns2:StatusCode>"
            f"<ns2:Description>{air_extra['description']}</ns2:Description>"
            f"<ns2:QuantityUsed>0</ns2:QuantityUsed>"
            f"<ns2:QuantityBought>{air_extra['quantity']}</ns2:QuantityBought>"
            f'<ns2:EMDTicketNumber couponNumber="1">{air_extra["document"]}</ns2:EMDTicketNumber>'
            "</ns2:AEDetails>"
            "</AEDetailsList>"
        )
    missing = ""
    if missing_bag:
        missing = (
            "<MissingBagList>"
            "<MissingBagDetails>"
            "<Airline>GF</Airline>"
            f"<BagTagNumber>{missing_bag}</BagTagNumber>"
            "</MissingBagDetails>"
            "</MissingBagList>"
        )
    seat_xml = f"<Seat>{seat}</Seat>" if seat else ""
    return (
        "<PassengerInfo>"
        "<Name_Details>"
        f"<LastName>{person['last']}</LastName>"
        f"<FirstName>{person['first']}</FirstName>"
        "</Name_Details>"
        f'<PNRLocator nameAssociationID="1">{person["pnr"]}</PNRLocator>'
        f"<PassengerID>{person['pid']}</PassengerID>"
        "<LineNumber>1</LineNumber>"
        "<BookingClass>Y</BookingClass>"
        "<Cabin>Y</Cabin>"
        f"{seat_xml}"
        "<Destination>DMM</Destination>"
        "<PassengerType>F</PassengerType>"
        "<BagCount>0</BagCount>"
        f"{edits}{extra}{missing}"
        "</PassengerInfo>"
    )


def _passenger_list(message_id, passengers):
    joined = "".join(passengers)
    return _envelope(
        "<GetPassengerListRS "
        'xmlns="http://services.sabre.com/checkin/getPassengerList/v4" '
        'xmlns:ns2="http://services.sabre.com/STL/v3" '
        'version="4.0.0" '
        f'messageID="{message_id}">'
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline><Flight>2057</Flight>"
        "<DepartureDate>2026-03-19</DepartureDate><Origin>BOM</Origin>"
        "<Destination>DMM</Destination>"
        "</Itinerary></ItineraryInfo>"
        f"<PassengerInfoList>{joined}</PassengerInfoList>"
        "</GetPassengerListRS>"
    )


def _lists():
    booked = _passenger_list(
        "booked-display",
        [_passenger(BOOKED_TRAVELER, seat="12A", edit_codes=["AE"])],
    )
    checked_in = _passenger_list(
        "checkedin-display",
        [
            _passenger(BOOKED_TRAVELER, missing_bag=BOOKED_BAG_TAG),
            _passenger(CHECKED_IN_ONLY),
        ],
    )
    no_show = _passenger_list(
        "noshow-display",
        [_passenger(NO_SHOW_ONLY)],
    )
    air_extra = _passenger_list(
        "airextra-display",
        [
            _passenger(
                BOOKED_TRAVELER,
                air_extra={
                    "item_id": "AE1",
                    "code": BOOKED_AE_CODE,
                    "status": BOOKED_AE_STATUS,
                    "description": "PREPAID BAG",
                    "quantity": BOOKED_AE_QTY,
                    "document": BOOKED_AE_DOC,
                },
            ),
            _passenger(
                CHECKED_IN_ONLY,
                air_extra={
                    "item_id": "AE2",
                    "code": "BAGS",
                    "status": "HI",
                    "description": "PREPAID BAG",
                    "quantity": CHECKED_IN_AE_QTY,
                    "document": CHECKED_IN_AE_DOC,
                },
            ),
        ],
    )
    return {
        "AE": air_extra,
        "BT": checked_in,
        "NS": no_show,
        "RV": booked,
    }


def _empty_rs(tag):
    return _envelope(f'<{tag} version="1"/>')


class _HttpResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _install_db(db):
    storage.init_db(db)
    database._db = db


def _scalars(value):
    if isinstance(value, dict):
        found = []
        for inner in value.values():
            found.extend(_scalars(inner))
        return found
    if isinstance(value, list):
        found = []
        for inner in value:
            found.extend(_scalars(inner))
        return found
    return [value]


def _passenger_by_id(passengers, passenger_id):
    matches = [pax for pax in passengers if pax.get("passengerId") == passenger_id]
    assert matches, f"passenger {passenger_id} was not stored"
    return matches[0]


def _assert_has(passenger, expected, label):
    values = _scalars(passenger)
    accepted = {expected}
    if isinstance(expected, str) and expected.isdigit():
        accepted.add(int(expected))
    assert any(item in values for item in accepted), (
        f"{label} {expected!r} is missing from the stored passenger "
        f"{passenger.get('passengerId')}"
    )


def _run_ingest(booked_http_status):
    """POST one flight through the ingest route with Sabre answered locally.

    ``requests.post`` is the HTTP boundary in ``SabreClient._post``. Parsing,
    the four-display merge, conversion, and storage all run in product code.
    """
    lists = _lists()
    calls = []

    def post(url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        body = data.decode() if isinstance(data, bytes) else (data or "")
        calls.append({"action": action, "body": body})
        if action == "SessionCreateRQ":
            return _HttpResponse(200, _envelope("<SessionCreateRS/>"))
        if action == "SessionCloseRQ":
            return _HttpResponse(200, _envelope("<SessionCloseRS/>"))
        if action == "GetPassengerListRQ":
            for code, xml in lists.items():
                if f"<v4:DisplayCode>{code}</v4:DisplayCode>" in body:
                    status = booked_http_status if code == "RV" else 200
                    if status != 200:
                        return _HttpResponse(status, "display unavailable")
                    return _HttpResponse(200, xml)
            raise AssertionError("passenger list request has no known display code")
        if action == "ACS_FlightDetailRQ":
            # SabreClient._extract_body looks up this prefixed element first.
            return _HttpResponse(200, _empty_rs("ns3:ACS_FlightDetailRS"))
        if action == "Trip_SearchRQ":
            return _HttpResponse(200, _empty_rs("Trip_SearchRS"))
        if action == "Trip_ReportsRQ":
            return _HttpResponse(200, _empty_rs("Trip_ReportsRS"))
        if action == "VerifyFlightDetailsLLSRQ":
            return _HttpResponse(200, _empty_rs("VerifyFlightDetailsRS"))
        raise AssertionError(f"unexpected SOAP action {action}")

    db = MemoryDB()
    _install_db(db)
    payload = SabreIngestRequest(
        airline="GF",
        flightNumber="2057",
        origin="BOM",
        departureDate=date(2026, 3, 19),
        departureDateTime=datetime(2026, 3, 19, 17, 40, 0),
    )
    with patch("backend.sabre.client.requests.post", post):
        result = asyncio.run(ingest_flight(payload))

    assert result["result"]["success"] is True
    passenger_list = result["result"]["apis"]["passengerList"]
    assert passenger_list["status"] == "success"
    screen = get_passengers(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
    )
    archived = [
        doc for doc in db["sabre_requests"].docs
        if doc.get("apiType") == "PassengerList"
    ]
    return {
        "calls": calls,
        "passengers": screen["passengers"],
        "archived": archived,
        "booked_http_status": booked_http_status,
    }


@pytest.fixture(params=[200, 500], ids=["booked-display-first", "booked-display-failed"])
def ingested(request):
    return _run_ingest(request.param)


def test_four_passenger_displays_are_requested(ingested):
    passenger_calls = [
        call for call in ingested["calls"]
        if call["action"] == "GetPassengerListRQ"
    ]
    bodies = "\n".join(call["body"] for call in passenger_calls)
    for code in ("RV", "XRV", "BP", "BT", "NS", "OFL", "AE"):
        assert f"<v4:DisplayCode>{code}</v4:DisplayCode>" in bodies


def test_later_display_details_reach_stored_passengers(ingested):
    """Item status, quantity, and document number survive first-display dedup."""
    booked = _passenger_by_id(ingested["passengers"], BOOKED_TRAVELER["pid"])
    checked_in_only = _passenger_by_id(ingested["passengers"], CHECKED_IN_ONLY["pid"])
    no_show = _passenger_by_id(ingested["passengers"], NO_SHOW_ONLY["pid"])

    _assert_has(booked, BOOKED_AE_STATUS, "air-extra status")
    _assert_has(booked, BOOKED_AE_QTY, "air-extra quantity")
    _assert_has(booked, BOOKED_AE_DOC, "air-extra document number")
    _assert_has(booked, BOOKED_AE_CODE, "air-extra code")
    _assert_has(booked, "PREPAID BAG", "air-extra description")
    _assert_has(booked, BOOKED_BAG_TAG, "missing-bag tag")
    if ingested["booked_http_status"] == 200:
        # The seat exists only on the booked display. Later displays must be
        # merged into that record, not replace it.
        _assert_has(booked, "12A", "booked seat")

    _assert_has(checked_in_only, CHECKED_IN_AE_DOC, "later air-extra document number")
    _assert_has(checked_in_only, CHECKED_IN_AE_QTY, "later air-extra quantity")
    _assert_has(no_show, NO_SHOW_ONLY["last"], "no-show traveler")


def test_each_passenger_list_response_is_archived(ingested):
    """The raw archive keeps every successful display body, not only the first."""
    archived_xml = "\n".join(
        doc.get("responseXml") or "" for doc in ingested["archived"]
    )
    markers = ["checkedin-display", "noshow-display", "airextra-display"]
    if ingested["booked_http_status"] == 200:
        markers.insert(0, "booked-display")
    for marker in markers:
        assert marker in archived_xml, (
            f"raw archive is missing the {marker} GetPassengerList response"
        )
