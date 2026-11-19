"""Data Audit must show the flight facts Sabre ingestion actually stores.

GET /data-audit/{flight_number}/compare loads the flight_status document
written by convert_flight_status and store_flight_status. That document
keeps status, aircraft.type, aircraft.registration, schedule times, and
gate. The comparison has to surface those values.

Schedule times are the local Sabre clock (YYYY-MM-DDThh:mmAM/PM). Their
row labels must say they are local and must not call them UTC.

The departure gate on the flight_status document is `gate`. A different
departureGate on the passenger_list document for the same flight must not
replace it. OTP has no gate column, so a stored Sabre gate is mongo-only.

These tests fail until that mapping is fixed.

Not covered, because those paths do not read the flight_status keys above:
- GET /data-audit/{flight_number}/passengers
- backend.feeder.comparison.load_sabre_flights (reads flights.summary)
Actual block-off and block-on are not asserted: convert_flight_status
does not store them.
"""

import copy
import os
import sys
from datetime import date, datetime, timezone

from bson import ObjectId

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.data_audit import compare_flight_data
from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.feeder.storage import init_db, store_flight_status, store_passenger_list
from backend.sabre.client import SabreClient


TARGET_FLIGHT = "2006"
TARGET_ORIGIN = "LHR"
TARGET_DATE = "2026-03-19"
DECOY_FLIGHT = "2007"
DECOY_ORIGIN = "BAH"
DECOY_DATE = "2026-03-20"


