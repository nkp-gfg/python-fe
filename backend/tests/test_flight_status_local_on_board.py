"""Local boarded counts from ACS Flight Detail must survive ingestion.

SabreClient.get_flight_status parses the SOAP body with xmltodict and hands
that dict to convert_flight_status. These tests start at that parsed body —
the same two calls get_flight_status makes — then run the converters and the
dashboard/status response builders. They fail while LocalOnBoard is dropped.

The figures below are synthetic. They follow the ACS PassengerCounts shape
(classOfService, Local, Thru, LocalOnBoard, TotalOnBoard) with local boarded
different from total boarded, so the missing element cannot be reconstructed
from the counts the converter already stores.
"""

from backend.api.routes.flights import _build_dashboard_payload, _strip_id
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient


def _flight_detail_soap(passenger_counts_xml: str) -> str:
    """SOAP envelope in the shape ACS_FlightDetailRS uses on the wire."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <Origin>BOM</Origin>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <FlightStatus>FINAL</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      {passenger_counts_xml}
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parsed_flight_detail(xml_text: str) -> dict:
    """Same parse and body extract as SabreClient.get_flight_status."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")


def _status_document(xml_text: str) -> dict:
    """Convert, then strip the way GET /flights/{id}/status returns the doc."""
    raw = _parsed_flight_detail(xml_text)
    doc = convert_flight_status(raw, "GF", "2057", "BOM", "2026-03-19")
    return _strip_id(doc)


def _dashboard(doc: dict) -> dict:
    """Same builder GET /flights/{id}/dashboard uses for the flight-status half."""
    return _build_dashboard_payload(doc, None, "BOM", "2026-03-19", {})


# Connecting bank: local boarded is neither the booked-local figure nor the
# total on board. Thru passengers make up the difference (40 local + 18 thru).
_TWO_CABIN_XML = _flight_detail_soap(
    """
      <PassengerCounts classOfService="Y">
        <Authorized>180</Authorized>
        <Booked>70</Booked>
        <Available>110</Available>
        <Thru>20</Thru>
        <Local>50</Local>
        <NonRevenueLocal>1</NonRevenueLocal>
        <Meals>57</Meals>
        <LocalOnBoard>40</LocalOnBoard>
        <TotalOnBoard>58</TotalOnBoard>
        <TotalBoardingPassIssued>58</TotalBoardingPassIssued>
      </PassengerCounts>
      <PassengerCounts classOfService="J">
        <Authorized>12</Authorized>
        <Booked>6</Booked>
        <Available>6</Available>
        <Thru>6</Thru>
        <Local>4</Local>
        <NonRevenueLocal>0</NonRevenueLocal>
        <LocalOnBoard>1</LocalOnBoard>
        <TotalOnBoard>9</TotalOnBoard>
        <TotalBoardingPassIssued>9</TotalBoardingPassIssued>
      </PassengerCounts>
    """
)

# xmltodict leaves a single repeated element as a dict, not a list.
# Local boarding has not started; thru passengers are already on board.
_ONE_CABIN_XML = _flight_detail_soap(
    """
      <PassengerCounts classOfService="Y">
        <Authorized>180</Authorized>
        <Booked>30</Booked>
        <Available>150</Available>
        <Thru>12</Thru>
        <Local>18</Local>
        <NonRevenueLocal>0</NonRevenueLocal>
        <LocalOnBoard>0</LocalOnBoard>
        <TotalOnBoard>12</TotalOnBoard>
        <TotalBoardingPassIssued>12</TotalBoardingPassIssued>
      </PassengerCounts>
    """
)

# Child elements carry the response namespace prefix. convert_flight_status
# strips prefixes before it reads counts, so LocalOnBoard must still be read.
_PREFIXED_XML = _flight_detail_soap(
    """
      <ns2:PassengerCounts classOfService="Y">
        <ns2:Authorized>180</ns2:Authorized>
        <ns2:Booked>20</ns2:Booked>
        <ns2:Available>160</ns2:Available>
        <ns2:Thru>8</ns2:Thru>
        <ns2:Local>10</ns2:Local>
        <ns2:NonRevenueLocal>0</ns2:NonRevenueLocal>
        <ns2:LocalOnBoard>7</ns2:LocalOnBoard>
        <ns2:TotalOnBoard>15</ns2:TotalOnBoard>
        <ns2:TotalBoardingPassIssued>15</ns2:TotalBoardingPassIssued>
      </ns2:PassengerCounts>
    """
)


def _assert_local_boarded(counts: dict, cabin: str, local_on_board: int, total_on_board: int):
    cabin_counts = counts[cabin]
    assert cabin_counts["onBoard"] == total_on_board
    assert cabin_counts["localOnBoard"] == local_on_board
    assert isinstance(cabin_counts["localOnBoard"], int)
    # Booked-local and total-on-board are different facts. A fix that copies
    # either of those into localOnBoard still hides connecting traffic.
    assert cabin_counts["localOnBoard"] != cabin_counts["onBoard"]
    assert cabin_counts["localOnBoard"] != cabin_counts["local"]


def test_two_cabins_keep_local_on_board_separate_from_total():
    doc = _status_document(_TWO_CABIN_XML)
    counts = doc["passengerCounts"]
    _assert_local_boarded(counts, "Y", 40, 58)
    _assert_local_boarded(counts, "J", 1, 9)
    # The number is not recoverable from any count already stored.
    stored = set(counts["Y"].values())
    assert 40 in stored


def test_single_cabin_element_keeps_zero_local_on_board():
    """One PassengerCounts element arrives as a dict from xmltodict."""
    raw = _parsed_flight_detail(_ONE_CABIN_XML)
    assert isinstance(raw["PassengerCounts"], dict)
    doc = _status_document(_ONE_CABIN_XML)
    cabin = doc["passengerCounts"]["Y"]
    assert cabin["onBoard"] == 12
    assert cabin["localOnBoard"] == 0
    assert cabin["local"] == 18


def test_prefixed_local_on_board_is_read_after_namespace_strip():
    doc = _status_document(_PREFIXED_XML)
    _assert_local_boarded(doc["passengerCounts"], "Y", 7, 15)


def test_dashboard_returns_local_on_board_when_it_differs_from_total():
    doc = _status_document(_TWO_CABIN_XML)
    payload = _dashboard(doc)
    counts = payload["flightStatus"]["passengerCounts"]
    _assert_local_boarded(counts, "Y", 40, 58)
    _assert_local_boarded(counts, "J", 1, 9)
    # _strip_id removes _raw. The split has to be on the cabin count itself.
    assert "_raw" not in payload["flightStatus"]


def test_status_document_exposes_local_on_board_without_raw_xml():
    """GET /flights/{id}/status returns _strip_id(doc), not the SOAP body."""
    doc = _status_document(_TWO_CABIN_XML)
    assert "_raw" not in doc
    _assert_local_boarded(doc["passengerCounts"], "Y", 40, 58)
    _assert_local_boarded(doc["passengerCounts"], "J", 1, 9)
