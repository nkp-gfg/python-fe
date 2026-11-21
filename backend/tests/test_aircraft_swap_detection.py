"""Aircraft type and registration changes must become flight events.

Ingestion already copies AircraftType and AircraftRegistration from a Sabre
flight-detail body, and AircraftType from a passenger-list body, into the
stored snapshot. Change detection has to emit one AIRCRAFT_CHANGE per changed
field, and the activity feed's flight category has to return those events.

Required change documents:

- flight status type: changeType AIRCRAFT_CHANGE, field aircraft.type
- flight status registration: changeType AIRCRAFT_CHANGE, field aircraft.registration
- passenger list type: changeType AIRCRAFT_CHANGE, field aircraftType

oldValue and newValue are the previous and current strings. The activity-feed
description has to contain both strings, because that response does not return
the raw values. The flight timeline has to list the same events with category
"flight". Equal equipment must not emit AIRCRAFT_CHANGE.

These tests fail while that comparison and category mapping are missing.
"""

import copy
from contextlib import contextmanager

from fastapi import HTTPException

from backend.api.routes import changes as changes_routes
from backend.api.routes.changes import get_activity_feed, get_flight_timeline
from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.feeder.runner import _process_api_call
from backend.feeder import storage as feeder_storage
from backend.sabre.client import SabreClient


FLIGHT_INFO = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}

AIRCRAFT_CHANGE = "AIRCRAFT_CHANGE"


def _flight_detail_xml(aircraft_type, registration, gate="67A"):
    """SOAP body shaped like ACS_FlightDetailRS, without session credentials."""
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
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>05:40PM</ScheduledDepartureTime>
          <DepartureGate>{gate}</DepartureGate>
          <DepartureTerminal>2</DepartureTerminal>
          <BoardingTime>05:00PM</BoardingTime>
          <FlightStatus>FINAL</FlightStatus>
          <AircraftType>{aircraft_type}</AircraftType>
          <AircraftConfigNumber>40</AircraftConfigNumber>
          <AircraftRegistration>{registration}</AircraftRegistration>
          <SeatConfig>12/180</SeatConfig>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <PassengerCounts classOfService="Y">
        <Booked>10</Booked>
        <TotalOnBoard>0</TotalOnBoard>
        <TotalBoardingPassIssued>0</TotalBoardingPassIssued>
      </PassengerCounts>
      <JumpSeat cockpitInUse="false" cabinInUse="false">
        <Cockpit>1</Cockpit>
        <Cabin>2</Cabin>
      </JumpSeat>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _passenger_list_xml(aircraft_type):
    """SOAP body shaped like GetPassengerListRS, without session credentials."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BOM</Origin>
          <Destination>DMM</Destination>
          <AircraftType>{aircraft_type}</AircraftType>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>BABOO</LastName>
            <FirstName>MOHIT MR</FirstName>
          </Name_Details>
          <PNRLocator>ESMMQK</PNRLocator>
          <PassengerID>CFBAAF430001</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>V</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>22A</Seat>
          <PassengerType>F</PassengerType>
          <BagCount>1</BagCount>
          <BoardingPassFlag>true</BoardingPassFlag>
          <CheckIn_Info>
            <CheckInStatus>true</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _parse_body(xml_text, response_key):
    """Same parse and body extraction SabreClient uses after the SOAP POST."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(SabreClient.__new__(SabreClient), parsed, response_key)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            for field, direct in reversed(key):
                self._docs.sort(
                    key=lambda doc, field=field: _sort_value(doc.get(field)),
                    reverse=direct < 0,
                )
        else:
            self._docs.sort(
                key=lambda doc: _sort_value(doc.get(key)),
                reverse=(direction or 1) < 0,
            )
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


def _sort_value(value):
    if value is None:
        return ""
    return value


def _matches(doc, query):
    if not query:
        return True
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return copy.deepcopy(doc)
    projected = copy.deepcopy(doc)
    excludes = [field for field, flag in projection.items() if flag == 0]
    includes = [field for field, flag in projection.items() if flag]
    if excludes and not includes:
        for field in excludes:
            projected.pop(field, None)
        return projected
    kept = {"_id": projected.get("_id")}
    for field in includes:
        if field in projected:
            kept[field] = projected[field]
    return kept


class _Collection:
    def __init__(self):
        self.docs = []
        self._next_id = 1

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = f"id-{self._next_id}"
        self._next_id += 1
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def insert_many(self, docs):
        ids = [self.insert_one(doc).inserted_id for doc in docs]
        return _InsertManyResult(ids)

    def find_one(self, query=None, sort=None, projection=None, **_kwargs):
        matched = [_project(doc, projection) for doc in self.docs if _matches(doc, query or {})]
        if sort:
            cursor = _Cursor(matched)
            cursor.sort(sort)
            matched = list(cursor)
        return matched[0] if matched else None

    def find(self, query=None, projection=None, skip=0, limit=0, sort=None, **_kwargs):
        matched = [_project(doc, projection) for doc in self.docs if _matches(doc, query or {})]
        cursor = _Cursor(matched)
        if sort:
            cursor.sort(sort)
        if skip:
            cursor._docs = cursor._docs[skip:]
        if limit:
            cursor.limit(limit)
        return cursor

    def update_one(self, query, update, upsert=False):
        return None


