"""Seat selection for connecting passengers on Trip_Search reservations.

Sabre lists every pre-reserved seat on a passenger in document order. On a
connection the earlier leg comes first. The seat stored for the flight being
ingested is the pre-reserved seat whose board point equals that flight's
origin: the first such seat in document order when several share the origin.
Other legs are kept on ``preReservedSeats`` and are not copied into the flat
seat fields. When no seat boards at the origin, the flat seat fields are
empty.

These tests fail while conversion keeps the first seat regardless of board
point.

Run from the repository root:

    python3 -m pytest backend/tests/test_reservation_connecting_seat.py -q
"""

import os
import sys
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import reservations as reservations_route
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.sabre.client import SabreClient

ORIGIN = "BKK"
FLIGHT_NUMBER = "2153"
DEPARTURE_DATE = "2026-03-17"
SAMPLE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "Trip_SearchRS_GF2153_BKK_2026-03-17.xml",
)

_FLAT_KEYS = (
    "seatNumber",
    "seatStatusCode",
    "seatTypeCode",
    "seatBoardPoint",
    "seatOffPoint",
)


def _parse_trip_search(xml_text):
    """Parse a Trip_Search SOAP response the same way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _load_sample_body():
    with open(SAMPLE_PATH, encoding="utf-8") as handle:
        return _parse_trip_search(handle.read())


def _flat_from_raw(seat):
    if not isinstance(seat, dict):
        seat = {}
    return {
        "seatNumber": seat.get("SeatNumber") or "",
        "seatStatusCode": seat.get("SeatStatusCode") or "",
        "seatTypeCode": seat.get("SeatTypeCode") or "",
        "seatBoardPoint": seat.get("BoardPoint") or "",
        "seatOffPoint": seat.get("OffPoint") or "",
    }


def _empty_flat():
    return {key: "" for key in _FLAT_KEYS}


def _raw_seat_lists(body):
    """Walk the same reservation/passenger nodes the converter walks.

    Seat choice is computed here from board point, not taken from conversion
    output. Returns one entry per passenger, in document order.
    """
    data = _strip_ns(body)
    reservations = _ensure_list(
        (data.get("ReservationsList", {}).get("Reservations") or {}).get(
            "Reservation", []
        )
    )
    records = []
    for reservation in reservations:
        if not isinstance(reservation, dict):
            continue
        inner = reservation.get("GetReservationRS", {}).get("Reservation", {})
        if not isinstance(inner, dict):
            inner = {}
        passengers = _ensure_list(
            (
                (inner.get("PassengerReservation") or {}).get("Passengers") or {}
            ).get("Passenger", [])
        )
        for passenger in passengers:
            if not isinstance(passenger, dict):
                continue
            seats = passenger.get("Seats") or {}
            pre_reserved = [
                seat
                for seat in _ensure_list(
                    (seats.get("PreReservedSeats") or {}).get("PreReservedSeat", [])
                )
                if isinstance(seat, dict)
            ]
            records.append(
                {
                    "pnr": reservation.get("@Locator", ""),
                    "nameId": passenger.get("@nameId", ""),
                    "seats": pre_reserved,
                }
            )
    return records


def _expected_for_origin(records, origin):
    expected = []
    for record in records:
        matches = [
            seat for seat in record["seats"] if (seat.get("BoardPoint") or "") == origin
        ]
        chosen = _flat_from_raw(matches[0]) if matches else _empty_flat()
        expected.append(
            {
                "pnr": record["pnr"],
                "nameId": record["nameId"],
                "flat": chosen,
                "preReservedSeats": [_flat_from_raw(seat) for seat in record["seats"]],
            }
        )
    return expected


def _assert_converted_passengers(doc, expected):
    actual = [
        passenger
        for reservation in doc["reservations"]
        for passenger in reservation["passengers"]
    ]
    assert len(actual) == len(expected)
    for index, (passenger, want) in enumerate(zip(actual, expected)):
        flat = {key: passenger.get(key, "") for key in _FLAT_KEYS}
        stored_seats = passenger.get("preReservedSeats")
        assert flat == want["flat"], index
        assert stored_seats == want["preReservedSeats"], index


def _assert_response_seat_numbers(payload, expected):
    actual = [
        passenger.get("seatNumber", "")
        for reservation in payload["reservations"]
        for passenger in reservation["passengers"]
    ]
    wanted = [want["flat"]["seatNumber"] for want in expected]
    assert actual == wanted


def _soap_trip(reservations_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          {reservations_xml}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _reservation_xml(locator, passengers_xml):
    return f"""
