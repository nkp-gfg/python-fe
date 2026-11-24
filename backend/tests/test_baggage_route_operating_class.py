"""Live passenger detail must keep both baggage-route booking classes.

Sabre GetPassengerData baggage routes carry BookingClass, MarketingBookingClass,
and OperatingBookingClass. On a codeshare segment those differ: the vendor
sample for EY 6418 operated by VA 880 sends marketing class L and operating
class N. The detail route is unauthenticated. It looks up the stored traveler,
calls GetPassengerData, and returns convert_passenger_data() unchanged. The
baggage card prints one class for that route.

These tests fail until each baggage route in that response keeps
marketingBookingClass and operatingBookingClass, and the baggage card prints
the operating class when the operating carrier differs from the marketing
carrier.

Not covered here:
- convert_passenger_list() also drops those fields, but stored passenger-list
  baggage routes are only counted by destination. No screen prints a class
  from them.
- The itinerary block already stores both class fields and still prints
  bookingClass. That line is not the baggage card.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = (
    REPO_ROOT
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-4_SamplePayload_4_2_2_3_3b.xml"
)
UI_RUNNER = Path(__file__).resolve().parent / "run_baggage_card_operating_class_test.mts"
FRONTEND_DIR = REPO_ROOT / "frontend"

# Obvious placeholders. The detail call must not reach a real Sabre host.
_SABRE_ENV = {
    "SABRE_BASE_URL": "http://placeholder.invalid",
    "SABRE_CPAID": "XX",
    "SABRE_USERNAME": "placeholder-user",
    "SABRE_PASSWORD": "placeholder-password",
    "SABRE_PSEUDO_CITY_CODE": "XXXX",
    "SABRE_ORGANIZATION": "XX",
    "SABRE_DOMAIN": "XX",
    "SABRE_API_DELAY_SECONDS": "0",
    "MONGODB_URI": "mongodb://placeholder.invalid:27017/falconeye",
}

# Marketing class M is intentionally different from BookingClass L so a fix
# cannot satisfy the card by printing either of those two fields.
_TWO_CLASS_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4"
                            xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>1</LineNumber>
          <LastName>HORN</LastName>
          <FirstName>YVETTE</FirstName>
          <PassengerID>F335D0A70001</PassengerID>
          <PNRLocator nameAssociationID="1">QKZOBC</PNRLocator>
          <BaggageRouteList>
            <ns2:BaggageRoute>
              <ns2:SegmentID>1</ns2:SegmentID>
              <ns2:Airline>EY</ns2:Airline>
              <ns2:Flight>6418</ns2:Flight>
              <ns2:BookingClass>L</ns2:BookingClass>
              <ns2:MarketingBookingClass>M</ns2:MarketingBookingClass>
              <ns2:OperatingBookingClass>N</ns2:OperatingBookingClass>
              <ns2:OperatingAirline>VA</ns2:OperatingAirline>
              <ns2:OperatingFlight>880</ns2:OperatingFlight>
              <ns2:Origin>SYD</ns2:Origin>
              <ns2:Destination>MEL</ns2:Destination>
              <ns2:DepartureDate>2019-05-29</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>2</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>010</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>DXB</ns2:Destination>
              <ns2:DepartureDate>2019-05-30</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>3</ns2:SegmentID>
              <ns2:Airline>GF</ns2:Airline>
              <ns2:Flight>011</ns2:Flight>
              <ns2:BookingClass>J</ns2:BookingClass>
              <ns2:OperatingAirline>WY</ns2:OperatingAirline>
              <ns2:OperatingFlight>222</ns2:OperatingFlight>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>MCT</ns2:Destination>
              <ns2:DepartureDate>2019-05-30</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
            </ns2:BaggageRoute>
            <ns2:BaggageRoute>
              <ns2:SegmentID>4</ns2:SegmentID>
              <ns2:Airline>gf</ns2:Airline>
              <ns2:Flight>012</ns2:Flight>
              <ns2:BookingClass>Y</ns2:BookingClass>
              <ns2:MarketingBookingClass>Y</ns2:MarketingBookingClass>
              <ns2:OperatingBookingClass>C</ns2:OperatingBookingClass>
              <ns2:OperatingAirline>GF</ns2:OperatingAirline>
              <ns2:OperatingFlight>012</ns2:OperatingFlight>
              <ns2:Origin>BAH</ns2:Origin>
              <ns2:Destination>DOH</ns2:Destination>
              <ns2:DepartureDate>2019-05-30</ns2:DepartureDate>
              <ns2:SegmentStatus>HK</ns2:SegmentStatus>
            </ns2:BaggageRoute>
          </BaggageRouteList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _vendor_response_envelope() -> str:
    """Return the SOAP response inside the vendor sample, not the request."""
    text = SAMPLE_PATH.read_text(encoding="utf-8")
    start = text.index("<soap-env:Envelope")
    end = text.index("</soap-env:Envelope>", start) + len("</soap-env:Envelope>")
    return text[start:end]


class _PassengerList:
    def __init__(self, flight_number: str, departure_date: str):
        self._flight_number = flight_number
        self._departure_date = departure_date

    def find_one(self, query, sort=None):
        assert query["flightNumber"] == self._flight_number
        assert query["departureDate"] == self._departure_date
        assert query["passengers.pnr"] == "QKZOBC"
        return {
            "flightNumber": self._flight_number,
            "departureDate": self._departure_date,
            "passengers": [
                {
                    "pnr": "QKZOBC",
                    "lastName": "HORN",
                    "firstName": "YVETTE",
                }
            ],
        }


class _DB:
    def __init__(self, flight_number: str, departure_date: str):
        self._lists = _PassengerList(flight_number, departure_date)

    def __getitem__(self, name):
        assert name == "passenger_list"
        return self._lists


def _detail_app():
    from backend.api.routes.passengers import router

    app = FastAPI()
    app.include_router(router)
    return app


def _get_detail(sabre_response_xml: str, *, flight_number: str, origin: str, date: str, airline: str):
    from backend.sabre.client import SabreClient

    captured: dict[str, str] = {}

    def fake_create_session(self):
        self._token = "placeholder-token"
        self._conversation_id = "placeholder-conversation"
        return self._token

    def fake_close_session(self):
        self._token = None

    def fake_post(self, action, body, timeout=30, endpoint=None):
        assert action == "GetPassengerDataRQ"
        captured["request"] = body
        # Same return shape as SabreClient._post: text, status, duration, request.
        return sabre_response_xml, 200, 1, "<GetPassengerDataRQ-placeholder/>"

    env = {**os.environ, **_SABRE_ENV}
    with patch.dict(os.environ, env, clear=False), \
            patch.object(SabreClient, "create_session", fake_create_session), \
            patch.object(SabreClient, "close_session", fake_close_session), \
            patch.object(SabreClient, "_post", fake_post), \
            patch(
                "backend.api.routes.passengers.get_db",
                return_value=_DB(flight_number, date),
            ):
        client = TestClient(_detail_app())
        response = client.get(
            f"/flights/{flight_number}/passengers/QKZOBC/detail",
            params={
                "origin": origin,
                "date": date,
                "airline": airline,
            },
        )
    return response, captured.get("request", "")


def _route_by_segment(payload: dict, segment_id: str) -> dict:
    passengers = payload["passengers"]
    assert len(passengers) == 1
    matches = [
        route for route in passengers[0]["baggageRoutes"]
        if str(route.get("segmentId")) == segment_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_vendor_codeshare_sample_keeps_operating_booking_class_on_live_detail():
    """EY 6418 / VA 880 from the vendor sample: marketing L, operating N."""
    response, request_xml = _get_detail(
        _vendor_response_envelope(),
        flight_number="6418",
        origin="SYD",
        date="2019-05-29",
        airline="EY",
    )
    assert response.status_code == 200, response.text[:400]
    assert "<LastName>HORN</LastName>" in request_xml
    assert "<PNRLocator>QKZOBC</PNRLocator>" in request_xml
    assert "<Airline>EY</Airline>" in request_xml
    assert "<Flight>6418</Flight>" in request_xml

    payload = response.json()
    passenger = payload["passengers"][0]
    # The same response already delivers the operating class on the itinerary.
    # The baggage route has to keep it too.
    itinerary = passenger["itinerary"][0]
    assert itinerary["operatingAirline"] == "VA"
    assert itinerary["operatingFlight"] == "880"
    assert itinerary["bookingClass"] == "L"
    assert itinerary["marketingBookingClass"] == "L"
    assert itinerary["operatingBookingClass"] == "N"

    route = _route_by_segment(payload, "1")
    assert route["airline"] == "EY"
    assert route["flight"] == "6418"
    assert route["operatingAirline"] == "VA"
    assert route["operatingFlight"] == "880"
    assert route["bookingClass"] == "L", route
    assert route.get("marketingBookingClass") == "L", route
    assert route.get("operatingBookingClass") == "N", route


def test_every_baggage_route_keeps_marketing_and_operating_class():
    """Codeshare, own-metal, and same-carrier routes all keep both class fields."""
    response, _request_xml = _get_detail(
        _TWO_CLASS_RESPONSE,
        flight_number="6418",
        origin="SYD",
        date="2019-05-29",
        airline="EY",
    )
    assert response.status_code == 200, response.text[:400]
    payload = response.json()

    codeshare = _route_by_segment(payload, "1")
    assert codeshare["bookingClass"] == "L", codeshare
    assert codeshare.get("marketingBookingClass") == "M", codeshare
    assert codeshare.get("operatingBookingClass") == "N", codeshare
    assert codeshare["operatingAirline"] == "VA"

    own_metal = _route_by_segment(payload, "2")
    assert own_metal["bookingClass"] == "Y", own_metal
    assert own_metal.get("marketingBookingClass") == "", own_metal
    assert own_metal.get("operatingBookingClass") == "", own_metal
    assert own_metal.get("operatingAirline", "") == ""

    codeshare_without_operating_class = _route_by_segment(payload, "3")
    assert codeshare_without_operating_class["bookingClass"] == "J", codeshare_without_operating_class
    assert codeshare_without_operating_class.get("marketingBookingClass") == "", codeshare_without_operating_class
    assert codeshare_without_operating_class.get("operatingBookingClass") == "", codeshare_without_operating_class
    assert codeshare_without_operating_class["operatingAirline"] == "WY"

    same_carrier = _route_by_segment(payload, "4")
    assert same_carrier["airline"] == "gf"
    assert same_carrier["bookingClass"] == "Y", same_carrier
    assert same_carrier.get("marketingBookingClass") == "Y", same_carrier
    assert same_carrier.get("operatingBookingClass") == "C", same_carrier
    assert same_carrier["operatingAirline"] == "GF"


def test_baggage_card_prints_operating_class_for_codeshare_only():
    """Render the passenger detail sheet and read the baggage-card Class cells.

    A codeshare route (operating carrier present and different from the
    marketing carrier) prints operatingBookingClass when that value is
    non-empty. Every other route prints bookingClass. Marketing class is
    never the printed class.
    """
    tsx = FRONTEND_DIR / "node_modules" / ".bin" / "tsx"
    if not tsx.is_file():
        pytest.fail(
            "frontend dependencies are missing. From the repo root run: "
            "cd frontend && npm install && "
            "npm install --no-save --no-package-lock happy-dom tsx"
        )

    link = Path(__file__).resolve().parent / "node_modules"
    target = FRONTEND_DIR / "node_modules"
    if not link.exists():
        link.symlink_to(target, target_is_directory=True)

    completed = subprocess.run(
        [str(tsx), "--tsconfig", str(Path(__file__).with_name("tsconfig.baggage-card.json")), str(UI_RUNNER)],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    assert completed.returncode == 0, completed.stderr or completed.stdout
