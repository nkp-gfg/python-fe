"""Passenger change detection must keep every person who shares a name.

Sabre can put several passengers on one record locator with the same last
and first name, and it stores unnamed group members as last name PAX with
an empty first name. These tests parse a GetPassengerListRS the way
SabreClient does, convert it, and diff consecutive snapshots the way the
feeder stores them.

They fail while passengers are keyed only by record locator + last name +
first name. A fix should keep each person as a separate before/after entry,
keyed by a stable id (passengerId, or line number when that is the unique
id), and each emitted event must name that person via passenger.passengerId
or passenger.lineNumber (metadata is accepted for those two fields).

detect_changes returns the documents store_changes writes. GET
/flights/{flightNumber}/changes returns those documents with _id removed
and does not re-key them. Phase-journey counts use _compute_transitions.

Run:
    python3 -m pytest backend/tests/test_passenger_collision_diff.py -q
"""

from backend.api.routes.flights import _compute_transitions
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.feeder.runner import (
    _extract_passenger_info_list,
    _pax_dedup_key,
    _set_passenger_info_list,
)
from backend.sabre.client import SabreClient

AIRLINE = "GF"
FLIGHT_NUMBER = "2006"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-03-19"
FLIGHT_INFO = {
    "airline": AIRLINE,
    "flightNumber": FLIGHT_NUMBER,
    "origin": ORIGIN,
    "departureDate": DEPARTURE_DATE,
}


