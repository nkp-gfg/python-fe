"""Seat-receipt free text must survive passenger-list ingestion and the detail API.

Sabre puts a paid seat's electronic miscellaneous document number in
FreeTextInfoList, as Text values under TextLine, with edit code AE. These
tests run that XML through the product's SOAP parser, the feeder's four
display-code merge, convert_passenger_list, and the passenger list and
detail routes.

They fail while that receipt text is dropped. The stored passenger and the
list response must expose it on freeText as edit code plus the Text values.
A copy that exists only on the raw Sabre document does not count: the list
route removes _raw before it responds.

Expected freeText entry shape on each passenger:

    {"editCode": "<EditCode>", "text": "<one Text>" or ["<Text>", ...]}
"""

import asyncio
import copy
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.ingestion import SabreIngestRequest, ingest_flight
from backend.api.routes.passengers import get_passenger_detail, get_passengers
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient, SabreError
from backend.sabre import templates


RECEIPT_ONLY_ON_BOOKED = (
    "AE-GF 2052/DMMMAA/SA  SA /HI/1 /0    -STANDARD SEAT             "
    "EMD-1111111111111 C01"
)
RECEIPT_ONLY_ON_AE = "EMD-2222222222222 C01"
AE_SECOND_TEXT = "COUPON-02"
RECEIPT_AE_ONLY_PASSENGER = "EMD-3333333333333 C01"
DOCS_TEXT = "P/BH/000/BH/01JAN80/M/01JAN30/AEONLY/PAX"
DETAIL_RECEIPT = "EMD-4444444444444 C01"

_META = {
    "requestXml": "<placeholder-request/>",
    "httpStatus": 200,
    "durationMs": 1,
    "sessionToken": "placeholder-session",
    "conversationId": "placeholder-conversation",
}


def _envelope(response_element):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<eb:MessageHeader xmlns:eb="http://www.ebxml.org/namespaces/messageHeader">'
        "<eb:Action>GetPassengerListRQ</eb:Action>"
        "</eb:MessageHeader>"
        "</soap-env:Header>"
        "<soap-env:Body>"
        f"{response_element}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _passenger_xml(last_name, first_name, pnr, passenger_id, seat, free_text_xml):
    """One PassengerInfo in the shape of a GetPassengerListRS traveler."""
    free_text = f"<FreeTextInfoList>{free_text_xml}</FreeTextInfoList>" if free_text_xml else ""
    return f"""
        <PassengerInfo>
            <Name_Details>
                <LastName>{last_name}</LastName>
                <FirstName>{first_name}</FirstName>
            </Name_Details>
            <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
            <PassengerID>{passenger_id}</PassengerID>
            <LineNumber>1</LineNumber>
            <BookingClass>L</BookingClass>
            <Cabin>Y</Cabin>
            <Seat>{seat}</Seat>
            <Destination>MAA</Destination>
            <BoardingPassFlag>false</BoardingPassFlag>
            <PassengerType>F</PassengerType>
            <BagCount>0</BagCount>
            <EditCodeList>
                <ns2:EditCode>AE</ns2:EditCode>
                <ns2:EditCode>DOCS</ns2:EditCode>
            </EditCodeList>
            {free_text}
        </PassengerInfo>
    """


def _free_text_info(edit_code, texts):
    text_nodes = "".join(f"<ns2:Text>{text}</ns2:Text>" for text in texts)
    return f"""
        <ns2:FreeTextInfo>
            <ns2:EditCode>{edit_code}</ns2:EditCode>
            <ns2:TextLine>{text_nodes}</ns2:TextLine>
        </ns2:FreeTextInfo>
    """


def _list_response(passengers_xml):
    body = f"""
        <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4"
                            xmlns:ns2="http://services.sabre.com/STL/v3">
            <ItineraryInfo>
                <Itinerary>
                    <Airline>GF</Airline>
                    <Flight>2052</Flight>
                    <DepartureDate>2026-03-29</DepartureDate>
                    <Origin>DMM</Origin>
                </Itinerary>
            </ItineraryInfo>
            <PassengerInfoList>
                {passengers_xml}
            </PassengerInfoList>
            <Result><ns2:Status>Success</ns2:Status></Result>
        </GetPassengerListRS>
    """
    return _envelope(body)


