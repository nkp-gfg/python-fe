"""Named passenger events for lap-infant and child flag changes.

A second passenger-list ingest must record a passenger change when a
traveler's hasInfant or isChild flag differs, and must still record the
anonymous count summary. These tests follow the ingest path: SOAP XML is
parsed the same way SabreClient.get_passenger_list parses a response,
convert_passenger_list sets the flags from CHD and INF edit codes, and
detect_changes is called with the snapshot shape runner._process_api_call
stores.

They fail while differ.passenger_list ignores those flags.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient

FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BAH",
    "departureDate": "2026-03-19",
}

# Change types already emitted for other fields. A flag transition must not
# be folded into one of these; the timeline query keys off change documents
# that carry passenger.pnr, and COUNT_CHANGE is the anonymous summary.
EXISTING_CHANGE_TYPES = {
    "PASSENGER_ADDED",
    "PASSENGER_REMOVED",
    "CHECKED_IN",
    "BOARDED",
    "CABIN_CHANGE",
    "CLASS_CHANGE",
    "SEAT_CHANGE",
    "BAG_COUNT_CHANGE",
    "PAX_TYPE_CHANGE",
    "PRIORITY_CHANGE",
    "UPGRADE_CONFIRMED",
    "LOYALTY_STATUS_ADDED",
    "DOCUMENT_ADDED",
    "COUNT_CHANGE",
    "STATUS_CHANGE",
    "GATE_CHANGE",
    "TERMINAL_CHANGE",
    "BOARDING_TIME_CHANGE",
    "JUMPSEAT_CHANGE",
    "RESERVATION_ADDED",
    "RESERVATION_REMOVED",
    "RESERVATION_PARTY_CHANGE",
}

COUNT_FIELDS = ("adultCount", "childCount", "infantCount", "totalSouls")


def _passenger_xml(last, first, pnr, name_assoc, passenger_id, line, edit_codes, seat="12A"):
    codes = "\n".join(f"            <ns2:EditCode>{code}</ns2:EditCode>" for code in edit_codes)
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="{name_assoc}">{pnr}</PNRLocator>
          <PassengerID>{passenger_id}</PassengerID>
          <ThruIndicator>false</ThruIndicator>
          <LineNumber>{line}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{seat}</Seat>
          <Destination>DXB</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>1</BagCount>
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
          <EditCodeList>
{codes}
          </EditCodeList>
        </PassengerInfo>
    """


