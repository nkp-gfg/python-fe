"""Group-booking changes on a matched manifest row must be recorded.

These tests follow the passenger-list path the feeder runs after Sabre
returns GetPassengerListRS:

  SabreClient XML parse -> convert_passenger_list -> snapshot checksum
  -> detect_changes (diff_passenger_list)

They fail until a later manifest that assigns, clears, or replaces
groupCode for the same traveler produces a change record. Check-in,
cabin, seat, and bag count stay the same in every case below.

The recorded change must use:
  changeType GROUP_CODE_CHANGE
  field groupCode
  oldValue / newValue as strings ("" when the traveler is not in a group)

Not covered here: GetPassengerDataRS and reservation ancillary group
codes are not snapshot-diffed, and the four display-code calls inside
one ingest keep the first raw row for a traveler rather than comparing
group codes across those calls.
"""

import copy
from pathlib import Path

from backend.api.routes.changes import _describe_event
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.feeder.runner import _extract_passenger_info_list
from backend.feeder.storage import _compute_checksum
from backend.sabre.client import SabreClient

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}

SAMPLE_XML = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)


def _parse_passenger_list(xml_text):
    """Parse a SOAP body the same way SabreClient.get_passenger_list does."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, "GetPassengerListRS")


def _passenger_xml(last, first, pnr, passenger_id, line, group_code, seat="11F", bags=0):
    group = "" if group_code is None else f"<GroupCode>{group_code}</GroupCode>"
    locator = ""
    if pnr is not None:
        locator = f'<PNRLocator nameAssociationID="2">{pnr}</PNRLocator>'
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          {locator}
          <PassengerID>{passenger_id}</PassengerID>
          <LineNumber>{line}</LineNumber>
          {group}
          <BookingClass>V</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{seat}</Seat>
          <Destination>DMM</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>{bags}</BagCount>
          <CheckIn_Info>
            <CheckInStatus>true</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
        </PassengerInfo>
    """


