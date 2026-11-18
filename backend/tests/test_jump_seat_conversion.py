"""Jump-seat counts and occupancy must follow ACS Flight Detail XML.

Sabre ACS_FlightDetailRS (v3) puts the seat count in the text of Cockpit and
Cabin, and the occupied flag on an inUse attribute of those elements. The
schema allows up to two of either element. These tests parse that XML with
the product's SOAP parser and then run the feeder conversion, the flight-status
diff, and the dashboard payload that dispatchers read.

They fail while conversion stores 0 / false for attributed elements.
"""

from pathlib import Path

import pytest

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_flight_status
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "sample_data" / "sample_xmlfiles"
LHR_SAMPLE = SAMPLE_DIR / "ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml"

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2006",
    "origin": "LHR",
    "departureDate": "2026-03-18",
}


def _flight_detail_body(xml_text):
    """Run the same parse and SOAP-body extract as SabreClient.get_flight_status."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def _envelope(jump_seat_xml):
    """Minimal ACS_FlightDetailRS envelope with the sample's element shape.

    No session token is included. JumpSeat is the only part under test.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2006</Flight>
          <Origin>LHR</Origin>
          <ScheduledDepartureDate>2026-03-18</ScheduledDepartureDate>
          <FlightStatus>PDC</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      {jump_seat_xml}
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _convert(xml_text, airline="GF", flight_number="2006", origin="LHR", departure_date="2026-03-18"):
    body = _flight_detail_body(xml_text)
    return convert_flight_status(body, airline, flight_number, origin, departure_date)


def _jump_seat_changes(before_doc, after_doc):
    before_snap = {"snapshotId": "snap-before", "data": before_doc}
    after_snap = {"snapshotId": "snap-after", "data": after_doc}
    changes = detect_changes(before_snap, after_snap, "flight_status", {
        "flightNumber": FLIGHT["flightNumber"],
        "origin": FLIGHT["origin"],
        "departureDate": FLIGHT["departureDate"],
    })
    return [c for c in changes if c["changeType"] == "JUMPSEAT_CHANGE"]


@pytest.mark.parametrize("sample_name", [
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml",
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml",
    "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml",
    "ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml",
])
def test_committed_flight_detail_samples_store_jump_seat_counts(sample_name):
    """Each committed flight-detail sample reports 2 cockpit and 2 cabin seats."""
    xml_text = (SAMPLE_DIR / sample_name).read_text(encoding="utf-8")
    doc = _convert(xml_text)
    jump = doc["jumpSeat"]
    assert jump["cockpit"] == 2
    assert jump["cabin"] == 2
    # These samples mark both seats not in use. The flags must follow that.
    assert jump["cockpitInUse"] is False
    assert jump["cabinInUse"] is False


def test_occupied_flags_come_from_cockpit_and_cabin_inuse_attributes():
    """inUse lives on Cockpit and Cabin, not on the JumpSeat wrapper."""
    xml_text = _envelope(
        """
      <JumpSeat>
        <Cockpit inUse="true">1</Cockpit>
        <Cabin inUse="true">1</Cabin>
      </JumpSeat>
        """
    )
    jump = _convert(xml_text)["jumpSeat"]
    assert jump["cockpit"] == 1
    assert jump["cabin"] == 1
    assert jump["cockpitInUse"] is True
    assert jump["cabinInUse"] is True


def test_repeated_cockpit_and_cabin_elements_are_summed():
    """JumpSeat.ACS allows two Cockpit and two Cabin elements (maxOccurs=2)."""
    xml_text = _envelope(
        """
      <JumpSeat>
        <Cockpit inUse="false">1</Cockpit>
        <Cockpit inUse="true">1</Cockpit>
        <Cabin inUse="true">1</Cabin>
        <Cabin inUse="false">2</Cabin>
      </JumpSeat>
        """
    )
    jump = _convert(xml_text)["jumpSeat"]
    assert jump["cockpit"] == 2
    assert jump["cabin"] == 3
    assert jump["cockpitInUse"] is True
    assert jump["cabinInUse"] is True


def test_plain_numeric_jump_seat_elements_still_parse():
    """Elements with no attribute stay plain numbers and are not in use."""
    xml_text = _envelope(
        """
      <JumpSeat>
        <Cockpit>3</Cockpit>
        <Cabin>4</Cabin>
      </JumpSeat>
        """
    )
    jump = _convert(xml_text)["jumpSeat"]
    assert jump == {
        "cockpit": 3,
        "cabin": 4,
        "cockpitInUse": False,
        "cabinInUse": False,
    }


def test_jump_seat_occupancy_flip_is_detected():
    """A later snapshot whose inUse flags flip must emit JUMPSEAT_CHANGE."""
    vacant = _convert(_envelope(
        """
      <JumpSeat>
        <Cockpit inUse="false">2</Cockpit>
        <Cabin inUse="false">2</Cabin>
      </JumpSeat>
        """
    ))
    occupied = _convert(_envelope(
        """
      <JumpSeat>
        <Cockpit inUse="true">2</Cockpit>
        <Cabin inUse="true">2</Cabin>
      </JumpSeat>
        """
    ))
    changes = _jump_seat_changes(vacant, occupied)
    by_field = {c["field"]: c for c in changes}
    assert set(by_field) == {"jumpSeat.cockpitInUse", "jumpSeat.cabinInUse"}
    assert by_field["jumpSeat.cockpitInUse"]["oldValue"] == "False"
    assert by_field["jumpSeat.cockpitInUse"]["newValue"] == "True"
    assert by_field["jumpSeat.cabinInUse"]["oldValue"] == "False"
    assert by_field["jumpSeat.cabinInUse"]["newValue"] == "True"


def test_dashboard_presents_parsed_jump_seat_total():
    """stateSummary and the passengers-on-board tree add cockpit and cabin."""
    xml_text = LHR_SAMPLE.read_text(encoding="utf-8")
    flight_status = _convert(xml_text)
    passenger_list = {
        "passengers": [],
        "totalPassengers": 10,
        "adultCount": 10,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": 10,
        "cabinSummary": [{"destination": "DMM"}],
        "destination": "DMM",
    }
    payload = _build_dashboard_payload(
        flight_status,
        passenger_list,
        origin="LHR",
        date="2026-03-18",
        change_summary={},
    )
    assert payload["stateSummary"]["others"]["jumpSeat"] == 4
    root = next(node for node in payload["tree"]["nodes"] if node["id"] == "root")
    assert root["value"] == 14
