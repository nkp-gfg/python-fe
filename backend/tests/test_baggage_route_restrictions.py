"""Baggage restrictions Sabre returns must survive live passenger detail.

The product parses GetPassengerDataRS and GetPassengerListRS with
SabreClient._parse_xml and SabreClient._extract_body, then
convert_passenger_data / convert_passenger_list. These tests feed those
functions the vendor response shape (and the baggage-route fields defined
beside it) and require each route to expose operator-visible strings:

- petRestriction
- shortCheckinReason
- baggageRestrictionLimit
- homePrintedBagTag, containing the restriction attribute and every Reason text

Absent elements are empty strings so a route with no restriction still has
the same keys. The live detail route must return that document unchanged.

They fail until that copy exists.
"""

import json
import re
from pathlib import Path

from backend.api.routes import passengers
from backend.feeder.converter import convert_passenger_data, convert_passenger_list
from backend.sabre.client import SabreClient

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-1_SamplePayload_4-2-4-1.xml"
)

STATION_BAN = "!HOME PRINTED BAG TAG NOT ALLOWED AT THIS STATION"
PET_CABIN_BAN = "PET IN CABIN NOT PERMITTED"
UNAUTHORIZED_ROUTE = "UNAUTHORIZED BAGGAGE ROUTE"
SHORT_CHECK_MISCONNECT = "No interline baggage agreement"
SHORT_CHECK_UNAUTHORIZED = "Unauthorized baggage route"


