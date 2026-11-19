"""Dashboard connecting counts must follow the connection, not the thru check-in flag.

Sabre's ThruIndicator is the earlier-leg check-in marker (isThru). A traveler is
connecting on this flight when the stored reservation segment for this flight
has outboundConnection or inboundConnection, or when the manifest edit codes
include OB or IB. Connection risk uses that same connection test plus the
existing not-checked-in and not-boarded rule.

These tests fail while the dashboard still counts connecting passengers from
isThru alone.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights as flights_api
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient

SAMPLE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "sample_data", "sample_xmlfiles"
)
PASSENGER_LIST_SAMPLE = os.path.join(
    SAMPLE_DIR, "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)
TRIP_SEARCH_SAMPLE = os.path.join(
    SAMPLE_DIR, "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)

AIRLINE = "GF"
SAMPLE_FLIGHT = "2057"
SAMPLE_ORIGIN = "BOM"
SAMPLE_DATE = "2026-03-19"
KAMBLE_PNR = "MIOGSW"

FIXTURE_FLIGHT = "4242"
FIXTURE_ORIGIN = "BAH"
FIXTURE_DATE = "2026-04-02"
OTHER_FLIGHT = "7777"

CONNECTION_EDIT_CODES = {"OB", "IB"}


def _parse_response(xml_text, response_key):
    """Parse a SOAP envelope the same way SabreClient parses a live response."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _load_xml(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def load_gf2057_documents():
    """Convert the committed GF2057 passenger list and trip search responses."""
    passenger_raw = _parse_response(
        _load_xml(PASSENGER_LIST_SAMPLE), "GetPassengerListRS"
    )
    reservation_raw = _parse_response(
        _load_xml(TRIP_SEARCH_SAMPLE), "Trip_SearchRS"
    )
    passenger_doc = convert_passenger_list(
        passenger_raw, AIRLINE, SAMPLE_FLIGHT, SAMPLE_DATE, SAMPLE_ORIGIN
    )
    reservation_doc = convert_reservations(
        reservation_raw, AIRLINE, SAMPLE_FLIGHT, SAMPLE_ORIGIN, SAMPLE_DATE
    )
    return passenger_doc, reservation_doc


def _segment_is_this_flight(segment, flight_number, origin, airline):
    number = str(segment.get("flightNumber") or "").lstrip("0")
    wanted = str(flight_number).lstrip("0")
    if not number or number != wanted:
        return False
    if (segment.get("departureAirport") or "") != origin:
        return False
    marketed = segment.get("marketingAirline") or airline
    return marketed == airline


def _reservation_connects(segment):
    return bool(segment.get("outboundConnection") or segment.get("inboundConnection"))


def connecting_pnrs(reservation_doc, flight_number, origin, airline):
    """PNRs whose stored segment for this flight is an inbound or outbound connection."""
    pnrs = set()
    if not reservation_doc:
        return pnrs
    for reservation in reservation_doc.get("reservations") or []:
        pnr = reservation.get("pnr") or ""
        if not pnr:
            continue
        for segment in reservation.get("segments") or []:
            if _segment_is_this_flight(segment, flight_number, origin, airline) and _reservation_connects(segment):
                pnrs.add(pnr)
                break
    return pnrs


def passenger_is_connecting(passenger, connected_pnrs):
    codes = set(passenger.get("editCodes") or [])
    if codes & CONNECTION_EDIT_CODES:
        return True
    pnr = passenger.get("pnr") or ""
    return bool(pnr) and pnr in connected_pnrs


def expected_connection_counts(passenger_doc, reservation_doc, flight_number, origin, airline):
    passengers = passenger_doc.get("passengers") or []
    connected = connecting_pnrs(reservation_doc, flight_number, origin, airline)
    connecting = [
        passenger for passenger in passengers
        if passenger_is_connecting(passenger, connected)
    ]
    at_risk = [
        passenger for passenger in connecting
        if not passenger.get("isCheckedIn") and not passenger.get("isBoarded")
    ]
    total = len(passengers)
    connecting_count = len(connecting)
    at_risk_count = len(at_risk)
    thru_no_seat = sum(
        1 for passenger in passengers
        if passenger.get("isThru") and not passenger.get("seat")
    )
    return {
        "connecting": connecting,
        "connectingCount": connecting_count,
        "local": total - connecting_count,
        "connectingPct": round(connecting_count / total * 100, 1) if total else 0,
        "atRiskCount": at_risk_count,
        "totalConnecting": connecting_count,
        "riskPct": round(at_risk_count / connecting_count * 100, 1) if connecting_count else 0,
        "thruNoSeat": thru_no_seat,
    }


