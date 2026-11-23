"""Seat-request status from Sabre Trip Search must survive reservation conversion.

These tests fail while conversion stores only the first pre-reserved seat and
drops SeatSpecialRequests. They are the spec for the fix.

Run:
    PYTHONPATH=. python3 -m pytest backend/tests/test_seat_request_conversion.py -q
"""

import asyncio
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.routing import serialize_response

from backend.api.routes import passengers as passengers_routes
from backend.api.routes import reservations as reservations_routes
from backend.feeder.converter import _strip_ns, convert_reservations
from backend.sabre.client import SabreClient

SAMPLE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "Trip_SearchRS_GF2057_BOM_2026-03-19.xml",
)

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}

# Same envelope shape as a Trip_SearchRS body, with one passenger-level
# SeatSpecialRequests entry and one single (non-repeating) segment SeatRequest.
# No credentials. Names are placeholders.
PASSENGER_LEVEL_TRIP_SEARCH = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS Version="4.5.0">
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="TST001">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>TST001</stl19:RecordLocator>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="01.01" nameType="S">
                      <stl19:LastName>TEST</stl19:LastName>
                      <stl19:FirstName>PASSENGER</stl19:FirstName>
                      <stl19:Seats>
                        <stl19:PreReservedSeats>
                          <stl19:PreReservedSeat>
                            <stl19:SeatNumber>6A</stl19:SeatNumber>
                            <stl19:SeatStatusCode>HK</stl19:SeatStatusCode>
                            <stl19:BoardPoint>DMM</stl19:BoardPoint>
                            <stl19:OffPoint>BAH</stl19:OffPoint>
                          </stl19:PreReservedSeat>
                          <stl19:PreReservedSeat>
                            <stl19:SeatNumber>25A</stl19:SeatNumber>
                            <stl19:SeatStatusCode>HK</stl19:SeatStatusCode>
                            <stl19:BoardPoint>BOM</stl19:BoardPoint>
                            <stl19:OffPoint>DMM</stl19:OffPoint>
                          </stl19:PreReservedSeat>
                        </stl19:PreReservedSeats>
                        <stl19:SeatSpecialRequests>
                          <stl19:SeatRequest>
                            <stl19:SeatCode>SEAT</stl19:SeatCode>
                            <stl19:FlightNumber>2057</stl19:FlightNumber>
                            <stl19:BoardCity>BOM</stl19:BoardCity>
                            <stl19:OffCity>DMM</stl19:OffCity>
                            <stl19:Comment>14F</stl19:Comment>
                            <stl19:ActionCode>NO</stl19:ActionCode>
                          </stl19:SeatRequest>
                        </stl19:SeatSpecialRequests>
                      </stl19:Seats>
                    </stl19:Passenger>
                  </stl19:Passengers>
                  <stl19:Segments>
                    <stl19:Segment>
                      <stl19:Air>
                        <stl19:DepartureAirport>BOM</stl19:DepartureAirport>
                        <stl19:ArrivalAirport>DMM</stl19:ArrivalAirport>
                        <stl19:MarketingAirlineCode>GF</stl19:MarketingAirlineCode>
                        <stl19:FlightNumber>2057</stl19:FlightNumber>
                        <stl19:ClassOfService>V</stl19:ClassOfService>
                        <stl19:ActionCode>HK</stl19:ActionCode>
                        <stl19:DepartureDateTime>2026-03-19T17:40:00</stl19:DepartureDateTime>
                        <stl19:ArrivalDateTime>2026-03-19T20:10:00</stl19:ArrivalDateTime>
                        <stl19:Seats>
                          <stl19:PreReservedSeats>
                            <stl19:PreReservedSeat>
                              <stl19:SeatNumber>12A</stl19:SeatNumber>
                              <stl19:SeatStatusCode>HK</stl19:SeatStatusCode>
                              <stl19:BoardPoint>BOM</stl19:BoardPoint>
                              <stl19:OffPoint>DMM</stl19:OffPoint>
                            </stl19:PreReservedSeat>
                            <stl19:PreReservedSeat>
                              <stl19:SeatNumber>14A</stl19:SeatNumber>
                              <stl19:SeatStatusCode>HK</stl19:SeatStatusCode>
                              <stl19:BoardPoint>BOM</stl19:BoardPoint>
                              <stl19:OffPoint>DMM</stl19:OffPoint>
                            </stl19:PreReservedSeat>
                          </stl19:PreReservedSeats>
                          <stl19:SeatSpecialRequests>
                            <stl19:SeatRequest>
                              <stl19:SeatCode>SEAT</stl19:SeatCode>
                              <stl19:FlightNumber>2057</stl19:FlightNumber>
                              <stl19:BoardCity>BOM</stl19:BoardCity>
                              <stl19:OffCity>DMM</stl19:OffCity>
                              <stl19:Comment>14A</stl19:Comment>
                              <stl19:ActionCode>KK</stl19:ActionCode>
                            </stl19:SeatRequest>
                          </stl19:SeatSpecialRequests>
                        </stl19:Seats>
                      </stl19:Air>
                    </stl19:Segment>
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


