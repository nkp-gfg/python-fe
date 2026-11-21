"""Reservation segments must keep Sabre SegmentSpecialRequests.

These tests fail while conversion drops each air segment's special-request
list and while the reservation response model has nowhere to put it. They
are the spec for the fix:

* Read SegmentSpecialRequests on every air segment.
* Store every generic, seat, and wheelchair request with kind, code,
  freeText, actionCode, airline, numberInParty (int), ticketNumber, and
  confirmed.
* confirmed is true only when actionCode is exactly HK.
* fareBrand is the first BRND free text with one leading slash removed.
* fareBrandConfirmed is true only when at least one BRND request exists and
  every BRND request has actionCode HK.
* The current reservations endpoint, the snapshot view of that endpoint, and
  the per-PNR endpoint all return those fields.

Untested on purpose: schedule segments from VerifyFlightDetails have no
SegmentSpecialRequests, and the dashboard special-request summary counts
passenger meal and wheelchair codes already copied from passenger
SpecialRequests. Batch ingestion calls the same run_feeder path exercised
here.
"""

import copy
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.main import app
from backend.feeder import storage
from backend.sabre.client import SabreClient, SabreError

SAMPLE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "Trip_SearchRS_GF2057_BOM_2026-03-18.xml",
)

_TOKEN_RE = re.compile(
    r"(<wsse:BinarySecurityToken[^>]*>)(.*?)(</wsse:BinarySecurityToken>)",
    re.DOTALL,
)


def _load_sample_xml():
    with open(SAMPLE_PATH, encoding="utf-8") as handle:
        xml = handle.read()
    return _TOKEN_RE.sub(r"\1placeholder\3", xml)


def _meta():
    return {
        "requestXml": "<Trip_SearchRQ/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": None,
        "conversationId": "test-conversation",
    }


