"""Sabre UpgradeCode must survive ingestion and become an upgrade event.

These tests follow the passenger-list path the feeder runs: SOAP parse
(SabreClient._parse_xml / _extract_body), the multi-call passenger merge in
the feeder runner, convert_passenger_list, and detect_changes. They also
cover GetPassengerData, which walks the same upgrade element on
PassengerItinerary and returns it from the passenger-detail route.

They fail while UpgradeCode is dropped and while change detection ignores a
J-series upgrade code that has no DesiredBookingClass.

Not covered here:
- Trip-report conversion. UpgradeCode lives under PaxEntry /
  OtherSupplementaryInformation / StaffTravelInformation, and
  convert_trip_report never reads that subtree. Change detection does not
  diff trip reports.
- The standby-list route and the flight-insight upgrade counter, which
  select passengers by priorityCode == "UPG" only.
- audit_rules.rule_desired_upgrade_not_processed, which only looks at
  desiredBookingClass.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.feeder.converter import convert_passenger_data, convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.feeder.runner import (
    _extract_passenger_info_list,
    _pax_dedup_key,
    _set_passenger_info_list,
)
from backend.sabre.client import SabreClient


FLIGHT = {
    "airline": "GF",
    "flightNumber": "2006",
    "origin": "LHR",
    "departureDate": "2026-03-18",
}


def _passenger_xml(
    *,
    last,
    first,
    pnr,
    passenger_id,
    line,
    cabin,
    booking_class,
    upgrade=None,
    priority=None,
    desired=None,
    prefixed_upgrade=False,
):
    priority_xml = f"<PriorityCode>{priority}</PriorityCode>" if priority else ""
    desired_xml = (
        f"<DesiredBookingClass>{desired}</DesiredBookingClass>" if desired else ""
    )
    if upgrade and prefixed_upgrade:
        # Prefix only. The list envelope already declares xmlns:ns2, matching
        # the baggage elements in the committed passenger lists.
        upgrade_xml = f"<ns2:UpgradeCode>{upgrade}</ns2:UpgradeCode>"
    elif upgrade:
        upgrade_xml = f"<UpgradeCode>{upgrade}</UpgradeCode>"
    else:
        upgrade_xml = ""
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>{passenger_id}</PassengerID>
          <ThruIndicator>false</ThruIndicator>
          <LineNumber>{line}</LineNumber>
          {priority_xml}
          {upgrade_xml}
          <BookingClass>{booking_class}</BookingClass>
          {desired_xml}
          <Cabin>{cabin}</Cabin>
          <Destination>DMM</Destination>
          <PassengerType>P</PassengerType>
          <BagCount>0</BagCount>
          <Indicators>
            <Indicator>NotBoarded</Indicator>
            <Indicator>Revenue</Indicator>
          </Indicators>
          <CheckIn_Info>
            <CheckInStatus>false</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
        </PassengerInfo>
    """