class _MemoryDb:
    """Stores the documents the feeder writes and applies the route queries.

    Checksums, conversion, and change detection stay in the product code.
    """

    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


def _bind_db(db):
    feeder_storage.init_db(db)
    changes_routes.get_db = lambda: db
    return db


@contextmanager
def _fetched_at(*stamps):
    """Pin snapshot timestamps so two identical bodies are not checksum duplicates."""
    from backend.feeder import converter

    pending = iter(stamps)
    original = converter._now_iso
    converter._now_iso = lambda: next(pending)
    try:
        yield
    finally:
        converter._now_iso = original


def _meta():
    return {
        "requestXml": "<request/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": None,
        "conversationId": "test-conversation",
    }


def _ingest_flight_status(db, xml_text):
    raw = _parse_body(xml_text, "ns3:ACS_FlightDetailRS")
    return _process_api_call(
        "FlightStatus",
        "flight_status",
        FLIGHT_INFO,
        raw,
        xml_text,
        _meta(),
        convert_flight_status,
        (
            raw,
            FLIGHT_INFO["airline"],
            FLIGHT_INFO["flightNumber"],
            FLIGHT_INFO["origin"],
            FLIGHT_INFO["departureDate"],
        ),
    )


def _ingest_passenger_list(db, xml_text):
    raw = _parse_body(xml_text, "GetPassengerListRS")
    return _process_api_call(
        "PassengerList",
        "passenger_list",
        FLIGHT_INFO,
        raw,
        xml_text,
        _meta(),
        convert_passenger_list,
        (
            raw,
            FLIGHT_INFO["airline"],
            FLIGHT_INFO["flightNumber"],
            FLIGHT_INFO["departureDate"],
            FLIGHT_INFO["origin"],
        ),
    )


def _stored_changes(db):
    return list(db["changes"].find({}))


def _aircraft_changes(db):
    return [doc for doc in _stored_changes(db) if doc.get("changeType") == AIRCRAFT_CHANGE]


def _by_field(changes):
    return {doc.get("field"): doc for doc in changes}


def _flight_feed(date="2026-03-19"):
    return get_activity_feed(date=date, limit=50, categories="flight")


def _timeline():
    try:
        return get_flight_timeline(
            FLIGHT_INFO["flightNumber"],
            origin=FLIGHT_INFO["origin"],
            date=FLIGHT_INFO["departureDate"],
        )
    except HTTPException as exc:
        return exc


def test_flight_status_stores_aircraft_from_sabre_body_then_drops_the_swap():
    db = _bind_db(_MemoryDb())
    first_xml = _flight_detail_xml("321", "A9CXF")
    second_xml = _flight_detail_xml("789", "A9CZZ")

    first = _ingest_flight_status(db, first_xml)
    parsed = _parse_body(first_xml, "ns3:ACS_FlightDetailRS")
    converted = convert_flight_status(
        parsed, "GF", "2057", "BOM", "2026-03-19")
    assert converted["aircraft"]["type"] == "321"
    assert converted["aircraft"]["registration"] == "A9CXF"
    assert first["isDuplicate"] is False

    second = _ingest_flight_status(db, second_xml)
    assert second["isDuplicate"] is False
    latest = db["flight_status"].docs[-1]
    assert latest["aircraft"]["type"] == "789"
    assert latest["aircraft"]["registration"] == "A9CZZ"

    feed = _flight_feed()
    timeline = _timeline()
    by_field = _by_field(_aircraft_changes(db))
    assert by_field.get("aircraft.type", {}).get("oldValue") == "321"
    assert by_field.get("aircraft.type", {}).get("newValue") == "789"
    assert by_field.get("aircraft.registration", {}).get("oldValue") == "A9CXF"
    assert by_field.get("aircraft.registration", {}).get("newValue") == "A9CZZ"

    feed_events = [event for event in feed["events"] if event["eventType"] == AIRCRAFT_CHANGE]
    descriptions = {event["description"] for event in feed_events}
    assert all(event["category"] == "flight" for event in feed_events)
    assert any("321" in text and "789" in text for text in descriptions)
    assert any("A9CXF" in text and "A9CZZ" in text for text in descriptions)

    assert not isinstance(timeline, HTTPException)
    timeline_events = [
        event for event in timeline["events"]
        if event["eventType"] == AIRCRAFT_CHANGE
    ]
    timeline_fields = {event["details"]["field"]: event for event in timeline_events}
    assert timeline_fields["aircraft.type"]["category"] == "flight"
    assert timeline_fields["aircraft.type"]["details"]["oldValue"] == "321"
    assert timeline_fields["aircraft.type"]["details"]["newValue"] == "789"
    assert timeline_fields["aircraft.registration"]["details"]["oldValue"] == "A9CXF"
    assert timeline_fields["aircraft.registration"]["details"]["newValue"] == "A9CZZ"