class _Collection:
    """Stand-in for a Mongo collection. Returns the document the pipeline stored."""

    def __init__(self, document):
        self.document = document

    def find_one(self, query, sort=None):
        return self.document


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _parse_trip_search(xml_text):
    """Parse a Trip Search SOAP body the way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _text(value):
    if isinstance(value, dict):
        return str(value.get("#text", "") or "")
    if value is None:
        return ""
    return str(value)


def _seat_requests(seats):
    if not isinstance(seats, dict):
        return []
    wrapper = seats.get("SeatSpecialRequests") or {}
    if not isinstance(wrapper, dict):
        return []
    requests = []
    for item in _as_list(wrapper.get("SeatRequest")):
        if not isinstance(item, dict):
            continue
        requests.append({
            "flightNumber": _text(item.get("FlightNumber")),
            "boardCity": _text(item.get("BoardCity")),
            "offCity": _text(item.get("OffCity")),
            "comment": _text(item.get("Comment")),
            "actionCode": _text(item.get("ActionCode")),
        })
    return requests


def _assigned_seats(seats):
    if not isinstance(seats, dict):
        return []
    wrapper = seats.get("PreReservedSeats") or {}
    if not isinstance(wrapper, dict):
        return []
    assigned = []
    for item in _as_list(wrapper.get("PreReservedSeat")):
        if not isinstance(item, dict):
            continue
        assigned.append({
            "seatNumber": _text(item.get("SeatNumber")),
            "seatStatusCode": _text(item.get("SeatStatusCode")),
            "boardPoint": _text(item.get("BoardPoint")),
            "offPoint": _text(item.get("OffPoint")),
        })
    return assigned


def _raw_reservations(body):
    data = _strip_ns(body)
    wrapper = data.get("ReservationsList") or {}
    reservations = (wrapper.get("Reservations") or {}).get("Reservation", [])
    return _as_list(reservations)


def _air_segments(raw_reservation):
    inner = (raw_reservation.get("GetReservationRS") or {}).get("Reservation") or {}
    passenger_reservation = inner.get("PassengerReservation") or {}
    segments = (passenger_reservation.get("Segments") or {}).get("Segment", [])
    air_segments = []
    for segment in _as_list(segments):
        if not isinstance(segment, dict):
            continue
        air = segment.get("Air") or {}
        if isinstance(air, dict) and air:
            air_segments.append(air)
    return air_segments


def _passengers(raw_reservation):
    inner = (raw_reservation.get("GetReservationRS") or {}).get("Reservation") or {}
    passenger_reservation = inner.get("PassengerReservation") or {}
    passengers = (passenger_reservation.get("Passengers") or {}).get("Passenger", [])
    return [p for p in _as_list(passengers) if isinstance(p, dict)]


def _convert(body):
    return convert_reservations(
        body,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["origin"],
        FLIGHT["departureDate"],
    )


def _sample_body():
    with open(SAMPLE_PATH, encoding="utf-8") as handle:
        return _parse_trip_search(handle.read())


def _assert_segment_contract(raw_reservation, converted_reservation):
    raw_air = _air_segments(raw_reservation)
    converted_segments = converted_reservation.get("segments") or []
    assert len(converted_segments) == len(raw_air), (
        f"{converted_reservation.get('pnr')}: expected {len(raw_air)} air segments, "
        f"got {len(converted_segments)}"
    )
    for air, converted in zip(raw_air, converted_segments):
        seats = air.get("Seats") or {}
        assert converted.get("seatRequests") == _seat_requests(seats), (
            f"{converted_reservation.get('pnr')} "
            f"{air.get('DepartureAirport')}-{air.get('ArrivalAirport')} "
            f"flight {air.get('FlightNumber')}: seat requests were dropped"
        )
        assert converted.get("assignedSeats") == _assigned_seats(seats), (
            f"{converted_reservation.get('pnr')} "
            f"{air.get('DepartureAirport')}-{air.get('ArrivalAirport')} "
            f"flight {air.get('FlightNumber')}: assigned seats were dropped"
        )


def _assert_passenger_contract(raw_reservation, converted_reservation):
    raw_passengers = _passengers(raw_reservation)
    converted_passengers = converted_reservation.get("passengers") or []
    assert len(converted_passengers) == len(raw_passengers)
    for raw_passenger, converted in zip(raw_passengers, converted_passengers):
        expected = _seat_requests(raw_passenger.get("Seats") or {})
        assert converted.get("seatRequests") == expected, (
            f"{converted_reservation.get('pnr')} "
            f"{converted.get('lastName')}: passenger seat requests were dropped"
        )
        assigned = _assigned_seats(raw_passenger.get("Seats") or {})
        if assigned:
            assert converted.get("seatNumber") == assigned[0]["seatNumber"]


def test_sample_trip_search_keeps_every_seat_request_and_assigned_seat():
    """The committed GF2057 Trip Search response keeps NO and KK requests."""
    body = _sample_body()
    converted = _convert(body)
    raw_reservations = _raw_reservations(body)
    assert len(converted["reservations"]) == len(raw_reservations)

    refused = []
    confirmed = []
    for raw, stored in zip(raw_reservations, converted["reservations"]):
        assert stored.get("pnr") == (raw.get("@Locator") or "")
        _assert_segment_contract(raw, stored)
        _assert_passenger_contract(raw, stored)
        for segment in stored.get("segments") or []:
            if segment.get("flightNumber") != "2057":
                continue
            for request in segment.get("seatRequests") or []:
                if request.get("actionCode") == "NO":
                    refused.append((stored.get("pnr"), request.get("comment")))
                if request.get("actionCode") == "KK":
                    confirmed.append((stored.get("pnr"), request.get("comment")))

    assert ("MIOGSW", "11A") in refused
    assert ("MIOGSW", "11AN") in confirmed
    assert ("MIOGSW", "25AN") in confirmed

    miogsw = next(item for item in converted["reservations"] if item["pnr"] == "MIOGSW")
    bom_leg = next(
        segment for segment in miogsw["segments"]
        if segment.get("departureAirport") == "BOM" and segment.get("flightNumber") == "2057"
    )
    assert {
        "seatNumber": "25A",
        "seatStatusCode": "HK",
        "boardPoint": "BOM",
        "offPoint": "DMM",
    } in bom_leg["assignedSeats"]
    # The connecting-leg seat stays on the passenger record beside any new list.
    assert miogsw["passengers"][0]["seatNumber"] == "6A"
    assert miogsw["passengers"][0]["seatBoardPoint"] == "DMM"
    assert miogsw["passengers"][0]["seatOffPoint"] == "BAH"


def _list_route():
    return next(
        route for route in reservations_routes.router.routes
        if getattr(route, "endpoint", None) is reservations_routes.get_reservations
    )


def test_reservations_endpoints_return_refused_and_confirmed_requests():
    """GET reservation routes expose the stored seat requests, not the raw archive."""
    body = _sample_body()
    stored = _convert(body)
    database = _Database({
        "reservations": _Collection(stored),
        "snapshots": _Collection(None),
    })
    original_get_db = reservations_routes.get_db
    reservations_routes.get_db = lambda: database
    try:
        listed = reservations_routes.get_reservations(
            "2057",
            origin="BOM",
            date="2026-03-19",
            snapshot_sequence=None,
        )
        serialized = asyncio.run(serialize_response(
            field=_list_route().response_field,
            response_content=copy.deepcopy(listed),
            is_coroutine=True,
        ))
        by_pnr = reservations_routes.get_reservation_by_pnr(
            "2057",
            "MIOGSW",
            date="2026-03-19",
        )
    finally:
        reservations_routes.get_db = original_get_db

    def _bom_requests(reservation):
        segment = next(
            item for item in reservation.get("segments") or []
            if item.get("departureAirport") == "BOM" and item.get("flightNumber") == "2057"
        )
        return segment.get("seatRequests") or []

    listed_mio = next(item for item in serialized["reservations"] if item["pnr"] == "MIOGSW")
    direct_mio = next(item for item in by_pnr["reservations"] if item["pnr"] == "MIOGSW")
    for reservation in (listed_mio, direct_mio):
        requests = _bom_requests(reservation)
        assert {"comment": "11A", "actionCode": "NO", "flightNumber": "2057",
                "boardCity": "BOM", "offCity": "DMM"} in requests
        assert {"comment": "11AN", "actionCode": "KK", "flightNumber": "2057",
                "boardCity": "BOM", "offCity": "DMM"} in requests
        assert {"comment": "25AN", "actionCode": "KK", "flightNumber": "2057",
                "boardCity": "BOM", "offCity": "DMM"} in requests
        segment = next(
            item for item in reservation["segments"]
            if item.get("departureAirport") == "BOM"
        )
        assert {
            "seatNumber": "25A",
            "seatStatusCode": "HK",
            "boardPoint": "BOM",
            "offPoint": "DMM",
        } in (segment.get("assignedSeats") or [])


def test_passenger_enrichment_shows_request_status_beside_assigned_seat():
    """The passenger list enrichment for this flight includes the seat-request status."""
    body = _sample_body()
    stored = _convert(body)
    miogsw = next(item for item in stored["reservations"] if item["pnr"] == "MIOGSW")
    last_name = miogsw["passengers"][0]["lastName"]
    passenger_list = {
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "passengers": [{
            "pnr": "MIOGSW",
            "lastName": last_name,
            "firstName": miogsw["passengers"][0]["firstName"],
            "seat": "25A",
        }],
    }
    database = _Database({
        "reservations": _Collection(stored),
        "passenger_list": _Collection(passenger_list),
    })
    original_get_db = passengers_routes.get_db
    passengers_routes.get_db = lambda: database
    try:
        enriched = passengers_routes.get_passengers(
            "2057",
            origin="BOM",
            date="2026-03-19",
            snapshot_sequence=None,
        )
    finally:
        passengers_routes.get_db = original_get_db

    passenger = enriched["passengers"][0]
    assert passenger["seat"] == "25A"
    requests = passenger.get("seatRequests") or []
    assert {"comment": "11A", "actionCode": "NO", "flightNumber": "2057",
            "boardCity": "BOM", "offCity": "DMM"} in requests
    assert {"comment": "11AN", "actionCode": "KK", "flightNumber": "2057",
            "boardCity": "BOM", "offCity": "DMM"} in requests
    assert {"comment": "25AN", "actionCode": "KK", "flightNumber": "2057",
            "boardCity": "BOM", "offCity": "DMM"} in requests
    assert all(item.get("flightNumber") == "2057" for item in requests)


def test_passenger_level_and_single_segment_seat_requests_are_kept():
    """SeatSpecialRequests on the passenger and a single segment SeatRequest are kept."""
    body = _parse_trip_search(PASSENGER_LEVEL_TRIP_SEARCH)
    converted = _convert(body)
    raw = _raw_reservations(body)[0]
    stored = converted["reservations"][0]
    _assert_segment_contract(raw, stored)
    _assert_passenger_contract(raw, stored)

    passenger_requests = stored["passengers"][0]["seatRequests"]
    assert passenger_requests == [{
        "flightNumber": "2057",
        "boardCity": "BOM",
        "offCity": "DMM",
        "comment": "14F",
        "actionCode": "NO",
    }]
    assert stored["passengers"][0]["seatNumber"] == "6A"
    assert stored["segments"][0]["assignedSeats"] == [
        {"seatNumber": "12A", "seatStatusCode": "HK", "boardPoint": "BOM", "offPoint": "DMM"},
        {"seatNumber": "14A", "seatStatusCode": "HK", "boardPoint": "BOM", "offPoint": "DMM"},
    ]
    assert stored["segments"][0]["seatRequests"] == [{
        "flightNumber": "2057",
        "boardCity": "BOM",
        "offCity": "DMM",
        "comment": "14A",
        "actionCode": "KK",
    }]