def _insight_view(payload):
    insights = payload["insights"]
    connecting = insights["connectingPassengers"]
    risk = insights["connectionRisk"]
    return {
        "connectingCount": connecting["connecting"],
        "local": connecting["local"],
        "connectingPct": connecting["connectingPct"],
        "atRiskCount": risk["atRiskCount"],
        "totalConnecting": risk["totalConnecting"],
        "riskPct": risk["riskPct"],
        "thruNoSeat": insights["operationalReadiness"]["thruNoSeat"],
    }


class _MemoryDB:
    """Enough of a Mongo database for the dashboard's find_one/find calls."""

    def __init__(self, collections):
        self._collections = {
            name: [copy.deepcopy(doc) for doc in docs]
            for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return _MemoryCollection(self._collections.get(name, []))


class _MemoryCollection:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self._docs if _query_matches(doc, query)]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda doc: doc.get(key), reverse=direction < 0)
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query, projection=None):
        return [
            copy.deepcopy(doc) for doc in self._docs if _query_matches(doc, query)
        ]


def _query_matches(doc, query):
    for key, expected in query.items():
        value = doc.get(key)
        if isinstance(expected, dict) and "$lte" in expected:
            if value is None or value > expected["$lte"]:
                return False
        elif value != expected:
            return False
    return True


def _collections_for(passenger_doc, reservation_doc, flight_number, origin, date):
    passenger_doc = copy.deepcopy(passenger_doc)
    reservation_doc = copy.deepcopy(reservation_doc)
    return {
        "passenger_list": [passenger_doc],
        "reservations": [reservation_doc],
        "flight_status": [],
        "trip_reports": [],
        "flight_schedules": [],
        "changes": [],
        "flights": [],
        "snapshots": [
            {
                "flightNumber": flight_number,
                "origin": origin,
                "departureDate": date,
                "snapshotType": "passenger_list",
                "sequenceNumber": 1,
                "data": passenger_doc,
            },
            {
                "flightNumber": flight_number,
                "origin": origin,
                "departureDate": date,
                "snapshotType": "reservations",
                "sequenceNumber": 1,
                "data": reservation_doc,
            },
        ],
    }


def dashboard_for(passenger_doc, reservation_doc, flight_number, origin, date, snapshot_sequence):
    database = _MemoryDB(
        _collections_for(passenger_doc, reservation_doc, flight_number, origin, date)
    )
    original = flights_api.get_db
    flights_api._dashboard_cache.clear()
    flights_api.get_db = lambda: database
    try:
        return flights_api.get_flight_dashboard(
            flight_number,
            origin=origin,
            date=date,
            snapshot_sequence=snapshot_sequence,
        )
    finally:
        flights_api.get_db = original
        flights_api._dashboard_cache.clear()


def _assert_dashboard_matches(passenger_doc, reservation_doc, flight_number, origin, airline, date):
    expected = expected_connection_counts(
        passenger_doc, reservation_doc, flight_number, origin, airline
    )
    expected_view = {
        key: expected[key]
        for key in (
            "connectingCount",
            "local",
            "connectingPct",
            "atRiskCount",
            "totalConnecting",
            "riskPct",
            "thruNoSeat",
        )
    }
    payload = dashboard_for(
        passenger_doc,
        reservation_doc,
        flight_number,
        origin,
        date,
        None,
    )
    assert _insight_view(payload) == expected_view


