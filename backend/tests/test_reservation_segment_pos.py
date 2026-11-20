"""Selling agency on Trip Search air segments must survive reservation conversion.

Trip_SearchRS air segments carry a Pos child (selling CRS code, agency IATA,
agency city, country, and duty code). Teletype bookings often have that Pos
and a reservation Source with no AgentSine. These tests are the spec for
keeping that selling identity:

- each stored air segment has pointOfSale.crsCode, iataNumber, agencyCityCode,
  countryCode, and dutyCode copied from that segment's Pos
- a segment with no Pos stores those fields as empty strings
- when the reservation Source has no AgentSine, dashboard booking attribution
  uses the segment CRS code (party size for insights, one count per reservation
  for booking sources)
- when the reservation Source has an AgentSine, that sine stays the booking
  channel and the segment Pos is still stored
- the reservations list response model returns the segment point of sale

The tests fail until that behavior exists.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient
from backend.sabre.models import ReservationsResponse


def _air(departure, arrival, pos_xml):
    return f"""<stl19:Segment>
      <stl19:Air>
        <stl19:DepartureAirport>{departure}</stl19:DepartureAirport>
        <stl19:ArrivalAirport>{arrival}</stl19:ArrivalAirport>
        <stl19:MarketingAirlineCode>GF</stl19:MarketingAirlineCode>
        <stl19:FlightNumber>2057</stl19:FlightNumber>
        <stl19:ClassOfService>Q</stl19:ClassOfService>
        <stl19:ActionCode>HK</stl19:ActionCode>
        {pos_xml}
      </stl19:Air>
    </stl19:Segment>"""


def _pos(crs, iata, city, country, duty):
    return f"""<stl19:Pos>
      <stl19:CrsCode>{crs}</stl19:CrsCode>
      <stl19:IataNumber>{iata}</stl19:IataNumber>
      <stl19:AgencyCityCode>{city}</stl19:AgencyCityCode>
      <stl19:CountryCode>{country}</stl19:CountryCode>
      <stl19:DutyCode>{duty}</stl19:DutyCode>
    </stl19:Pos>"""


def _reservation(locator, party, source_xml, segments_xml):
    return f"""<Reservation Locator="{locator}">
      <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
        <stl19:Reservation numberInParty="{party}" numberOfInfants="0">
          <stl19:BookingDetails>
            <stl19:CreationTimestamp>2026-03-16T22:53:00</stl19:CreationTimestamp>
          </stl19:BookingDetails>
          {source_xml}
          <stl19:PassengerReservation>
            <stl19:Passengers>
              <stl19:Passenger>
                <stl19:LastName>EXAMPLE</stl19:LastName>
                <stl19:FirstName>PASSENGER</stl19:FirstName>
              </stl19:Passenger>
            </stl19:Passengers>
            <stl19:Segments>
              {segments_xml}
            </stl19:Segments>
          </stl19:PassengerReservation>
        </stl19:Reservation>
      </stl19:GetReservationRS>
    </Reservation>"""


def _tty_source(crs, iata, country):
    """Reservation source shaped like a teletype booking: no AgentSine."""
    return f"""<stl19:POS>
      <stl19:Source AirlineVendorID="GF" BookingSource="A0A0"
                    HomePseudoCityCode="TTY" ISOCountry="{country}" PseudoCityCode="TTY">
        <stl19:TTYRecordLocator>
          <stl19:CRSCode>{crs}</stl19:CRSCode>
          <stl19:IataNumber>{iata}</stl19:IataNumber>
        </stl19:TTYRecordLocator>
      </stl19:Source>
    </stl19:POS>"""


def _agent_source(sine):
    return f"""<stl19:POS>
      <stl19:Source AgentDutyCode="4" AgentSine="{sine}" AirlineVendorID="GF"
                    BookingSource="A0A0" HomePseudoCityCode="WGF" ISOCountry="BH"
                    PseudoCityCode="WGF"/>
    </stl19:POS>"""


def _trip_search_xml():
    amadeus = _reservation(
        "TTY1A1",
        1,
        _tty_source("1A", "11111111", "US"),
        _air("BOM", "DMM", _pos("1A", "11111111", "HOU", "US", "R")),
    )
    galileo = _reservation(
        "TTY1G1",
        3,
        _tty_source("1G", "22222222", "BH"),
        _air("BOM", "BAH", "")
        + _air("BAH", "DMM", _pos("1G", "22222222", "BAH", "BH", "R")),
    )
    web = _reservation(
        "WEB001",
        2,
        _agent_source("WEB"),
        _air("BOM", "DMM", _pos("1S", "33333333", "BAH", "BH", "4")),
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord" Version="4.5.0">
      <Success>Success</Success>
      <ReservationsList NumberResults="3" TotalResults="3">
        <Reservations>
          {amadeus}
          {galileo}
          {web}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _convert():
    """Parse a Trip Search response the way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(_trip_search_xml())
    body = SabreClient._extract_body(SabreClient.__new__(SabreClient), parsed, "Trip_SearchRS")
    return convert_reservations(body, "GF", "2057", "BOM", "2026-03-18")