def _booked_passengers():
    """Booked display: one traveler already carrying receipt text, one without."""
    with_receipt = _passenger_xml(
        "ONLYBOOK", "PAX", "BOOK01", "PAXBOOK", "23B",
        _free_text_info("AE", [RECEIPT_ONLY_ON_BOOKED]),
    )
    without_receipt = _passenger_xml(
        "BOTH", "PAX", "BOTH01", "PAXBOTH", "22A", "",
    )
    return _list_response(with_receipt + without_receipt)


def _ae_passengers():
    """Air-extra display: receipt text for the shared traveler, plus a new one."""
    both = _passenger_xml(
        "BOTH", "PAX", "BOTH01", "PAXBOTH", "22A",
        _free_text_info("AE", [RECEIPT_ONLY_ON_AE, AE_SECOND_TEXT]),
    )
    only_ae = _passenger_xml(
        "AEONLY", "PAX", "AE0001", "PAXAE", "18F",
        _free_text_info("AE", [RECEIPT_AE_ONLY_PASSENGER])
        + _free_text_info("DOCS", [DOCS_TEXT]),
    )
    return _list_response(both + only_ae)


def _empty_list():
    return _list_response("")


def _detail_response():
    body = f"""
        <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/ACS/BSO/getPassengerData/v3"
                                xmlns:ns2="http://services.sabre.com/STL/v3">
            <PassengerDataResponseList>
                <PassengerDataResponse>
                    <LastName>BOTH</LastName>
                    <FirstName>PAX</FirstName>
                    <PassengerID>PAXBOTH</PassengerID>
                    <PNRLocator>BOTH01</PNRLocator>
                    <FreeTextInfoList>
                        {_free_text_info("AE", [DETAIL_RECEIPT, AE_SECOND_TEXT])}
                    </FreeTextInfoList>
                </PassengerDataResponse>
            </PassengerDataResponseList>
        </ns3:GetPassengerDataRS>
    """
    return _envelope(body)


def _parse_body(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, spec):
        for key, direction in reversed(spec):
            self._docs.sort(key=lambda doc: doc.get(key, 0), reverse=direction < 0)
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


def _dotted_match(doc, parts, expected):
    if not parts:
        return doc == expected
    if not isinstance(doc, dict):
        return False
    head, *rest = parts
    value = doc.get(head)
    if isinstance(value, list):
        return any(
            _dotted_match(item, rest, expected)
            for item in value
            if isinstance(item, dict)
        )
    if rest:
        return _dotted_match(value, rest, expected)
    return value == expected


def _matches(doc, query):
    for key, expected in (query or {}).items():
        if isinstance(expected, dict) and any(str(op).startswith("$") for op in expected):
            actual = doc.get(key)
            if "$lte" in expected and not (
                actual is not None and actual <= expected["$lte"]
            ):
                return False
            continue
        if "." in key:
            if not _dotted_match(doc, key.split("."), expected):
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


def _apply_update(doc, update, inserted):
    if inserted:
        for key, value in (update.get("$setOnInsert") or {}).items():
            doc[key] = copy.deepcopy(value)
    for key, value in (update.get("$set") or {}).items():
        doc[key] = copy.deepcopy(value)
    for key, value in (update.get("$inc") or {}).items():
        doc[key] = doc.get(key, 0) + value


class _Collection:
    def __init__(self):
        self.docs = []
        self._next_id = 1

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = f"mem-{self._next_id}"
        self._next_id += 1
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def insert_many(self, docs):
        return _InsertManyResult([self.insert_one(doc).inserted_id for doc in docs])

    def find_one(self, query=None, sort=None, projection=None):
        docs = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for key, direction in reversed(sort):
                docs.sort(key=lambda doc: doc.get(key, 0), reverse=direction < 0)
        if not docs:
            return None
        return copy.deepcopy(docs[0])

    def find(self, query=None, projection=None, sort=None):
        docs = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        cursor = _Cursor(docs)
        if sort:
            cursor.sort(sort)
        return cursor

    def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update, inserted=False)
                return
        if upsert:
            created = {}
            for key, value in query.items():
                if not isinstance(value, dict):
                    created[key] = copy.deepcopy(value)
            _apply_update(created, update, inserted=True)
            self.docs.append(created)


