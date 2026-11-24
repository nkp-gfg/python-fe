"""The reservations API keeps each air leg's arrival time.

These checks pass while the flight board still hides that time. The failing
spec is the React test that renders the Reservations tab
(``frontend/components/dashboard/reservation-arrival.test.tsx``). A fix has to
keep the timestamps asserted here and print them, labeled, on each segment.
"""

from backend.tests.reservation_arrival_payload import (
    reservation_by_pnr,
    reservations_list_response,
)


def _segment(reservation: dict, airline: str, flight_number: str, origin: str) -> dict:
    matches = [
        segment
        for segment in reservation["segments"]
        if segment["marketingAirline"] == airline
        and segment["flightNumber"] == flight_number
        and segment["departureAirport"] == origin
    ]
    assert len(matches) == 1, (airline, flight_number, origin, len(matches))
    return matches[0]


def test_list_response_keeps_inbound_and_operating_arrival_times():
    body = reservations_list_response()
    assert body["flightNumber"] == "2057"
    assert body["departureAirport"] == "BOM"
    assert body["departureDate"] == "2026-03-18"
    assert "_raw" not in body

    booked = next(item for item in body["reservations"] if item["pnr"] == "QCWBUJ")
    inbound = _segment(booked, "AI", "2788", "COK")
    operating = _segment(booked, "GF", "2057", "BOM")

    assert inbound["departureDate"] == "2026-03-18T10:00:00"
    assert inbound["arrivalDate"] == "2026-03-18T12:20:00"
    assert inbound["arrivalAirport"] == "BOM"
    assert operating["departureDate"] == "2026-03-18T17:40:00"
    assert operating["arrivalDate"] == "2026-03-18T20:10:00"
    assert operating["arrivalAirport"] == "DMM"


def test_every_stored_air_leg_is_returned_with_its_own_arrival():
    body = reservations_list_response()
    assert body["reservations"], "sample flight has no reservations"
    seen = 0
    for reservation in body["reservations"]:
        assert reservation["segments"], reservation["pnr"]
        for segment in reservation["segments"]:
            seen += 1
            assert segment["departureDate"]
            assert segment["arrivalDate"]
            assert "T" in segment["arrivalDate"]
            # The arrival clock is a stored value, not a copy of the departure.
            assert segment["arrivalDate"] != segment["departureDate"]
    assert seen >= 2


def test_single_pnr_read_keeps_the_same_arrival_times():
    body = reservation_by_pnr("QCWBUJ")
    assert body["flightNumber"] == "2057"
    assert len(body["reservations"]) == 1
    booked = body["reservations"][0]
    inbound = _segment(booked, "AI", "2788", "COK")
    operating = _segment(booked, "GF", "2057", "BOM")
    assert inbound["arrivalDate"] == "2026-03-18T12:20:00"
    assert operating["arrivalDate"] == "2026-03-18T20:10:00"