def _by_pnr(doc, pnr):
    matches = [item for item in doc["reservations"] if item.get("pnr") == pnr]
    assert len(matches) == 1, pnr
    return matches[0]


def _assert_pos(segment, crs, iata, city, country, duty):
    pos = segment.get("pointOfSale")
    assert pos == {
        "crsCode": crs,
        "iataNumber": iata,
        "agencyCityCode": city,
        "countryCode": country,
        "dutyCode": duty,
    }, pos


def test_stored_segments_keep_air_segment_point_of_sale():
    doc = _convert()

    amadeus = _by_pnr(doc, "TTY1A1")
    assert amadeus["segments"][0]["marketingAirline"] == "GF"
    assert amadeus["segments"][0]["flightNumber"] == "2057"
    assert amadeus["segments"][0]["departureAirport"] == "BOM"
    _assert_pos(amadeus["segments"][0], "1A", "11111111", "HOU", "US", "R")
    assert amadeus["pointOfSale"].get("agentSine", "") in ("", "1A")

    galileo = _by_pnr(doc, "TTY1G1")
    assert len(galileo["segments"]) == 2
    _assert_pos(galileo["segments"][0], "", "", "", "", "")
    _assert_pos(galileo["segments"][1], "1G", "22222222", "BAH", "BH", "R")

    web = _by_pnr(doc, "WEB001")
    assert web["pointOfSale"]["agentSine"] == "WEB"
    _assert_pos(web["segments"][0], "1S", "33333333", "BAH", "BH", "4")
    print("  PASS: test_stored_segments_keep_air_segment_point_of_sale")


def test_selling_crs_is_the_booking_channel_when_agent_sine_is_missing():
    doc = _convert()
    passenger_list = {
        "passengers": [
            {"lastName": "EXAMPLE", "pnr": "TTY1A1", "cabin": "Y"},
        ],
        "totalPassengers": 1,
        "totalSouls": 1,
        "origin": "BOM",
        "destination": "DMM",
        "departureDate": "2026-03-18",
    }
    dashboard = _build_dashboard_payload(
        None, passenger_list, "BOM", "2026-03-18", {}, reservation_doc=doc,
    )
    channels = dashboard["insights"]["bookingChannels"]["channels"]
    sources = dashboard["specialRequestsSummary"]["bookingSources"]

    assert channels.get("1A") == 1, channels
    assert channels.get("1G") == 3, channels
    assert channels.get("WEB") == 2, channels
    assert "1S" not in channels, channels

    assert sources.get("1A") == 1, sources
    assert sources.get("1G") == 1, sources
    assert sources.get("WEB") == 1, sources
    assert "1S" not in sources, sources
    print("  PASS: test_selling_crs_is_the_booking_channel_when_agent_sine_is_missing")


def test_reservations_list_response_includes_segment_point_of_sale():
    doc = _convert()
    public = ReservationsResponse.model_validate(doc).model_dump()
    amadeus = _by_pnr(public, "TTY1A1")
    _assert_pos(amadeus["segments"][0], "1A", "11111111", "HOU", "US", "R")
    galileo = _by_pnr(public, "TTY1G1")
    _assert_pos(galileo["segments"][0], "", "", "", "", "")
    _assert_pos(galileo["segments"][1], "1G", "22222222", "BAH", "BH", "R")
    print("  PASS: test_reservations_list_response_includes_segment_point_of_sale")


if __name__ == "__main__":
    print("Running reservation segment point-of-sale tests...")
    tests = [
        test_stored_segments_keep_air_segment_point_of_sale,
        test_selling_crs_is_the_booking_channel_when_agent_sine_is_missing,
        test_reservations_list_response_includes_segment_point_of_sale,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAIL: {test.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        print(f"{failed} TEST(S) FAILED")
        raise SystemExit(1)
    print("ALL TESTS PASS")