def _passenger_xml(p):
    """One PassengerInfo element in the shape GetPassengerListRS uses."""
    pnr = p.get("pnr")
    pnr_xml = ""
    if pnr is not None:
        assoc = p.get("nameAssociationId", "1")
        pnr_xml = (
            f'<PNRLocator nameAssociationID="{assoc}">{pnr}</PNRLocator>'
        )
    group = p.get("groupCode") or ""
    group_xml = f"<GroupCode>{group}</GroupCode>" if group else ""
    desired = p.get("desiredBookingClass") or ""
    desired_xml = (
        f"<DesiredBookingClass>{desired}</DesiredBookingClass>" if desired else ""
    )
    upgrade = p.get("upgradeCode") or ""
    upgrade_xml = f"<UpgradeCode>{upgrade}</UpgradeCode>" if upgrade else ""
    priority = p.get("priorityCode") or ""
    priority_xml = f"<PriorityCode>{priority}</PriorityCode>" if priority else ""
    seat = p.get("seat") or ""
    seat_xml = f"<Seat>{seat}</Seat>" if seat else ""
    edits = "".join(f"<EditCode>{code}</EditCode>" for code in p.get("editCodes") or [])
    edit_xml = f"<EditCodeList>{edits}</EditCodeList>" if edits else ""
    checked = "true" if p.get("isCheckedIn") else "false"
    boarded = "true" if p.get("isBoarded") else "false"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{p.get("lastName", "")}</LastName>
            <FirstName>{p.get("firstName", "")}</FirstName>
          </Name_Details>
          {pnr_xml}
          <PassengerID>{p["passengerId"]}</PassengerID>
          <LineNumber>{p["lineNumber"]}</LineNumber>
          {group_xml}
          <BookingClass>{p.get("bookingClass", "Y")}</BookingClass>
          {desired_xml}
          {upgrade_xml}
          <Cabin>{p.get("cabin", "Y")}</Cabin>
          {seat_xml}
          <Destination>LHR</Destination>
          <PassengerType>{p.get("passengerType", "F")}</PassengerType>
          <BagCount>{p.get("bagCount", 0)}</BagCount>
          {priority_xml}
          <CheckIn_Info>
            <CheckInStatus>{checked}</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>{boarded}</BoardStatus>
          </Boarding_Info>
          {edit_xml}
        </PassengerInfo>
    """


def _soap(passengers):
    body = "\n".join(_passenger_xml(p) for p in passengers)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT_NUMBER}</Flight>
          <DepartureDate>{DEPARTURE_DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>LHR</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_body(passengers):
    """Parse the same way SabreClient.get_passenger_list does, without a network call.

    _parse_xml is static. _extract_body does not read instance state; it only
    walks the SOAP envelope. Passing no client avoids reading Sabre credentials.
    """
    parsed = SabreClient._parse_xml(_soap(passengers))
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _convert(passengers):
    return convert_passenger_list(
        _parse_body(passengers), AIRLINE, FLIGHT_NUMBER, DEPARTURE_DATE, ORIGIN,
    )


def _snapshot(passengers, snapshot_id):
    """Snapshot document shape stored by feeder.storage.store_snapshot."""
    return {"snapshotId": snapshot_id, "data": _convert(passengers)}


def _changes(before, after):
    before_snap = _snapshot(before, "snap-before")
    after_snap = _snapshot(after, "snap-after")
    return detect_changes(before_snap, after_snap, "passenger_list", FLIGHT_INFO)


def _identifies(change, passenger):
    """True when the stored change names this passenger, not just the shared name."""
    pax = change.get("passenger") or {}
    meta = change.get("metadata") or {}
    pid = pax.get("passengerId") or meta.get("passengerId")
    if pid:
        return pid == passenger["passengerId"]
    line = pax.get("lineNumber")
    if line is None:
        line = meta.get("lineNumber")
    if line is None:
        return False
    return line == passenger["lineNumber"]


def _of_type(changes, change_type, passenger):
    return [
        c for c in changes
        if c.get("changeType") == change_type and _identifies(c, passenger)
    ]


def _merge_like_runner(raw_calls):
    """Replay the passenger-list merge in backend/feeder/runner.py (the ingest loop)."""
    merged_raw = None
    for raw in raw_calls:
        if merged_raw is None:
            merged_raw = raw
            continue
        subsequent_pax = _extract_passenger_info_list(raw)
        if not subsequent_pax:
            continue
        base_pax_list = _extract_passenger_info_list(merged_raw)
        existing_keys = set()
        for pax in base_pax_list:
            existing_keys.add(_pax_dedup_key(pax))
        for pax in subsequent_pax:
            key = _pax_dedup_key(pax)
            if key not in existing_keys:
                base_pax_list.append(pax)
                existing_keys.add(key)
        _set_passenger_info_list(merged_raw, base_pax_list)
    return merged_raw


NAMESAKE_A = {
    "passengerId": "PID-A",
    "lineNumber": 1,
    "pnr": "ABC123",
    "lastName": "SMITH",
    "firstName": "JOHN",
    "nameAssociationId": "10",
    "bookingClass": "Y",
    "cabin": "Y",
    "bagCount": 0,
    "passengerType": "F",
}
NAMESAKE_B = {
    "passengerId": "PID-B",
    "lineNumber": 2,
    "pnr": "ABC123",
    "lastName": "SMITH",
    "firstName": "JOHN",
    "nameAssociationId": "11",
    "bookingClass": "Y",
    "cabin": "Y",
    "bagCount": 0,
    "passengerType": "F",
}


def _group_member(passenger_id, line_number, **overrides):
    member = {
        "passengerId": passenger_id,
        "lineNumber": line_number,
        "pnr": "GRP001",
        "lastName": "PAX",
        "firstName": "",
        "nameAssociationId": str(line_number),
        "groupCode": "TOUR",
        "bookingClass": "Y",
        "cabin": "Y",
        "bagCount": 0,
        "passengerType": "F",
    }
    member.update(overrides)
    return member


def test_manifest_keeps_namesakes_and_unnamed_group_members():
    """The converter keeps every person. The loss happens later, in the diff."""
    group = [
        _group_member("G1", 1),
        _group_member("G2", 2),
        _group_member("G3", 3),
    ]
    doc = _convert([NAMESAKE_A, NAMESAKE_B, *group])
    ids = [p["passengerId"] for p in doc["passengers"]]
    assert ids == ["PID-A", "PID-B", "G1", "G2", "G3"]
    unnamed = [p for p in doc["passengers"] if p["passengerId"].startswith("G")]
    assert all(p["isUnnamedGroup"] for p in unnamed)
    assert all(p["lastName"] == "PAX" and not p["firstName"] for p in unnamed)
    assert doc["totalPassengers"] == 5


def test_checkin_of_one_namesake_is_not_hidden_by_the_other():
    """The person who checks in is listed first. The unchanged namesake is last.

    A locator+name map keeps the last row, so the check-in disappears.
    """
    before = [dict(NAMESAKE_A), dict(NAMESAKE_B)]
    after_a = dict(NAMESAKE_A, isCheckedIn=True)
    after = [after_a, dict(NAMESAKE_B)]

    changes = _changes(before, after)
    matched = _of_type(changes, "CHECKED_IN", NAMESAKE_A)
    assert len(matched) == 1
    assert matched[0]["oldValue"] == "False"
    assert matched[0]["newValue"] == "True"
    assert _of_type(changes, "CHECKED_IN", NAMESAKE_B) == []


def test_boarding_of_one_namesake_is_not_hidden_by_the_other():
    before = [
        dict(NAMESAKE_A, isCheckedIn=True),
        dict(NAMESAKE_B, isCheckedIn=True),
    ]
    after = [
        dict(NAMESAKE_A, isCheckedIn=True, isBoarded=True),
        dict(NAMESAKE_B, isCheckedIn=True),
    ]
    changes = _changes(before, after)
    assert len(_of_type(changes, "BOARDED", NAMESAKE_A)) == 1
    assert _of_type(changes, "BOARDED", NAMESAKE_B) == []


def test_both_namesakes_check_in_and_board():
    """Two real check-ins must produce two events, not one shared event."""
    before = [dict(NAMESAKE_A), dict(NAMESAKE_B)]
    after = [
        dict(NAMESAKE_A, isCheckedIn=True, isBoarded=True),
        dict(NAMESAKE_B, isCheckedIn=True, isBoarded=True),
    ]
    changes = _changes(before, after)
    assert len(_of_type(changes, "CHECKED_IN", NAMESAKE_A)) == 1
    assert len(_of_type(changes, "CHECKED_IN", NAMESAKE_B)) == 1
    assert len(_of_type(changes, "BOARDED", NAMESAKE_A)) == 1
    assert len(_of_type(changes, "BOARDED", NAMESAKE_B)) == 1


def test_removing_one_namesake_records_that_passenger():
    before = [dict(NAMESAKE_A), dict(NAMESAKE_B)]
    after = [dict(NAMESAKE_B)]
    changes = _changes(before, after)
    removed = _of_type(changes, "PASSENGER_REMOVED", NAMESAKE_A)
    assert len(removed) == 1
    assert _of_type(changes, "PASSENGER_REMOVED", NAMESAKE_B) == []


def test_adding_one_namesake_records_that_passenger():
    before = [dict(NAMESAKE_B)]
    after = [dict(NAMESAKE_A), dict(NAMESAKE_B)]
    changes = _changes(before, after)
    added = _of_type(changes, "PASSENGER_ADDED", NAMESAKE_A)
    assert len(added) == 1
    assert _of_type(changes, "PASSENGER_ADDED", NAMESAKE_B) == []


def test_upgrade_and_other_state_changes_stay_with_the_passenger_who_changed():
    """Every per-passenger comparison uses the same map, so each event type
    must follow the person who changed, including when a namesake is last.
    """
    before_a = dict(
        NAMESAKE_A,
        desiredBookingClass="J",
        seat="",
        bagCount=0,
        passengerType="F",
        priorityCode="",
        editCodes=[],
    )
    before_b = dict(NAMESAKE_B, seat="22B", bagCount=1)
    after_a = dict(
        before_a,
        isCheckedIn=True,
        isBoarded=True,
        cabin="J",
        bookingClass="J",
        desiredBookingClass="",
        upgradeCode="LMU",
        seat="1A",
        bagCount=2,
        passengerType="S",
        priorityCode="UPG",
        editCodes=["FF", "DOCS"],
    )
    changes = _changes([before_a, before_b], [after_a, dict(before_b)])

    expected = [
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
    ]
    for change_type in expected:
        matched = _of_type(changes, change_type, NAMESAKE_A)
        assert len(matched) == 1, change_type
        assert _of_type(changes, change_type, NAMESAKE_B) == [], change_type

    cabin = _of_type(changes, "CABIN_CHANGE", NAMESAKE_A)[0]
    assert cabin["oldValue"] == "Y"
    assert cabin["newValue"] == "J"
    assert cabin["metadata"]["direction"] == "UPGRADE"
    assert cabin["metadata"]["upgradeType"] == "LMU"

    upgrade = _of_type(changes, "UPGRADE_CONFIRMED", NAMESAKE_A)[0]
    assert upgrade["oldValue"] == "J"
    assert upgrade["newValue"] == "J"


def test_reorder_of_namesakes_does_not_invent_events():
    """Same people, swapped order, no status change. Diff must be empty."""
    before_a = dict(NAMESAKE_A, isCheckedIn=True, cabin="J", seat="1A", bagCount=1)
    before_b = dict(NAMESAKE_B, cabin="Y", seat="22B", bagCount=0)
    changes = _changes([before_a, before_b], [dict(before_b), dict(before_a)])
    assert changes == []


def test_unnamed_group_checkin_boarding_removal_and_upgrade():
    """Every unnamed member is PAX with a blank first name on one locator.

    Line number is not part of the key when the locator is present, so the
    whole group collapses to one entry.
    """
    m1 = _group_member("G1", 1)
    m2 = _group_member("G2", 2, desiredBookingClass="J")
    m3 = _group_member("G3", 3)
    before = [m1, m2, m3]
    after_m2 = dict(
        m2,
        isCheckedIn=True,
        isBoarded=True,
        cabin="J",
        bookingClass="J",
        desiredBookingClass="",
        upgradeCode="LMU",
        seat="2A",
    )
    # The member who changed is in the middle. The last member is unchanged,
    # which is the row a locator+name map keeps.
    changes = _changes(before, [m1, after_m2, m3])

    for change_type in ("CHECKED_IN", "BOARDED", "CABIN_CHANGE", "UPGRADE_CONFIRMED"):
        assert len(_of_type(changes, change_type, m2)) == 1, change_type
        assert _of_type(changes, change_type, m1) == []
        assert _of_type(changes, change_type, m3) == []

    # All three check in and board on the next snapshot.
    all_done = [
        dict(m1, isCheckedIn=True, isBoarded=True),
        dict(after_m2),
        dict(m3, isCheckedIn=True, isBoarded=True),
    ]
    # Compare against the original manifest so m1 and m3 are new check-ins
    # and m2 is too. m2 also upgrades here if we start from `before`.
    both = _changes(
        before,
        [
            dict(m1, isCheckedIn=True, isBoarded=True),
            dict(m2, isCheckedIn=True, isBoarded=True),
            dict(m3, isCheckedIn=True, isBoarded=True),
        ],
    )
    for member in (m1, m2, m3):
        assert len(_of_type(both, "CHECKED_IN", member)) == 1
        assert len(_of_type(both, "BOARDED", member)) == 1

    removed = _changes(before, [m2, m3])
    assert len(_of_type(removed, "PASSENGER_REMOVED", m1)) == 1
    assert _of_type(removed, "PASSENGER_REMOVED", m2) == []
    assert _of_type(removed, "PASSENGER_REMOVED", m3) == []

    added = _changes([m2, m3], before)
    assert len(_of_type(added, "PASSENGER_ADDED", m1)) == 1

    # The converted manifest still lists every group member.
    manifest = _convert(all_done)
    assert [p["passengerId"] for p in manifest["passengers"]] == ["G1", "G2", "G3"]
    assert manifest["groupBookings"][0]["totalMembers"] == 3
    assert manifest["groupBookings"][0]["unnamedMembers"] == 3


def test_runner_merge_keeps_both_passengers_and_diff_attributes_checkin():
    """Booked and checked-in display calls are merged by passenger id.

    The checked-in namesake is absent from the booked call and appended.
    Both people are on the converted manifest. The diff must credit the
    person who checked in, not the namesake who was already on the base list.
    """
    booked = _parse_body([dict(NAMESAKE_B)])
    checked_in = _parse_body([dict(NAMESAKE_A, isCheckedIn=True)])
    merged = _merge_like_runner([booked, checked_in])
    after_doc = convert_passenger_list(
        merged, AIRLINE, FLIGHT_NUMBER, DEPARTURE_DATE, ORIGIN,
    )
    assert [p["passengerId"] for p in after_doc["passengers"]] == ["PID-B", "PID-A"]
    by_id = {p["passengerId"]: p for p in after_doc["passengers"]}
    assert by_id["PID-A"]["isCheckedIn"] is True
    assert by_id["PID-B"]["isCheckedIn"] is False

    before_doc = _convert([dict(NAMESAKE_A), dict(NAMESAKE_B)])
    changes = detect_changes(
        {"snapshotId": "snap-before", "data": before_doc},
        {"snapshotId": "snap-after", "data": after_doc},
        "passenger_list",
        FLIGHT_INFO,
    )
    assert len(_of_type(changes, "CHECKED_IN", NAMESAKE_A)) == 1
    assert _of_type(changes, "CHECKED_IN", NAMESAKE_B) == []
    assert _of_type(changes, "PASSENGER_REMOVED", NAMESAKE_A) == []
    assert _of_type(changes, "PASSENGER_ADDED", NAMESAKE_A) == []


def test_phase_transitions_count_each_namesake():
    """GET /flights/{flightNumber}/phase-journey counts flows with the same
    locator+name key. Two people moving state must count as two.
    """
    stayed = _convert([dict(NAMESAKE_A), dict(NAMESAKE_B)])["passengers"]
    one_checked_in = _convert([
        dict(NAMESAKE_A, isCheckedIn=True),
        dict(NAMESAKE_B),
    ])["passengers"]
    flows = {
        (item["fromState"], item["toState"]): item["count"]
        for item in _compute_transitions(stayed, one_checked_in)
    }
    assert flows.get(("booked", "checkedIn")) == 1
    assert flows.get(("booked", "booked")) == 1

    both = _convert([
        dict(NAMESAKE_A, isCheckedIn=True),
        dict(NAMESAKE_B, isCheckedIn=True),
    ])["passengers"]
    both_flows = {
        (item["fromState"], item["toState"]): item["count"]
        for item in _compute_transitions(stayed, both)
    }
    assert both_flows.get(("booked", "checkedIn")) == 2

    removed = _convert([dict(NAMESAKE_B)])["passengers"]
    removed_flows = {
        (item["fromState"], item["toState"]): item["count"]
        for item in _compute_transitions(stayed, removed)
    }
    assert removed_flows.get(("booked", "removed")) == 1
    assert removed_flows.get(("booked", "booked")) == 1

    added_flows = {
        (item["fromState"], item["toState"]): item["count"]
        for item in _compute_transitions(removed, stayed)
    }
    assert added_flows.get(("new", "booked")) == 1


def test_phase_transitions_count_each_unnamed_group_member():
    """Unnamed group rows store a blank first name. The phase-journey key
    must still count each member, and a blank first name must not abort it.
    """
    before = _convert([
        _group_member("G1", 1),
        _group_member("G2", 2),
    ])["passengers"]
    after = _convert([
        _group_member("G1", 1, isCheckedIn=True, isBoarded=True),
        _group_member("G2", 2),
    ])["passengers"]
    flows = {
        (item["fromState"], item["toState"]): item["count"]
        for item in _compute_transitions(before, after)
    }
    assert flows.get(("booked", "boarded")) == 1
    assert flows.get(("booked", "booked")) == 1


def test_empty_locator_staff_with_the_same_name_stay_separate():
    """Staff without a record locator are already keyed by line number.
    Reordering them must not invent a boarding, and a real boarding must
    still be attributed to that line.
    """
    staff_a = {
        "passengerId": "STAFF-A",
        "lineNumber": 4,
        "pnr": None,
        "lastName": "CREW",
        "firstName": "ALEX",
        "bookingClass": "S",
        "cabin": "Y",
        "passengerType": "S",
    }
    staff_b = dict(staff_a, passengerId="STAFF-B", lineNumber=5)
    quiet = _changes(
        [staff_a, dict(staff_b, isBoarded=True, isCheckedIn=True)],
        [dict(staff_b, isBoarded=True, isCheckedIn=True), staff_a],
    )
    assert quiet == []

    boarded = _changes(
        [staff_a, staff_b],
        [dict(staff_a, isCheckedIn=True, isBoarded=True), staff_b],
    )
    assert len(_of_type(boarded, "BOARDED", staff_a)) == 1
    assert _of_type(boarded, "BOARDED", staff_b) == []
