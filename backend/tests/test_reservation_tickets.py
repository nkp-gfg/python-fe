"""Ticket serials from Trip Search must be stored and returned.

These tests fail while convert_reservations reads Ticketing / @RPH /
@eTicketNumber instead of TicketingInfo/TicketDetails/TicketNumber.
They are expected to pass once that conversion is fixed.
"""

import copy
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from backend.api.routes import reservations as reservations_api
from backend.feeder import storage
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient
from backend.sabre.models import ReservationsResponse

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)

TRIP_NS = "http://webservices.sabre.com/triprecord"
PNRB_NS = "http://webservices.sabre.com/pnrbuilder/v1_19"

AIRLINE = "GF"
FLIGHT = "2057"
ORIGIN = "BOM"
DATE = "2026-03-19"

# One TicketDetails element (xmltodict leaves a single child as a dict) plus a
# Ticketing sibling whose attributes are not the issued serial.
SINGLE_TICKET_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="{TRIP_NS}">
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="ONLYTD">
            <stl19:GetReservationRS xmlns:stl19="{PNRB_NS}">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>ONLYTD</stl19:RecordLocator>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:TicketingInfo>
                    <stl19:Ticketing RPH="9" eTicketNumber="NOT-THE-SERIAL"/>
                    <stl19:TicketDetails id="21" index="2" elementId="pnr-21">
                      <stl19:TicketNumber>0722136405999</stl19:TicketNumber>
                      <stl19:PassengerName>DOE/J</stl19:PassengerName>
                    </stl19:TicketDetails>
                  </stl19:TicketingInfo>
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


def _parse_trip_search(xml_text):
    """Parse a Trip_SearchRS SOAP body the way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "Trip_SearchRS")


def _expected_tickets(xml_text):
    """TicketNumber and PassengerName pairs from each TicketDetails element."""
    root = ET.fromstring(xml_text)
    expected = {}
    reservation_tag = f"{{{TRIP_NS}}}Reservation"
    details_tag = f"{{{PNRB_NS}}}TicketDetails"
    number_tag = f"{{{PNRB_NS}}}TicketNumber"
    name_tag = f"{{{PNRB_NS}}}PassengerName"
    for reservation in root.iter(reservation_tag):
        locator = reservation.get("Locator")
        if not locator:
            continue
        pairs = []
        for details in reservation.findall(f".//{details_tag}"):
            number_el = details.find(number_tag)
            name_el = details.find(name_tag)
            number = (number_el.text or "").strip() if number_el is not None else ""
            if not number:
                continue
            name = (name_el.text or "").strip() if name_el is not None else ""
            pairs.append((number, name))
        expected[locator] = pairs
    return expected


def _assert_tickets(test, reservations, expected):
    by_pnr = {reservation.get("pnr"): reservation for reservation in reservations}
    test.assertEqual(set(by_pnr), set(expected))
    for pnr, pairs in expected.items():
        tickets = by_pnr[pnr].get("tickets") or []
        got_numbers = [ticket.get("ticketNumber") for ticket in tickets]
        test.assertEqual(
            got_numbers,
            [number for number, _name in pairs],
            pnr,
        )
        for ticket, (number, name) in zip(tickets, pairs):
            test.assertEqual(ticket.get("ticketNumber"), number, pnr)
            if name:
                test.assertIn(name, ticket.values(), pnr)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class MemoryCollection:
    """insert_one / find_one stand-in for the calls storage and the routes make."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"mem-{len(self.docs) + 1}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.docs if self._matches(doc, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: doc.get(field) if doc.get(field) is not None else "",
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    @staticmethod
    def _matches(doc, query):
        for key, expected in query.items():
            if key == "reservations.pnr":
                pnrs = [
                    reservation.get("pnr")
                    for reservation in doc.get("reservations", [])
                ]
                if expected not in pnrs:
                    return False
                continue
            actual = doc.get(key)
            if isinstance(expected, dict) and set(expected) <= {"$lte"}:
                if actual is None or not actual <= expected["$lte"]:
                    return False
                continue
            if actual != expected:
                return False
        return True


class MemoryDB(dict):
    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, MemoryCollection())
        return dict.__getitem__(self, name)


class ReservationTicketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample_xml = SAMPLE.read_text(encoding="utf-8")
        cls.sample_body = _parse_trip_search(cls.sample_xml)
        cls.expected = _expected_tickets(cls.sample_xml)
        cls.stored = convert_reservations(
            cls.sample_body, AIRLINE, FLIGHT, ORIGIN, DATE
        )
        cls.db = MemoryDB()
        storage.init_db(cls.db)
        try:
            storage.store_reservations(cls.stored)
            storage.store_snapshot(
                request_id="req-reservation-tickets",
                airline=AIRLINE,
                flight_number=FLIGHT,
                origin=ORIGIN,
                departure_date=DATE,
                snapshot_type="reservations",
                data=cls.stored,
            )
        finally:
            storage._db = None
            storage._client = None
            storage._owns_connection = False

    def test_sample_ticket_details_are_stored_on_reservations(self):
        counts = [len(pairs) for pairs in self.expected.values()]
        self.assertIn(("0722136405715", "SHAIK/A"), self.expected["DQGZWU"])
        self.assertIn(("0722136405716", "KURUP/P"), self.expected["DQGZWU"])
        self.assertIn(1, counts)
        self.assertTrue(any(count > 1 for count in counts))
        _assert_tickets(self, self.stored["reservations"], self.expected)

    def test_single_ticket_details_beats_ticketing_attributes(self):
        expected = _expected_tickets(SINGLE_TICKET_XML)
        body = _parse_trip_search(SINGLE_TICKET_XML)
        doc = convert_reservations(body, AIRLINE, FLIGHT, ORIGIN, DATE)
        self.assertEqual(expected["ONLYTD"], [("0722136405999", "DOE/J")])
        _assert_tickets(self, doc["reservations"], expected)
        stored_numbers = [
            ticket.get("ticketNumber")
            for ticket in doc["reservations"][0]["tickets"]
        ]
        self.assertNotIn("9", stored_numbers)
        self.assertNotIn("NOT-THE-SERIAL", stored_numbers)
        self.assertNotIn("2", stored_numbers)

    def test_reservations_endpoint_returns_sample_ticket_numbers(self):
        with patch.object(reservations_api, "get_db", return_value=self.db):
            result = reservations_api.get_reservations(
                FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None
            )
        self.assertNotIn("_raw", result)
        _assert_tickets(self, result["reservations"], self.expected)
        dumped = ReservationsResponse.model_validate(result).model_dump()
        _assert_tickets(self, dumped["reservations"], self.expected)

    def test_reservation_by_pnr_returns_sample_ticket_numbers(self):
        with patch.object(reservations_api, "get_db", return_value=self.db):
            listed = reservations_api.get_reservations(
                FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None
            )
            one = reservations_api.get_reservation_by_pnr(
                FLIGHT, "DQGZWU", date=DATE
            )
            single_pnr = next(
                pnr for pnr, pairs in self.expected.items() if len(pairs) == 1
            )
            single = reservations_api.get_reservation_by_pnr(
                FLIGHT, single_pnr, date=DATE
            )
        self.assertNotIn("_raw", listed)
        _assert_tickets(
            self,
            one["reservations"],
            {"DQGZWU": self.expected["DQGZWU"]},
        )
        _assert_tickets(
            self,
            single["reservations"],
            {single_pnr: self.expected[single_pnr]},
        )

    def test_snapshot_reservations_endpoint_returns_sample_ticket_numbers(self):
        with patch.object(reservations_api, "get_db", return_value=self.db):
            result = reservations_api.get_reservations(
                FLIGHT,
                origin=ORIGIN,
                date=DATE,
                snapshot_sequence=1,
            )
        self.assertNotIn("_raw", result)
        _assert_tickets(self, result["reservations"], self.expected)
        dumped = ReservationsResponse.model_validate(result).model_dump()
        _assert_tickets(self, dumped["reservations"], self.expected)


if __name__ == "__main__":
    unittest.main()