class _MemoryDb:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


class _FeederClient:
    """Stand-in for the Sabre session. Responses are still parsed by SabreClient."""

    def __init__(self, passenger_lists):
        self._passenger_lists = passenger_lists

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        body = {
            "ItineraryResponseList": {
                "ItineraryInfoResponse": {"FlightStatus": "OPEN"}
            },
            "Result": {"Status": "Success"},
        }
        return body, "<flight-status/>", dict(_META)

    def get_passenger_list(self, airline, flight_number, departure_date, origin,
                           display_codes=None):
        xml_text = self._passenger_lists.get(display_codes)
        if xml_text is None:
            raise SabreError("passenger list display unavailable")
        body = _parse_body(xml_text, "GetPassengerListRS")
        return body, xml_text, dict(_META)

    def get_reservations(self, airline, flight_number, departure_airport, departure_datetime):
        return {}, "<reservations/>", dict(_META)

    def get_trip_report(self, airline, flight_number, departure_date, origin, report_type):
        return {}, "<trip-report/>", dict(_META)

    def verify_flight_details(self, airline, flight_number, departure_date,
                              origin, destination=""):
        return {}, "<schedule/>", dict(_META)


class _DetailClient:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_passenger_data(self, airline, flight_number, departure_date, origin,
                           last_name, first_name=None, pnr=None):
        xml_text = _detail_response()
        body = _parse_body(xml_text, "GetPassengerDataRS")
        return body, xml_text, dict(_META)


def _texts_for(passenger, edit_code):
    assert "freeText" in passenger, passenger.keys()
    found = []
    for entry in passenger["freeText"]:
        assert isinstance(entry, dict), entry
        if entry.get("editCode") != edit_code:
            continue
        text = entry.get("text", "")
        if isinstance(text, list):
            found.extend(str(item) for item in text if item)
        elif text:
            found.append(str(text))
    return found


def _assert_lines(passenger, edit_code, lines):
    stored = "\n".join(_texts_for(passenger, edit_code))
    for line in lines:
        assert line in stored, (edit_code, lines, passenger.get("freeText"))


def _passenger_by_id(passengers, passenger_id):
    matches = [pax for pax in passengers if pax.get("passengerId") == passenger_id]
    assert len(matches) == 1, passengers
    return matches[0]


def _ingest(memory_db, passenger_lists):
    storage.init_db(memory_db)
    payload = SabreIngestRequest(
        airline="GF",
        flightNumber="2052",
        origin="DMM",
        departureDate=date(2026, 3, 29),
        departureDateTime=datetime(2026, 3, 29, 17, 0, 0),
        flightSequenceNumber=1,
    )

    class _Client(_FeederClient):
        def __init__(self):
            super().__init__(passenger_lists)

    original_client = run_feeder.__globals__["SabreClient"]
    run_feeder.__globals__["SabreClient"] = _Client
    try:
        result = asyncio.run(ingest_flight(payload))
    finally:
        run_feeder.__globals__["SabreClient"] = original_client
    assert result["result"]["apis"]["passengerList"]["status"] == "success"
    return result


def _list_passengers(memory_db):
    original = get_passengers.__globals__["get_db"]
    get_passengers.__globals__["get_db"] = lambda: memory_db
    try:
        # Omitted query params resolve to None. Passing that explicitly matches
        # the value FastAPI injects; the Query default object is not a sequence.
        document = get_passengers(
            "2052", origin="DMM", date="2026-03-29", snapshot_sequence=None,
        )
    finally:
        get_passengers.__globals__["get_db"] = original
    assert "_raw" not in document
    for passenger in document["passengers"]:
        assert "_raw" not in passenger
    return document["passengers"]