def _parse_manifest(passengers_xml):
    """Parse a GetPassengerListRS body the way get_passenger_list does."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BAH</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {passengers_xml}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    parsed = SabreClient._parse_xml(xml)
    parser = object.__new__(SabreClient)
    return SabreClient._extract_body(parser, parsed, "GetPassengerListRS")


def _convert(raw):
    return convert_passenger_list(
        raw, FLIGHT["airline"], FLIGHT["flightNumber"], FLIGHT["departureDate"], FLIGHT["origin"]
    )


def _ingest_pair(before_xml, after_xml):
    """Convert two manifests and diff them the way a second ingest does."""
    before = _convert(_parse_manifest(before_xml))
    after = _convert(_parse_manifest(after_xml))
    before_snap = {"snapshotId": "snap-before", "data": before}
    after_snap = {"snapshotId": "snap-after", "data": after}
    changes = detect_changes(before_snap, after_snap, "passenger_list", FLIGHT)
    return before, after, changes


def _passenger(doc, pnr):
    matches = [pax for pax in doc["passengers"] if pax.get("pnr") == pnr]
    assert len(matches) == 1, f"expected one passenger {pnr}, got {matches}"
    return matches[0]


def _timeline_for(changes, pnr):
    """Same match as GET /{flight}/passengers/{pnr}/timeline: passenger.pnr."""
    return [change for change in changes if (change.get("passenger") or {}).get("pnr") == pnr]


def _assert_named_flag(changes, pnr, last, first, field, old, new):
    hits = [
        change for change in _timeline_for(changes, pnr)
        if change.get("field") == field and change.get("changeType") not in EXISTING_CHANGE_TYPES
    ]
    assert len(hits) == 1, (
        f"expected one passenger change field={field} for {pnr} "
        f"({last}/{first}) {old}->{new}, got {changes}"
    )
    change = hits[0]
    assert change["passenger"]["pnr"] == pnr
    assert change["passenger"]["lastName"] == last
    assert change["passenger"]["firstName"] == first
    assert change["oldValue"] == str(old)
    assert change["newValue"] == str(new)
    assert change["flightNumber"] == FLIGHT["flightNumber"]
    assert change["origin"] == FLIGHT["origin"]
    assert change["departureDate"] == FLIGHT["departureDate"]
    assert "passenger" in change
    return change


def _assert_no_flag_event(changes, pnr, field):
    hits = [
        change for change in _timeline_for(changes, pnr)
        if change.get("field") == field
    ]
    assert hits == [], f"unexpected {field} event for {pnr}: {hits}"


def _assert_count_summaries(changes, before, after):
    for field in COUNT_FIELDS:
        old = before.get(field, 0)
        new = after.get(field, 0)
        hits = [
            change for change in changes
            if change.get("changeType") == "COUNT_CHANGE" and change.get("field") == field
        ]
        if old == new:
            assert hits == [], f"unexpected COUNT_CHANGE for unchanged {field}: {hits}"
            continue
        assert len(hits) == 1, f"expected one anonymous {field} summary, got {hits}"
        assert "passenger" not in hits[0], f"count summary must stay anonymous: {hits[0]}"
        assert hits[0]["oldValue"] == str(old)
        assert hits[0]["newValue"] == str(new)


def test_added_lap_infant_names_the_traveler_and_keeps_count_summary():
    before_xml = _passenger_xml(
        "HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS", "ET"]
    ) + _passenger_xml(
        "KHAN", "OMAR MR", "ZZ99YY", "2", "PAX0002", 2, ["DOCS"], seat="14C"
    )
    after_xml = _passenger_xml(
        "HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS", "ET", "INF"]
    ) + _passenger_xml(
        "KHAN", "OMAR MR", "ZZ99YY", "2", "PAX0002", 2, ["DOCS"], seat="14C"
    )
    before, after, changes = _ingest_pair(before_xml, after_xml)

    assert _passenger(before, "AB12CD")["hasInfant"] is False
    assert _passenger(after, "AB12CD")["hasInfant"] is True
    assert _passenger(after, "AB12CD")["isChild"] is False
    assert "INF" in _passenger(after, "AB12CD")["editCodes"]

    _assert_named_flag(changes, "AB12CD", "HASSAN", "LAYLA MRS", "hasInfant", False, True)
    _assert_no_flag_event(changes, "AB12CD", "isChild")
    _assert_no_flag_event(changes, "ZZ99YY", "hasInfant")
    _assert_no_flag_event(changes, "ZZ99YY", "isChild")
    _assert_count_summaries(changes, before, after)
    assert before["infantCount"] == 0 and after["infantCount"] == 1
    assert after["totalSouls"] == before["totalSouls"] + 1


def test_removed_lap_infant_names_the_traveler():
    before_xml = _passenger_xml(
        "HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS", "INF"]
    )
    after_xml = _passenger_xml(
        "HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS"]
    )
    before, after, changes = _ingest_pair(before_xml, after_xml)

    assert _passenger(before, "AB12CD")["hasInfant"] is True
    assert _passenger(after, "AB12CD")["hasInfant"] is False
    _assert_named_flag(changes, "AB12CD", "HASSAN", "LAYLA MRS", "hasInfant", True, False)
    _assert_count_summaries(changes, before, after)
    assert before["infantCount"] == 1 and after["infantCount"] == 0
    assert after["totalSouls"] == before["totalSouls"] - 1


def test_single_edit_code_element_sets_and_clears_infant():
    """One EditCode is a string after XML parsing, not a list."""
    before_xml = _passenger_xml(
        "ALI", "NOOR MISS", "CD34EF", "3", "PAX0003", 3, ["ET"]
    )
    after_xml = _passenger_xml(
        "ALI", "NOOR MISS", "CD34EF", "3", "PAX0003", 3, ["INF"]
    )
    before, after, changes = _ingest_pair(before_xml, after_xml)
    assert _passenger(after, "CD34EF")["editCodes"] == ["INF"]
    assert _passenger(before, "CD34EF")["hasInfant"] is False
    assert _passenger(after, "CD34EF")["hasInfant"] is True
    _assert_named_flag(changes, "CD34EF", "ALI", "NOOR MISS", "hasInfant", False, True)
    _assert_count_summaries(changes, before, after)


def test_child_flag_added_and_removed_names_the_traveler():
    adult = _passenger_xml("SALEH", "HANA MRS", "GH56IJ", "4", "PAX0004", 4, ["DOCS"])
    child = _passenger_xml("SALEH", "HANA MRS", "GH56IJ", "4", "PAX0004", 4, ["DOCS", "CHD"])
    companion = _passenger_xml(
        "NAIR", "DEV MR", "KL78MN", "5", "PAX0005", 5, ["DOCS"], seat="16A"
    )

    before, after, changes = _ingest_pair(adult + companion, child + companion)
    assert _passenger(before, "GH56IJ")["isChild"] is False
    assert _passenger(after, "GH56IJ")["isChild"] is True
    assert _passenger(after, "GH56IJ")["hasInfant"] is False
    _assert_named_flag(changes, "GH56IJ", "SALEH", "HANA MRS", "isChild", False, True)
    _assert_no_flag_event(changes, "GH56IJ", "hasInfant")
    _assert_no_flag_event(changes, "KL78MN", "isChild")
    _assert_count_summaries(changes, before, after)
    assert before["childCount"] == 0 and after["childCount"] == 1
    assert after["adultCount"] == before["adultCount"] - 1
    assert before["infantCount"] == after["infantCount"]
    assert before["totalSouls"] == after["totalSouls"]

    before_rm, after_rm, removed = _ingest_pair(child + companion, adult + companion)
    _assert_named_flag(removed, "GH56IJ", "SALEH", "HANA MRS", "isChild", True, False)
    _assert_count_summaries(removed, before_rm, after_rm)


def test_child_who_gains_a_lap_infant_records_both_flags():
    before_xml = _passenger_xml(
        "FERNANDES", "RITA MISS", "OP90QR", "6", "PAX0006", 6, ["DOCS"]
    )
    after_xml = _passenger_xml(
        "FERNANDES", "RITA MISS", "OP90QR", "6", "PAX0006", 6, ["DOCS", "CHD", "INF"]
    )
    before, after, changes = _ingest_pair(before_xml, after_xml)
    assert _passenger(after, "OP90QR")["isChild"] is True
    assert _passenger(after, "OP90QR")["hasInfant"] is True
    _assert_named_flag(changes, "OP90QR", "FERNANDES", "RITA MISS", "isChild", False, True)
    _assert_named_flag(changes, "OP90QR", "FERNANDES", "RITA MISS", "hasInfant", False, True)
    _assert_count_summaries(changes, before, after)


def test_swapped_infants_still_name_both_travelers_when_totals_match():
    """Net infant and soul counts stay the same, so the summary is not enough."""
    first = _passenger_xml("HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS", "INF"])
    second = _passenger_xml("KHAN", "OMAR MR", "ZZ99YY", "2", "PAX0002", 2, ["DOCS"], seat="14C")
    before, after, changes = _ingest_pair(
        first + second,
        _passenger_xml("HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS"])
        + _passenger_xml("KHAN", "OMAR MR", "ZZ99YY", "2", "PAX0002", 2, ["DOCS", "INF"], seat="14C"),
    )
    assert before["infantCount"] == after["infantCount"] == 1
    assert before["totalSouls"] == after["totalSouls"]
    _assert_named_flag(changes, "AB12CD", "HASSAN", "LAYLA MRS", "hasInfant", True, False)
    _assert_named_flag(changes, "ZZ99YY", "KHAN", "OMAR MR", "hasInfant", False, True)
    _assert_count_summaries(changes, before, after)


def test_swapped_child_flags_name_both_travelers_when_totals_match():
    child = _passenger_xml("SALEH", "HANA MRS", "GH56IJ", "4", "PAX0004", 4, ["DOCS", "CHD"])
    adult = _passenger_xml("NAIR", "DEV MR", "KL78MN", "5", "PAX0005", 5, ["DOCS"], seat="16A")
    before, after, changes = _ingest_pair(
        child + adult,
        _passenger_xml("SALEH", "HANA MRS", "GH56IJ", "4", "PAX0004", 4, ["DOCS"])
        + _passenger_xml("NAIR", "DEV MR", "KL78MN", "5", "PAX0005", 5, ["DOCS", "CHD"], seat="16A"),
    )
    assert before["childCount"] == after["childCount"] == 1
    assert before["adultCount"] == after["adultCount"]
    _assert_named_flag(changes, "GH56IJ", "SALEH", "HANA MRS", "isChild", True, False)
    _assert_named_flag(changes, "KL78MN", "NAIR", "DEV MR", "isChild", False, True)
    _assert_count_summaries(changes, before, after)


def test_infant_edit_alongside_loyalty_and_seat_keeps_those_events():
    before_xml = _passenger_xml(
        "HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS"], seat="12A"
    )
    after_xml = _passenger_xml(
        "HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS", "GLD", "INF"], seat="12B"
    )
    before, after, changes = _ingest_pair(before_xml, after_xml)
    _assert_named_flag(changes, "AB12CD", "HASSAN", "LAYLA MRS", "hasInfant", False, True)

    loyalty = [
        change for change in _timeline_for(changes, "AB12CD")
        if change.get("changeType") == "LOYALTY_STATUS_ADDED" and change.get("newValue") == "GLD"
    ]
    seats = [
        change for change in _timeline_for(changes, "AB12CD")
        if change.get("changeType") == "SEAT_CHANGE"
        and change.get("oldValue") == "12A" and change.get("newValue") == "12B"
    ]
    assert len(loyalty) == 1, changes
    assert len(seats) == 1, changes
    assert loyalty[0]["passenger"]["lastName"] == "HASSAN"
    _assert_count_summaries(changes, before, after)


def test_unchanged_infant_and_child_flags_emit_no_flag_or_count_events():
    xml = _passenger_xml(
        "HASSAN", "LAYLA MRS", "AB12CD", "1", "PAX0001", 1, ["DOCS", "INF", "CHD"]
    ) + _passenger_xml(
        "KHAN", "OMAR MR", "ZZ99YY", "2", "PAX0002", 2, ["DOCS"], seat="14C"
    )
    before, after, changes = _ingest_pair(xml, xml)
    assert _passenger(before, "AB12CD")["hasInfant"] is True
    assert _passenger(before, "AB12CD")["isChild"] is True
    assert changes == []
    _assert_count_summaries(changes, before, after)


def _run_all():
    tests = [
        test_added_lap_infant_names_the_traveler_and_keeps_count_summary,
        test_removed_lap_infant_names_the_traveler,
        test_single_edit_code_element_sets_and_clears_infant,
        test_child_flag_added_and_removed_names_the_traveler,
        test_child_who_gains_a_lap_infant_records_both_flags,
        test_swapped_infants_still_name_both_travelers_when_totals_match,
        test_swapped_child_flags_name_both_travelers_when_totals_match,
        test_infant_edit_alongside_loyalty_and_seat_keeps_those_events,
        test_unchanged_infant_and_child_flags_emit_no_flag_or_count_events,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    if failed:
        print(f"{failed} test(s) failed")
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_run_all())
