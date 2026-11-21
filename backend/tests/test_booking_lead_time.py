"""Booking-window lead time must use calendar dates.

The commercial tab reads ``insights.bookingLeadTime`` from the flight
dashboard payload. Same-day means the reservation creation calendar date
equals the departure calendar date. A creation time later on that date is
still same-day, and a creation on the previous calendar date is one day
out (within 7 days), not same-day.

These tests fail while lead time is computed by subtracting the creation
clock from midnight on the departure date.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

DEPARTURE_DATE = "2026-03-18"
SAMPLE_SOAP = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

# Calendar-day lead times for every CreationTimestamp in the GF2057 sample.
# 2026-03-18 (1), 2026-03-17 (6), 2026-03-16 (8), 2026-03-15 (3),
# 2025-12-23 (85), 2025-12-03 (105).
GF2057_LEAD_TIME = {
    "avgDays": 11.1,
    "minDays": 0,
    "maxDays": 105,
    "medianDays": 2,
    "distribution": {
        "sameDay": 1,
        "within7d": 17,
        "within30d": 0,
        "within90d": 1,
        "over90d": 1,
    },
}


def _reservations_from_soap(xml_text, departure_date=DEPARTURE_DATE):
    """Parse a Trip_Search SOAP body the same way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(body, "GF", "2057", "BOM", departure_date)


def _dashboard_lead_time(reservation_doc, departure_date=DEPARTURE_DATE):
    """Build the dashboard payload the commercial tab renders."""
    passenger_list = {
        "departureDate": departure_date,
        "origin": "BOM",
        "destination": "BAH",
        "totalPassengers": 1,
        "passengers": [{
            "pnr": "WALKUP",
            "lastName": "TEST",
            "firstName": "PAX",
            "cabin": "Y",
            "bookingClass": "Y",
            "passengerType": "F",
            "isRevenue": True,
        }],
    }
    payload = _build_dashboard_payload(
        None,
        passenger_list,
        "BOM",
        departure_date,
        {},
        reservation_doc,
        None,
        None,
    )
    return payload["insights"]["bookingLeadTime"]


def _trip_search_soap(reservations):
    """reservations is a list of (locator, creation_timestamp or None)."""
    blocks = []
    for locator, created in reservations:
        created_xml = (
            f"<stl19:CreationTimestamp>{created}</stl19:CreationTimestamp>"
            if created else ""
        )
        blocks.append(
            f"""
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{locator}</stl19:RecordLocator>
                  {created_xml}
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger>
                      <stl19:LastName>TEST</stl19:LastName>
                      <stl19:FirstName>PAX</stl19:FirstName>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>"""
        )
    joined = "\n".join(blocks)
    count = len(reservations)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="{count}" TotalResults="{count}">
        <Reservations>
          {joined}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def test_gf2057_sample_keeps_departure_date_booking_as_same_day():
    """WUFOLD was created at 02:27 on the departure date and must count as same-day.

    The six reservations created on 2026-03-17 are one day before departure.
    """
    doc = _reservations_from_soap(SAMPLE_SOAP.read_text(encoding="utf-8"))
    by_pnr = {rv["pnr"]: rv["createdAt"] for rv in doc["reservations"]}

    assert by_pnr["WUFOLD"] == "2026-03-18T02:27:00"
    created_on_departure = [
        pnr for pnr, created in by_pnr.items() if created[:10] == DEPARTURE_DATE
    ]
    created_previous_day = [
        pnr for pnr, created in by_pnr.items() if created[:10] == "2026-03-17"
    ]
    assert created_on_departure == ["WUFOLD"]
    assert len(created_previous_day) == 6

    lead = _dashboard_lead_time(doc)
    assert lead == GF2057_LEAD_TIME
    assert sum(lead["distribution"].values()) == len(by_pnr)


def test_calendar_dates_around_midnight_and_the_365_day_cap():
    """Clock time on a calendar date must not move the booking into another bucket."""
    rows = [
        ("MIDNIGHT", "2026-03-18T00:00:00"),
        ("JUSTAFTER", "2026-03-18T00:00:01"),
        ("ENDOFDAY", "2026-03-18T23:59:59"),
        ("WITHZ", "2026-03-18T02:27:00Z"),
        ("PREVEXACT", "2026-03-17T00:00:00"),
        ("PREVSEC", "2026-03-17T00:00:01"),
        ("PREVLATE", "2026-03-17T23:59:59"),
        ("TWODAYS", "2026-03-16T12:00:00"),
        ("SEVEN", "2026-03-11T23:59:59"),
        ("EIGHT", "2026-03-10T00:00:01"),
        ("AFTER", "2026-03-19T01:00:00"),
        ("CAP", "2025-03-18T23:59:59"),
        ("OVERCAP", "2025-03-17T00:00:01"),
        ("MISSING", None),
    ]
    doc = _reservations_from_soap(_trip_search_soap(rows))
    stored = {rv["pnr"]: rv["createdAt"] for rv in doc["reservations"]}
    assert stored["WITHZ"] == "2026-03-18T02:27:00Z"
    assert stored["JUSTAFTER"] == "2026-03-18T00:00:01"
    assert stored["PREVLATE"] == "2026-03-17T23:59:59"
    assert stored["MISSING"] == ""

    lead = _dashboard_lead_time(doc)
    included = [
        (datetime.strptime(DEPARTURE_DATE, "%Y-%m-%d").date()
         - datetime.fromisoformat(created.replace("Z", "+00:00")).date()).days
        for created in stored.values()
        if created
    ]
    included = [days for days in included if 0 <= days <= 365]
    assert included.count(0) == 4
    assert included.count(1) == 3
    assert 366 not in included
    assert -1 not in included

    assert lead == {
        "avgDays": round(sum(included) / len(included), 1),
        "minDays": 0,
        "maxDays": 365,
        "medianDays": sorted(included)[len(included) // 2],
        "distribution": {
            "sameDay": 4,
            "within7d": 5,
            "within30d": 1,
            "within90d": 0,
            "over90d": 1,
            },
        }
