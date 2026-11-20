"""Network booking lead time counts one reservation once.

GET /analytics/network builds bookingLeadTime from reservation documents
produced by Trip_Search ingestion. A connecting itinerary stores every air
leg, and the mix must not treat those legs as extra bookings.

Each reservation contributes at most one bucket. The booked date is the
segment whose marketing carrier and flight number are the ingested flight
and whose departure day is the ingested departure date. A gap of zero days
is same-day. A booked date after departure is left out. An earlier snapshot
of the same flight is left out.

These tests fail while every segment is counted.
"""

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.database import get_db  # noqa: E402
from backend.api.routes.analytics import (  # noqa: E402
    _run_network_analytics,
    get_network_analytics,
)
from backend.feeder.converter import convert_reservations  # noqa: E402
from backend.feeder.storage import store_reservations  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_TRIP_SEARCH = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

# One count per reservation in the Mumbai GF2057 Trip_Search sample:
# 19 segments booked 1–7 days before 2026-03-18, and one booked that day.
# The other legs in that file (Air India connections, earlier Gulf Air
# flights, and GF2057 on 2026-03-27) are not additional bookings.
SAMPLE_LEAD_TIME = {
    "sameDay": 1,
    "within7d": 19,
    "within30d": 0,
    "within90d": 0,
    "over90d": 0,
}


def _buckets():
    return {
        "sameDay": 0,
        "within7d": 0,
        "within30d": 0,
        "within90d": 0,
        "over90d": 0,
    }


def _classify(days):
    """Lead-time bucket for one reservation. Negative gaps are not bookings."""
    if days < 0:
        return None
    if days == 0:
        return "sameDay"
    if days <= 7:
        return "within7d"
    if days <= 30:
        return "within30d"
    if days <= 90:
        return "within90d"
    return "over90d"


def expected_lead_time(docs, date_from, date_to, origin=None, flight_number=None):
    """Spec for bookingLeadTime over the reservation documents ingestion stored."""
    latest = {}
    for doc in docs:
        departure = doc.get("departureDate") or ""
        if not (date_from <= departure <= date_to):
            continue
        if origin and doc.get("departureAirport") != origin:
            continue
        if flight_number and doc.get("flightNumber") != flight_number:
            continue
        key = (doc.get("flightNumber"), doc.get("departureAirport"), departure)
        previous = latest.get(key)
        if previous is None or (doc.get("fetchedAt") or "") > (previous.get("fetchedAt") or ""):
            latest[key] = doc

    buckets = _buckets()
    for doc in latest.values():
        airline = doc.get("airline")
        flight_number_value = str(doc.get("flightNumber"))
        departure = doc.get("departureDate")
        for reservation in doc.get("reservations") or []:
            booked_dates = []
            for segment in reservation.get("segments") or []:
                segment_departure = str(segment.get("departureDate") or "")[:10]
                if (
                    segment.get("marketingAirline") == airline
                    and str(segment.get("flightNumber")) == flight_number_value
                    and segment_departure == departure
                ):
                    booked = str(segment.get("segmentBookedDate") or "")[:10]
                    if len(booked) == 10:
                        booked_dates.append(booked)
            if not booked_dates:
                continue
            # One reservation can carry the same operated leg twice. Count the
            # earliest booked date on that leg, never each copy.
            booked = min(booked_dates)
            days = (
                datetime.strptime(departure, "%Y-%m-%d")
                - datetime.strptime(booked, "%Y-%m-%d")
            ).days
            bucket = _classify(days)
            if bucket:
                buckets[bucket] += 1
    return buckets


def _segment(origin, destination, airline, flight_number, departure, booked, informational):
    return f"""
        <stl19:Segment>
          <stl19:Air>
            <stl19:DepartureAirport>{origin}</stl19:DepartureAirport>
            <stl19:ArrivalAirport>{destination}</stl19:ArrivalAirport>
            <stl19:MarketingAirlineCode>{airline}</stl19:MarketingAirlineCode>
            <stl19:FlightNumber>{flight_number}</stl19:FlightNumber>
            <stl19:DepartureDateTime>{departure}</stl19:DepartureDateTime>
            <stl19:SegmentBookedDate>{booked}</stl19:SegmentBookedDate>
            <stl19:Informational>{"true" if informational else "false"}</stl19:Informational>
            <stl19:ActionCode>HK</stl19:ActionCode>
          </stl19:Air>
        </stl19:Segment>
    """