<Reservation Locator="{locator}">
  <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
    <stl19:Reservation numberInParty="1" numberOfInfants="0">
      <stl19:BookingDetails>
        <stl19:CreationTimestamp>2026-03-16T00:00:00</stl19:CreationTimestamp>
      </stl19:BookingDetails>
      <stl19:PassengerReservation>
        <stl19:Passengers>
          {passengers_xml}
        </stl19:Passengers>
      </stl19:PassengerReservation>
    </stl19:Reservation>
  </stl19:GetReservationRS>
</Reservation>
"""


def _passenger_xml(name_id, seats):
    seat_xml = ""
    for seat_number, status, seat_type, board, off in seats:
        seat_xml += f"""
        <stl19:PreReservedSeat>
          <stl19:SeatNumber>{seat_number}</stl19:SeatNumber>
          <stl19:SeatStatusCode>{status}</stl19:SeatStatusCode>
          <stl19:SeatTypeCode>{seat_type}</stl19:SeatTypeCode>
          <stl19:BoardPoint>{board}</stl19:BoardPoint>
          <stl19:OffPoint>{off}</stl19:OffPoint>
        </stl19:PreReservedSeat>
        """
    return f"""
<stl19:Passenger nameId="{name_id}" nameType="ADT">
  <stl19:LastName>EXAMPLE</stl19:LastName>
  <stl19:FirstName>PASSENGER</stl19:FirstName>
  <stl19:Seats>
    <stl19:PreReservedSeats>
      {seat_xml}
    </stl19:PreReservedSeats>
  </stl19:Seats>
