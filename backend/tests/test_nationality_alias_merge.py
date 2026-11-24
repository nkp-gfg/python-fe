"""Nationality and point-of-sale alias counts must survive as one group.

These tests fail while alpha-2 and alpha-3 country codes that name the same
country are grouped and plotted separately. They are the spec for the fix.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import mongomock

from backend.api.routes.analytics import _run_network_analytics
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
GF2057_TRIP_SEARCH = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

# Codes the analytics label map already treats as one country, plus BHR,
# which is shown as raw text beside Bahrain.
ALIAS_GROUPS = {
    "IN": "IN",
    "IND": "IN",
    "BH": "BH",
    "BHR": "BH",
    "PH": "PH",
    "PHL": "PH",
}

ALIAS_SOAP = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="4">
        <Reservations>
          <Reservation Locator="AAA111">
            <GetReservationRS>
              <Reservation>
                <POS><Source ISOCountry="IN" /></POS>
                <PassengerReservation>
                  <Passengers>
                    <Passenger>
                      <LastName>ONE</LastName>
                      <FirstName>A</FirstName>
                      <SpecialRequests>
                        <APISRequest>
                          <DOCSEntry>
                            <Gender>M</Gender>
                            <DocumentNationalityCountry>IN</DocumentNationalityCountry>
                          </DOCSEntry>
                        </APISRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger>
                      <LastName>TWO</LastName>
                      <FirstName>B</FirstName>
                      <SpecialRequests>
                        <APISRequest>
                          <DOCSEntry>
                            <Gender>F</Gender>
                            <DocumentNationalityCountry>BH</DocumentNationalityCountry>
                          </DOCSEntry>
                        </APISRequest>
                      </SpecialRequests>
                    </Passenger>
                  </Passengers>
                </PassengerReservation>
              </Reservation>
            </GetReservationRS>
          </Reservation>
          <Reservation Locator="BBB222">
            <GetReservationRS>
              <Reservation>
                <POS><Source ISOCountry="IND" /></POS>
                <PassengerReservation>
                  <Passengers>
                    <Passenger>
                      <LastName>THREE</LastName>
                      <FirstName>C</FirstName>
                      <SpecialRequests>
                        <APISRequest>
                          <DOCSEntry>
                            <Gender>M</Gender>
                            <DocumentNationalityCountry>IND</DocumentNationalityCountry>
                          </DOCSEntry>
                        </APISRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger>
                      <LastName>FOUR</LastName>
                      <FirstName>D</FirstName>
                      <SpecialRequests>
                        <APISRequest>
                          <DOCSEntry>
                            <Gender>F</Gender>
                            <DocumentNationalityCountry>BHR</DocumentNationalityCountry>
                          </DOCSEntry>
                        </APISRequest>
                      </SpecialRequests>
                    </Passenger>
                  </Passengers>
                </PassengerReservation>
              </Reservation>
            </GetReservationRS>
          </Reservation>
          <Reservation Locator="CCC333">
            <GetReservationRS>
              <Reservation>
                <POS><Source ISOCountry="BH" /></POS>
                <PassengerReservation>
                  <Passengers>
                    <Passenger>
                      <LastName>FIVE</LastName>
                      <FirstName>E</FirstName>
                      <SpecialRequests>
                        <APISRequest>
                          <DOCSEntry>
                            <Gender>M</Gender>
                            <DocumentNationalityCountry>PH</DocumentNationalityCountry>
                          </DOCSEntry>
                        </APISRequest>
                      </SpecialRequests>
                    </Passenger>
                  </Passengers>
                </PassengerReservation>
              </Reservation>
            </GetReservationRS>
          </Reservation>
          <Reservation Locator="DDD444">
            <GetReservationRS>
              <Reservation>
                <POS><Source ISOCountry="BHR" /></POS>
                <PassengerReservation>
                  <Passengers>
                    <Passenger>
                      <LastName>SIX</LastName>
                      <FirstName>F</FirstName>
                      <SpecialRequests>
                        <APISRequest>
                          <DOCSEntry>
                            <Gender>F</Gender>
                            <DocumentNationalityCountry>PHL</DocumentNationalityCountry>
                          </DOCSEntry>
                        </APISRequest>
                      </SpecialRequests>
                    </Passenger>
                  </Passengers>
                </PassengerReservation>
              </Reservation>
            </GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_trip_search(xml_text: str):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(SabreClient.__new__(SabreClient), parsed, "Trip_SearchRS")


def _analytics_for(doc, date_from, date_to, origin, flight_number):
    client = mongomock.MongoClient()
    db = client["falconeye"]
    db.reservations.insert_one(doc)
    db.flights.insert_one({
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": date_from,
    })
    db.passenger_list.insert_one({
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": date_from,
        "fetchedAt": "2026-03-18T00:00:00Z",
        "totalPassengers": 0,
        "passengers": [],
        "cabinSummary": [],
    })
    with patch("backend.api.routes.analytics.get_db", return_value=db):
        return _run_network_analytics(
            date_from, date_to, origin=origin, flight_number=flight_number,
        )


def _rows_for_alias(distribution, code_key, canonical):
    members = {code for code, group in ALIAS_GROUPS.items() if group == canonical}
    members.add(canonical)
    return [row for row in distribution if row[code_key] in members]


def _selected_nationalities(raw_data):
    """Document nationality convert_reservations reads for each passenger.

    Mirrors the first gendered DOCSEntry on the first APISRequest. Counts are
    taken from the parsed Trip Search body so a fix may normalize either
    during conversion or during analytics grouping.
    """
    from backend.feeder.converter import _ensure_list, _strip_ns

    data = _strip_ns(raw_data)
    reservations = _ensure_list(
        (data.get("ReservationsList", {}).get("Reservations") or {}).get("Reservation", [])
    )
    codes = []
    for reservation in reservations:
        inner = reservation.get("GetReservationRS", {}).get("Reservation", {})
        passengers = _ensure_list(
            ((inner.get("PassengerReservation") or {}).get("Passengers") or {}).get("Passenger", [])
        )
        for passenger in passengers:
            special_requests = passenger.get("SpecialRequests") or {}
            apis_request = special_requests.get("APISRequest") or {}
            if isinstance(apis_request, list):
                apis_request = apis_request[0] if apis_request else {}
            if not isinstance(apis_request, dict):
                apis_request = {}
            nationality = ""
            for doc_entry in _ensure_list(apis_request.get("DOCSEntry", [])):
                if isinstance(doc_entry, dict) and doc_entry.get("Gender", ""):
                    nationality = doc_entry.get("DocumentNationalityCountry", "") or ""
                    break
            if nationality:
                codes.append(nationality)
    return codes


def _selected_pos_countries(raw_data):
    from backend.feeder.converter import _ensure_list, _strip_ns

    data = _strip_ns(raw_data)
    reservations = _ensure_list(
        (data.get("ReservationsList", {}).get("Reservations") or {}).get("Reservation", [])
    )
    countries = []
    for reservation in reservations:
        inner = reservation.get("GetReservationRS", {}).get("Reservation", {})
        pos = inner.get("POS") or {}
        source = pos.get("Source") or {} if isinstance(pos, dict) else {}
        if isinstance(source, dict):
            country = source.get("@ISOCountry", "") or ""
            if country:
                countries.append(country)
    return countries


class NationalityAliasMergeTests(unittest.TestCase):
    def test_captured_bom_trip_search_keeps_india_as_one_group(self):
        """GF2057 BOM 2026-03-18 stores both IN and IND as passenger nationality."""
        xml_text = GF2057_TRIP_SEARCH.read_text(encoding="utf-8")
        raw = _parse_trip_search(xml_text)
        selected = _selected_nationalities(raw)
        india_passengers = sum(1 for code in selected if ALIAS_GROUPS.get(code, code) == "IN")
        gb_passengers = sum(1 for code in selected if code == "GB")
        self.assertGreater(india_passengers, 0)
        self.assertGreater(selected.count("IN"), 0)
        self.assertGreater(selected.count("IND"), 0)

        doc = convert_reservations(raw, "GF", "2057", "BOM", "2026-03-18")
        result = _analytics_for(doc, "2026-03-18", "2026-03-18", "BOM", "2057")
        distribution = result["nationalityDistribution"]
        india_rows = _rows_for_alias(distribution, "nationality", "IN")
        self.assertEqual(
            len(india_rows),
            1,
            f"IN and IND must be one nationality group, got {distribution}",
        )
        self.assertEqual(india_rows[0]["count"], india_passengers)
        gb_rows = [row for row in distribution if row["nationality"] == "GB"]
        self.assertEqual(len(gb_rows), 1)
        self.assertEqual(gb_rows[0]["count"], gb_passengers)

    def test_alpha2_and_alpha3_aliases_group_before_the_chart(self):
        """Selected document nationality and POS country are stored unchanged."""
        raw = _parse_trip_search(ALIAS_SOAP)
        selected = _selected_nationalities(raw)
        pos = _selected_pos_countries(raw)
        doc = convert_reservations(raw, "GF", "1", "BAH", "2026-03-18")
        result = _analytics_for(doc, "2026-03-18", "2026-03-18", "BAH", "1")

        nationality = result["nationalityDistribution"]
        for canonical in ("IN", "BH", "PH"):
            with self.subTest(distribution="nationality", canonical=canonical):
                expected = sum(1 for code in selected if ALIAS_GROUPS.get(code, code) == canonical)
                rows = _rows_for_alias(nationality, "nationality", canonical)
                self.assertGreater(expected, 1)
                self.assertEqual(
                    len(rows),
                    1,
                    f"{canonical} aliases must be one nationality, got {nationality}",
                )
                self.assertEqual(rows[0]["count"], expected)

        point_of_sale = result["pointOfSaleDistribution"]
        for canonical in ("IN", "BH"):
            with self.subTest(distribution="pointOfSale", canonical=canonical):
                expected = sum(1 for code in pos if ALIAS_GROUPS.get(code, code) == canonical)
                rows = _rows_for_alias(point_of_sale, "country", canonical)
                self.assertGreater(expected, 1)
                self.assertEqual(
                    len(rows),
                    1,
                    f"{canonical} aliases must be one point-of-sale country, got {point_of_sale}",
                )
                self.assertEqual(rows[0]["count"], expected)


if __name__ == "__main__":
    os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")
    unittest.main()
