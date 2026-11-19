"""Baggage late-check, embargo, and short-check must survive Sabre parsing.

Sabre GetPassengerDataRS / GetPassengerListRS mark a BaggageRoute with
LateCheckin (sample value ``Y``), BagEmbargo (xsd:short, sample value ``2``),
and ShortCheckinReason, while SegmentStatus stays the booking status ``HK``.

These tests parse a SOAP envelope with SabreClient and run the real
converters. They fail while those markers are dropped or compared to the
string ``true``.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.feeder.converter import convert_passenger_data, convert_passenger_list
from backend.sabre.client import SabreClient

LATE_REASON = "Unauthorized baggage route"
SHORT_REASON = "No interline baggage agreement"


def _soap_body(xml_text, response_key):
    """Parse the same way a live Sabre response is parsed, without a session."""
    parsed = SabreClient._parse_xml(xml_text)
    client = object.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, response_key)


def _passenger_data_xml():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>ALNAHAR</LastName>
          <FirstName>NOOR</FirstName>
          <PassengerID>PAX0001</PassengerID>
          <PNRLocator nameAssociationID="1">ABC123</PNRLocator>
          <BaggageRouteList>
            <ns2:BaggageRoute>
              <ns2:SegmentID>1</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>2057</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>LHR</ns2:Destination>
              <ns2:DepartureDate>2026-03-18</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAX0001</ns2:PassengerID>
              <ns2:LateCheckin>Y</ns2:LateCheckin>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>2</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>632</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>MCT</ns2:Destination>
              <ns2:DepartureDate>2026-03-18</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAX0001</ns2:PassengerID>
              <ns2:BagEmbargo>2</ns2:BagEmbargo>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>3</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>941</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>DXB</ns2:Destination>
              <ns2:DepartureDate>2026-03-18</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAX0001</ns2:PassengerID>
              <ns2:BagEmbargo>1</ns2:BagEmbargo>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>4</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>942</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:Origin>MCT</ns2:Origin>
              <ns2:Destination>DXB</ns2:Destination>
              <ns2:DepartureDate>2026-03-18</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAX0001</ns2:PassengerID>
              <ns2:ShortCheckinReason>{LATE_REASON}</ns2:ShortCheckinReason>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>5</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>11</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:Origin>DOH</ns2:Origin>
              <ns2:Destination>BAH</ns2:Destination>
              <ns2:DepartureDate>2026-03-18</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAX0001</ns2:PassengerID>
              <ns2:LateCheckin>true</ns2:LateCheckin>
              <ns2:BagEmbargo>0</ns2:BagEmbargo>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>6</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>12</ns2:Flight>
              <ns2:BookingClass>J</ns2:BookingClass>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>CDG</ns2:Destination>
              <ns2:DepartureDate>2026-03-18</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:PassengerID>PAX0001</ns2:PassengerID>
            </ns2:BaggageRoute>
          </BaggageRouteList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger_list_xml():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <DepartureDate>2026-03-18</DepartureDate>
          <Origin>BAH</Origin>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>ALNAHAR</LastName>
            <FirstName>NOOR</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">ABC123</PNRLocator>
          <PassengerID>PAX0001</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>Y</BookingClass>
          <BaggageRouteList>
            <ns2:BaggageRoute>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>2057</ns2:Flight>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>LHR</ns2:Destination>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
              <ns2:LateCheckin>Y</ns2:LateCheckin>
              <ns2:BagEmbargo>2</ns2:BagEmbargo>
              <ns2:ShortCheckinReason>{SHORT_REASON}</ns2:ShortCheckinReason>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>12</ns2:Flight>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>CDG</ns2:Destination>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
            </ns2:BaggageRoute>
          </BaggageRouteList>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _routes_by_flight(routes):
    return {route["flight"]: route for route in routes}


def _assert_embargo_code(route, expected):
    assert type(route["bagEmbargo"]) is int, route["bagEmbargo"]
    assert route["bagEmbargo"] == expected


def _assert_clear_route(route):
    assert route["segmentStatus"] == "HK"
    assert route["lateCheckin"] is False
    _assert_embargo_code(route, 0)
    assert route["shortCheckinReason"] == ""


def test_passenger_data_keeps_late_check_embargo_and_short_check():
    raw = _soap_body(_passenger_data_xml(), "GetPassengerDataRS")
    doc = convert_passenger_data(raw, "GF", "2057", "2026-03-18", "BAH")
    routes = _routes_by_flight(doc["passengers"][0]["baggageRoutes"])

    late = routes["2057"]
    assert late["segmentStatus"] == "HK"
    assert late["lateCheckin"] is True
    _assert_embargo_code(late, 0)
    assert late["shortCheckinReason"] == ""

    embargo_two = routes["632"]
    assert embargo_two["segmentStatus"] == "HK"
    assert embargo_two["lateCheckin"] is False
    _assert_embargo_code(embargo_two, 2)
    assert embargo_two["shortCheckinReason"] == ""

    embargo_one = routes["941"]
    assert embargo_one["lateCheckin"] is False
    _assert_embargo_code(embargo_one, 1)

    short = routes["942"]
    assert short["segmentStatus"] == "HK"
    assert short["lateCheckin"] is False
    _assert_embargo_code(short, 0)
    assert short["shortCheckinReason"] == LATE_REASON

    literal_true = routes["11"]
    assert literal_true["segmentStatus"] == "HK"
    assert literal_true["lateCheckin"] is True
    _assert_embargo_code(literal_true, 0)
    assert literal_true["shortCheckinReason"] == ""

    _assert_clear_route(routes["12"])


def test_passenger_list_keeps_late_check_embargo_and_short_check():
    raw = _soap_body(_passenger_list_xml(), "GetPassengerListRS")
    doc = convert_passenger_list(raw, "GF", "2057", "2026-03-18", "BAH")
    routes = _routes_by_flight(doc["passengers"][0]["baggageRoutes"])

    marked = routes["2057"]
    assert marked["segmentStatus"] == "HK"
    assert marked["lateCheckin"] is True
    _assert_embargo_code(marked, 2)
    assert marked["shortCheckinReason"] == SHORT_REASON

    _assert_clear_route(routes["12"])


if __name__ == "__main__":
    test_passenger_data_keeps_late_check_embargo_and_short_check()
    test_passenger_list_keeps_late_check_embargo_and_short_check()
    print("PASS")
