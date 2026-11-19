"""ACS Flight Detail aircraft-change responses must stay renderable.

Sabre's ACS_FlightDetailRS schema allows AircraftType to carry a change
attribute. xmltodict then yields an object ({"@change", "#text"}) instead of
the type code. These tests follow that response through the product's SOAP
parser, convert_flight_status, the dashboard and history payloads, and the
flight header, workbench, status history, and compare screen.

Contract once the bug is fixed:
- aircraft["type"] is the element's text, always a str
- aircraft["change"] is the change attribute's string, or "" when it is absent
- those screens render that type code as text, along with the gate and status

They fail while the object is stored or rendered.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.routes.flights import _build_dashboard_payload, _strip_id
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient


FLIGHT = ("GF", "2057", "BOM", "2026-03-19")
TYPE_CODE = "777"
CHANGE = "Y"
PLAIN_TYPE = "321"


def _flight_detail_xml(aircraft_type_xml: str) -> str:
    """SOAP envelope in the shape get_flight_status parses.

    The body matches ACS_FlightDetailRS. No session token is included.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <Origin>BOM</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>05:40PM</ScheduledDepartureTime>
          <EstimatedDepartureDate>2026-03-19</EstimatedDepartureDate>
          <EstimatedDepartureTime>05:40PM</EstimatedDepartureTime>
          <ScheduledArrivalDate>2026-03-19</ScheduledArrivalDate>
          <ScheduledArrivalTime>08:10PM</ScheduledArrivalTime>
          <EstimatedArrivalDate>2026-03-19</EstimatedArrivalDate>
          <EstimatedArrivalTime>09:25PM</EstimatedArrivalTime>
          <DepartureGate>67A</DepartureGate>
          <BoardingTime>05:00PM</BoardingTime>
          {aircraft_type_xml}
          <FlightStatus>FINAL</FlightStatus>
          <AircraftRegistration>A9CXF</AircraftRegistration>
          <SeatConfig>12/180</SeatConfig>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_flight_detail(xml_text: str) -> dict:
    """Same two calls get_flight_status makes after the SOAP response arrives."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def _convert(xml_text: str) -> dict:
    raw = _parse_flight_detail(xml_text)
    airline, flight_number, origin, departure_date = FLIGHT
    return convert_flight_status(
        raw, airline, flight_number, origin, departure_date,
    )


def _assert_aircraft(aircraft: dict, type_code: str, change: str) -> None:
    stored_type = aircraft.get("type")
    assert isinstance(stored_type, str), (
        "aircraft.type must be the AircraftType text, not "
        f"{type(stored_type).__name__}: {stored_type!r}"
    )
    assert stored_type == type_code, stored_type
    assert aircraft.get("change") == change, aircraft.get("change")
    assert aircraft.get("registration") == "A9CXF"


class AircraftChangeIndicatorTests(unittest.TestCase):
    def test_change_attribute_is_parsed_as_text_and_flag(self):
        doc = _convert(
            _flight_detail_xml(f'<AircraftType change="{CHANGE}">{TYPE_CODE}</AircraftType>')
        )
        _assert_aircraft(doc["aircraft"], TYPE_CODE, CHANGE)
        self.assertEqual(doc["status"], "FINAL")
        self.assertEqual(doc["gate"], "67A")

    def test_aircraft_type_without_change_stays_a_string(self):
        doc = _convert(
            _flight_detail_xml(f"<AircraftType>{PLAIN_TYPE}</AircraftType>")
        )
        _assert_aircraft(doc["aircraft"], PLAIN_TYPE, "")

    def test_dashboard_and_history_payloads_keep_the_string_type(self):
        doc = _convert(
            _flight_detail_xml(f'<AircraftType change="{CHANGE}">{TYPE_CODE}</AircraftType>')
        )
        airline, flight_number, origin, departure_date = FLIGHT
        dashboard = _build_dashboard_payload(
            doc, None, origin, departure_date, {},
        )
        _assert_aircraft(dashboard["flightStatus"]["aircraft"], TYPE_CODE, CHANGE)
        self.assertEqual(dashboard["flightStatus"]["gate"], "67A")
        self.assertEqual(dashboard["flightStatus"]["status"], "FINAL")

        history_doc = _strip_id(dict(doc))
        encoded = json.loads(json.dumps(history_doc))
        _assert_aircraft(encoded["aircraft"], TYPE_CODE, CHANGE)

    def test_flight_screens_render_the_type_code(self):
        doc = _convert(
            _flight_detail_xml(f'<AircraftType change="{CHANGE}">{TYPE_CODE}</AircraftType>')
        )
        _, flight_number, origin, departure_date = FLIGHT
        dashboard = _build_dashboard_payload(
            doc, None, origin, departure_date, {},
        )
        history_doc = _strip_id(json.loads(json.dumps(doc)))
        payload = {
            "typeCode": TYPE_CODE,
            "flightNumber": flight_number,
            "origin": origin,
            "date": departure_date,
            "gate": "67A",
            "status": "FINAL",
            "flightStatus": dashboard["flightStatus"],
            "dashboard": dashboard,
            "historyRecord": history_doc,
        }
        frontend = REPO_ROOT / "frontend"
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "payload.json"
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [
                    "npx", "--yes", "tsx",
                    "--tsconfig", "tests/tsconfig.json",
                    "tests/aircraft-change-screens.tsx",
                    str(payload_path),
                ],
                cwd=frontend,
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(
                "flight screens did not render the aircraft type code\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