def test_registration_only_swap_emits_one_aircraft_event():
    db = _bind_db(_MemoryDb())
    _ingest_flight_status(db, _flight_detail_xml("321", "A9CXF"))
    _ingest_flight_status(db, _flight_detail_xml("321", "A9CZZ"))

    feed = _flight_feed()
    by_field = _by_field(_aircraft_changes(db))
    assert set(by_field) == {"aircraft.registration"}
    assert by_field["aircraft.registration"]["oldValue"] == "A9CXF"
    assert by_field["aircraft.registration"]["newValue"] == "A9CZZ"

    feed_events = [event for event in feed["events"] if event["eventType"] == AIRCRAFT_CHANGE]
    assert len(feed_events) == 1
    assert feed_events[0]["category"] == "flight"
    assert "A9CXF" in feed_events[0]["description"]
    assert "A9CZZ" in feed_events[0]["description"]


def test_type_only_swap_emits_one_aircraft_event():
    db = _bind_db(_MemoryDb())
    _ingest_flight_status(db, _flight_detail_xml("321", "A9CXF"))
    _ingest_flight_status(db, _flight_detail_xml("789", "A9CXF"))

    timeline = _timeline()
    by_field = _by_field(_aircraft_changes(db))
    assert set(by_field) == {"aircraft.type"}
    assert by_field["aircraft.type"]["oldValue"] == "321"
    assert by_field["aircraft.type"]["newValue"] == "789"

    assert not isinstance(timeline, HTTPException)
    typed = [
        event for event in timeline["events"]
        if event.get("details", {}).get("field") == "aircraft.type"
    ]
    assert len(typed) == 1
    assert typed[0]["category"] == "flight"
    assert typed[0]["eventType"] == AIRCRAFT_CHANGE


def test_aircraft_swap_is_reported_with_a_gate_change():
    db = _bind_db(_MemoryDb())
    _ingest_flight_status(db, _flight_detail_xml("321", "A9CXF", gate="67A"))
    _ingest_flight_status(db, _flight_detail_xml("789", "A9CZZ", gate="12"))

    feed = _flight_feed()
    stored = _stored_changes(db)
    types = {doc["changeType"] for doc in stored}
    assert "GATE_CHANGE" in types
    by_field = _by_field(_aircraft_changes(db))
    assert by_field["aircraft.type"]["newValue"] == "789"
    assert by_field["aircraft.registration"]["newValue"] == "A9CZZ"

    feed_types = {event["eventType"] for event in feed["events"]}
    assert "GATE_CHANGE" in feed_types
    assert AIRCRAFT_CHANGE in feed_types


def test_unchanged_equipment_does_not_emit_an_aircraft_event():
    db = _bind_db(_MemoryDb())
    with _fetched_at("2026-03-19T10:00:00Z", "2026-03-19T10:00:01Z"):
        _ingest_flight_status(db, _flight_detail_xml("321", "A9CXF"))
        second = _ingest_flight_status(db, _flight_detail_xml("321", "A9CXF"))

    # fetchedAt is part of the checksummed snapshot, so the later body is stored
    # and compared. Equal type and registration still must not be a change.
    assert second["isDuplicate"] is False
    assert db["flight_status"].docs[-1]["aircraft"] == {
        "type": "321",
        "registration": "A9CXF",
        "configNumber": "40",
        "seatConfig": "12/180",
    }
    assert _aircraft_changes(db) == []
    feed = _flight_feed()
    assert [event for event in feed["events"] if event["eventType"] == AIRCRAFT_CHANGE] == []


def test_passenger_list_aircraft_type_swap_reaches_the_flight_feed():
    db = _bind_db(_MemoryDb())
    first_xml = _passenger_list_xml("321")
    parsed = _parse_body(first_xml, "GetPassengerListRS")
    converted = convert_passenger_list(parsed, "GF", "2057", "2026-03-19", "BOM")
    assert converted["aircraftType"] == "321"

    _ingest_passenger_list(db, first_xml)
    second = _ingest_passenger_list(db, _passenger_list_xml("789"))
    assert second["isDuplicate"] is False
    assert db["passenger_list"].docs[-1]["aircraftType"] == "789"

    feed = _flight_feed()
    timeline = _timeline()
    by_field = _by_field(_aircraft_changes(db))
    assert by_field.get("aircraftType", {}).get("oldValue") == "321"
    assert by_field.get("aircraftType", {}).get("newValue") == "789"


    feed_events = [event for event in feed["events"] if event["eventType"] == AIRCRAFT_CHANGE]
    assert len(feed_events) == 1
    assert feed_events[0]["category"] == "flight"
    assert "321" in feed_events[0]["description"]
    assert "789" in feed_events[0]["description"]

    assert not isinstance(timeline, HTTPException)
    typed = [
        event for event in timeline["events"]
        if event.get("details", {}).get("field") == "aircraftType"
    ]
    assert len(typed) == 1
    assert typed[0]["category"] == "flight"
    assert typed[0]["details"]["oldValue"] == "321"
    assert typed[0]["details"]["newValue"] == "789"
