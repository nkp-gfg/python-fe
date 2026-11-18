"""Airline is part of a flight's identity.

These tests fail while snapshot sequences, previous-snapshot diffs, and the
live passenger, reservation, and flight-status reads ignore airline. They
pass once each of those operations is scoped to the requested airline and
ingest refuses a Sabre response whose carrier does not match the request
before any live collection is written.

Run from the repository root:

    PYTHONPATH=. python3 -m pytest backend/tests/test_airline_snapshot_identity.py -q
"""

import os

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")

import asyncio
import inspect
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from mongomock.collection import Collection

from backend.api import database
from backend.api.routes.audit import get_flight_audit
from backend.api.routes.changes import (
    get_boarding_progress,
    get_changes,
    get_changes_summary,
    get_flight_timeline,
    get_passenger_changes,
    get_passengers_history_badges,
    get_snapshots,
)
from backend.api.routes.flights import (
    get_flight_dashboard,
    get_flight_status,
    get_flight_status_history,
    get_flight_tree,
    get_phase_journey,
)
from backend.api.routes.ingestion import SabreIngestRequest, ingest_flight
from backend.api.routes.passengers import (
    get_group_bookings,
    get_passenger_by_pnr,
    get_passenger_summary,
    get_passengers,
    get_standby_list,
)
from backend.api.routes.reservations import get_reservation_by_pnr, get_reservations
from backend.feeder import storage
from backend.sabre.client import SabreClient

ORIGIN = "BAH"
DATE = "2026-03-19"
DEPARTURE_AT = "2026-03-19T08:00:00"

LIVE_COLLECTIONS = {
    "snapshots",
    "changes",
    "flights",
    "flight_status",
    "passenger_list",
    "reservations",
    "trip_reports",
    "flight_schedules",
}

# Mutable script the SOAP stand-in reads. Response bodies keep the carrier
# fields from the captured Sabre samples:
#   GetPassengerListRS Itinerary/Airline
#   ACS_FlightDetailRS ItineraryInfoResponse/Airline
#   Trip_SearchRS MarketingAirlineCode and OperatingAirlineCode
#   VerifyFlightDetailsRS MarketingAirline/@Code
#   Trip_ReportsRS AirlineCode
BOX = {
    "by_airline": {},
    "force_response_carrier": None,
    "requests": [],
}


class _Clock:
    def __init__(self):
        self.current = datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc)

    def iso(self):
        stamp = self.current.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.current += timedelta(seconds=1)
        return stamp


_CLOCK = _Clock()


def _manifest(passengers, gate, status, res_last, res_first, res_nationality):
    return {
        "passengers": passengers,
        "gate": gate,
        "status": status,
        "res_pnr": "SAME01",
        "res_last": res_last,
        "res_first": res_first,
        "res_nationality": res_nationality,
    }


def _pax(last, first, pnr, pid, checked, boarded, priority="", group=""):
    return {
        "last": last,
        "first": first,
        "pnr": pnr,
        "id": pid,
        "checked": checked,
        "boarded": boarded,
        "priority": priority,
        "group": group,
    }