</stl19:Passenger>
"""


def _convert_synthetic(reservations_xml, origin=ORIGIN):
    body = _parse_trip_search(_soap_trip(reservations_xml))
    doc = convert_reservations(body, "GF", FLIGHT_NUMBER, origin, DEPARTURE_DATE)
    expected = _expected_for_origin(_raw_seat_lists(body), origin)
    return doc, expected


class _Collection:
    def __init__(self, documents):
        self.documents = documents

    def find_one(self, query, sort=None):
        matches = [doc for doc in self.documents if _query_matches(doc, query)]
        if not matches:
            return None
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc: doc.get(field) is None,
                )
                matches.sort(
                    key=lambda doc, field=field: doc.get(field),
                    reverse=direction < 0,
                )
        return matches[0]


class _Database:
    def __init__(self, reservations_doc, snapshot_doc):
        self._collections = {
            "reservations": _Collection([reservations_doc]),
            "snapshots": _Collection([snapshot_doc]),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _query_matches(doc, query):
    for key, expected in query.items():
        if not _value_matches(doc, key, expected):
            return False
    return True


def _value_matches(doc, key, expected):
    if "." in key:
        parent, child = key.split(".", 1)
        values = doc.get(parent)
        if not isinstance(values, list):
            return False
        return any(
            isinstance(item, dict) and _value_matches(item, child, expected)
            for item in values
        )
    actual = doc.get(key)
    if isinstance(expected, dict) and "$lte" in expected:
        return actual is not None and actual <= expected["$lte"]
    return actual == expected


def _stored_documents(doc):
    stored = deepcopy(doc)
    snapshot = {
        "flightNumber": FLIGHT_NUMBER,
        "snapshotType": "reservations",
        "sequenceNumber": 2,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "capturedAt": "2026-03-17T13:02:20Z",
        "data": deepcopy(doc),
    }
    return stored, snapshot


def _client_for(doc):
    stored, snapshot = _stored_documents(doc)
    database = _Database(stored, snapshot)
    app = FastAPI()
    app.include_router(reservations_route.router)
    app.dependency_overrides.clear()
    reservations_route.get_db = lambda: database
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_get_db():
    original = reservations_route.get_db
    yield
    reservations_route.get_db = original


def test_sample_lists_an_earlier_leg_before_the_ingested_origin():
    """The committed production response is a connection, not a single leg."""
    records = _raw_seat_lists(_load_sample_body())
    connecting = [
        record
        for record in records
        if record["seats"]
        and (record["seats"][0].get("BoardPoint") or "") != ORIGIN
        and any((seat.get("BoardPoint") or "") == ORIGIN for seat in record["seats"])
    ]
    assert connecting, "sample no longer contains a connecting passenger"
    assert any(
        (record["seats"][0].get("OffPoint") or "") == ORIGIN for record in connecting
    )


def test_sample_conversion_stores_the_seat_boarding_at_the_flight_origin():
    body = _load_sample_body()
    doc = convert_reservations(body, "GF", FLIGHT_NUMBER, ORIGIN, DEPARTURE_DATE)
    expected = _expected_for_origin(_raw_seat_lists(body), ORIGIN)
    assert doc["flightNumber"] == FLIGHT_NUMBER
    assert doc["departureAirport"] == ORIGIN
    _assert_converted_passengers(doc, expected)
    assert any(
        want["flat"]["seatBoardPoint"] == ORIGIN
        and want["preReservedSeats"][0]["seatBoardPoint"] != ORIGIN
        for want in expected
    )


def test_sample_conversion_for_the_earlier_origin_keeps_that_leg():
    """Ingesting the inbound sector must not keep the later sector's seat."""
    body = _load_sample_body()
    doc = convert_reservations(body, "GF", "0152", "BAH", DEPARTURE_DATE)
    expected = _expected_for_origin(_raw_seat_lists(body), "BAH")
    _assert_converted_passengers(doc, expected)


def test_matching_seat_in_the_middle_is_not_replaced_by_either_end():
    passengers = _passenger_xml(
        "01.01",
        [
            ("1A", "HK", "ALMI", "BAH", "BKK"),
            ("2B", "HK", "WRMKOI", "BKK", "DMM"),
            ("3C", "HK", "ALMI", "DMM", "BAH"),
        ],
    )
    doc, expected = _convert_synthetic(_reservation_xml("AAAAAA", passengers))
    _assert_converted_passengers(doc, expected)
    assert expected[0]["flat"]["seatNumber"] == "2B"
    assert expected[0]["flat"]["seatBoardPoint"] == "BKK"
    assert expected[0]["flat"]["seatOffPoint"] == "DMM"
    assert [seat["seatNumber"] for seat in expected[0]["preReservedSeats"]] == [
        "1A",
        "2B",
        "3C",
    ]


def test_first_matching_board_point_wins_when_a_later_seat_also_matches():
    passengers = _passenger_xml(
        "01.01",
        [
            ("1A", "HK", "ALMI", "BAH", "BKK"),
            ("2B", "HK", "WRMKOI", "BKK", "DMM"),
            ("3C", "NN", "ALMI", "BKK", "DXB"),
        ],
    )
    doc, expected = _convert_synthetic(_reservation_xml("BBBBBB", passengers))
    _assert_converted_passengers(doc, expected)
    assert expected[0]["flat"]["seatNumber"] == "2B"
    assert expected[0]["flat"]["seatStatusCode"] == "HK"
    assert expected[0]["preReservedSeats"][-1]["seatNumber"] == "3C"


def test_current_leg_listed_first_is_kept_when_another_leg_follows():
    passengers = _passenger_xml(
        "01.01",
        [
            ("8A", "HK", "WRMKOI", "BKK", "DMM"),
            ("9B", "HK", "ALMI", "DMM", "BAH"),
        ],
    )
    doc, expected = _convert_synthetic(_reservation_xml("CCCCCC", passengers))
    _assert_converted_passengers(doc, expected)
    assert expected[0]["flat"]["seatNumber"] == "8A"