def _envelope(passengers_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BOM</Origin>
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


def _convert(xml_text):
    raw = _parse_passenger_list(xml_text)
    # The feeder keeps the first successful display-code response as the
    # manifest and only appends passengers that are not already present.
    assert _extract_passenger_info_list(raw), "parsed manifest has no passengers"
    return convert_passenger_list(
        raw,
        FLIGHT["airline"],
        FLIGHT["flightNumber"],
        FLIGHT["departureDate"],
        FLIGHT["origin"],
    )


def _changes(before, after):
    """Run the snapshot gate from the feeder, then the real differ.

    store_snapshot marks a payload duplicate only when its checksum matches
    the previous snapshot. A group-code edit has to reach detect_changes,
    which is what _process_api_call calls in that case.
    """
    before_frozen = copy.deepcopy(before)
    after_frozen = copy.deepcopy(after)
    before_frozen["fetchedAt"] = after_frozen["fetchedAt"] = "2026-03-19T00:00:00Z"
    assert _compute_checksum(before_frozen) != _compute_checksum(after_frozen)
    return detect_changes(
        {"snapshotId": "snap-before", "data": before},
        {"snapshotId": "snap-after", "data": after},
        "passenger_list",
        FLIGHT,
    )


def _group_changes(changes):
    return [c for c in changes if c.get("field") == "groupCode"]


def _assert_group_change(changes, pnr, last, first, old, new):
    matched = [
        c for c in _group_changes(changes)
        if c.get("passenger", {}).get("pnr") == pnr
        and c.get("passenger", {}).get("lastName") == last
        and c.get("passenger", {}).get("firstName") == first
    ]
    assert len(matched) == 1
    change = matched[0]
    assert change["changeType"] == "GROUP_CODE_CHANGE"
    assert change["oldValue"] == old
    assert change["newValue"] == new
    assert change["flightNumber"] == "2057"
    assert change["origin"] == "BOM"
    assert change["departureDate"] == "2026-03-19"
    description = _describe_event(change)
    if old:
        assert old in description
    if new:
        assert new in description
    return change


def test_sample_manifest_keeps_group_code_al5():
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    converted = _convert(xml_text)
    botla = [
        p for p in converted["passengers"]
        if p["pnr"] == "LSGNZL" and p["lastName"] == "BOTLA"
    ]
    assert len(botla) == 1
    assert botla[0]["groupCode"] == "AL5"
    assert botla[0]["firstName"] == "VINAYAK MR"
    assert botla[0]["isCheckedIn"] is True
    assert botla[0]["cabin"] == "Y"
    assert botla[0]["seat"] == "11F"
    assert botla[0]["bagCount"] == 0


def test_sample_traveler_moving_from_al5_to_ab2_is_recorded():
    raw = _parse_passenger_list(SAMPLE_XML.read_text(encoding="utf-8"))
    before = convert_passenger_list(
        raw, FLIGHT["airline"], FLIGHT["flightNumber"],
        FLIGHT["departureDate"], FLIGHT["origin"],
    )
    after_raw = copy.deepcopy(raw)
    moved = False
    for passenger in _extract_passenger_info_list(after_raw):
        locator = passenger.get("PNRLocator")
        pnr = locator.get("#text") if isinstance(locator, dict) else locator
        name = passenger.get("Name_Details") or {}
        if pnr == "LSGNZL" and name.get("LastName") == "BOTLA":
            assert passenger.get("GroupCode") == "AL5"
            passenger["GroupCode"] = "AB2"
            moved = True
    assert moved

    after = convert_passenger_list(
        after_raw, FLIGHT["airline"], FLIGHT["flightNumber"],
        FLIGHT["departureDate"], FLIGHT["origin"],
    )
    botla = next(p for p in after["passengers"] if p["pnr"] == "LSGNZL" and p["lastName"] == "BOTLA")
    assert botla["groupCode"] == "AB2"
    assert botla["isCheckedIn"] is True
    assert botla["cabin"] == "Y"
    assert botla["seat"] == "11F"
    assert botla["bagCount"] == 0

    changes = _changes(before, after)
    _assert_group_change(changes, "LSGNZL", "BOTLA", "VINAYAK MR", "AL5", "AB2")
    other_types = {c["changeType"] for c in changes if c.get("field") != "groupCode"}
    assert other_types == set()


def test_named_traveler_joins_leaves_and_unchanged_group():
    common = dict(
        last="BOTLA", first="VINAYAK MR", pnr="LSGNZL",
        passenger_id="F0B8DBBD0002", line=5,
    )
    companion = _passenger_xml(
        "SMITH", "JOHN", "ABC123", "P1", 6, "AL5", seat="12A", bags=1,
    )

    none = _convert(_envelope(_passenger_xml(**common, group_code=None) + companion))
    al5 = _convert(_envelope(_passenger_xml(**common, group_code="AL5") + companion))
    ab2 = _convert(_envelope(_passenger_xml(**common, group_code="AB2") + companion))

    joined = _changes(none, al5)
    _assert_group_change(joined, "LSGNZL", "BOTLA", "VINAYAK MR", "", "AL5")
    assert _group_changes(joined) == [
        c for c in joined if c["passenger"]["pnr"] == "LSGNZL"
    ]
    assert all(c["passenger"]["pnr"] != "ABC123" or c.get("field") != "groupCode" for c in joined)

    left = _changes(al5, none)
    _assert_group_change(left, "LSGNZL", "BOTLA", "VINAYAK MR", "AL5", "")

    moved = _changes(al5, ab2)
    _assert_group_change(moved, "LSGNZL", "BOTLA", "VINAYAK MR", "AL5", "AB2")

    same = _changes(al5, copy.deepcopy(al5))
    assert _group_changes(same) == []
    assert same == []


def test_group_change_is_recorded_with_a_seat_change():
    before = _convert(_envelope(_passenger_xml(
        "BOTLA", "VINAYAK MR", "LSGNZL", "F0B8DBBD0002", 5, "AL5", seat="11F",
    )))
    after = _convert(_envelope(_passenger_xml(
        "BOTLA", "VINAYAK MR", "LSGNZL", "F0B8DBBD0002", 5, "AB2", seat="14C",
    )))
    changes = _changes(before, after)
    _assert_group_change(changes, "LSGNZL", "BOTLA", "VINAYAK MR", "AL5", "AB2")
    seats = [c for c in changes if c["changeType"] == "SEAT_CHANGE"]
    assert len(seats) == 1
    assert seats[0]["oldValue"] == "11F"
    assert seats[0]["newValue"] == "14C"


def test_traveler_without_locator_group_move_uses_line_identity():
    before = _convert(_envelope(_passenger_xml(
        "CREW", "EXTRA", None, "STAFF1", 9, "AL5",
    )))
    after = _convert(_envelope(_passenger_xml(
        "CREW", "EXTRA", None, "STAFF1", 9, None,
    )))
    assert before["passengers"][0]["pnr"] == ""
    assert before["passengers"][0]["groupCode"] == "AL5"
    assert after["passengers"][0]["groupCode"] == ""
    changes = _changes(before, after)
    _assert_group_change(changes, "", "CREW", "EXTRA", "AL5", "")


def test_empty_group_element_matches_omitted_group_code():
    omitted = _convert(_envelope(_passenger_xml(
        "BOTLA", "VINAYAK MR", "LSGNZL", "F0B8DBBD0002", 5, None,
    )))
    empty = _convert(_envelope(_passenger_xml(
        "BOTLA", "VINAYAK MR", "LSGNZL", "F0B8DBBD0002", 5, "",
    )))
    assert omitted["passengers"][0]["groupCode"] == ""
    assert empty["passengers"][0]["groupCode"] in ("", None)
    changes = _changes(omitted, empty)
    assert _group_changes(changes) == []