def _parse_trip_search(xml_text):
    """Same parse the Sabre client uses before conversion."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS"), xml_text


class _SabreStub:
    """Stand-in for the SOAP session. Trip Search still uses the real parser."""

    reservations_xml = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, *args, **kwargs):
        return {}, "<ACS_FlightDetailRS/>", _meta()

    def get_passenger_list(self, *args, **kwargs):
        return {}, "<GetPassengerListRS/>", _meta()

    def get_reservations(self, *args, **kwargs):
        xml = self.reservations_xml
        if xml is None:
            xml = _load_sample_xml()
        data, xml_text = _parse_trip_search(xml)
        return data, xml_text, _meta()

    def get_trip_report(self, *args, **kwargs):
        raise SabreError("trip report unused")

    def verify_flight_details(self, *args, **kwargs):
        raise SabreError("schedule unused")


def _match_value(actual, expected):
    if isinstance(expected, dict) and "$lte" in expected:
        return actual is not None and actual <= expected["$lte"]
    return actual == expected


def _matches(doc, query):
    if not query:
        return True
    for key, expected in query.items():
        if "." in key:
            parent, child = key.split(".", 1)
            items = doc.get(parent)
            if not isinstance(items, list) or not any(
                isinstance(item, dict) and _match_value(item.get(child), expected)
                for item in items
            ):
                return False
            continue
        if not _match_value(doc.get(key), expected):
            return False
    return True


def _sorted(docs, sort):
    ordered = list(docs)
    if not sort:
        return ordered
    for field, direction in reversed(list(sort)):
        ordered.sort(
            key=lambda doc, field=field: (
                doc.get(field) is None,
                "" if doc.get(field) is None else doc.get(field),
            ),
            reverse=direction < 0,
        )
    return ordered


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        return _Cursor(self._docs[:count])

    def __iter__(self):
        return iter(self._docs)


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _UpdateResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class _Collection:
    """In-memory stand-in for the Mongo collections these routes write and read.

    insert_one stores the document unchanged. find_one returns a copy, which is
    what the reservation route then strips and what FastAPI validates.
    """

    def __init__(self):
        self._docs = []
        self._next_id = 1

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = self._next_id
        self._next_id += 1
        self._docs.append(stored)
        return _InsertOneResult(stored["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertManyResult(ids)

    def find(self, query=None, projection=None, sort=None):
        found = [
            copy.deepcopy(doc) for doc in self._docs if _matches(doc, query or {})
        ]
        return _Cursor(_sorted(found, sort))

    def find_one(self, query=None, *args, **kwargs):
        sort = kwargs.get("sort")
        matches = _sorted(
            [doc for doc in self._docs if _matches(doc, query or {})],
            sort,
        )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def update_one(self, query, update, upsert=False):
        matches = [doc for doc in self._docs if _matches(doc, query or {})]
        if not matches:
            if not upsert:
                return _UpdateResult(0)
            created = copy.deepcopy(query)
            self._docs.append(created)
            matches = [created]
        target = matches[0]
        for key, value in (update.get("$setOnInsert") or {}).items():
            target.setdefault(key, copy.deepcopy(value))
        for key, value in (update.get("$set") or {}).items():
            target[key] = copy.deepcopy(value)
        for key, value in (update.get("$inc") or {}).items():
            target[key] = target.get(key, 0) + value
        return _UpdateResult(1)

    def index_information(self):
        return {"flight_lookup": {}}

    def create_index(self, *args, **kwargs):
        return "unused"

    def drop_index(self, *args, **kwargs):
        return None


class _MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]

    def command(self, name):
        if name == "ping":
            return {"ok": 1}
        raise AssertionError(f"unexpected command {name}")


def _segment(body, pnr, flight_number):
    reservations = body["reservations"]
    for reservation in reservations:
        if reservation.get("pnr") != pnr:
            continue
        for segment in reservation.get("segments", []):
            if segment.get("flightNumber") == flight_number:
                return segment
    raise AssertionError(
        f"segment {flight_number} for {pnr} missing from {list(body)[:6]}"
    )


def _assert_request(actual, expected):
    assert actual["kind"] == expected["kind"]
    assert actual["code"] == expected["code"]
    assert actual["freeText"] == expected["freeText"]
    assert actual["actionCode"] == expected["actionCode"]
    assert actual["airline"] == expected["airline"]
    assert actual["numberInParty"] == expected["numberInParty"]
    assert isinstance(actual["numberInParty"], int)
    assert actual["ticketNumber"] == expected["ticketNumber"]
    assert actual["confirmed"] is expected["confirmed"]


def _assert_segment(segment, fare_brand, fare_brand_confirmed, requests):
    assert segment["fareBrand"] == fare_brand
    assert segment["fareBrandConfirmed"] is fare_brand_confirmed
    assert len(segment["specialRequests"]) == len(requests)
    for actual, expected in zip(segment["specialRequests"], requests):
        _assert_request(actual, expected)


def _stored_segment(db, flight_number, pnr, segment_flight):
    doc = db["reservations"].find_one({"flightNumber": flight_number})
    assert doc is not None
    return _segment(doc, pnr, segment_flight)


@pytest.fixture
def api(monkeypatch):
    database = _MemoryDB()
    monkeypatch.setattr(storage, "_db", database)
    monkeypatch.setattr(storage, "_owns_connection", False)
    monkeypatch.setattr("backend.api.main.get_db", lambda: database)
    monkeypatch.setattr("backend.api.routes.reservations.get_db", lambda: database)
    monkeypatch.setattr("backend.api.main.get_redis", _redis_disabled)
    monkeypatch.setattr("backend.feeder.runner.SabreClient", _SabreStub)
    with TestClient(app) as client:
        yield client, database


def _redis_disabled():
    raise RuntimeError("redis disabled in test")


def _ingest(client, flight_number, xml_text=None):
    if xml_text is not None:
        _SabreStub.reservations_xml = xml_text
    else:
        _SabreStub.reservations_xml = None
    response = client.post(
        "/flights/ingest",
        json={
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": "BOM",
            "departureDate": "2026-03-18",
            "departureDateTime": "2026-03-18T17:40:00",
            "flightSequenceNumber": 1,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["apis"]["reservations"]["status"] == "success"
    return body


def _views(client, flight_number, pnr):
    current = client.get(
        f"/flights/{flight_number}/reservations",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert current.status_code == 200, current.text
    historical = client.get(
        f"/flights/{flight_number}/reservations",
        params={"origin": "BOM", "date": "2026-03-18", "snapshot_sequence": 1},
    )
    assert historical.status_code == 200, historical.text
    by_pnr = client.get(
        f"/flights/{flight_number}/reservations/{pnr}",
        params={"date": "2026-03-18"},
    )
    assert by_pnr.status_code == 200, by_pnr.text
    return current.json(), historical.json(), by_pnr.json()


def _trip_xml(locator, segments_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1" TotalResults="1">
        <Reservations>
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-16T22:53:00</stl19:CreationTimestamp>
                  <stl19:UpdateTimestamp>2026-03-17T10:48:33</stl19:UpdateTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers/>
                  <stl19:Segments>
                    {segments_xml}
                  </stl19:Segments>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _air_segment(flight_number, requests_xml):
    return f"""