def _list_envelope(passengers_xml, flight="2006", origin="LHR", departure_date="2026-03-18"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4"
        xmlns:ns2="http://services.sabre.com/STL/v3" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{flight}</Flight>
          <DepartureDate>{departure_date}</DepartureDate>
          <Origin>{origin}</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {passengers_xml}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_passenger_list(xml):
    """Same parse the passenger-list client uses before conversion."""
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _convert_list(xml):
    raw = _parse_passenger_list(xml)
    doc = convert_passenger_list(
        raw,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["departureDate"],
        FLIGHT["origin"],
    )
    return doc, raw


def _by_pnr(doc):
    return {p["pnr"]: p for p in doc["passengers"]}


def _snapshot(doc, snapshot_id):
    return {"snapshotId": snapshot_id, "data": doc}


def _diff(before_doc, after_doc):
    return detect_changes(
        _snapshot(before_doc, "before"),
        _snapshot(after_doc, "after"),
        "passenger_list",
        FLIGHT,
    )


def _for_pnr(changes, pnr):
    return [c for c in changes if (c.get("passenger") or {}).get("pnr") == pnr]


def _records_upgrade_code(change, code):
    meta = change.get("metadata") or {}
    if meta.get("upgradeCode") == code:
        return True
    return change.get("field") == "upgradeCode" and str(change.get("newValue")) == code


def _revenue_j07():
    """Economy revenue passenger with upgrade code J07 and no standby priority.

    Same shape as the committed GF2006 list: UpgradeCode is present,
    PriorityCode and DesiredBookingClass are absent, cabin stays Y.
    """
    return _passenger_xml(
        last="ALANSARI",
        first="MOHAMED MR",
        pnr="OWNPDE",
        passenger_id="D3488AC40001",
        line=5,
        cabin="Y",
        booking_class="Y",
        upgrade="J07",
    )


def _staff_b3_j06():
    """Passenger carrying a standby priority and a separate upgrade code.

    Same shape as the committed GF2056 list.
    """
    return _passenger_xml(
        last="ALBAHRI",
        first="AHMED MR",
        pnr="MYHANC",
        passenger_id="5EA4B3470001",
        line=9,
        cabin="Y",
        booking_class="Y",
        priority="B3",
        upgrade="J06",
    )


def _plain(cabin="Y", booking_class="Y"):
    return _passenger_xml(
        last="SMITH",
        first="JOHN MR",
        pnr="PLAIN1",
        passenger_id="PLAIN0001",
        line=1,
        cabin=cabin,
        booking_class=booking_class,
    )


def test_manifest_row_keeps_upgrade_code_separate_from_standby_priority():
    xml = _list_envelope(_revenue_j07() + _staff_b3_j06() + _plain())
    doc, raw = _convert_list(xml)

    parsed = _extract_passenger_info_list(raw)
    parsed_by_text = {}
    for passenger in parsed:
        locator = passenger.get("PNRLocator")
        pnr = locator.get("#text") if isinstance(locator, dict) else locator
        parsed_by_text[pnr] = passenger
    assert parsed_by_text["OWNPDE"]["UpgradeCode"] == "J07"
    assert "PriorityCode" not in parsed_by_text["OWNPDE"]
    assert "DesiredBookingClass" not in parsed_by_text["OWNPDE"]
    assert parsed_by_text["MYHANC"]["PriorityCode"] == "B3"
    assert parsed_by_text["MYHANC"]["UpgradeCode"] == "J06"

    stored = _by_pnr(doc)
    revenue = stored["OWNPDE"]
    staff = stored["MYHANC"]
    plain = stored["PLAIN1"]

    assert revenue["upgradeCode"] == "J07"
    assert revenue.get("priorityCode", "") == ""
    assert revenue.get("desiredBookingClass", "") == ""
    assert revenue["cabin"] == "Y"
    assert revenue["bookingClass"] == "Y"

    assert staff["upgradeCode"] == "J06"
    assert staff["priorityCode"] == "B3"
    assert staff.get("desiredBookingClass", "") == ""

    assert plain.get("upgradeCode", "") == ""
    assert "J07" not in str(plain.get("priorityCode", ""))


def test_prefixed_upgrade_code_is_stored_after_namespace_strip():
    xml = _list_envelope(
        _passenger_xml(
            last="NOOR",
            first="FAWAZ MR",
            pnr="PREF01",
            passenger_id="PREF0001",
            line=4,
            cabin="Y",
            booking_class="Y",
            upgrade="J05",
            prefixed_upgrade=True,
        )
    )
    doc, _raw = _convert_list(xml)
    assert _by_pnr(doc)["PREF01"]["upgradeCode"] == "J05"


def test_merged_display_code_calls_keep_upgrade_code():
    """The feeder issues four GetPassengerList calls and merges PassengerInfo.

    UpgradeCode arrives on a later call and must still be on the stored row.
    """
    first = _parse_passenger_list(_list_envelope(_plain()))
    second = _parse_passenger_list(_list_envelope(_revenue_j07()))

    merged = first
    base = _extract_passenger_info_list(merged)
    seen = {_pax_dedup_key(p) for p in base}
    for passenger in _extract_passenger_info_list(second):
        key = _pax_dedup_key(passenger)
        if key not in seen:
            base.append(passenger)
            seen.add(key)
    _set_passenger_info_list(merged, base)

    doc = convert_passenger_list(
        merged,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["departureDate"],
        FLIGHT["origin"],
    )
    stored = _by_pnr(doc)
    assert stored["OWNPDE"]["upgradeCode"] == "J07"
    assert stored["PLAIN1"].get("upgradeCode", "") == ""


def test_new_j_series_passenger_is_classified_without_desired_class():
    before, _raw = _convert_list(_list_envelope(_plain()))
    full, _raw = _convert_list(
        _list_envelope(_revenue_j07() + _staff_b3_j06() + _plain())
    )
    changes = _diff(before, full)

    revenue_added = [
        c
        for c in _for_pnr(changes, "OWNPDE")
        if c["changeType"] == "PASSENGER_ADDED"
    ]
    assert len(revenue_added) == 1
    meta = revenue_added[0].get("metadata") or {}
    assert meta.get("upgradeCode") == "J07"
    assert meta.get("upgradeType")
    assert meta.get("desiredCabin", "") in ("", None)

    revenue_requests = [
        c
        for c in _for_pnr(changes, "OWNPDE")
        if c["changeType"] == "UPGRADE_REQUEST" and _records_upgrade_code(c, "J07")
    ]
    assert len(revenue_requests) == 1
    assert revenue_requests[0].get("metadata", {}).get("upgradeType")
    assert "UPGRADE_CONFIRMED" not in {
        c["changeType"] for c in _for_pnr(changes, "OWNPDE")
    }

    staff = [
        c
        for c in _for_pnr(changes, "MYHANC")
        if c["changeType"] == "PASSENGER_ADDED"
    ]
    assert len(staff) == 1
    staff_meta = staff[0].get("metadata") or {}
    assert staff_meta.get("upgradeCode") == "J06"
    assert staff_meta.get("upgradeType")
    assert staff_meta.get("upgradeType") != "B3"
    staff_requests = [
        c
        for c in _for_pnr(changes, "MYHANC")
        if c["changeType"] == "UPGRADE_REQUEST" and _records_upgrade_code(c, "J06")
    ]
    assert len(staff_requests) == 1
    assert staff_requests[0].get("metadata", {}).get("upgradeType") not in ("", None, "B3")

    assert _for_pnr(changes, "PLAIN1") == []


def test_appearing_upgrade_code_is_an_upgrade_request_without_desired_class():
    """A passenger already on the manifest gains J07. No DesiredBookingClass.

    Change detection must record one upgrade request. The passenger is still
    in economy, so this is not UPGRADE_CONFIRMED.
    """
    before, _raw = _convert_list(
        _list_envelope(
            _passenger_xml(
                last="ALANSARI",
                first="MOHAMED MR",
                pnr="OWNPDE",
                passenger_id="D3488AC40001",
                line=5,
                cabin="Y",
                booking_class="Y",
            )
            + _plain()
        )
    )
    after, _raw = _convert_list(_list_envelope(_revenue_j07() + _plain()))
    changes = _diff(before, after)

    requests = [
        c
        for c in _for_pnr(changes, "OWNPDE")
        if c["changeType"] == "UPGRADE_REQUEST" and _records_upgrade_code(c, "J07")
    ]
    assert len(requests) == 1
    assert requests[0].get("metadata", {}).get("upgradeType")
    assert "UPGRADE_CONFIRMED" not in {c["changeType"] for c in changes}
    assert _for_pnr(changes, "PLAIN1") == []


def test_same_upgrade_code_on_both_snapshots_is_not_recorded_again():
    """A later snapshot that still shows the same upgrade code is not a new request."""
    doc, _raw = _convert_list(_list_envelope(_revenue_j07()))
    changes = _diff(doc, doc)
    assert [c for c in changes if c["changeType"] == "UPGRADE_REQUEST"] == []


def test_cabin_upgrade_with_j_series_code_confirms_without_desired_class():
    """J07 is enough to confirm an upgrade when the cabin actually changes.

    A second passenger who changes cabin without an upgrade code must not
    be recorded as UPGRADE_CONFIRMED. DesiredBookingClass is absent on both
    sides.
    """
    before, _raw = _convert_list(
        _list_envelope(_revenue_j07() + _plain(cabin="Y", booking_class="V"))
    )
    after, _raw = _convert_list(
        _list_envelope(
            _passenger_xml(
                last="ALANSARI",
                first="MOHAMED MR",
                pnr="OWNPDE",
                passenger_id="D3488AC40001",
                line=5,
                cabin="J",
                booking_class="J",
                upgrade="J07",
            )
            + _plain(cabin="J", booking_class="D")
        )
    )
    changes = _diff(before, after)
    confirmed = [
        c for c in _for_pnr(changes, "OWNPDE") if c["changeType"] == "UPGRADE_CONFIRMED"
    ]
    assert len(confirmed) == 1
    assert confirmed[0].get("metadata", {}).get("upgradeType")
    assert not any(
        c["changeType"] == "UPGRADE_CONFIRMED" for c in _for_pnr(changes, "PLAIN1")
    )


def test_passenger_data_itinerary_keeps_upgrade_code():
    """GetPassengerDataRS places UpgradeCode on PassengerItinerary."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4"
        xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        <PassengerDataResponse>
          <LineNumber>9</LineNumber>
          <LastName>ALBAHRI</LastName>
          <FirstName>AHMED MR</FirstName>
          <PassengerID>5EA4B3470001</PassengerID>
          <PNRLocator nameAssociationID="1">MYHANC</PNRLocator>
          <PassengerItineraryList>
            <PassengerItinerary>
              <SegmentID>1</SegmentID>
              <Airline>GF</Airline>
              <Flight>2056</Flight>
              <BookingClass>Y</BookingClass>
              <Origin>DMM</Origin>
              <Destination>BOM</Destination>
              <DepartureDate>2026-03-16</DepartureDate>
              <Cabin>Y</Cabin>
              <PassengerType>P</PassengerType>
              <Priority>B3</Priority>
              <UpgradeCode>J06</UpgradeCode>
              <BagCount>1</BagCount>
            </PassengerItinerary>
          </PassengerItineraryList>
        </PassengerDataResponse>
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    parsed = SabreClient._parse_xml(xml)
    raw = SabreClient._extract_body(None, parsed, "GetPassengerDataRS")
    doc = convert_passenger_data(raw, "GF", "2056", "2026-03-16", "DMM")
    segment = doc["passengers"][0]["itinerary"][0]
    assert segment.get("priority") == "B3"
    assert segment.get("upgradeCode") == "J06"