def test_no_seat_boarding_at_the_origin_does_not_borrow_another_leg():
    passengers = _passenger_xml(
        "01.01",
        [
            ("4D", "HK", "ALMI", "BAH", "BKK"),
            ("5E", "HK", "ALMI", "DMM", "BAH"),
        ],
    )
    doc, expected = _convert_synthetic(_reservation_xml("DDDDDD", passengers))
    _assert_converted_passengers(doc, expected)
    assert expected[0]["flat"] == _empty_flat()
    assert len(expected[0]["preReservedSeats"]) == 2


def test_single_pre_reserved_seat_dict_is_kept_when_it_boards_at_the_origin():
    passengers = _passenger_xml(
        "01.01",
        [("12A", "HK", "ALMI", "BKK", "DMM")],
    )
    doc, expected = _convert_synthetic(_reservation_xml("EEEEEE", passengers))
    _assert_converted_passengers(doc, expected)
    assert expected[0]["flat"]["seatNumber"] == "12A"
    assert len(expected[0]["preReservedSeats"]) == 1


def test_passenger_without_seats_stays_empty():
    passengers = """
<stl19:Passenger nameId="01.01" nameType="ADT">
  <stl19:LastName>EXAMPLE</stl19:LastName>
  <stl19:FirstName>PASSENGER</stl19:FirstName>
</stl19:Passenger>
"""
    doc, expected = _convert_synthetic(_reservation_xml("FFFFFF", passengers))
    _assert_converted_passengers(doc, expected)
    assert expected[0]["flat"] == _empty_flat()
    assert expected[0]["preReservedSeats"] == []


def test_list_endpoint_returns_the_origin_seat_for_the_sample():
    body = _load_sample_body()
    doc = convert_reservations(body, "GF", FLIGHT_NUMBER, ORIGIN, DEPARTURE_DATE)
    expected = _expected_for_origin(_raw_seat_lists(body), ORIGIN)
    client = _client_for(doc)
    response = client.get(
        f"/flights/{FLIGHT_NUMBER}/reservations",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["departureAirport"] == ORIGIN
    _assert_response_seat_numbers(payload, expected)


def test_snapshot_endpoint_returns_the_stored_origin_seat():
    body = _load_sample_body()
    doc = convert_reservations(body, "GF", FLIGHT_NUMBER, ORIGIN, DEPARTURE_DATE)
    expected = _expected_for_origin(_raw_seat_lists(body), ORIGIN)
    client = _client_for(doc)
    response = client.get(
        f"/flights/{FLIGHT_NUMBER}/reservations",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE, "snapshot_sequence": 3},
    )
    assert response.status_code == 200
    _assert_response_seat_numbers(response.json(), expected)


def test_pnr_endpoint_returns_the_origin_seat_and_the_other_legs():
    body = _load_sample_body()
    doc = convert_reservations(body, "GF", FLIGHT_NUMBER, ORIGIN, DEPARTURE_DATE)
    expected = _expected_for_origin(_raw_seat_lists(body), ORIGIN)
    connecting = next(
        want
        for want in expected
        if want["preReservedSeats"]
        and want["preReservedSeats"][0]["seatBoardPoint"] != ORIGIN
        and want["flat"]["seatBoardPoint"] == ORIGIN
    )
    client = _client_for(doc)
    response = client.get(
        f"/flights/{FLIGHT_NUMBER}/reservations/{connecting['pnr']}",
        params={"date": DEPARTURE_DATE},
    )
    assert response.status_code == 200
    payload = response.json()
    assert [reservation["pnr"] for reservation in payload["reservations"]] == [
        connecting["pnr"]
    ]
    passengers = payload["reservations"][0]["passengers"]
    assert len(passengers) == 1
    flat = {key: passengers[0].get(key, "") for key in _FLAT_KEYS}
    stored_seats = passengers[0].get("preReservedSeats")
    assert flat == connecting["flat"]
    assert stored_seats == connecting["preReservedSeats"]
