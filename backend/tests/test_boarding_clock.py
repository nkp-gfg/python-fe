"""Board date and board time from GetPassengerList must survive ingestion.

GetPassengerList Boarding_Info carries BoardDate, BoardTime, and BoardStatus.
Ingestion parses that SOAP body, merges the display-code responses, and stores
the converted passenger list. These tests fail while conversion keeps only the
boarded flag and while a later passenger-list diff ignores a clock change.

Run:
    python3 -m pytest backend/tests/test_boarding_clock.py -q
"""

from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.feeder.runner import (
    _extract_passenger_info_list,
    _pax_dedup_key,
    _set_passenger_info_list,
)
from backend.sabre.client import SabreClient

FLIGHT = {
    "airline": "GF",
    "flightNumber": "118",
    "origin": "AUH",
    "departureDate": "2019-02-09",
}


def _soap(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{body}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _parse_passenger_list(xml_text: str) -> dict:
    """Same parse and body extract get_passenger_list uses after the SOAP call."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _live_response(passengers_xml: str) -> dict:
    """Unprefixed GetPassengerListRS, the shape captured from the live service."""
    body = (
        '<GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4">'
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline><Flight>118</Flight>"
        "<DepartureDate>2019-02-09</DepartureDate>"
        "<Origin>AUH</Origin><Destination>LHR</Destination>"
        "</Itinerary></ItineraryInfo>"
        f"<PassengerInfoList>{passengers_xml}</PassengerInfoList>"
        "</GetPassengerListRS>"
    )
    return _parse_passenger_list(_soap(body))


def _passenger_xml(last, first, pnr, passenger_id, board_status,
                   board_date=None, board_time=None) -> str:
    clock = ""
    if board_date is not None:
        clock += f"<BoardDate>{board_date}</BoardDate>"
    if board_time is not None:
        clock += f"<BoardTime>{board_time}</BoardTime>"
    return (
        "<PassengerInfo>"
        "<Name_Details>"
        f"<LastName>{last}</LastName><FirstName>{first}</FirstName>"
        "</Name_Details>"
        f'<PNRLocator nameAssociationID="1">{pnr}</PNRLocator>'
        f"<PassengerID>{passenger_id}</PassengerID>"
        "<LineNumber>1</LineNumber>"
        "<BookingClass>Y</BookingClass><Cabin>Y</Cabin><Seat>40A</Seat>"
        "<PassengerType>F</PassengerType><BagCount>1</BagCount>"
        "<Boarding_Info>"
        f"{clock}<BoardStatus>{board_status}</BoardStatus>"
        "</Boarding_Info>"
        "</PassengerInfo>"
    )


def _design_response() -> dict:
    """Vendor design response: prefixed BoardDate, BoardTime, and BoardStatus."""
    body = """
    <v4:GetPassengerListRS xmlns:v4="http://services.sabre.com/checkin/getPassengerList/v4">
      <v4:ItineraryInfo>
        <v4:Itinerary>
          <v4:Airline>K0</v4:Airline>
          <v4:Flight>118</v4:Flight>
          <v4:DepartureDate>2019-02-09</v4:DepartureDate>
          <v4:Origin>AUH</v4:Origin>
          <v4:Destination>LHR</v4:Destination>
        </v4:Itinerary>
      </v4:ItineraryInfo>
      <v4:PassengerInfoList>
        <v4:PassengerInfo>
          <v4:Name_Details>
            <v4:LastName>NIKE</v4:LastName>
            <v4:FirstName>ALEX</v4:FirstName>
          </v4:Name_Details>
          <v4:PNRLocator nameAssociationID="2">SDKBCH</v4:PNRLocator>
          <v4:PassengerID>C4FB6A8B0672</v4:PassengerID>
          <v4:LineNumber>1</v4:LineNumber>
          <v4:BookingClass>K</v4:BookingClass>
          <v4:Cabin>Y</v4:Cabin>
          <v4:Seat>40H</v4:Seat>
          <v4:PassengerType>F</v4:PassengerType>
          <v4:BagCount>1</v4:BagCount>
          <v4:CheckIn_Info>
            <v4:CheckInDate>2019-02-09</v4:CheckInDate>
            <v4:CheckInTime>04:20</v4:CheckInTime>
            <v4:CheckInStatus>true</v4:CheckInStatus>
          </v4:CheckIn_Info>
          <v4:Boarding_Info>
            <v4:BoardDate>2019-02-09</v4:BoardDate>
            <v4:BoardTime>04:20</v4:BoardTime>
            <v4:BoardStatus>true</v4:BoardStatus>
          </v4:Boarding_Info>
        </v4:PassengerInfo>
      </v4:PassengerInfoList>
    </v4:GetPassengerListRS>
    """
    return _parse_passenger_list(_soap(body))


def _convert(raw) -> dict:
    return convert_passenger_list(
        raw,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["departureDate"],
        FLIGHT["origin"],
    )


def _merge_display_code_responses(raws: list[dict]) -> dict:
    """Merge passenger-list bodies the way the feeder merges its four calls."""
    merged_raw = None
    for raw in raws:
        if merged_raw is None:
            merged_raw = raw
            continue
        subsequent_pax = _extract_passenger_info_list(raw)
        if not subsequent_pax:
            continue
        base_pax_list = _extract_passenger_info_list(merged_raw)
        existing_keys = {_pax_dedup_key(p) for p in base_pax_list}
        for passenger in subsequent_pax:
            key = _pax_dedup_key(passenger)
            if key not in existing_keys:
                base_pax_list.append(passenger)
                existing_keys.add(key)
        _set_passenger_info_list(merged_raw, base_pax_list)
    return merged_raw


def _by_pnr(doc, pnr: str) -> dict:
    matches = [p for p in doc["passengers"] if p.get("pnr") == pnr]
    assert len(matches) == 1
    return matches[0]


def _clock_change(changes, field, pnr):
    return [
        change for change in changes
        if change.get("field") == field
        and (change.get("passenger") or {}).get("pnr") == pnr
    ]


def test_design_response_stores_board_date_and_time():
    doc = _convert(_design_response())
    passenger = _by_pnr(doc, "SDKBCH")

    assert passenger["isBoarded"] is True
    assert passenger["boardDate"] == "2019-02-09"
    assert passenger["boardTime"] == "04:20"
    assert passenger["checkInDate"] == "2019-02-09"
    assert passenger["checkInTime"] == "04:20"


def test_live_manifest_keeps_each_passengers_board_clock():
    raw = _live_response(
        _passenger_xml("NIKE", "ALEX", "SDKBCH", "C4FB6A8B0672", "true",
                       "2019-02-09", "04:20")
        + _passenger_xml("SMITH", "JOHN", "ABC123", "P2", "true",
                         "2019-02-09", "04:35")
    )
    doc = _convert(raw)

    first = _by_pnr(doc, "SDKBCH")
    second = _by_pnr(doc, "ABC123")
    assert first["isBoarded"] is True
    assert second["isBoarded"] is True
    assert first["boardDate"] == "2019-02-09"
    assert first["boardTime"] == "04:20"
    assert second["boardDate"] == "2019-02-09"
    assert second["boardTime"] == "04:35"
    assert (first["boardDate"], first["boardTime"]) != (
        second["boardDate"], second["boardTime"]
    )


def test_missing_board_clock_is_stored_blank():
    raw = _live_response(
        "<PassengerInfo>"
        "<Name_Details><LastName>SMITH</LastName><FirstName>JOHN</FirstName></Name_Details>"
        "<PNRLocator>ABC123</PNRLocator>"
        "<PassengerID>P2</PassengerID>"
        "<Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>"
        "</PassengerInfo>"
    )
    passenger = _by_pnr(_convert(raw), "ABC123")

    assert passenger["isBoarded"] is False
    assert passenger["boardDate"] == ""
    assert passenger["boardTime"] == ""


def test_merged_checked_in_response_keeps_board_clock():
    booked = _live_response(
        _passenger_xml("SMITH", "JOHN", "ABC123", "P2", "false")
    )
    checked_in = _live_response(
        _passenger_xml("NIKE", "ALEX", "SDKBCH", "C4FB6A8B0672", "true",
                       "2019-02-09", "04:20")
    )
    merged = _merge_display_code_responses([booked, checked_in])
    doc = _convert(merged)

    assert _by_pnr(doc, "ABC123")["isBoarded"] is False
    boarded = _by_pnr(doc, "SDKBCH")
    assert boarded["isBoarded"] is True
    assert boarded["boardDate"] == "2019-02-09"
    assert boarded["boardTime"] == "04:20"


def test_boarding_transition_records_the_clock():
    before = _convert(_live_response(
        _passenger_xml("NIKE", "ALEX", "SDKBCH", "C4FB6A8B0672", "false")
    ))
    after = _convert(_live_response(
        _passenger_xml("NIKE", "ALEX", "SDKBCH", "C4FB6A8B0672", "true",
                       "2019-02-09", "04:20")
    ))
    changes = detect_changes(
        {"snapshotId": "before", "data": before},
        {"snapshotId": "after", "data": after},
        "passenger_list",
        FLIGHT,
    )

    boarded = [
        change for change in changes
        if change.get("changeType") == "BOARDED"
        and (change.get("passenger") or {}).get("pnr") == "SDKBCH"
    ]
    assert len(boarded) == 1
    date_changes = _clock_change(changes, "boardDate", "SDKBCH")
    time_changes = _clock_change(changes, "boardTime", "SDKBCH")
    assert len(date_changes) == 1
    assert date_changes[0]["oldValue"] == ""
    assert date_changes[0]["newValue"] == "2019-02-09"
    assert len(time_changes) == 1
    assert time_changes[0]["oldValue"] == ""
    assert time_changes[0]["newValue"] == "04:20"


def test_board_clock_change_is_detected_after_boarding():
    before = _convert(_live_response(
        _passenger_xml("NIKE", "ALEX", "SDKBCH", "C4FB6A8B0672", "true",
                       "2019-02-09", "04:20")
    ))
    after = _convert(_live_response(
        _passenger_xml("NIKE", "ALEX", "SDKBCH", "C4FB6A8B0672", "true",
                       "2019-02-09", "04:35")
    ))
    changes = detect_changes(
        {"snapshotId": "before", "data": before},
        {"snapshotId": "after", "data": after},
        "passenger_list",
        FLIGHT,
    )

    time_changes = _clock_change(changes, "boardTime", "SDKBCH")
    assert len(time_changes) == 1
    assert time_changes[0]["oldValue"] == "04:20"
    assert time_changes[0]["newValue"] == "04:35"
    assert _clock_change(changes, "boardDate", "SDKBCH") == []
    assert not any(
        change.get("changeType") == "BOARDED"
        and (change.get("passenger") or {}).get("pnr") == "SDKBCH"
        for change in changes
    )