GF_V1 = _manifest(
    [
        _pax("SMITH", "JOHN", "SAME01", "P1", False, False, priority="UPG"),
        _pax("ALICE", "ANNE", "GFONLY", "P2", False, False, priority="B01", group="G1"),
    ],
    gate="A1",
    status="OPENCI",
    res_last="SMITH",
    res_first="JOHN",
    res_nationality="BH",
)
GF_V2 = _manifest(
    [
        _pax("SMITH", "JOHN", "SAME01", "P1", True, False, priority="UPG"),
        _pax("ALICE", "ANNE", "GFONLY", "P2", False, False, priority="B01", group="G1"),
    ],
    gate="A1",
    status="OPENCI",
    res_last="SMITH",
    res_first="JOHN",
    res_nationality="BH",
)
XX_MANIFEST = _manifest(
    [
        _pax("SMITH", "JOHN", "SAME01", "P9", True, True, priority="UPG"),
        _pax("BOB", "BOBBY", "XXONLY", "P8", True, False),
    ],
    gate="Z9",
    status="OPENCI",
    res_last="SMITH",
    res_first="JOHN",
    res_nationality="US",
)
FOREIGN_MANIFEST = _manifest(
    [_pax("FOREIGN", "PAX", "FOR001", "PX", True, True)],
    gate="Z9",
    status="OPENCI",
    res_last="FOREIGN",
    res_first="PAX",
    res_nationality="ZZ",
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


def _requested_airline(action, body):
    if action == "VerifyFlightDetailsLLSRQ":
        match = re.search(r'Code="([A-Z0-9]{2})"', body)
    elif action in ("Trip_SearchRQ", "Trip_ReportsRQ"):
        match = re.search(r"<AirlineCode>([A-Z0-9]{2})</AirlineCode>", body)
    else:
        match = re.search(r"<[^>]*Airline>([A-Z0-9]{2})</[^>]*Airline>", body)
    if not match:
        raise AssertionError(f"could not read airline from {action} request")
    return match.group(1)


def _passenger_xml(pax):
    group = f"<GroupCode>{pax['group']}</GroupCode>" if pax["group"] else ""
    priority = f"<PriorityCode>{pax['priority']}</PriorityCode>" if pax["priority"] else ""
    checked = "true" if pax["checked"] else "false"
    boarded = "true" if pax["boarded"] else "false"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{pax['last']}</LastName>
            <FirstName>{pax['first']}</FirstName>
          </Name_Details>
          <PNRLocator>{pax['pnr']}</PNRLocator>
          <PassengerID>{pax['id']}</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>12A</Seat>
          <Destination>DXB</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <BoardingPassFlag>{boarded}</BoardingPassFlag>
          {priority}
          {group}
          <CheckIn_Info><CheckInStatus>{checked}</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>{boarded}</BoardStatus></Boarding_Info>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
        </PassengerInfo>"""


def _passenger_list_body(carrier, manifest, flight_number):
    passengers = "".join(_passenger_xml(pax) for pax in manifest["passengers"])
    return f"""
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>{carrier}</Airline>
          <Flight>{flight_number}</Flight>
          <DepartureDate>{DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>{passengers}</PassengerInfoList>
    </GetPassengerListRS>"""


def _flight_status_body(carrier, manifest, flight_number):
    return f"""
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>{carrier}</Airline>
          <Flight>{flight_number}</Flight>
          <Origin>{ORIGIN}</Origin>
          <DepartureDate>{DATE}</DepartureDate>
          <ScheduledDepartureDate>{DATE}</ScheduledDepartureDate>
          <ScheduledDepartureTime>08:00AM</ScheduledDepartureTime>
          <DepartureGate>{manifest['gate']}</DepartureGate>
          <FlightStatus>{manifest['status']}</FlightStatus>
          <AircraftType>321</AircraftType>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>"""


def _reservations_body(carrier, manifest, flight_number):
    return f"""
    <Trip_SearchRS>
      <Success>Success</Success>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="{manifest['res_pnr']}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{manifest['res_pnr']}</stl19:RecordLocator>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger>
                      <stl19:LastName>{manifest['res_last']}</stl19:LastName>
                      <stl19:FirstName>{manifest['res_first']}</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:DocumentNationalityCountry>{manifest['res_nationality']}</stl19:DocumentNationalityCountry>
                            <stl19:Gender>M</stl19:Gender>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                      </stl19:SpecialRequests>
                    </stl19:Passenger>
                  </stl19:Passengers>
                  <stl19:Segments>
                    <stl19:Segment>
                      <stl19:Air>
                        <stl19:DepartureAirport>{ORIGIN}</stl19:DepartureAirport>
                        <stl19:ArrivalAirport>DXB</stl19:ArrivalAirport>
                        <stl19:OperatingAirlineCode>{carrier}</stl19:OperatingAirlineCode>
                        <stl19:MarketingAirlineCode>{carrier}</stl19:MarketingAirlineCode>
                        <stl19:FlightNumber>{flight_number}</stl19:FlightNumber>
                        <stl19:ClassOfService>Y</stl19:ClassOfService>
                        <stl19:ActionCode>HK</stl19:ActionCode>
                      </stl19:Air>
                    </stl19:Segment>
                  </stl19:Segments>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>"""


def _trip_report_body(carrier, manifest):
    pax = manifest["passengers"][0]
    return f"""
    <Trip_ReportsRS>
      <AirlineCode>{carrier}</AirlineCode>
      <PassengerList>
        <Passenger>
          <LastName>{pax['last']}</LastName>
          <FirstName>{pax['first']}</FirstName>
          <PNRLocator>{pax['pnr']}</PNRLocator>
        </Passenger>
      </PassengerList>
    </Trip_ReportsRS>"""


def _schedule_body(carrier, flight_number):
    return f"""
    <VerifyFlightDetailsRS>
      <ApplicationResults status="Complete"/>
      <OriginDestinationOptions>
        <OriginDestinationOption>
          <FlightSegment DepartureDateTime="03-19T08:00" ArrivalDateTime="03-19T10:00">
            <OriginLocation LocationCode="{ORIGIN}"/>
            <DestinationLocation LocationCode="DXB"/>
            <Equipment AirEquipType="321"/>
            <MarketingAirline Code="{carrier}" FlightNumber="{flight_number}"/>
          </FlightSegment>
        </OriginDestinationOption>
      </OriginDestinationOptions>
    </VerifyFlightDetailsRS>"""


def _response_for(action, requested, flight_number):
    carrier = BOX["force_response_carrier"] or requested
    if BOX["force_response_carrier"]:
        manifest = FOREIGN_MANIFEST
    else:
        manifest = BOX["by_airline"][requested]
    if action == "ACS_FlightDetailRQ":
        return _flight_status_body(carrier, manifest, flight_number)
    if action == "GetPassengerListRQ":
        return _passenger_list_body(carrier, manifest, flight_number)
    if action == "Trip_SearchRQ":
        return _reservations_body(carrier, manifest, flight_number)
    if action == "Trip_ReportsRQ":
        return _trip_report_body(carrier, manifest)
    if action == "VerifyFlightDetailsLLSRQ":
        return _schedule_body(carrier, flight_number)
    raise AssertionError(f"unexpected Sabre action {action}")


def _flight_number_from(action, body):
    if action == "VerifyFlightDetailsLLSRQ":
        match = re.search(r'FlightNumber="(\d+)"', body)
    else:
        match = re.search(r"<[^>]*(?:Flight|FlightNumber)>(\d+)</[^>]*(?:Flight|FlightNumber)>", body)
    if not match:
        raise AssertionError(f"could not read flight number from {action} request")
    return match.group(1)


def _stub_post(self, action, body, timeout=30, endpoint=None):
    self._rate_limit()
    BOX["requests"].append((action, body))
    if action == "SessionCreateRQ":
        xml = _envelope("<SessionCreateRS/>")
    elif action == "SessionCloseRQ":
        xml = _envelope("<SessionCloseRS/>")
    else:
        requested = _requested_airline(action, body)
        flight_number = _flight_number_from(action, body)
        xml = _envelope(_response_for(action, requested, flight_number))
    return xml, 200, 1, body


@pytest.fixture
def db(monkeypatch):
    client = __import__("mongomock").MongoClient()
    database_handle = client["falconeye"]
    storage.init_db(database_handle)
    database._db = database_handle
    database._client = client
    monkeypatch.setattr(storage, "_now_iso", _CLOCK.iso)
    monkeypatch.setattr("backend.feeder.converter._now_iso", _CLOCK.iso)
    monkeypatch.setattr(SabreClient, "_post", _stub_post)
    BOX["by_airline"] = {}
    BOX["force_response_carrier"] = None
    BOX["requests"] = []
    yield database_handle
    storage.close()
    database._db = None
    database._client = None


def _ingest(airline, flight_number):
    payload = SabreIngestRequest(
        airline=airline,
        flightNumber=flight_number,
        origin=ORIGIN,
        departureDate=DATE,
        departureDateTime=DEPARTURE_AT,
    )
    return asyncio.run(ingest_flight(payload))


def _names(doc):
    return {pax["lastName"] for pax in doc.get("passengers", [])}


def _call(func, *args, **kwargs):
    """Pass airline once the route accepts it; call today's signature until then."""
    allowed = inspect.signature(func).parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in allowed.values()):
        return func(*args, **kwargs)
    return func(*args, **{key: value for key, value in kwargs.items() if key in allowed})


def _arm_live_write_spy():
    events = []
    original_insert = Collection._insert
    original_update = Collection.update_one

    def insert(self, data, session=None, ordered=True):
        if self.name in LIVE_COLLECTIONS:
            events.append(self.name)
        return original_insert(self, data, session=session, ordered=ordered)

    def update(self, *args, **kwargs):
        if self.name in LIVE_COLLECTIONS:
            events.append(self.name)
        return original_update(self, *args, **kwargs)

    Collection._insert = insert
    Collection.update_one = update
    return events, original_insert, original_update


def _restore_spy(original_insert, original_update):
    Collection._insert = original_insert
    Collection.update_one = original_update


def test_other_airline_does_not_replace_current_manifest(db):
    """POST /flights/ingest with a second airline must not become the live record."""
    flight_number = "2057"
    BOX["by_airline"] = {"GF": GF_V1, "XX": XX_MANIFEST}

    first = _ingest("GF", flight_number)
    assert first["result"]["success"] is True
    second = _ingest("XX", flight_number)
    assert second["result"]["success"] is True

    passenger_requests = [
        body for action, body in BOX["requests"] if action == "GetPassengerListRQ"
    ]
    assert any(">GF<" in body for body in passenger_requests)
    assert any(">XX<" in body for body in passenger_requests)

    passengers = _call(get_passengers, 
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None, airline="GF")
    assert passengers["airline"] == "GF"
    assert _names(passengers) == {"SMITH", "ALICE"}
    assert all(pax["isCheckedIn"] is False and pax["isBoarded"] is False
               for pax in passengers["passengers"])
    smith = next(pax for pax in passengers["passengers"] if pax["lastName"] == "SMITH")
    assert smith["nationality"] == "BH"

    summary = _call(get_passenger_summary, 
        flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert summary["checkedIn"] == 0
    assert summary["boarded"] == 0
    assert summary["totalPassengers"] == 2

    standby = _call(get_standby_list, 
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None, airline="GF")
    standby_names = {pax["lastName"] for pax in standby["standby"]["passengers"]}
    assert "ALICE" in standby_names

    groups = _call(get_group_bookings, 
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None, airline="GF")
    assert [group["groupCode"] for group in groups["groups"]] == ["G1"]

    by_pnr = _call(get_passenger_by_pnr, 
        flight_number, "SAME01", date=DATE, airline="GF")
    assert by_pnr["passengers"][0]["lastName"] == "SMITH"
    assert by_pnr["passengers"][0]["isBoarded"] is False

    reservations = _call(get_reservations, 
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None, airline="GF")
    assert reservations["airline"] == "GF"
    reserved = reservations["reservations"][0]["passengers"][0]
    assert reserved["lastName"] == "SMITH"
    assert reserved["nationality"] == "BH"

    by_reservation = _call(get_reservation_by_pnr, 
        flight_number, "SAME01", date=DATE, airline="GF")
    assert by_reservation["reservations"][0]["passengers"][0]["nationality"] == "BH"

    status = _call(get_flight_status, 
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None, airline="GF")
    assert status["airline"] == "GF"
    assert status["gate"] == "A1"

    history = _call(get_flight_status_history, 
        flight_number, origin=ORIGIN, date=DATE, limit=20, airline="GF")
    assert [doc["gate"] for doc in history] == ["A1"]

    dashboard = _call(get_flight_dashboard, 
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None, airline="GF")
    assert dashboard["flightStatus"]["airline"] == "GF"
    assert dashboard["flightStatus"]["gate"] == "A1"
    assert dashboard["stateSummary"]["boarded"]["totalPassengers"] == 0
    assert dashboard["stateSummary"]["booked"]["totalPassengers"] == 2
    assert dashboard["overview"]["trackedChanges"] == 0
    assert dashboard["schedule"]["airline"] == "GF"

    other_dashboard = _call(get_flight_dashboard, 
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None, airline="XX")
    assert other_dashboard["flightStatus"]["airline"] == "XX"
    assert other_dashboard["flightStatus"]["gate"] == "Z9"
    assert other_dashboard["stateSummary"]["boarded"]["totalPassengers"] == 1

    tree = _call(get_flight_tree, 
        flight_number, origin=ORIGIN, date=DATE, snapshot_sequence=None, airline="GF")
    boarded = next(card for card in tree["statusCards"] if card["id"] == "boarded")
    assert boarded["value"] == 0

    audit = _call(get_flight_audit, 
        flight_number, origin=ORIGIN, date=DATE, airline="GF")
    gf_fetched = db["passenger_list"].find_one(
        {"airline": "GF", "flightNumber": flight_number})["fetchedAt"]
    assert audit["fetchedAt"] == gf_fetched

    with pytest.raises(HTTPException) as no_changes:
        _call(get_changes, flight_number, origin=ORIGIN, date=DATE,
              change_type=None, limit=100, airline="GF")
    assert no_changes.value.status_code == 404
    with pytest.raises(HTTPException) as no_summary:
        _call(get_changes_summary, flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert no_summary.value.status_code == 404
    with pytest.raises(HTTPException) as no_timeline:
        _call(get_flight_timeline, flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert no_timeline.value.status_code == 404

    progress = _call(get_boarding_progress, 
        flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert progress["checkinProgress"]["current"] == 0
    assert progress["boardingProgress"]["current"] == 0
    assert progress["totalPassengers"] == 2

    journey = _call(get_phase_journey, 
        flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert sum(item["addedCount"] for item in journey["transitions"]) == 0
    assert sum(item["removedCount"] for item in journey["transitions"]) == 0


def test_snapshot_sequence_and_diffs_stay_on_one_airline(db):
    """A later carrier must not share sequence numbers or produce cross-carrier diffs."""
    flight_number = "2058"
    BOX["by_airline"] = {"GF": GF_V1, "XX": XX_MANIFEST}
    _ingest("GF", flight_number)
    _ingest("XX", flight_number)
    BOX["by_airline"]["GF"] = GF_V2
    _ingest("GF", flight_number)

    snaps = list(db["snapshots"].find(
        {"flightNumber": flight_number, "snapshotType": "passenger_list"}))
    by_airline = {}
    for snap in snaps:
        by_airline.setdefault(snap["airline"], []).append(snap["sequenceNumber"])
    assert sorted(by_airline["GF"]) == [1, 2]
    assert by_airline["XX"] == [1]

    stored_changes = list(db["changes"].find({"flightNumber": flight_number}))
    assert [change["changeType"] for change in stored_changes] == ["CHECKED_IN"]
    assert stored_changes[0]["passenger"]["pnr"] == "SAME01"
    assert stored_changes[0]["passenger"]["lastName"] == "SMITH"

    listed = _call(get_snapshots, 
        flight_number, origin=ORIGIN, date=DATE,
        snapshot_type="passenger_list", limit=20, airline="GF")
    assert [snap["sequenceNumber"] for snap in listed] == [2, 1]
    assert {snap["airline"] for snap in listed} == {"GF"}

    historical = _call(get_passengers, 
        flight_number, origin=ORIGIN, date=DATE,
        snapshot_sequence=2, airline="GF")
    assert _names(historical) == {"SMITH", "ALICE"}
    smith = next(pax for pax in historical["passengers"] if pax["lastName"] == "SMITH")
    assert smith["isCheckedIn"] is True
    assert smith["isBoarded"] is False

    changes = _call(get_changes, 
        flight_number, origin=ORIGIN, date=DATE, change_type=None, limit=100, airline="GF")
    assert [change["changeType"] for change in changes] == ["CHECKED_IN"]

    passenger_changes = _call(get_passenger_changes, 
        flight_number, "SAME01", date=DATE, limit=100, airline="GF")
    assert [change["changeType"] for change in passenger_changes] == ["CHECKED_IN"]

    with pytest.raises(HTTPException):
        _call(get_passenger_changes, 
            flight_number, "XXONLY", date=DATE, limit=100, airline="GF")

    badges = _call(get_passengers_history_badges, 
        flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert set(badges) == {"SAME01"}
    assert badges["SAME01"]["changeCount"] == 1

    timeline = _call(get_flight_timeline, 
        flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert [event["eventType"] for event in timeline["events"]] == ["CHECKED_IN"]

    progress = _call(get_boarding_progress, 
        flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert progress["checkinProgress"]["current"] == 1
    assert progress["boardingProgress"]["current"] == 0
    assert progress["totalPassengers"] == 2

    journey = _call(get_phase_journey, 
        flight_number, origin=ORIGIN, date=DATE, airline="GF")
    assert sum(item["addedCount"] for item in journey["transitions"]) == 0
    assert sum(item["removedCount"] for item in journey["transitions"]) == 0


def test_snapshot_index_includes_airline(db):
    storage.ensure_indexes()
    keys = [name for name, _direction in db["snapshots"].index_information()["snap_flight_lookup"]["key"]]
    assert "airline" in keys


def test_response_carrier_mismatch_writes_no_live_documents(db):
    """A Sabre carrier that differs from the requested airline is not stored."""
    flight_number = "3099"
    BOX["by_airline"] = {"GF": GF_V1}
    BOX["force_response_carrier"] = "XX"
    events, original_insert, original_update = _arm_live_write_spy()
    try:
        try:
            result = _ingest("GF", flight_number)
        except HTTPException:
            result = None
        else:
            assert result["result"]["success"] is False
    finally:
        _restore_spy(original_insert, original_update)

    business = [
        body for action, body in BOX["requests"]
        if action not in ("SessionCreateRQ", "SessionCloseRQ")
    ]
    assert business
    assert any(">GF<" in body or 'Code="GF"' in body for body in business)
    assert events == []
    for name in LIVE_COLLECTIONS:
        assert db[name].count_documents({"flightNumber": flight_number}) == 0
        assert db[name].count_documents({"airline": "GF", "flightNumber": flight_number}) == 0