def _trip_search_xml(reservations):
    """SOAP Trip_SearchRS body in the shape SabreClient.get_reservations parses."""
    blocks = []
    for reservation in reservations:
        segments = "\n".join(reservation["segments"])
        blocks.append(f"""
          <Reservation Locator="{reservation["locator"]}">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>{reservation["created"]}</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Segments>
                    {segments}
                  </stl19:Segments>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        """)
    joined = "\n".join(blocks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="{len(reservations)}">
        <Reservations>
          {joined}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class BookingLeadTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("MONGODB_URI"):
            raise unittest.SkipTest("MONGODB_URI is required")
        cls.db = get_db()
        cls.db.command("ping")

    def setUp(self):
        self.db["reservations"].delete_many({})
        self.stored = []

    def _ingest(self, xml, airline, flight_number, origin, departure_date, fetched_at=None):
        parsed = SabreClient._parse_xml(xml)
        raw = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
        doc = convert_reservations(
            raw, airline, flight_number, origin, departure_date)
        if fetched_at:
            doc["fetchedAt"] = fetched_at
        store_reservations(doc)
        self.stored.append(doc)
        return doc

    def _lead_time(self, date_from, date_to, origin=None, flight_number=None):
        result = _run_network_analytics(
            date_from, date_to, origin=origin, flight_number=flight_number)
        return result["bookingLeadTime"]

    def test_connecting_itinerary_counts_as_one_within_seven_day_booking(self):
        """An Air India leg and the Gulf Air leg are one booking, not two."""
        xml = _trip_search_xml([{
            "locator": "CNX001",
            "created": "2026-03-16T07:46:00",
            "segments": [
                _segment("COK", "BOM", "AI", "2788",
                         "2026-03-18T10:00:00", "2026-03-16T07:46:00", True),
                _segment("BOM", "DMM", "GF", "2057",
                         "2026-03-18T17:40:00", "2026-03-16T07:46:00", False),
            ],
        }])
        self._ingest(xml, "GF", "2057", "BOM", "2026-03-18")
        expected = {
            "sameDay": 0,
            "within7d": 1,
            "within30d": 0,
            "within90d": 0,
            "over90d": 0,
        }
        self.assertEqual(
            expected_lead_time(self.stored, "2026-03-18", "2026-03-18"),
            expected,
        )
        self.assertEqual(self._lead_time("2026-03-18", "2026-03-18"), expected)

    def test_other_gulf_air_leg_does_not_add_a_same_day_booking(self):
        """A later GF2057 departure booked on this flight's departure day is not same-day demand."""
        xml = _trip_search_xml([{
            "locator": "LAT001",
            "created": "2026-03-17T15:15:00",
            "segments": [
                _segment("BOM", "DMM", "GF", "2057",
                         "2026-03-18T17:40:00", "2026-03-17T15:15:00", False),
                _segment("BOM", "DMM", "GF", "2057",
                         "2026-03-27T02:55:00", "2026-03-18T02:53:00", False),
            ],
        }])
        self._ingest(xml, "GF", "2057", "BOM", "2026-03-18")
        expected = {
            "sameDay": 0,
            "within7d": 1,
            "within30d": 0,
            "within90d": 0,
            "over90d": 0,
        }
        self.assertEqual(self._lead_time("2026-03-18", "2026-03-18"), expected)

    def test_booked_after_departure_is_dropped_and_not_same_day(self):
        """A zero-day gap is same-day. A booked date after departure is omitted."""
        xml = _trip_search_xml([
            {
                "locator": "DAY000",
                "created": "2026-06-01T09:00:00",
                "segments": [
                    _segment("BAH", "DXB", "GF", "100",
                             "2026-06-01T08:00:00", "2026-06-01T09:00:00", False),
                    _segment("LHR", "BAH", "AI", "900",
                             "2026-06-01T01:00:00", "2026-01-01T00:00:00", True),
                ],
            },
            {
                "locator": "DAY007",
                "created": "2026-05-25T09:00:00",
                "segments": [
                    _segment("BAH", "DXB", "GF", "100",
                             "2026-06-01T08:00:00", "2026-05-25T09:00:00", False),
                ],
            },
            {
                "locator": "DAY008",
                "created": "2026-05-24T09:00:00",
                "segments": [
                    _segment("BAH", "DXB", "GF", "100",
                             "2026-06-01T08:00:00", "2026-05-24T09:00:00", False),
                ],
            },
            {
                "locator": "DAY030",
                "created": "2026-05-02T09:00:00",
                "segments": [
                    _segment("BAH", "DXB", "GF", "100",
                             "2026-06-01T08:00:00", "2026-05-02T09:00:00", False),
                ],
            },
            {
                "locator": "DAY031",
                "created": "2026-05-01T09:00:00",
                "segments": [
                    _segment("BAH", "DXB", "GF", "100",
                             "2026-06-01T08:00:00", "2026-05-01T09:00:00", False),
                ],
            },
            {
                "locator": "DAY090",
                "created": "2026-03-03T09:00:00",
                "segments": [
                    _segment("BAH", "DXB", "GF", "100",
                             "2026-06-01T08:00:00", "2026-03-03T09:00:00", False),
                ],
            },
            {
                "locator": "DAY091",
                "created": "2026-03-02T09:00:00",
                "segments": [
                    _segment("BAH", "DXB", "GF", "100",
                             "2026-06-01T08:00:00", "2026-03-02T09:00:00", False),
                ],
            },
            {
                "locator": "AFTER1",
                "created": "2026-06-02T09:00:00",
                "segments": [
                    _segment("BAH", "DXB", "GF", "100",
                             "2026-06-01T08:00:00", "2026-06-02T09:00:00", False),
                    _segment("DXB", "BAH", "GF", "200",
                             "2026-06-10T08:00:00", "2026-06-01T08:00:00", False),
                ],
            },
        ])
        self._ingest(xml, "GF", "100", "BAH", "2026-06-01")
        expected = {
            "sameDay": 1,
            "within7d": 1,
            "within30d": 2,
            "within90d": 2,
            "over90d": 1,
        }
        self.assertEqual(
            expected_lead_time(self.stored, "2026-06-01", "2026-06-01"),
            expected,
        )
        self.assertEqual(self._lead_time("2026-06-01", "2026-06-01"), expected)

    def test_other_airline_leg_in_a_different_window_is_not_counted(self):
        """A connection booked months earlier must not fill the over-90-day bucket."""
        xml = _trip_search_xml([{
            "locator": "OLD001",
            "created": "2026-03-16T12:31:00",
            "segments": [
                _segment("BAH", "BOM", "GF", "0274",
                         "2026-02-23T22:20:00", "2025-12-03T08:21:00", False),
                _segment("BOM", "DMM", "GF", "2057",
                         "2026-03-18T17:40:00", "2026-03-16T12:31:00", False),
            ],
        }])
        self._ingest(xml, "GF", "2057", "BOM", "2026-03-18")
        expected = {
            "sameDay": 0,
            "within7d": 1,
            "within30d": 0,
            "within90d": 0,
            "over90d": 0,
        }
        self.assertEqual(self._lead_time("2026-03-18", "2026-03-18"), expected)

    def test_two_ingested_flights_each_count_their_own_leg_once(self):
        """Each flight document carries the whole itinerary. Each flight counts its own leg once."""
        first = _trip_search_xml([{
            "locator": "TWO001",
            "created": "2026-03-01T00:00:00",
            "segments": [
                _segment("BAH", "LHR", "GF", "100",
                         "2026-04-01T08:00:00", "2026-03-01T00:00:00", False),
                _segment("LHR", "JFK", "GF", "200",
                         "2026-04-10T08:00:00", "2026-04-09T00:00:00", False),
            ],
        }])
        second = _trip_search_xml([{
            "locator": "TWO001",
            "created": "2026-03-01T00:00:00",
            "segments": [
                _segment("BAH", "LHR", "GF", "100",
                         "2026-04-01T08:00:00", "2026-03-01T00:00:00", False),
                _segment("LHR", "JFK", "GF", "200",
                         "2026-04-10T08:00:00", "2026-04-09T00:00:00", False),
            ],
        }])
        self._ingest(first, "GF", "100", "BAH", "2026-04-01")
        self._ingest(second, "GF", "200", "LHR", "2026-04-10")
        both = {
            "sameDay": 0,
            "within7d": 1,
            "within30d": 0,
            "within90d": 1,
            "over90d": 0,
        }
        only_first = {
            "sameDay": 0,
            "within7d": 0,
            "within30d": 0,
            "within90d": 1,
            "over90d": 0,
        }
        self.assertEqual(
            expected_lead_time(self.stored, "2026-04-01", "2026-04-10"),
            both,
        )
        self.assertEqual(self._lead_time("2026-04-01", "2026-04-10"), both)
        self.assertEqual(
            self._lead_time("2026-04-01", "2026-04-10", flight_number="100"),
            only_first,
        )

    def test_only_the_latest_snapshot_is_counted(self):
        """A replaced snapshot must not keep contributing its segments."""
        older_xml = _trip_search_xml([{
            "locator": "SNP001",
            "created": "2025-12-01T00:00:00",
            "segments": [
                _segment("BOM", "DMM", "GF", "2057",
                         "2026-03-18T17:40:00", "2025-12-01T00:00:00", False),
            ],
        }])
        newer_xml = _trip_search_xml([{
            "locator": "SNP001",
            "created": "2026-03-16T00:00:00",
            "segments": [
                _segment("COK", "BOM", "AI", "2788",
                         "2026-03-18T10:00:00", "2025-11-01T00:00:00", True),
                _segment("BOM", "DMM", "GF", "2057",
                         "2026-03-18T17:40:00", "2026-03-16T00:00:00", False),
            ],
        }])
        self._ingest(
            older_xml, "GF", "2057", "BOM", "2026-03-18",
            fetched_at="2026-03-17T00:00:00Z",
        )
        self._ingest(
            newer_xml, "GF", "2057", "BOM", "2026-03-18",
            fetched_at="2026-03-18T00:00:00Z",
        )
        expected = {
            "sameDay": 0,
            "within7d": 1,
            "within30d": 0,
            "within90d": 0,
            "over90d": 0,
        }
        self.assertEqual(
            expected_lead_time(self.stored, "2026-03-18", "2026-03-18"),
            expected,
        )
        self.assertEqual(self._lead_time("2026-03-18", "2026-03-18"), expected)

    def test_sample_trip_search_lead_time_matches_one_count_per_reservation(self):
        """The committed GF2057 Mumbai Trip_Search response follows the same rule."""
        xml = SAMPLE_TRIP_SEARCH.read_text(encoding="utf-8")
        self._ingest(xml, "GF", "2057", "BOM", "2026-03-18")
        self.assertEqual(
            expected_lead_time(self.stored, "2026-03-18", "2026-03-18"),
            SAMPLE_LEAD_TIME,
        )
        self.assertEqual(
            self._lead_time("2026-03-18", "2026-03-18", origin="BOM", flight_number="2057"),
            SAMPLE_LEAD_TIME,
        )

    def test_network_route_returns_one_count_per_reservation(self):
        """The unauthenticated network handler returns that same mix."""
        import asyncio

        xml = _trip_search_xml([{
            "locator": "RTE001",
            "created": "2026-03-16T07:46:00",
            "segments": [
                _segment("COK", "BOM", "AI", "2788",
                         "2026-03-18T10:00:00", "2026-03-16T07:46:00", True),
                _segment("BOM", "DMM", "GF", "2057",
                         "2026-03-18T17:40:00", "2026-03-16T07:46:00", False),
            ],
        }])
        self._ingest(xml, "GF", "2057", "BOM", "2026-03-18")
        result = asyncio.run(get_network_analytics(
            "2026-03-18",
            "2026-03-18",
            origin=None,
            cabin=None,
            flight_number=None,
            destination=None,
        ))
        self.assertEqual(result["bookingLeadTime"], {
            "sameDay": 0,
            "within7d": 1,
            "within30d": 0,
            "within90d": 0,
            "over90d": 0,
        })


if __name__ == "__main__":
    unittest.main()