def _flight_status_xml(flight, origin, departure_date, status, aircraft, registration, gate,
                       sched_dep_time, est_dep_time, sched_arr_time, est_arr_time,
                       sched_arr_date, est_arr_date):
    """SOAP body in the shape ACS_FlightDetailRS uses. No session token."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>{flight}</Flight>
          <Origin>{origin}</Origin>
          <DepartureDate>{departure_date}</DepartureDate>
          <ScheduledDepartureDate>{departure_date}</ScheduledDepartureDate>
          <ScheduledDepartureTime>{sched_dep_time}</ScheduledDepartureTime>
          <EstimatedDepartureDate>{departure_date}</EstimatedDepartureDate>
          <EstimatedDepartureTime>{est_dep_time}</EstimatedDepartureTime>
          <DepartureGate>{gate}</DepartureGate>
          <ScheduledArrivalDate>{sched_arr_date}</ScheduledArrivalDate>
          <ScheduledArrivalTime>{sched_arr_time}</ScheduledArrivalTime>
          <EstimatedArrivalDate>{est_arr_date}</EstimatedArrivalDate>
          <EstimatedArrivalTime>{est_arr_time}</EstimatedArrivalTime>
          <AircraftType>{aircraft}</AircraftType>
          <AircraftRegistration>{registration}</AircraftRegistration>
          <FlightStatus>{status}</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger_list_xml(flight, origin, departure_date, aircraft, gate):
    """SOAP body in the shape GetPassengerListRS uses. No session token."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{flight}</Flight>
          <Origin>{origin}</Origin>
          <Destination>DMM</Destination>
          <AircraftType>{aircraft}</AircraftType>
        </Itinerary>
        <DepartureGate>{gate}</DepartureGate>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>SAMPLE</LastName>
            <FirstName>PAX</FirstName>
          </Name_Details>
          <PNRLocator>ABC123</PNRLocator>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_body(xml_text, response_key):
    """Run the Sabre client's SOAP parse and body extract. No network call."""
    parsed = SabreClient._parse_xml(xml_text)
    parser = SabreClient.__new__(SabreClient)
    return SabreClient._extract_body(parser, parsed, response_key)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _MemoryCollection:
    """In-memory stand-in for the pymongo calls this route makes.

    insert_one adds _id when missing and keeps the same document object the
    caller passed, which is what pymongo 4.20 Collection.insert_one does
    before handing that object to the insert command. find_one keeps
    documents whose fields equal the filter and honors sort. It returns a
    copy so the route can pop _id without changing the stored document.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, document):
        if "_id" not in document:
            document["_id"] = ObjectId()
        self.docs.append(document)
        return _InsertResult(document["_id"])

    def find_one(self, filter=None, *args, **kwargs):
        if filter is not None and not isinstance(filter, dict):
            filter = {"_id": filter}
        filter = filter or {}
        matches = [
            doc for doc in self.docs
            if all(doc.get(key) == value for key, value in filter.items())
        ]
        for key, direction in reversed(list(kwargs.get("sort") or [])):
            matches.sort(
                key=lambda doc, field=key: (doc.get(field) is None, str(doc.get(field))),
                reverse=direction < 0,
            )
        if not matches:
            return None
        return copy.deepcopy(matches[0])


class _MemoryDB(dict):
    def __getitem__(self, name):
        if name not in self:
            dict.__setitem__(self, name, _MemoryCollection())
        return dict.__getitem__(self, name)


def _ingest():
    """Parse Sabre XML, convert it, and insert it the way the feeder does."""
    target_xml = _flight_status_xml(
        TARGET_FLIGHT, TARGET_ORIGIN, TARGET_DATE,
        status="OPENCI", aircraft="789", registration="A9C-FA", gate="B41",
        sched_dep_time="09:50PM", est_dep_time="10:15PM",
        sched_arr_time="07:20AM", est_arr_time="07:45AM",
        sched_arr_date="2026-03-20", est_arr_date="2026-03-20",
    )
    decoy_xml = _flight_status_xml(
        DECOY_FLIGHT, DECOY_ORIGIN, DECOY_DATE,
        status="PDC", aircraft="320", registration="A9C-DE", gate="C12",
        sched_dep_time="01:00AM", est_dep_time="01:30AM",
        sched_arr_time="02:00AM", est_arr_time="02:30AM",
        sched_arr_date="2026-03-20", est_arr_date="2026-03-20",
    )
    pax_xml = _passenger_list_xml(
        TARGET_FLIGHT, TARGET_ORIGIN, TARGET_DATE, aircraft="XXX", gate="Z99",
    )

    target_body = _parse_body(target_xml, "ns3:ACS_FlightDetailRS")
    decoy_body = _parse_body(decoy_xml, "ns3:ACS_FlightDetailRS")
    pax_body = _parse_body(pax_xml, "GetPassengerListRS")

    target = convert_flight_status(
        target_body, "GF", TARGET_FLIGHT, TARGET_ORIGIN, TARGET_DATE)
    decoy = convert_flight_status(
        decoy_body, "GF", DECOY_FLIGHT, DECOY_ORIGIN, DECOY_DATE)
    passengers = convert_passenger_list(
        pax_body, "GF", TARGET_FLIGHT, TARGET_DATE, TARGET_ORIGIN)

    db = _MemoryDB()
    init_db(db)
    # Decoy is inserted first. A lookup that drops the flight filter returns it.
    store_flight_status(decoy)
    store_flight_status(target)
    store_passenger_list(passengers)
    return db, target, passengers


def _pg_row(**overrides):
    row = {
        "flight_number": TARGET_FLIGHT,
        "scheduled_origin": TARGET_ORIGIN,
        "flight_date": date(2026, 3, 19),
        "flight_status": "DELAYED",
        "aircraft_type": "32A",
        "aircraft_registration": "A9C-YY",
        "scheduled_departure_utc": datetime(2026, 3, 19, 18, 50, tzinfo=timezone.utc),
        "scheduled_arrival_utc": datetime(2026, 3, 20, 4, 20, tzinfo=timezone.utc),
        "estimated_block_off_utc": datetime(2026, 3, 19, 19, 15, tzinfo=timezone.utc),
        "estimated_block_on_utc": datetime(2026, 3, 20, 4, 45, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _compare(db, pg_row):
    from unittest.mock import patch

    def query_all(sql, params):
        assert "otp.flight_xml_current" in sql
        assert params[0] == TARGET_FLIGHT
        assert TARGET_DATE in params
        assert TARGET_ORIGIN in params
        return [pg_row]

    with patch("backend.api.routes.data_audit.get_db", return_value=db), \
            patch("backend.api.routes.data_audit.query_all", side_effect=query_all):
        # seq=None is the value FastAPI supplies when the query param is omitted.
        # Calling the handler directly otherwise leaves the Query default object
        # in place, which is not a request the route serves.
        return compare_flight_data(
            TARGET_FLIGHT, origin=TARGET_ORIGIN, date=TARGET_DATE, seq=None)


def _row_with_mongo(result, value):
    found = [row for row in result.rows if row.mongoValue == value]
    rendered = [
        (row.field, row.pgValue, row.mongoValue, row.match) for row in result.rows
    ]
    assert found, f"Sabre value {value!r} was not shown. Rows: {rendered}"
    assert len(found) == 1, f"Sabre value {value!r} appeared more than once: {rendered}"
    return found[0]


def _assert_local_time(row):
    label = row.field.lower()
    assert "utc" not in label, row.field
    assert "local" in label, row.field


def _assert_ingested_shape(stored, passengers):
    assert stored["status"] == "OPENCI"
    assert stored["aircraft"]["type"] == "789"
    assert stored["aircraft"]["registration"] == "A9C-FA"
    assert stored["gate"] == "B41"
    assert stored["schedule"]["scheduledDeparture"] == "2026-03-19T09:50PM"
    assert stored["schedule"]["estimatedDeparture"] == "2026-03-19T10:15PM"
    assert stored["schedule"]["scheduledArrival"] == "2026-03-20T07:20AM"
    assert stored["schedule"]["estimatedArrival"] == "2026-03-20T07:45AM"
    assert "flightStatus" not in stored
    assert "aircraftType" not in stored
    assert "aircraftRegistration" not in stored
    assert "scheduledDepartureUtc" not in stored
    assert "departureGate" not in stored
    assert passengers["departureGate"] == "Z99"
    assert passengers["aircraftType"] == "XXX"


def test_different_sabre_facts_are_shown_as_mismatches():
    db, stored, passengers = _ingest()
    _assert_ingested_shape(stored, passengers)

    result = _compare(db, _pg_row())

    assert result.mongoFound is True
    assert result.pgFound is True

    status = _row_with_mongo(result, stored["status"])
    assert status.pgValue == "DELAYED"
    assert status.match == "mismatch"

    aircraft = _row_with_mongo(result, stored["aircraft"]["type"])
    assert aircraft.pgValue == "32A"
    assert aircraft.match == "mismatch"

    registration = _row_with_mongo(result, stored["aircraft"]["registration"])
    assert registration.pgValue == "A9C-YY"
    assert registration.match == "mismatch"

    schedule = stored["schedule"]
    for sabre_time, pg_prefix in (
        (schedule["scheduledDeparture"], "2026-03-19T18:50:00"),
        (schedule["estimatedDeparture"], "2026-03-19T19:15:00"),
        (schedule["scheduledArrival"], "2026-03-20T04:20:00"),
        (schedule["estimatedArrival"], "2026-03-20T04:45:00"),
    ):
        row = _row_with_mongo(result, sabre_time)
        assert row.pgValue.startswith(pg_prefix)
        assert row.match == "mismatch"
        _assert_local_time(row)

    gate = _row_with_mongo(result, stored["gate"])
    assert gate.pgValue in (None, "")
    assert gate.match == "mongo_only"
    assert gate.mongoValue != passengers["departureGate"]

    # The other flight's status must not be returned for this request.
    assert all(row.mongoValue != "PDC" for row in result.rows)


def test_equal_sabre_and_otp_values_match():
    db, stored, passengers = _ingest()
    _assert_ingested_shape(stored, passengers)
    schedule = stored["schedule"]

    result = _compare(db, _pg_row(
        flight_status=stored["status"],
        aircraft_type=stored["aircraft"]["type"],
        aircraft_registration=stored["aircraft"]["registration"],
        scheduled_departure_utc=schedule["scheduledDeparture"],
        estimated_block_off_utc=schedule["estimatedDeparture"],
        scheduled_arrival_utc=schedule["scheduledArrival"],
        estimated_block_on_utc=schedule["estimatedArrival"],
    ))

    for value in (
        stored["status"],
        stored["aircraft"]["type"],
        stored["aircraft"]["registration"],
        schedule["scheduledDeparture"],
        schedule["estimatedDeparture"],
        schedule["scheduledArrival"],
        schedule["estimatedArrival"],
    ):
        row = _row_with_mongo(result, value)
        assert row.pgValue == value
        assert row.match == "match"
        if value in schedule.values():
            _assert_local_time(row)

    gate = _row_with_mongo(result, stored["gate"])
    assert gate.match == "mongo_only"
    assert gate.mongoValue != passengers["departureGate"]
