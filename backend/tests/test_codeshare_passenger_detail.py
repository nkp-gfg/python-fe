"""Spec for codeshare labels on the live passenger-detail sheet.

The sheet an agent reads is built from Sabre GetPassengerData. These tests
parse that response the same way SabreClient does, run convert_passenger_data,
and check the payload the sheet receives.

They fail while a codeshare baggage route drops OperatingBookingClass. The
matching UI test renders that payload and fails until the itinerary card, the
baggage routing table, the fare table, and the ancillary table show the
operating flight and the operating booking class beside the marketed ones.

Run:
    PYTHONPATH=. python3 -m unittest backend.tests.test_codeshare_passenger_detail
    PYTHONPATH=. python3 backend/tests/test_codeshare_passenger_detail.py --json
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.feeder.converter import _ensure_list, _strip_ns, convert_passenger_data
from backend.sabre.client import SabreClient

SAMPLE = (
    ROOT
    / "sabre_docs/GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-4_SamplePayload_4_2_2_3_3b.xml"
)

# The published sample has no ancillary item. The extras tab labels each
# ancillary row with the segment's airline and flight, so the fixture adds
# one item and lets the real parser and converter carry it.
_AE_XML = (
    "<AEDetailsList><AEDetails>"
    "<ItemID>1</ItemID><ATPCOGroupCode>BG</ATPCOGroupCode>"
    "<StatusCode>HI</StatusCode><Quantity>1</Quantity>"
    "<PriceDetails><Amount>10</Amount><Currency>AUD</Currency></PriceDetails>"
    "</AEDetails></AEDetailsList>"
)


def _response_envelope(text: str) -> str:
    """The sample file wraps a request and a response. The client parses the response."""
    start = text.index("<soap-env:Envelope")
    end = text.index("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    return text[start:end]


def _parse_response(xml: str) -> dict:
    parsed = SabreClient._parse_xml(xml)
    # _extract_body does not use instance state; skip SabreClient() so the
    # test does not read session credentials.
    return SabreClient._extract_body(object(), parsed, "GetPassengerDataRS")


def _flight(airline: str, number: str) -> str:
    airline = airline or ""
    number = number or ""
    if not airline and not number:
        return ""
    return f"{airline}{number}"


def _operating_flight(node: dict) -> str:
    return _flight(node.get("OperatingAirline") or "", node.get("OperatingFlight") or "")


def _oracle(body: dict) -> dict:
    """Flight identity after namespace stripping, before the converter drops fields."""
    data = _strip_ns(body)
    passengers = _ensure_list(
        (data.get("PassengerDataResponseList") or {}).get("PassengerDataResponse", [])
    )
    pax = passengers[0]
    pnr_raw = pax.get("PNRLocator", "")
    if isinstance(pnr_raw, dict):
        pnr = pnr_raw.get("#text", "")
    else:
        pnr = str(pnr_raw)

    segments = []
    for seg in _ensure_list(
        (pax.get("PassengerItineraryList") or {}).get("PassengerItinerary", [])
    ):
        segments.append(
            {
                "origin": seg.get("Origin", ""),
                "destination": seg.get("Destination", ""),
                "marketingFlight": _flight(seg.get("Airline", ""), seg.get("Flight", "")),
                "operatingFlight": _operating_flight(seg),
                "marketedClass": seg.get("BookingClass", "") or "",
                "operatingClass": seg.get("OperatingBookingClass", "") or "",
                "hasFare": bool(
                    _ensure_list((seg.get("VCRInfoList") or {}).get("VCRInfo", []))
                ),
                "hasAncillary": bool(
                    _ensure_list((seg.get("AEDetailsList") or {}).get("AEDetails", []))
                ),
            }
        )

    routes = []
    for br in _ensure_list((pax.get("BaggageRouteList") or {}).get("BaggageRoute", [])):
        routes.append(
            {
                "origin": br.get("Origin", ""),
                "destination": br.get("Destination", ""),
                "marketingFlight": _flight(br.get("Airline", ""), br.get("Flight", "")),
                "operatingFlight": _operating_flight(br),
                "marketedClass": br.get("BookingClass", "") or "",
                "operatingClass": br.get("OperatingBookingClass", "") or "",
            }
        )

    return {
        "lastName": pax.get("LastName", ""),
        "firstName": pax.get("FirstName", ""),
        "pnr": pnr,
        "segments": segments,
        "routes": routes,
    }


def _detail_from_xml(xml: str, airline: str, flight: str, date: str, origin: str) -> dict:
    body = _parse_response(xml)
    oracle = _oracle(body)
    detail = convert_passenger_data(body, airline, flight, date, origin)
    # The sample DOCS line is a travel-document number. The flight cards do not
    # render it; drop it so a test failure cannot print it.
    for pax in detail.get("passengers", []):
        pax["freeText"] = []
        pax["docsData"] = {}
        pax["timaticInfo"] = []
    return {"detail": detail, "oracle": oracle}


def _envelope(passenger_xml: str) -> str:
    return (
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        '<ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4">'
        "<PassengerDataResponseList><PassengerDataResponse>"
        f"{passenger_xml}"
        "</PassengerDataResponse></PassengerDataResponseList>"
        "</ns3:GetPassengerDataRS></soap-env:Body></soap-env:Envelope>"
    )


def _itinerary(
    *,
    segment_id: str = "1",
    origin: str,
    destination: str,
    airline: str,
    flight: str,
    booking_class: str,
    operating_airline: str = "",
    operating_flight: str = "",
    operating_class: str = "",
    fare: bool = False,
    ancillary: bool = False,
) -> str:
    operating = ""
    if operating_airline or operating_flight or operating_class:
        operating = (
            f"<OperatingAirline>{operating_airline}</OperatingAirline>"
            f"<OperatingFlight>{operating_flight}</OperatingFlight>"
            f"<OperatingBookingClass>{operating_class}</OperatingBookingClass>"
        )
    extras = ""
    if fare:
        extras += (
            "<VCRInfoList><VCRInfo><FareBasisCode>YLOW</FareBasisCode>"
            "<BagAllowance>1</BagAllowance></VCRInfo></VCRInfoList>"
        )
    if ancillary:
        extras += _AE_XML
    return (
        "<PassengerItinerary>"
        f"<SegmentID>{segment_id}</SegmentID><Airline>{airline}</Airline><Flight>{flight}</Flight>"
        f"{operating}<BookingClass>{booking_class}</BookingClass>"
        f"<MarketingAirline>{airline}</MarketingAirline><MarketingFlight>{flight}</MarketingFlight>"
        f"<MarketingBookingClass>{booking_class}</MarketingBookingClass>"
        f"<Origin>{origin}</Origin><Destination>{destination}</Destination>"
        "<DepartureDate>2026-01-02</DepartureDate><Cabin>Y</Cabin>"
        f"{extras}</PassengerItinerary>"
    )


def _route(
    *,
    segment_id: str = "1",
    origin: str,
    destination: str,
    airline: str,
    flight: str,
    booking_class: str,
    operating_airline: str = "",
    operating_flight: str = "",
    operating_class: str = "",
) -> str:
    operating = ""
    if operating_airline or operating_flight or operating_class:
        operating = (
            f"<OperatingAirline>{operating_airline}</OperatingAirline>"
            f"<OperatingFlight>{operating_flight}</OperatingFlight>"
            f"<OperatingBookingClass>{operating_class}</OperatingBookingClass>"
        )
    return (
        "<BaggageRoute>"
        f"<SegmentID>{segment_id}</SegmentID><Airline>{airline}</Airline><Flight>{flight}</Flight>"
        f"<BookingClass>{booking_class}</BookingClass>{operating}"
        f"<Origin>{origin}</Origin><Destination>{destination}</Destination>"
        "<DepartureDate>2026-01-02</DepartureDate><SegmentStatus>HK</SegmentStatus>"
        "</BaggageRoute>"
    )


def build_cases() -> list[dict]:
    vendor_xml = _response_envelope(SAMPLE.read_text())
    vendor_xml = vendor_xml.replace(
        "</PassengerItinerary>", _AE_XML + "</PassengerItinerary>", 1
    )
    vendor = _detail_from_xml(vendor_xml, "EY", "6418", "2019-05-29", "SYD")

    mixed_xml = _envelope(
        "<LineNumber>1</LineNumber><LastName>QUILL</LastName><FirstName>ADA</FirstName>"
        "<PassengerID>P1</PassengerID><PNRLocator>TST001</PNRLocator>"
        "<BaggageRouteList>"
        + _route(
            origin="BAH",
            destination="LHR",
            airline="XX",
            flight="100",
            booking_class="J",
            operating_airline="YY",
            operating_flight="200",
            operating_class="F",
        )
        + _route(
            segment_id="2",
            origin="DOH",
            destination="DXB",
            airline="ZZ",
            flight="300",
            booking_class="Q",
        )
        + "</BaggageRouteList><PassengerItineraryList>"
        + _itinerary(
            origin="BAH",
            destination="LHR",
            airline="XX",
            flight="100",
            booking_class="J",
            operating_airline="YY",
            operating_flight="200",
            operating_class="F",
            fare=True,
            ancillary=True,
        )
        + _itinerary(
            segment_id="2",
            origin="DOH",
            destination="DXB",
            airline="ZZ",
            flight="300",
            booking_class="Q",
            fare=True,
        )
        + "</PassengerItineraryList>"
    )
    mixed = _detail_from_xml(mixed_xml, "XX", "100", "2026-01-02", "BAH")

    same_flight_xml = _envelope(
        "<LineNumber>1</LineNumber><LastName>QUILL</LastName><FirstName>ADA</FirstName>"
        "<PassengerID>P2</PassengerID><PNRLocator>TST002</PNRLocator>"
        "<BaggageRouteList>"
        + _route(
            origin="AUH",
            destination="MCT",
            airline="WW",
            flight="400",
            booking_class="M",
            operating_airline="WW",
            operating_flight="400",
            operating_class="S",
        )
        + "</BaggageRouteList><PassengerItineraryList>"
        + _itinerary(
            origin="AUH",
            destination="MCT",
            airline="WW",
            flight="400",
            booking_class="M",
            operating_airline="WW",
            operating_flight="400",
            operating_class="S",
            fare=True,
            ancillary=True,
        )
        + "</PassengerItineraryList>"
    )
    same_flight = _detail_from_xml(same_flight_xml, "WW", "400", "2026-01-02", "AUH")

    return [
        {"name": "vendor-codeshare", **vendor},
        {"name": "mixed-itinerary", **mixed},
        {"name": "same-flight-different-class", **same_flight},
    ]


def _case(name: str) -> dict:
    for case in build_cases():
        if case["name"] == name:
            return case
    raise AssertionError(name)


class CodesharePassengerDetailPayloadTests(unittest.TestCase):
    def test_vendor_itinerary_keeps_marketed_and_operating_identity(self):
        case = _case("vendor-codeshare")
        seg = case["detail"]["passengers"][0]["itinerary"][0]
        expected = case["oracle"]["segments"][0]
        self.assertEqual(expected["marketingFlight"], "EY6418")
        self.assertEqual(expected["operatingFlight"], "VA880")
        self.assertEqual(expected["marketedClass"], "L")
        self.assertEqual(expected["operatingClass"], "N")
        self.assertEqual(seg["airline"] + seg["flight"], "EY6418")
        self.assertEqual(seg["operatingAirline"] + seg["operatingFlight"], "VA880")
        self.assertEqual(seg["bookingClass"], "L")
        self.assertEqual(seg["operatingBookingClass"], "N")
        self.assertTrue(seg["aeDetails"])
        self.assertTrue(seg["vcrInfo"])

    def test_vendor_baggage_route_keeps_operating_flight_and_class(self):
        case = _case("vendor-codeshare")
        route = case["detail"]["passengers"][0]["baggageRoutes"][0]
        expected = case["oracle"]["routes"][0]
        self.assertEqual(expected["marketingFlight"], "EY6418")
        self.assertEqual(expected["operatingFlight"], "VA880")
        self.assertEqual(expected["operatingClass"], "N")
        self.assertEqual(route["airline"] + route["flight"], "EY6418")
        self.assertEqual(route["operatingAirline"] + route["operatingFlight"], "VA880")
        self.assertEqual(route["bookingClass"], "L")
        # OperatingBookingClass is on the baggage route in the vendor sample.
        # The sheet cannot show it unless the converter keeps it.
        self.assertEqual(route.get("operatingBookingClass"), "N")

    def test_other_codeshares_keep_operating_class_on_the_baggage_route(self):
        for name in ("mixed-itinerary", "same-flight-different-class"):
            case = _case(name)
            for route, expected in zip(
                case["detail"]["passengers"][0]["baggageRoutes"],
                case["oracle"]["routes"],
            ):
                self.assertEqual(
                    route["airline"] + route["flight"], expected["marketingFlight"]
                )
                self.assertEqual(
                    (route.get("operatingAirline") or "")
                    + (route.get("operatingFlight") or ""),
                    expected["operatingFlight"],
                )
                self.assertEqual(route["bookingClass"], expected["marketedClass"])
                self.assertEqual(
                    route.get("operatingBookingClass") or "",
                    expected["operatingClass"],
                    name,
                )

    def test_fixture_json_has_no_session_material_or_free_text(self):
        cases = build_cases()
        blob = json.dumps(cases)
        self.assertNotIn("BinarySecurityToken", blob)
        self.assertNotIn("TextLine", blob)
        for case in cases:
            for pax in case["detail"]["passengers"]:
                self.assertEqual(pax["freeText"], [])
                self.assertEqual(pax["docsData"], {})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        json.dump(build_cases(), sys.stdout)
    else:
        unittest.main()
