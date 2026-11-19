"""Spec for upgrade priority versus manifest line number.

These tests fail while the skipped-upgrade audit treats GetPassengerList
LineNumber as priority. They pass once waiting and upgraded passengers are
ranked by seniority date and Sabre's upgrade priority code.

LineNumber is the sequence number in the response list. Ingestion always
requests SortSequence Name, which the Sabre schema defines as last-name
order, so a smaller line number is an earlier surname. It is not upgrade
priority.

Ranking the tests require:

* A present seniority date outranks a later seniority date, and outranks a
  missing seniority date. A missing date is not earlier than a real date
  (the empty string the converter stores must not sort first) and two
  missing dates are not ordered by response position.
* When seniority does not decide, UpgradeCode decides. The integer embedded
  in the code is the rank; a smaller integer is higher priority. That covers
  the schema example ``01J`` and the codes on captured responses (``J06``,
  ``J07``), including unequal widths such as ``9J`` before ``10J``. A code
  with no integer does not outrank a code that has one.
* LineNumber is not a tie-break.
* A passenger whose upgrade was recorded (cabin moved to the desired cabin,
  or UPGRADE_CONFIRMED after DesiredBookingClass was cleared) is still
  compared with passengers who are waiting for that cabin.

The upgrade queue on ``GET /flights/{flight_number}/passengers/standby-list``
uses the same line-number ordering. Its upgrade list must follow UpgradeCode
rank, and its standby list must follow the integer in PriorityCode (B01
before B13) rather than line number, with seniority date still first when
both passengers have one.
"""

from backend.api.audit_rules import run_audit
from backend.api.routes.audit import get_flight_audit
from backend.api.routes.passengers import get_standby_list
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient

FLIGHT = "4242"
ORIGIN = "BAH"
DATE = "2026-04-01"
FLIGHT_INFO = {
    "flightNumber": FLIGHT,
    "origin": ORIGIN,
    "departureDate": DATE,
}


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def find_one(self, query, sort=None):
        return self._one

    def find(self, query, projection=None):
        return _Cursor(self._many)