<stl19:Segment>
  <stl19:Air>
    <stl19:DepartureAirport>BOM</stl19:DepartureAirport>
    <stl19:ArrivalAirport>DMM</stl19:ArrivalAirport>
    <stl19:DepartureDateTime>2026-03-18T17:40:00</stl19:DepartureDateTime>
    <stl19:ArrivalDateTime>2026-03-18T20:10:00</stl19:ArrivalDateTime>
    <stl19:MarketingAirlineCode>GF</stl19:MarketingAirlineCode>
    <stl19:FlightNumber>{flight_number}</stl19:FlightNumber>
    <stl19:ClassOfService>Q</stl19:ClassOfService>
    <stl19:ActionCode>HK</stl19:ActionCode>
    <stl19:SegmentSpecialRequests>
      {requests_xml}
    </stl19:SegmentSpecialRequests>
  </stl19:Air>
</stl19:Segment>
"""


GF2057_REQUESTS = [
    {
        "kind": "generic",
        "code": "TKNE",
        "freeText": "GF HK1 BOMDMM2057Q18MAR/0727472959677C1",
        "actionCode": "HK",
        "airline": "GF",
        "numberInParty": 1,
        "ticketNumber": "0727472959677",
        "confirmed": True,
    },
    {
        "kind": "generic",
        "code": "BRND",
        "freeText": "/YL",
        "actionCode": "NN",
        "airline": "GF",
        "numberInParty": 1,
        "ticketNumber": "",
        "confirmed": False,
    },
    {
        "kind": "generic",
        "code": "BRND",
        "freeText": "/YL",
        "actionCode": "NN",
        "airline": "GF",
        "numberInParty": 1,
        "ticketNumber": "",
        "confirmed": False,
    },
]


def test_ingested_gf2057_keeps_unconfirmed_brand_and_ticket(api):
    client, database = api
    parsed, _xml = _parse_trip_search(_load_sample_xml())
    reservation = parsed["ReservationsList"]["Reservations"]["Reservation"][0]
    air = reservation["stl19:GetReservationRS"]["stl19:Reservation"][
        "stl19:PassengerReservation"
    ]["stl19:Segments"]["stl19:Segment"]["stl19:Air"]
    requests = air["stl19:SegmentSpecialRequests"]["stl19:GenericSpecialRequest"]
    assert [item["stl19:Code"] for item in requests] == ["TKNE", "BRND", "BRND"]
    assert requests[1]["stl19:FreeText"] == "/YL"
    assert requests[1]["stl19:ActionCode"] == "NN"

    _ingest(client, "2057")

    stored = _stored_segment(database, "2057", "GCPVKA", "2057")
    _assert_segment(stored, "YL", False, GF2057_REQUESTS)

    current, historical, by_pnr = _views(client, "2057", "GCPVKA")
    for body in (current, historical, by_pnr):
        segment = _segment(body, "GCPVKA", "2057")
        _assert_segment(segment, "YL", False, GF2057_REQUESTS)


def test_segment_request_shapes_survive_ingestion_and_response_model(api):
    client, database = api
    mixed = _air_segment(
        "2058",
        """
        <stl19:GenericSpecialRequest>
          <stl19:Code>BRND</stl19:Code>
          <stl19:FreeText>/YL</stl19:FreeText>
          <stl19:ActionCode>HK</stl19:ActionCode>
          <stl19:NumberInParty>1</stl19:NumberInParty>
          <stl19:AirlineCode>GF</stl19:AirlineCode>
        </stl19:GenericSpecialRequest>
        <stl19:GenericSpecialRequest>
          <stl19:Code>BRND</stl19:Code>
          <stl19:FreeText>/XX</stl19:FreeText>
          <stl19:ActionCode>KK</stl19:ActionCode>
          <stl19:NumberInParty>2</stl19:NumberInParty>
          <stl19:AirlineCode>GF</stl19:AirlineCode>
        </stl19:GenericSpecialRequest>
        <stl19:SeatRequest>
          <stl19:SeatCode>NSST</stl19:SeatCode>
          <stl19:VendorCode>GF</stl19:VendorCode>
          <stl19:Comment>AISLE</stl19:Comment>
          <stl19:ActionCode>NN</stl19:ActionCode>
          <stl19:NumberInParty>2</stl19:NumberInParty>
        </stl19:SeatRequest>
        <stl19:WheelchairRequest>
          <stl19:WheelchairCode>WCHR</stl19:WheelchairCode>
          <stl19:FreeText>NEED ASSIST</stl19:FreeText>
          <stl19:VendorCode>GF</stl19:VendorCode>
          <stl19:ActionCode>HK</stl19:ActionCode>
          <stl19:NumberInParty>1</stl19:NumberInParty>
        </stl19:WheelchairRequest>
        <stl19:GenericSpecialRequest>
          <stl19:Code>OTHS</stl19:Code>
          <stl19:FreeText>NOTE</stl19:FreeText>
          <stl19:ActionCode></stl19:ActionCode>
          <stl19:NumberInParty>1</stl19:NumberInParty>
          <stl19:AirlineCode>GF</stl19:AirlineCode>
        </stl19:GenericSpecialRequest>
        """,
    )
    confirmed = _air_segment(
        "2059",
        """
        <stl19:GenericSpecialRequest>
          <stl19:Code>BRND</stl19:Code>
          <stl19:FreeText>YL</stl19:FreeText>
          <stl19:ActionCode>HK</stl19:ActionCode>
          <stl19:NumberInParty>1</stl19:NumberInParty>
          <stl19:AirlineCode>GF</stl19:AirlineCode>
          <stl19:TicketNumber></stl19:TicketNumber>
        </stl19:GenericSpecialRequest>
        """,
    )
    xml = _trip_xml("SYN001", mixed + confirmed)
    _ingest(client, "2058", xml)

    mixed_expected = [
        {
            "kind": "generic",
            "code": "BRND",
            "freeText": "/YL",
            "actionCode": "HK",
            "airline": "GF",
            "numberInParty": 1,
            "ticketNumber": "",
            "confirmed": True,
        },
        {
            "kind": "generic",
            "code": "BRND",
            "freeText": "/XX",
            "actionCode": "KK",
            "airline": "GF",
            "numberInParty": 2,
            "ticketNumber": "",
            "confirmed": False,
        },
        {
            "kind": "seat",
            "code": "NSST",
            "freeText": "AISLE",
            "actionCode": "NN",
            "airline": "GF",
            "numberInParty": 2,
            "ticketNumber": "",
            "confirmed": False,
        },
        {
            "kind": "wheelchair",
            "code": "WCHR",
            "freeText": "NEED ASSIST",
            "actionCode": "HK",
            "airline": "GF",
            "numberInParty": 1,
            "ticketNumber": "",
            "confirmed": True,
        },
        {
            "kind": "generic",
            "code": "OTHS",
            "freeText": "NOTE",
            "actionCode": "",
            "airline": "GF",
            "numberInParty": 1,
            "ticketNumber": "",
            "confirmed": False,
        },
    ]
    confirmed_expected = [
        {
            "kind": "generic",
            "code": "BRND",
            "freeText": "YL",
            "actionCode": "HK",
            "airline": "GF",
            "numberInParty": 1,
            "ticketNumber": "",
            "confirmed": True,
        }
    ]

    stored_mixed = _stored_segment(database, "2058", "SYN001", "2058")
    stored_confirmed = _stored_segment(database, "2058", "SYN001", "2059")
    _assert_segment(stored_mixed, "YL", False, mixed_expected)
    _assert_segment(stored_confirmed, "YL", True, confirmed_expected)

    current, historical, by_pnr = _views(client, "2058", "SYN001")
    for body in (current, historical, by_pnr):
        _assert_segment(_segment(body, "SYN001", "2058"), "YL", False, mixed_expected)
        _assert_segment(
            _segment(body, "SYN001", "2059"), "YL", True, confirmed_expected
        )
