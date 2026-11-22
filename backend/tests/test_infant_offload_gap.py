"""Infant offload and no-show totals must include infants the trip reports omit.

Sabre Trip Reports MLX and MLC do not return passenger type Infant
(Trip_Report_User_Guide.txt, MLC characteristic 2 and MLX characteristic 3).
The feeder still stores those reports, and the flight dashboard uses the
stored rows as the Offloaded count and, on FINAL or PDC, the No-Show count.

These tests drive the same transforms the feeder and dashboard run:

1. xmltodict via SabreClient._parse_xml (process_namespaces=False)
2. SabreClient._extract_body
3. convert_trip_report + merge_trip_reports
4. convert_reservations and convert_passenger_list
5. convert_flight_status
6. _build_dashboard_payload, which get_flight_dashboard and get_flight_tree both use

store_trip_reports inserts that merged document unchanged. The route passes
the stored documents into _build_dashboard_payload without a second formula.
The React board renders stateSummary.others and the tree status cards; it
does not recompute the counts, so it is not exercised here.

Expected totals for the fixture below, on FINAL and PDC:

- Offloaded = 3. One cancelled adult from the MLX GroupEntry, plus two
  cancelled infants who exist only on the reservation snapshot (segment
  status HX and XX, the cancellation codes documented for MLX).
- No-Show = 3. One ever-booked adult who is absent from the manifest, plus
  two booked infants who are absent from the manifest. One of those infants
  shares the parent's record locator and last name, and the parent is on
  the manifest without an INF edit code. Matching only (pnr, lastName)
  treats that infant as present.

A lap infant whose parent is on the manifest with edit code INF is on the
flight and must not be counted. A cancelled passenger must not also be
counted as a no-show. The status-card sublabel must say the Sabre trip
report excludes infants.

The tests fail while the dashboard treats the trip-report row counts as
complete.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import (
    convert_flight_status,
    convert_passenger_list,
    convert_reservations,
    convert_trip_report,
    merge_trip_reports,
)
from backend.sabre.client import SabreClient

AIRLINE = "GF"
FLIGHT = "1001"
ORIGIN = "BAH"
DATE = "2026-04-02"

# MLX user-guide cancellation statuses. An infant segment in one of these
# is an offload, not a no-show.
CANCEL_STATUSES = ("HX", "XX", "XK", "XL", "IX", "RM", "WK", "UN", "CX", "ZX")


def _extract(xml, response_key):
    """Parse a SOAP body the way SabreClient.get_* does, without a session."""
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(None, parsed, response_key)


def _envelope(body_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{body_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _group_entry(sequence_id, last_name, first_name, locator, status):
    """One Trip_ReportsRS GroupEntry, shaped like the 1.3.0 response XSD."""
    return f"""
    <GroupEntry sequenceId="{sequence_id}">
      <PnrIndicatorType></PnrIndicatorType>
      <NumberInParty>1</NumberInParty>
      <Name>{last_name}/{first_name}</Name>
      <Locator>{locator}</Locator>
      <ClassOfService>Y</ClassOfService>
      <StatusCode>{status}</StatusCode>
      <NumberOfPassengers>1</NumberOfPassengers>
      <AgentId></AgentId>
      <UpdateDateTime>2026-04-01T12:00:00</UpdateDateTime>
    </GroupEntry>"""


def _trip_report_xml(report_name, report_format, entries_xml):
    """Trip_ReportsRS 1.3.0. Infants are intentionally absent, as Sabre documents."""
    return _envelope(f"""
    <Trip_ReportsRS xmlns="http://webservices.sabre.com/triprecord">
      <Success>Success</Success>
      <Report>
        <ReportName>{report_name}</ReportName>
        <ReportFormat>{report_format}</ReportFormat>
        <ReportResult>
          <Header>
            <AirlineCode>{AIRLINE}</AirlineCode>
            <FlightNumber>{FLIGHT}</FlightNumber>
            <DepartureDateTime>{DATE}T10:00:00</DepartureDateTime>
            <DepartureAirport>{ORIGIN}</DepartureAirport>
            <ArrivalAirport>DXB</ArrivalAirport>
            <DisplayType>{report_format}</DisplayType>
          </Header>
          <Details>
            <Entry>
              <EntryHeader>
                <DepartureAirport>{ORIGIN}</DepartureAirport>
                <ArrivalAirport>DXB</ArrivalAirport>
                <EntryType>SEGMENT</EntryType>
              </EntryHeader>
              {entries_xml}
            </Entry>
          </Details>
        </ReportResult>
      </Report>
    </Trip_ReportsRS>""")


def _passenger_xml(last_name, first_name, pnr, *, infant=False):
    edit_codes = "<ns2:EditCode>INF</ns2:EditCode>" if infant else ""
    edit_codes = "<ns2:EditCode>ET</ns2:EditCode>" + edit_codes
    return f"""
    <PassengerInfo>
      <Name_Details>
        <LastName>{last_name}</LastName>
        <FirstName>{first_name}</FirstName>
      </Name_Details>
      <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
      <PassengerID>PAX{pnr}</PassengerID>
      <LineNumber>1</LineNumber>
      <BookingClass>Y</BookingClass>
      <Cabin>Y</Cabin>
      <Seat>12A</Seat>
      <Destination>DXB</Destination>
      <PassengerType>F</PassengerType>
      <Indicators>
        <Indicator>CheckedIn</Indicator>
        <Indicator>Boarded</Indicator>
        <Indicator>Revenue</Indicator>
      </Indicators>
      <CheckIn_Info>
        <CheckInStatus>true</CheckInStatus>
      </CheckIn_Info>
      <Boarding_Info>
        <BoardStatus>true</BoardStatus>
      </Boarding_Info>
      <EditCodeList>
        {edit_codes}
      </EditCodeList>
    </PassengerInfo>"""


def _reservation_xml(locator, number_of_infants, passengers, action_code):
    """One Trip_Search reservation. passengers are (name_type, last, first)."""
    pax_xml = []
    for index, (name_type, last_name, first_name) in enumerate(passengers, start=1):
        pax_xml.append(f"""
        <stl19:Passenger nameType="{name_type}" nameId="0{index}.01">
          <stl19:LastName>{last_name}</stl19:LastName>
          <stl19:FirstName>{first_name}</stl19:FirstName>
        </stl19:Passenger>""")
    party = len(passengers)
    return f"""
    <Reservation Locator="{locator}">
      <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
        <stl19:Reservation numberInParty="{party}" numberOfInfants="{number_of_infants}">
          <stl19:BookingDetails>
            <stl19:RecordLocator>{locator}</stl19:RecordLocator>
          </stl19:BookingDetails>
          <stl19:PassengerReservation>
            <stl19:Passengers>
              {''.join(pax_xml)}
            </stl19:Passengers>
            <stl19:Segments>
              <stl19:Segment>
                <stl19:Air>
                  <stl19:DepartureAirport>{ORIGIN}</stl19:DepartureAirport>
                  <stl19:ArrivalAirport>DXB</stl19:ArrivalAirport>
                  <stl19:MarketingAirlineCode>{AIRLINE}</stl19:MarketingAirlineCode>
                  <stl19:FlightNumber>{FLIGHT}</stl19:FlightNumber>
                  <stl19:DepartureDateTime>{DATE}T10:00:00</stl19:DepartureDateTime>
                  <stl19:ActionCode>{action_code}</stl19:ActionCode>
                </stl19:Air>
              </stl19:Segment>
            </stl19:Segments>
          </stl19:PassengerReservation>
        </stl19:Reservation>
      </stl19:GetReservationRS>
    </Reservation>"""


def _flight_status_xml(status):
    return _envelope(f"""
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <FlightStatus>{status}</FlightStatus>
          <Origin>{ORIGIN}</Origin>
          <Destination>DXB</Destination>
          <ScheduledDepartureDate>{DATE}</ScheduledDepartureDate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>""")


def _build_documents(flight_status):
    """Run captured-response XML through the feeder converters.

    MLX contains only the cancelled adult. MLC contains the non-infant
    ever-booked names. Neither report contains an infant, which is the
    Sabre behavior these counts currently trust.
    """
    mlx_xml = _trip_report_xml(
        "MLX",
        "X",
        _group_entry(1, "CANCEL", "CARL", "PNRCAN", "HX"),
    )
    mlc_entries = "\n".join([
        _group_entry(1, "ADULTSTAY", "ANN", "PNRST1", "HK"),
        _group_entry(2, "PARENT", "PAT", "PNRINF", "HK"),
        _group_entry(3, "NIA", "PARENT", "PNRNSI", "HK"),
        _group_entry(4, "NOSHOW", "NORA", "PNRNSH", "HK"),
        _group_entry(5, "CANCEL", "CARL", "PNRCAN", "HX"),
    ])
    mlc_xml = _trip_report_xml("MLC", "C", mlc_entries)

    mlx_raw = _extract(mlx_xml, "Trip_ReportsRS")
    mlc_raw = _extract(mlc_xml, "Trip_ReportsRS")
    mlx_doc = convert_trip_report(mlx_raw, AIRLINE, FLIGHT, DATE, ORIGIN, "MLX")
    mlc_doc = convert_trip_report(mlc_raw, AIRLINE, FLIGHT, DATE, ORIGIN, "MLC")
    trip_report = merge_trip_reports(mlx_doc, mlc_doc, AIRLINE, FLIGHT, DATE, ORIGIN)

    passenger_xml = _envelope(f"""
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4">
      <ItineraryInfo>
        <Itinerary>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {_passenger_xml("ADULTSTAY", "ANN", "PNRST1")}
        {_passenger_xml("PARENT", "PAT", "PNRINF", infant=True)}
        {_passenger_xml("NIA", "PARENT", "PNRNSI")}
      </PassengerInfoList>
    </GetPassengerListRS>""")
    passenger_raw = _extract(passenger_xml, "GetPassengerListRS")
    passenger_list = convert_passenger_list(
        passenger_raw, AIRLINE, FLIGHT, DATE, ORIGIN)

    reservations = [
        # Adult who travels.
        _reservation_xml("PNRST1", 0, [("S", "ADULTSTAY", "ANN")], "HK"),
        # Lap infant who is on the manifest via the parent's INF edit code.
        _reservation_xml(
            "PNRINF", 1,
            [("S", "PARENT", "PAT"), ("I", "PARENT", "PIP")],
            "HK",
        ),
        # Infant shares the parent's locator and last name, but the parent
        # has no INF edit code, so the infant is not on the flight.
        _reservation_xml(
            "PNRNSI", 1,
            [("S", "NIA", "PARENT"), ("I", "NIA", "IVO")],
            "HK",
        ),
        # Booked infant with no manifest row and no MLC row.
        _reservation_xml("PNRANI", 1, [("I", "ALONE", "IVA")], "HK"),
        # Adult no-show.
        _reservation_xml("PNRNSH", 0, [("S", "NOSHOW", "NORA")], "HK"),
        # Adult cancellation, also present as the sole MLX GroupEntry.
        _reservation_xml("PNRCAN", 0, [("S", "CANCEL", "CARL")], "HX"),
        # Cancelled infants. Sabre does not put them on MLX.
        _reservation_xml("PNRCX1", 1, [("I", "IDA", "CANCELINF")], "HX"),
        _reservation_xml("PNRCX2", 1, [("I", "IDB", "CANCELINF")], "XX"),
    ]
    reservation_xml = _envelope(f"""
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord">
      <Success>Success</Success>
      <ReservationsList NumberResults="{len(reservations)}">
        <Reservations>
          {''.join(reservations)}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>""")
    reservation_raw = _extract(reservation_xml, "Trip_SearchRS")
    reservation_doc = convert_reservations(
        reservation_raw, AIRLINE, FLIGHT, ORIGIN, DATE)

    status_raw = _extract(_flight_status_xml(flight_status), "ns3:ACS_FlightDetailRS")
    status_doc = convert_flight_status(status_raw, AIRLINE, FLIGHT, ORIGIN, DATE)
    return status_doc, passenger_list, reservation_doc, trip_report


def _dashboard(flight_status):
    status_doc, passenger_list, reservation_doc, trip_report = _build_documents(
        flight_status)
    # The lap infant must survive conversion, or a later count of "every
    # reservation infant" cannot be distinguished from the manifest marker.
    attached = [
        pax for pax in passenger_list["passengers"]
        if pax["pnr"] == "PNRINF" and pax["hasInfant"]
    ]
    assert attached, "fixture failed to preserve the manifest INF edit code"
    infant_rows = [
        pax for res in reservation_doc["reservations"] for pax in res["passengers"]
        if pax.get("nameType") == "I"
    ]
    assert len(infant_rows) == 5, infant_rows
    cancelled = {
        res["pnr"]: res["segments"][0]["status"]
        for res in reservation_doc["reservations"]
        if res["segments"] and res["segments"][0]["status"] in CANCEL_STATUSES
    }
    assert cancelled["PNRCX1"] == "HX"
    assert cancelled["PNRCX2"] == "XX"

    payload = _build_dashboard_payload(
        status_doc, passenger_list, ORIGIN, DATE, {},
        reservation_doc, trip_report, None,
    )
    return payload


def _card(payload, card_id):
    cards = payload["tree"]["statusCards"]
    return next(card for card in cards if card["id"] == card_id)


def _assert_report_label(sub_label, report_name):
    text = (sub_label or "").lower()
    assert "infant" in text and "exclud" in text, (
        f"{report_name} sublabel {sub_label!r} must say the Sabre trip "
        "report excludes infants"
    )


def test_final_flight_includes_infants_omitted_by_trip_reports():
    payload = _dashboard("FINAL")
    others = payload["stateSummary"]["others"]
    offloaded = _card(payload, "offloaded")
    no_show = _card(payload, "noShow")

    assert others["offloaded"] == 3, (
        f"offloaded={others['offloaded']!r} "
        f"available={others['offloadedAvailable']!r}; "
        "expected 3 (MLX adult CANCEL/CARL plus cancelled infants "
        "IDA and IDB, whose HX/XX reservations are not MLX rows)"
    )
    assert others["offloadedAvailable"] is True
    assert offloaded["value"] == others["offloaded"]
    _assert_report_label(offloaded["subLabel"], "MLX")

    assert others["noShow"] == 3, (
        f"noShow={others['noShow']!r} "
        f"available={others['noShowAvailable']!r}; "
        "expected 3 (MLC adult NOSHOW/NORA, booked infant ALONE/IVA, "
        "and booked infant NIA/IVO who shares PNRNSI and last name NIA "
        "with a manifest parent that has no INF edit code)"
    )
    assert others["noShowAvailable"] is True
    assert others["flightClosed"] is True
    assert no_show["value"] == others["noShow"]
    _assert_report_label(no_show["subLabel"], "MLC")
    print("  PASS: test_final_flight_includes_infants_omitted_by_trip_reports")


def test_pdc_flight_uses_the_same_infant_totals():
    payload = _dashboard("PDC")
    others = payload["stateSummary"]["others"]
    assert others["flightClosed"] is True
    assert others["offloaded"] == 3, others["offloaded"]
    assert others["noShow"] == 3, others["noShow"]
    assert _card(payload, "offloaded")["value"] == 3
    assert _card(payload, "noShow")["value"] == 3
    _assert_report_label(_card(payload, "offloaded")["subLabel"], "MLX")
    _assert_report_label(_card(payload, "noShow")["subLabel"], "MLC")
    print("  PASS: test_pdc_flight_uses_the_same_infant_totals")


def test_open_flight_counts_cancelled_infants_and_does_not_invent_no_shows():
    payload = _dashboard("OPEN")
    others = payload["stateSummary"]["others"]
    assert others["flightClosed"] is False
    assert others["offloaded"] == 3, others["offloaded"]
    assert others["offloadedAvailable"] is True
    assert others["noShow"] is None, others["noShow"]
    assert others["noShowAvailable"] is False
    assert _card(payload, "offloaded")["value"] == 3
    _assert_report_label(_card(payload, "offloaded")["subLabel"], "MLX")
    print("  PASS: test_open_flight_counts_cancelled_infants_and_does_not_invent_no_shows")


if __name__ == "__main__":
    print("Running infant offload gap tests...")
    test_final_flight_includes_infants_omitted_by_trip_reports()
    test_pdc_flight_uses_the_same_infant_totals()
    test_open_flight_counts_cancelled_infants_and_does_not_invent_no_shows()
    print("ALL TESTS PASS")