class _DB:
    def __init__(self, **collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _passenger(**fields):
    passenger = {
        "last": "ABEL",
        "first": "NOOR",
        "pnr": "AAA111",
        "cabin": "Y",
        "booking": "Y",
        "desired": "J",
        "seniority": None,
        "upgrade": None,
        "priority": "",
    }
    passenger.update(fields)
    return passenger


def _xml(passengers):
    """Response list in last-name order, LineNumber equal to that position."""
    ordered = sorted(passengers, key=lambda p: (p["last"], p["first"], p["pnr"]))
    blocks = []
    for index, pax in enumerate(ordered, start=1):
        optional = []
        if pax["priority"]:
            optional.append(f"<PriorityCode>{pax['priority']}</PriorityCode>")
        if pax["upgrade"] is not None:
            optional.append(f"<UpgradeCode>{pax['upgrade']}</UpgradeCode>")
        if pax["desired"]:
            optional.append(
                f"<DesiredBookingClass>{pax['desired']}</DesiredBookingClass>"
            )
        if pax["seniority"]:
            optional.append(f"<SeniorityDate>{pax['seniority']}</SeniorityDate>")
        blocks.append(
            f"""
            <PassengerInfo>
              <Name_Details>
                <LastName>{pax['last']}</LastName>
                <FirstName>{pax['first']}</FirstName>
              </Name_Details>
              <PNRLocator>{pax['pnr']}</PNRLocator>
              <PassengerID>{pax['pnr']}ID</PassengerID>
              <LineNumber>{index}</LineNumber>
              {''.join(optional)}
              <BookingClass>{pax['booking']}</BookingClass>
              <Cabin>{pax['cabin']}</Cabin>
              <Destination>LHR</Destination>
              <PassengerType>F</PassengerType>
            </PassengerInfo>
            """
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{FLIGHT}</Flight>
          <DepartureDate>{DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {''.join(blocks)}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _ingest(passengers):
    """Parse a name-sorted GetPassengerListRS the way the Sabre client does."""
    parsed = SabreClient._parse_xml(_xml(passengers))
    client = SabreClient.__new__(SabreClient)
    body = client._extract_body(parsed, "GetPassengerListRS")
    return convert_passenger_list(body, "GF", FLIGHT, DATE, ORIGIN)


def _by_pnr(doc):
    return {pax["pnr"]: pax for pax in doc["passengers"]}


def _line_number(doc, pnr):
    return _by_pnr(doc)[pnr]["lineNumber"]


def _upgrade_changes(before_passengers, after_passengers):
    before = _ingest(before_passengers)
    after = _ingest(after_passengers)
    changes = detect_changes(
        {"snapshotId": "snap-before", "data": before},
        {"snapshotId": "snap-after", "data": after},
        "passenger_list",
        FLIGHT_INFO,
    )
    return before, after, changes


def _skipped(alerts):
    return [alert for alert in alerts if alert["ruleId"] == "UPGRADE_QUEUE_SKIPPED"]


def _audit_alerts(after_doc, changes):
    result = run_audit(after_doc, {"reservations": []}, changes)
    return _skipped(result["alerts"])


def _route_alerts(after_doc, changes):
    db = _DB(
        passenger_list=_Collection(one=after_doc),
        reservations=_Collection(one={"reservations": []}),
        changes=_Collection(many=changes),
    )
    original = get_flight_audit.__globals__["get_db"]
    get_flight_audit.__globals__["get_db"] = lambda: db
    try:
        result = get_flight_audit(FLIGHT, origin=ORIGIN, date=DATE)
    finally:
        get_flight_audit.__globals__["get_db"] = original
    return _skipped(result["alerts"])


def _assert_no_skip(before_passengers, after_passengers):
    _before, after, changes = _upgrade_changes(before_passengers, after_passengers)
    assert (_audit_alerts(after, changes), _route_alerts(after, changes)) == ([], [])


def _assert_skipped(before_passengers, after_passengers, skipped_pnr, upgraded_pnr):
    _before, after, changes = _upgrade_changes(before_passengers, after_passengers)
    skipped = _by_pnr(after)[skipped_pnr]
    upgraded = _by_pnr(after)[upgraded_pnr]
    seen = []
    for alerts in (_audit_alerts(after, changes), _route_alerts(after, changes)):
        assert [alert["pnr"] for alert in alerts] == [skipped_pnr]
        message = alerts[0]["message"]
        assert skipped["lastName"] in message
        assert upgraded["lastName"] in message
        assert alerts[0]["details"]["upgradedPnr"] == upgraded_pnr
        seen.append(alerts[0]["pnr"])
    assert seen == [skipped_pnr, skipped_pnr]


def _waiting(pnr, last, **fields):
    fields.setdefault("desired", "J")
    return _passenger(
        pnr=pnr, last=last, first="NOOR", cabin="Y", booking="Y", **fields
    )


def _moved_to_desired(pnr, last, **fields):
    """Cabin is now the desired cabin, and DesiredBookingClass is still present."""
    return _passenger(pnr=pnr, last=last, first="NOOR", cabin="J", booking="J", desired="J", **fields)


def _confirmed(pnr, last, **fields):
    """Desired class cleared after the booking class and cabin became J."""
    return _passenger(pnr=pnr, last=last, first="NOOR", cabin="J", booking="J", desired="", **fields)


def test_manifest_line_number_follows_surname_and_upgrade_code_is_kept():
    """The ingestion shape the audit reads.

    Fails while convert_passenger_list drops UpgradeCode. Line numbers are
    assigned from last-name order because that is the list Sabre returns for
    SortSequence Name.
    """
    doc = _ingest([
        _waiting("ZZZ999", "ZED", upgrade="J06"),
        _waiting("AAA111", "ABEL", upgrade="9J"),
    ])
    passengers = _by_pnr(doc)
    assert _line_number(doc, "AAA111") < _line_number(doc, "ZZZ999")
    assert passengers["AAA111"]["lastName"] < passengers["ZZZ999"]["lastName"]
    assert passengers["AAA111"]["seniorityDate"] == ""
    assert passengers["AAA111"]["upgradeCode"] == "9J"
    assert passengers["ZZZ999"]["upgradeCode"] == "J06"


def test_surname_line_number_does_not_flag_a_skipped_upgrade():
    """No seniority and no upgrade code: do not warn from name order."""
    earlier = _waiting("AAA111", "ABEL")
    later = _waiting("ZZZ999", "ZED")
    _assert_no_skip(
        [earlier, later],
        [earlier, _moved_to_desired("ZZZ999", "ZED")],
    )


def test_equal_seniority_dates_do_not_use_line_number():
    earlier = _waiting("AAA111", "ABEL", seniority="2000-01-15")
    later = _waiting("ZZZ999", "ZED", seniority="2000-01-15")
    _assert_no_skip(
        [earlier, later],
        [earlier, _moved_to_desired("ZZZ999", "ZED", seniority="2000-01-15")],
    )


def test_missing_seniority_does_not_outrank_a_real_date():
    """The passenger with no seniority date is not the one who was skipped."""
    undated = _waiting("AAA111", "ABEL")
    dated = _waiting("ZZZ999", "ZED", seniority="1990-06-01")
    _assert_no_skip(
        [undated, dated],
        [undated, _moved_to_desired("ZZZ999", "ZED", seniority="1990-06-01")],
    )


def test_lower_upgrade_rank_does_not_outrank_a_higher_rank_by_line_number():
    """10J is a worse rank than 9J even though '10J' sorts before '9J' as text."""
    worse = _waiting("AAA111", "ABEL", upgrade="10J")
    better = _waiting("ZZZ999", "ZED", upgrade="9J")
    _assert_no_skip(
        [worse, better],
        [worse, _moved_to_desired("ZZZ999", "ZED", upgrade="9J")],
    )


def test_higher_upgrade_rank_is_not_skipped_when_its_line_number_is_greater():
    worse = _waiting("AAA111", "ABEL", upgrade="10J")
    better = _waiting("ZZZ999", "ZED", upgrade="9J")
    _assert_skipped(
        [worse, better],
        [_moved_to_desired("AAA111", "ABEL", upgrade="10J"), better],
        skipped_pnr="ZZZ999",
        upgraded_pnr="AAA111",
    )


def test_schema_and_captured_upgrade_codes_use_the_embedded_integer():
    """01J outranks J06, and J06 outranks J07. The later surname holds the better code."""
    schema_better = _waiting("ZZZ999", "ZED", upgrade="01J")
    captured_worse = _waiting("AAA111", "ABEL", upgrade="J06")
    _assert_no_skip(
        [captured_worse, schema_better],
        [captured_worse, _moved_to_desired("ZZZ999", "ZED", upgrade="01J")],
    )

    captured_better = _waiting("ZZZ999", "ZED", upgrade="J06")
    captured_worse = _waiting("AAA111", "ABEL", upgrade="J07")
    _assert_skipped(
        [captured_worse, captured_better],
        [_moved_to_desired("AAA111", "ABEL", upgrade="J07"), captured_better],
        skipped_pnr="ZZZ999",
        upgraded_pnr="AAA111",
    )


def test_confirmed_upgrade_still_compares_upgrade_rank():
    """UPGRADE_CONFIRMED clears DesiredBookingClass. The queue comparison remains."""
    worse = _waiting("AAA111", "ABEL", upgrade="10J")
    better = _waiting("ZZZ999", "ZED", upgrade="9J")
    before, after, changes = _upgrade_changes(
        [worse, better],
        [_confirmed("AAA111", "ABEL", upgrade="10J"), better],
    )
    assert any(change["changeType"] == "UPGRADE_CONFIRMED" for change in changes)
    assert [
        [alert["pnr"] for alert in _audit_alerts(after, changes)],
        [alert["pnr"] for alert in _route_alerts(after, changes)],
    ] == [["ZZZ999"], ["ZZZ999"]]


def test_confirmed_upgrade_without_a_rank_does_not_use_line_number():
    earlier = _waiting("AAA111", "ABEL")
    later = _waiting("ZZZ999", "ZED")
    _assert_no_skip(
        [earlier, later],
        [earlier, _confirmed("ZZZ999", "ZED")],
    )


def test_earlier_seniority_outranks_line_number_and_upgrade_code():
    """A worse upgrade code with an earlier seniority date was skipped."""
    later_seniority = _waiting("AAA111", "ABEL", seniority="2015-01-01", upgrade="9J")
    earlier_seniority = _waiting("ZZZ999", "ZED", seniority="1990-06-01", upgrade="10J")
    _assert_skipped(
        [later_seniority, earlier_seniority],
        [
            _moved_to_desired("AAA111", "ABEL", seniority="2015-01-01", upgrade="9J"),
            earlier_seniority,
        ],
        skipped_pnr="ZZZ999",
        upgraded_pnr="AAA111",
    )


def test_real_seniority_outranks_a_missing_date_regardless_of_line_number():
    dated = _waiting("ZZZ999", "ZED", seniority="1990-06-01")
    undated = _waiting("AAA111", "ABEL")
    _assert_skipped(
        [undated, dated],
        [_moved_to_desired("AAA111", "ABEL"), dated],
        skipped_pnr="ZZZ999",
        upgraded_pnr="AAA111",
    )


def test_upgrade_queue_is_ordered_by_upgrade_code_not_line_number():
    doc = _ingest([
        _waiting("AAA111", "ABEL", priority="UPG", upgrade="10J", desired=""),
        _waiting("ZZZ999", "ZED", priority="UPG", upgrade="9J", desired=""),
    ])
    db = _DB(passenger_list=_Collection(one=doc))
    original = get_standby_list.__globals__["get_db"]
    get_standby_list.__globals__["get_db"] = lambda: db
    try:
        result = get_standby_list(
            FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None
        )
    finally:
        get_standby_list.__globals__["get_db"] = original

    upgrade = result["upgrade"]["passengers"]
    assert [pax["pnr"] for pax in upgrade] == ["ZZZ999", "AAA111"]
    assert [pax["upgradeCode"] for pax in upgrade] == ["9J", "10J"]


def test_standby_queue_is_ordered_by_priority_code_not_line_number():
    """No seniority date, so response position must not decide the queue."""
    doc = _ingest([
        _waiting("AAA111", "ABEL", priority="B13", desired=""),
        _waiting("ZZZ999", "ZED", priority="B01", desired=""),
    ])
    db = _DB(passenger_list=_Collection(one=doc))
    original = get_standby_list.__globals__["get_db"]
    get_standby_list.__globals__["get_db"] = lambda: db
    try:
        result = get_standby_list(
            FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None
        )
    finally:
        get_standby_list.__globals__["get_db"] = original

    standby = result["standby"]["passengers"]
    assert [pax["pnr"] for pax in standby] == ["ZZZ999", "AAA111"]
    assert [pax["priorityCode"] for pax in standby] == ["B01", "B13"]