def _passenger_xml(last_name, first_name, pnr, thru, checked_in, boarded, edit_codes, seat):
    codes = "".join(f"<EditCode>{code}</EditCode>" for code in edit_codes)
    edit_list = f"<EditCodeList>{codes}</EditCodeList>" if edit_codes else ""
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>{first_name}</FirstName>
          </Name_Details>
          <PNRLocator>{pnr}</PNRLocator>
          <ThruIndicator>{"true" if thru else "false"}</ThruIndicator>
          <Seat>{seat}</Seat>
          <CheckIn_Info>
            <CheckInStatus>{"true" if checked_in else "false"}</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>{"true" if boarded else "false"}</BoardStatus>
          </Boarding_Info>
          {edit_list}
        </PassengerInfo>
    """


def _air_segment(flight_number, origin, destination, outbound, inbound):
    return f"""
                    <Segment>
                      <Air>
                        <DepartureAirport>{origin}</DepartureAirport>
                        <ArrivalAirport>{destination}</ArrivalAirport>
                        <MarketingAirlineCode>GF</MarketingAirlineCode>
                        <FlightNumber>{flight_number}</FlightNumber>
                        <inboundConnection>{"true" if inbound else "false"}</inboundConnection>
                        <outboundConnection>{"true" if outbound else "false"}</outboundConnection>
                      </Air>
                    </Segment>
    """


def _reservation_xml(pnr, last_name, first_name, segments_xml):
    return f"""
          <Reservation Locator="{pnr}">
            <GetReservationRS>
              <Reservation numberInParty="1">
                <BookingDetails>
                  <RecordLocator>{pnr}</RecordLocator>
                </BookingDetails>
                <PassengerReservation>
                  <Passengers>
                    <Passenger>
                      <LastName>{last_name}</LastName>
                      <FirstName>{first_name}</FirstName>
                    </Passenger>
                  </Passengers>
                  <Segments>
                    {segments_xml}
                  </Segments>
                </PassengerReservation>
              </Reservation>
            </GetReservationRS>
          </Reservation>
    """


def load_isolated_connection_documents():
    """One manifest that separates each connection signal from ThruIndicator.

    EDITOB connects only by the OB edit code. EDITIB connects only by IB.
    RESOUT and the second passenger on that PNR connect only by the reservation
    segment for this flight. RESIN connects only by inboundConnection.
    THRUONLY has ThruIndicator set and a connection on a different flight.
    XOBONLY carries the guide's negative XOB display, which is not OB.
    """
    passengers = [
        _passenger_xml("EDITOB", "TEST", "EDITOB", False, False, False, ["OB"], ""),
        _passenger_xml("EDITIB", "TEST", "EDITIB", False, True, True, ["IB"], "4A"),
        _passenger_xml("RESOUT", "ONE", "RESOUT", False, False, False, ["ET"], ""),
        _passenger_xml("RESOUT", "TWO", "RESOUT", False, False, False, [], ""),
        _passenger_xml("RESIN", "TEST", "RESIN", False, True, False, ["DOCS"], "8C"),
        _passenger_xml("THRUONLY", "TEST", "THRUONLY", True, False, False, ["ET"], ""),
        _passenger_xml("LOCAL", "TEST", "LOCAL", False, True, True, ["ET"], "10A"),
        _passenger_xml("XOBONLY", "TEST", "XOBONLY", False, False, False, ["XOB"], ""),
    ]
    passenger_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{FIXTURE_FLIGHT}</Flight>
          <DepartureDate>{FIXTURE_DATE}</DepartureDate>
          <Origin>{FIXTURE_ORIGIN}</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {''.join(passengers)}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    this_leg = _air_segment(FIXTURE_FLIGHT, FIXTURE_ORIGIN, "DXB", False, False)
    onward = _air_segment(OTHER_FLIGHT, "DXB", "MCT", True, False)
    reservations = [
        _reservation_xml("EDITOB", "EDITOB", "TEST", this_leg),
        _reservation_xml(
            "RESOUT",
            "RESOUT",
            "ONE",
            _air_segment(FIXTURE_FLIGHT, FIXTURE_ORIGIN, "DXB", True, False),
        ),
        _reservation_xml(
            "RESIN",
            "RESIN",
            "TEST",
            _air_segment(FIXTURE_FLIGHT, FIXTURE_ORIGIN, "DXB", False, True),
        ),
        _reservation_xml("THRUONLY", "THRUONLY", "TEST", this_leg + onward),
        _reservation_xml("LOCAL", "LOCAL", "TEST", this_leg),
        _reservation_xml("XOBONLY", "XOBONLY", "TEST", this_leg),
    ]
    reservation_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="{len(reservations)}">
        <Reservations>
          {''.join(reservations)}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    passenger_doc = convert_passenger_list(
        _parse_response(passenger_xml, "GetPassengerListRS"),
        AIRLINE,
        FIXTURE_FLIGHT,
        FIXTURE_DATE,
        FIXTURE_ORIGIN,
    )
    reservation_doc = convert_reservations(
        _parse_response(reservation_xml, "Trip_SearchRS"),
        AIRLINE,
        FIXTURE_FLIGHT,
        FIXTURE_ORIGIN,
        FIXTURE_DATE,
    )
    return passenger_doc, reservation_doc


def test_gf2057_stores_thru_flag_separately_from_the_onward_connection():
    passenger_doc, reservation_doc = load_gf2057_documents()
    kamble = next(
        passenger for passenger in passenger_doc["passengers"]
        if passenger["pnr"] == KAMBLE_PNR and passenger["lastName"] == "KAMBLE"
    )
    reservation = next(
        item for item in reservation_doc["reservations"] if item["pnr"] == KAMBLE_PNR
    )
    this_flight = next(
        segment for segment in reservation["segments"]
        if _segment_is_this_flight(segment, SAMPLE_FLIGHT, SAMPLE_ORIGIN, AIRLINE)
    )
    onward = next(
        segment for segment in reservation["segments"]
        if str(segment.get("flightNumber") or "").lstrip("0") == "5023"
    )

    assert kamble["isThru"] is False
    assert "OB" in kamble["editCodes"]
    assert "IB" not in kamble["editCodes"]
    assert kamble["isCheckedIn"] is True
    assert kamble["isBoarded"] is True
    assert this_flight["outboundConnection"] is True
    assert this_flight["inboundConnection"] is False
    assert onward["departureAirport"] == "DMM"
    assert onward["arrivalAirport"] == "BAH"
    assert onward["inboundConnection"] is True

    expected = expected_connection_counts(
        passenger_doc, reservation_doc, SAMPLE_FLIGHT, SAMPLE_ORIGIN, AIRLINE
    )
    assert kamble in expected["connecting"]
    assert all(passenger["isThru"] is False for passenger in expected["connecting"])
    assert expected["connectingCount"] > 0
    assert expected["atRiskCount"] == 0


def test_gf2057_dashboard_counts_connecting_passengers_not_thru_checkin():
    passenger_doc, reservation_doc = load_gf2057_documents()
    _assert_dashboard_matches(
        passenger_doc,
        reservation_doc,
        SAMPLE_FLIGHT,
        SAMPLE_ORIGIN,
        AIRLINE,
        SAMPLE_DATE,
    )


def test_connection_risk_uses_the_connection_test_not_thru_indicator():
    passenger_doc, reservation_doc = load_isolated_connection_documents()
    by_name = {
        (passenger["pnr"], passenger["firstName"]): passenger
        for passenger in passenger_doc["passengers"]
    }
    assert by_name[("THRUONLY", "TEST")]["isThru"] is True
    assert by_name[("EDITOB", "TEST")]["isThru"] is False
    assert "OB" in by_name[("EDITOB", "TEST")]["editCodes"]
    assert "IB" in by_name[("EDITIB", "TEST")]["editCodes"]
    assert "XOB" in by_name[("XOBONLY", "TEST")]["editCodes"]

    expected = expected_connection_counts(
        passenger_doc, reservation_doc, FIXTURE_FLIGHT, FIXTURE_ORIGIN, AIRLINE
    )
    connecting_names = {
        (passenger["pnr"], passenger["firstName"]) for passenger in expected["connecting"]
    }
    assert connecting_names == {
        ("EDITOB", "TEST"),
        ("EDITIB", "TEST"),
        ("RESOUT", "ONE"),
        ("RESOUT", "TWO"),
        ("RESIN", "TEST"),
    }
    assert expected["atRiskCount"] == 3
    assert expected["thruNoSeat"] == 1

    _assert_dashboard_matches(
        passenger_doc,
        reservation_doc,
        FIXTURE_FLIGHT,
        FIXTURE_ORIGIN,
        AIRLINE,
        FIXTURE_DATE,
    )


if __name__ == "__main__":
    test_gf2057_stores_thru_flag_separately_from_the_onward_connection()
    print("  PASS: test_gf2057_stores_thru_flag_separately_from_the_onward_connection")
    test_gf2057_dashboard_counts_connecting_passengers_not_thru_checkin()
    print("  PASS: test_gf2057_dashboard_counts_connecting_passengers_not_thru_checkin")
    test_connection_risk_uses_the_connection_test_not_thru_indicator()
    print("  PASS: test_connection_risk_uses_the_connection_test_not_thru_indicator")