def test_merged_displays_keep_seat_receipt_on_passenger_list():
    """Booked display runs before the air-extra display, as the feeder does.

    The shared traveler's receipt exists only on the later AE response.
    Another traveler's receipt exists only on the booked response.
    A third traveler exists only on the AE response, with two free-text entries.
    """
    memory_db = _MemoryDb()
    try:
        _ingest(memory_db, {
            templates.DISPLAY_CODES_BOOKED: _booked_passengers(),
            templates.DISPLAY_CODES_CHECKEDIN: _booked_passengers(),
            templates.DISPLAY_CODES_NOSHOW_OFL: _empty_list(),
            templates.DISPLAY_CODES_ALL: _ae_passengers(),
        })
        passengers = _list_passengers(memory_db)
    finally:
        storage._db = None

    booked = _passenger_by_id(passengers, "PAXBOOK")
    assert booked["seat"] == "23B"
    assert "AE" in booked["editCodes"]
    _assert_lines(booked, "AE", [RECEIPT_ONLY_ON_BOOKED])

    shared = _passenger_by_id(passengers, "PAXBOTH")
    assert shared["seat"] == "22A"
    assert "AE" in shared["editCodes"]
    _assert_lines(shared, "AE", [RECEIPT_ONLY_ON_AE, AE_SECOND_TEXT])

    extra = _passenger_by_id(passengers, "PAXAE")
    assert extra["seat"] == "18F"
    assert "AE" in extra["editCodes"]
    _assert_lines(extra, "AE", [RECEIPT_AE_ONLY_PASSENGER])
    _assert_lines(extra, "DOCS", [DOCS_TEXT])


def test_air_extra_display_alone_keeps_seat_receipt_on_passenger_list():
    """Earlier display calls fail, so the AE response is the document that is stored."""
    memory_db = _MemoryDb()
    try:
        _ingest(memory_db, {
            templates.DISPLAY_CODES_ALL: _ae_passengers(),
        })
        passengers = _list_passengers(memory_db)
    finally:
        storage._db = None

    shared = _passenger_by_id(passengers, "PAXBOTH")
    assert shared["seat"] == "22A"
    assert "AE" in shared["editCodes"]
    _assert_lines(shared, "AE", [RECEIPT_ONLY_ON_AE, AE_SECOND_TEXT])

    extra = _passenger_by_id(passengers, "PAXAE")
    _assert_lines(extra, "AE", [RECEIPT_AE_ONLY_PASSENGER])
    _assert_lines(extra, "DOCS", [DOCS_TEXT])


def test_passenger_detail_keeps_textline_receipt():
    """GetPassengerDataRS uses the same Text-under-TextLine free-text list."""
    memory_db = _MemoryDb()
    memory_db["passenger_list"].insert_one({
        "flightNumber": "2052",
        "origin": "DMM",
        "departureDate": "2026-03-29",
        "fetchedAt": "2026-03-29T00:00:00Z",
        "passengers": [{
            "pnr": "BOTH01",
            "lastName": "BOTH",
            "firstName": "PAX",
            "passengerId": "PAXBOTH",
        }],
    })
    original_db = get_passenger_detail.__globals__["get_db"]
    original_client = get_passenger_detail.__globals__["SabreClient"]
    get_passenger_detail.__globals__["get_db"] = lambda: memory_db
    get_passenger_detail.__globals__["SabreClient"] = _DetailClient
    try:
        document = get_passenger_detail(
            "2052", "BOTH01", origin="DMM", date="2026-03-29", airline="GF",
        )
    finally:
        get_passenger_detail.__globals__["get_db"] = original_db
        get_passenger_detail.__globals__["SabreClient"] = original_client

    passenger = document["passengers"][0]
    _assert_lines(passenger, "AE", [DETAIL_RECEIPT, AE_SECOND_TEXT])


if __name__ == "__main__":
    failures = 0
    for test in (
        test_merged_displays_keep_seat_receipt_on_passenger_list,
        test_air_extra_display_alone_keeps_seat_receipt_on_passenger_list,
        test_passenger_detail_keeps_textline_receipt,
    ):
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    if failures:
        raise SystemExit(1)
    print("ALL TESTS PASS")
