"""Equipment insight must surface the cabin layout Sabre already returned.

The dashboard route loads the flight-status document and the passenger-list
document, then `_build_dashboard_payload` builds `insights`. ACS Flight Detail
stores SeatConfig on `aircraft.seatConfig`. The passenger-list and published
schedule converters do not write `seatConfig`. These tests drive that path:

  SOAP body -> SabreClient parse/extract -> converter -> dashboard payload

They fail until the equipment insight reads `aircraft.seatConfig` from the
flight-status document, and uses schedule then passenger-list aircraft type
only when flight status has no type.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import (
    convert_flight_status,
    convert_passenger_list,
    convert_schedule,
)
from backend.sabre.client import SabreClient

_SAMPLE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "sample_data", "sample_xmlfiles"
)


def _sabre_body(xml_text, response_key):
    """Run the same parse and body extract `SabreClient.get_*` uses after HTTP."""
    parsed = SabreClient._parse_xml(xml_text)
    # `_extract_body` does not read client credentials. Skip `__init__` so the
    # test does not need Sabre environment variables.
    client = object.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, response_key)


def _read_sample(name):
    with open(os.path.join(_SAMPLE_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def _seat_config_pill(equipment):
    """Label rendered by flight-insights.tsx for the Seat Config pill.

    The card is shown when aircraft type or seat config is non-empty. An empty
    seat config is rendered as an em dash.
    """
    visible = bool(equipment.get("aircraftType") or equipment.get("seatConfig"))
    assert visible, "equipment card is hidden, so Seat Config is not rendered"
    return equipment.get("seatConfig") or "—"


def _schedule_xml(aircraft_type):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <VerifyFlightDetailsRS>
      <ApplicationResults status="Complete"/>
      <OriginDestinationOptions>
        <OriginDestinationOption OriginTimeZone="ASM" DestinationTimeZone="AST">
          <FlightSegment DepartureDateTime="03-19T17:40" ArrivalDateTime="03-19T21:25"
                         AirMilesFlown="1600" ElapsedTime="06.15">
            <OriginLocation LocationCode="BOM" Terminal="2"/>
            <DestinationLocation LocationCode="DMM" Terminal="1"/>
            <Equipment AirEquipType="{aircraft_type}"/>
            <MarketingAirline Code="GF" FlightNumber="2057"/>
            <Meal Code="M"/>
          </FlightSegment>
        </OriginDestinationOption>
      </OriginDestinationOptions>
    </VerifyFlightDetailsRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger_list_xml(aircraft_type, origin="BOM", flight="2057", departure_date="2026-03-19"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{flight}</Flight>
          <DepartureDate>{departure_date}</DepartureDate>
          <Origin>{origin}</Origin>
          <Destination>DMM</Destination>
          <AircraftType>{aircraft_type}</AircraftType>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>SMITH</LastName>
            <FirstName>JANE</FirstName>
          </Name_Details>
          <PNRLocator>ABC123</PNRLocator>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>22A</Seat>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _flight_detail_xml(aircraft_type, seat_config, origin="BOM", flight="2057"):
    seat = f"<SeatConfig>{seat_config}</SeatConfig>" if seat_config is not None else ""
    aircraft = f"<AircraftType>{aircraft_type}</AircraftType>" if aircraft_type is not None else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>{flight}</Flight>
          <Origin>{origin}</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>05:40PM</ScheduledDepartureTime>
          <FlightStatus>FINAL</FlightStatus>
          {aircraft}
          {seat}
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _convert_flight_status(xml_text, airline, flight, origin, departure_date):
    body = _sabre_body(xml_text, "ns3:ACS_FlightDetailRS")
    return convert_flight_status(body, airline, flight, origin, departure_date)


def _convert_passenger_list(xml_text, airline, flight, origin, departure_date):
    body = _sabre_body(xml_text, "GetPassengerListRS")
    return convert_passenger_list(body, airline, flight, departure_date, origin)


def _convert_schedule(xml_text, airline, flight, departure_date):
    body = _sabre_body(xml_text, "VerifyFlightDetailsRS")
    return convert_schedule(body, airline, flight, departure_date)


def _dashboard(fs, pl, schedule, origin, departure_date):
    return _build_dashboard_payload(
        fs,
        pl,
        origin,
        departure_date,
        {},
        reservation_doc=None,
        trip_report_doc=None,
        schedule_doc=schedule,
    )


def test_gf2057_equipment_insight_uses_flight_detail_seat_config():
    """ACS Flight Detail for GF2057 names SeatConfig 12/180. The insight does not."""
    flight_status = _convert_flight_status(
        _read_sample("ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    passenger_list = _convert_passenger_list(
        _read_sample("GetPassengerListRS_GF2057_BOM_2026-03-19.xml"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    schedule = _convert_schedule(_schedule_xml("321"), "GF", "2057", "2026-03-19")

    assert flight_status["aircraft"]["seatConfig"] == "12/180"
    assert flight_status["aircraft"]["type"] == "321"
    assert flight_status["passengerCounts"]["J"]["authorized"] == 12
    assert flight_status["passengerCounts"]["Y"]["authorized"] == 180
    assert passenger_list.get("seatConfig", "") == ""
    assert passenger_list.get("aircraftType") == "321"
    assert passenger_list.get("passengers")
    assert schedule.get("seatConfig", "") == ""
    assert schedule.get("aircraftType") == "321"

    payload = _dashboard(flight_status, passenger_list, schedule, "BOM", "2026-03-19")

    assert payload["flightStatus"]["aircraft"]["seatConfig"] == "12/180"
    assert payload["flightStatus"]["passengerCounts"]["J"]["authorized"] == 12
    assert payload["flightStatus"]["passengerCounts"]["Y"]["authorized"] == 180

    equipment = payload["insights"]["equipment"]
    assert equipment["seatConfig"] == "12/180"
    assert _seat_config_pill(equipment) == "12/180"


def test_gf2006_equipment_insight_uses_flight_detail_seat_config():
    """Same drop for the other committed flight-detail response (SeatConfig 26/256)."""
    flight_status = _convert_flight_status(
        _read_sample("ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml"),
        "GF",
        "2006",
        "LHR",
        "2026-03-18",
    )
    passenger_list = _convert_passenger_list(
        _passenger_list_xml("789", origin="LHR", flight="2006", departure_date="2026-03-18"),
        "GF",
        "2006",
        "LHR",
        "2026-03-18",
    )
    schedule = _convert_schedule(_schedule_xml("789"), "GF", "2006", "2026-03-18")

    assert flight_status["aircraft"]["seatConfig"] == "26/256"
    assert passenger_list.get("seatConfig", "") == ""
    assert schedule.get("seatConfig", "") == ""

    payload = _dashboard(flight_status, passenger_list, schedule, "LHR", "2026-03-18")
    equipment = payload["insights"]["equipment"]
    assert equipment["seatConfig"] == "26/256"
    assert _seat_config_pill(equipment) == "26/256"


def test_equipment_aircraft_type_prefers_flight_status_over_schedule():
    """Flight-status type wins. Schedule type is only a fallback."""
    flight_status = _convert_flight_status(
        _flight_detail_xml("789", "26/256", origin="LHR", flight="2006"),
        "GF",
        "2006",
        "LHR",
        "2026-03-18",
    )
    passenger_list = _convert_passenger_list(
        _passenger_list_xml("320", origin="LHR", flight="2006", departure_date="2026-03-18"),
        "GF",
        "2006",
        "LHR",
        "2026-03-18",
    )
    schedule = _convert_schedule(_schedule_xml("32A"), "GF", "2006", "2026-03-18")

    payload = _dashboard(flight_status, passenger_list, schedule, "LHR", "2026-03-18")
    insights = payload["insights"]
    assert insights["flightInfo"]["aircraftType"] == "32A"
    assert insights["equipment"]["aircraftType"] == "789"
    assert insights["equipment"]["seatConfig"] == "26/256"


def test_equipment_aircraft_type_falls_back_to_schedule_then_passenger_list():
    flight_status = _convert_flight_status(
        _flight_detail_xml(None, None),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    passenger_list = _convert_passenger_list(
        _passenger_list_xml("320"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    schedule = _convert_schedule(_schedule_xml("32A"), "GF", "2057", "2026-03-19")

    assert flight_status["aircraft"]["type"] == ""
    assert flight_status["aircraft"]["seatConfig"] == ""

    payload = _dashboard(flight_status, passenger_list, schedule, "BOM", "2026-03-19")
    assert payload["insights"]["equipment"]["aircraftType"] == "32A"
    assert payload["insights"]["equipment"]["seatConfig"] == ""


def test_equipment_aircraft_type_uses_passenger_list_when_schedule_has_no_type():
    flight_status = _convert_flight_status(
        _flight_detail_xml(None, None),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    passenger_list = _convert_passenger_list(
        _passenger_list_xml("320"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    schedule = _convert_schedule(_schedule_xml(""), "GF", "2057", "2026-03-19")

    payload = _dashboard(flight_status, passenger_list, schedule, "BOM", "2026-03-19")
    assert payload["insights"]["equipment"]["aircraftType"] == "320"


def test_seat_config_on_flight_status_wins_over_passenger_list_and_schedule():
    """A stored passenger-list or schedule seatConfig must not hide the flight-detail value."""
    flight_status = _convert_flight_status(
        _flight_detail_xml("321", "12/180"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    passenger_list = _convert_passenger_list(
        _passenger_list_xml("321"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    schedule = _convert_schedule(_schedule_xml("321"), "GF", "2057", "2026-03-19")
    passenger_list["seatConfig"] = "1/1"
    schedule["seatConfig"] = "9/9"

    payload = _dashboard(flight_status, passenger_list, schedule, "BOM", "2026-03-19")
    assert payload["insights"]["equipment"]["seatConfig"] == "12/180"


def test_seat_config_falls_back_to_passenger_list_then_schedule():
    """When flight status has no layout, keep the previous passenger-list then schedule lookup."""
    flight_status = _convert_flight_status(
        _flight_detail_xml("321", None),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    passenger_list = _convert_passenger_list(
        _passenger_list_xml("321"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    schedule = _convert_schedule(_schedule_xml("321"), "GF", "2057", "2026-03-19")
    assert flight_status["aircraft"]["seatConfig"] == ""
    passenger_list["seatConfig"] = "10/150"
    schedule["seatConfig"] = "99/999"

    payload = _dashboard(flight_status, passenger_list, schedule, "BOM", "2026-03-19")
    assert payload["insights"]["equipment"]["seatConfig"] == "10/150"

    passenger_list.pop("seatConfig", None)
    payload = _dashboard(flight_status, passenger_list, schedule, "BOM", "2026-03-19")
    assert payload["insights"]["equipment"]["seatConfig"] == "99/999"