def _vendor_response_xml():
    """The GetPassengerData response envelope from the vendor sample.

    The sample file wraps a request and a response, and the response header
    carries a sample credential. Only the response envelope is what
    get_passenger_data parses, and the security header is removed before
    parse so this test never reads that value.
    """
    text = SAMPLE.read_text()
    start = text.index("<soap-env:Envelope")
    end = text.index("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    xml = text[start:end]
    xml = re.sub(r"<wsse:Security\b[\s\S]*?</wsse:Security>", "", xml, count=1)
    return xml


def _with_route_restrictions(xml):
    """Add the sibling restriction elements the baggage-route type defines.

    The vendor sample already sets the home-printed tag ban on flight 129.
    PetRestriction, BaggageRestrictionLimit, and ShortCheckinReason are
    inserted on that same route. A further route uses two Reason elements
    that carry the code attribute from the passenger-list design sample.
    """
    insertion = (
        "<ns2:PetRestriction>Y</ns2:PetRestriction>"
        "<ns2:BaggageRestrictionLimit>2</ns2:BaggageRestrictionLimit>"
        f"<ns2:ShortCheckinReason>{SHORT_CHECK_MISCONNECT}</ns2:ShortCheckinReason>"
    )
    needle = '<ns2:HomePrintedBagTag restriction="true">'
    assert xml.count(needle) == 1
    xml = xml.replace(needle, insertion + needle, 1)
    extra = f"""
                  <ns2:BaggageRoute>
                     <ns2:SegmentID>9</ns2:SegmentID>
                     <ns2:Airline>LA</ns2:Airline>
                     <ns2:Flight>999</ns2:Flight>
                     <ns2:BookingClass>J</ns2:BookingClass>
                     <ns2:Origin>LIM</ns2:Origin>
                     <ns2:Destination>GRU</ns2:Destination>
                     <ns2:DepartureDate>2016-07-06</ns2:DepartureDate>
                     <ns2:SegmentStatus>HK</ns2:SegmentStatus>
                     <ns2:PetRestriction>N</ns2:PetRestriction>
                     <ns2:BaggageRestrictionLimit>0</ns2:BaggageRestrictionLimit>
                     <ns2:ShortCheckinReason>{SHORT_CHECK_UNAUTHORIZED}</ns2:ShortCheckinReason>
                     <ns2:HomePrintedBagTag restriction="true">
                        <ns2:ReasonList>
                           <ns2:Reason code="14">{PET_CABIN_BAN}</ns2:Reason>
                           <ns2:Reason code="15">{UNAUTHORIZED_ROUTE}</ns2:Reason>
                        </ns2:ReasonList>
                     </ns2:HomePrintedBagTag>
                  </ns2:BaggageRoute>
"""
    assert xml.count("</BaggageRouteList>") == 1
    return xml.replace("</BaggageRouteList>", extra + "</BaggageRouteList>", 1)


def _parse_body(xml, response_key):
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(None, parsed, response_key)


def _detail_from_xml(xml):
    body = _parse_body(xml, "GetPassengerDataRS")
    return convert_passenger_data(body, "LA", "129", "2016-07-04", "ANF")


def _routes_by_flight(document):
    routes = document["passengers"][0]["baggageRoutes"]
    found = {}
    for route in routes:
        found[route["flight"]] = route
    return found


def _assert_restriction(route, pet, limit, short_check, *tag_parts):
    assert route["petRestriction"] == pet
    assert route["baggageRestrictionLimit"] == limit
    assert route["shortCheckinReason"] == short_check
    tag = route["homePrintedBagTag"]
    assert isinstance(tag, str)
    assert not isinstance(tag, dict)
    for part in tag_parts:
        assert part in tag


def _assert_no_restriction(route):
    _assert_restriction(route, "", "", "")
    assert route["homePrintedBagTag"] == ""


def test_parser_delivers_vendor_home_printed_ban_before_conversion():
    """The drop happens after parse. This locks the input the converter sees."""
    body = _parse_body(_vendor_response_xml(), "GetPassengerDataRS")
    from backend.feeder.converter import _strip_ns

    data = _strip_ns(body)
    routes = data["PassengerDataResponseList"]["PassengerDataResponse"][
        "BaggageRouteList"
    ]["BaggageRoute"]
    first = routes[0]
    tag = first["HomePrintedBagTag"]
    assert tag["@restriction"] == "true"
    assert tag["ReasonList"]["Reason"] == STATION_BAN
    assert "PetRestriction" not in first


def test_vendor_sample_home_printed_ban_is_a_display_string():
    document = _detail_from_xml(_vendor_response_xml())
    routes = _routes_by_flight(document)
    _assert_restriction(routes["129"], "", "", "", "true", STATION_BAN)
    _assert_no_restriction(routes["632"])


def test_restriction_siblings_and_coded_reasons_are_copied():
    document = _detail_from_xml(_with_route_restrictions(_vendor_response_xml()))
    routes = _routes_by_flight(document)
    _assert_restriction(
        routes["129"],
        "Y",
        "2",
        SHORT_CHECK_MISCONNECT,
        "true",
        STATION_BAN,
    )
    _assert_no_restriction(routes["632"])
    _assert_restriction(
        routes["999"],
        "N",
        "0",
        SHORT_CHECK_UNAUTHORIZED,
        "true",
        PET_CABIN_BAN,
        UNAUTHORIZED_ROUTE,
    )


def test_live_passenger_detail_returns_those_strings():
    """get_passenger_detail returns convert_passenger_data with no extra filter."""
    body = _parse_body(
        _with_route_restrictions(_vendor_response_xml()), "GetPassengerDataRS"
    )

    class _List:
        def find_one(self, query, sort=None):
            assert query["flightNumber"] == "129"
            assert query["passengers.pnr"] == "DXXRYZ"
            assert query["departureDate"] == "2016-07-04"
            return {
                "passengers": [
                    {
                        "pnr": "DXXRYZ",
                        "lastName": "HDGLAX",
                        "firstName": "VRDJ",
                    }
                ]
            }

    class _DB:
        def __getitem__(self, name):
            assert name == "passenger_list"
            return _List()

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_passenger_data(self, **kwargs):
            assert kwargs["airline"] == "LA"
            assert kwargs["flight_number"] == "129"
            assert kwargs["departure_date"] == "2016-07-04"
            assert kwargs["origin"] == "ANF"
            assert kwargs["last_name"] == "HDGLAX"
            assert kwargs["first_name"] == "VRDJ"
            assert kwargs["pnr"] == "DXXRYZ"
            return body, "<response-xml-omitted>", {}

    original_db = passengers.get_db
    original_client = passengers.SabreClient
    passengers.get_db = lambda: _DB()
    passengers.SabreClient = _Client
    try:
        result = passengers.get_passenger_detail(
            "129",
            "DXXRYZ",
            origin="ANF",
            date="2016-07-04",
            airline="LA",
        )
    finally:
        passengers.get_db = original_db
        passengers.SabreClient = original_client

    payload = json.loads(json.dumps(result))
    routes = _routes_by_flight(payload)
    _assert_restriction(
        routes["129"],
        "Y",
        "2",
        SHORT_CHECK_MISCONNECT,
        "true",
        STATION_BAN,
    )
    _assert_restriction(
        routes["999"],
        "N",
        "0",
        SHORT_CHECK_UNAUTHORIZED,
        "true",
        PET_CABIN_BAN,
        UNAUTHORIZED_ROUTE,
    )
    _assert_no_restriction(routes["632"])


def _passenger_list_xml():
    """GetPassengerListRS baggage route, same element shape as the design sample."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <v4:GetPassengerListRS xmlns:v4="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:v3="http://services.sabre.com/STL/v3">
      <v4:PassengerInfoList>
        <v4:PassengerInfo>
          <v4:Name_Details>
            <v4:LastName>NIKE</v4:LastName>
            <v4:FirstName>ALEX</v4:FirstName>
          </v4:Name_Details>
          <v4:PNRLocator nameAssociationID="2">SDKBCH</v4:PNRLocator>
          <v4:BaggageRouteList>
            <v3:BaggageRoute>
              <v3:SegmentID>1</v3:SegmentID>
              <v3:Airline>K0</v3:Airline>
              <v3:Flight>121</v3:Flight>
              <v3:Origin>AAA</v3:Origin>
              <v3:Destination>BBB</v3:Destination>
              <v3:SegmentStatus>HK</v3:SegmentStatus>
              <v3:PetRestriction>Y</v3:PetRestriction>
              <v3:BaggageRestrictionLimit>2</v3:BaggageRestrictionLimit>
              <v3:ShortCheckinReason>{SHORT_CHECK_MISCONNECT}</v3:ShortCheckinReason>
              <v3:HomePrintedBagTag restriction="true">
                <v3:ReasonList>
                  <v3:Reason code="14">{STATION_BAN}</v3:Reason>
                </v3:ReasonList>
              </v3:HomePrintedBagTag>
            </v3:BaggageRoute>
            <v3:BaggageRoute>
              <v3:Airline>K0</v3:Airline>
              <v3:Flight>122</v3:Flight>
              <v3:Origin>BBB</v3:Origin>
              <v3:Destination>CCC</v3:Destination>
              <v3:SegmentStatus>HK</v3:SegmentStatus>
            </v3:BaggageRoute>
          </v4:BaggageRouteList>
        </v4:PassengerInfo>
      </v4:PassengerInfoList>
    </v4:GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def test_passenger_list_conversion_copies_the_same_restrictions():
    body = _parse_body(_passenger_list_xml(), "GetPassengerListRS")
    document = convert_passenger_list(body, "K0", "121", "2019-02-09", "AUH")
    routes = {route["flight"]: route for route in document["passengers"][0]["baggageRoutes"]}
    _assert_restriction(
        routes["121"],
        "Y",
        "2",
        SHORT_CHECK_MISCONNECT,
        "true",
        STATION_BAN,
    )
    _assert_no_restriction(routes["122"])
