"""Staff-in-premium audit: CorpID T is an electronic ticket, not a staff code.

These tests exercise the ingestion parse/convert path and the flight audit
route. They fail while ``rule_staff_in_premium_no_upgrade`` treats CorpID ``T``
as proof the passenger is staff.

Sabre GetPassengerList defines the fields used here:

- CorpID ``T`` means the passenger has an electronic ticket, otherwise blank.
- PassengerType ``E`` means an employee passenger. ``F`` is full fare.
- SeniorityDate is the company employment seniority date.

A critical ``STAFF_PREMIUM_NO_UPGRADE`` alert is expected only for an employee
(passenger type ``E`` or a seniority date) seated in cabin ``J`` or ``F`` when
no upgrade change names that passenger's record locator. An
``UPGRADE_CONFIRMED`` change, or a ``CABIN_CHANGE`` whose metadata direction is
``UPGRADE``, suppresses the alert for that locator only.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.audit_rules import run_audit
from backend.api.routes.audit import get_flight_audit
from backend.feeder import storage
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import diff_passenger_list
from backend.sabre.client import SabreClient

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)
_FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}
_STAFF_RULE = "STAFF_PREMIUM_NO_UPGRADE"


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        # Same call the audit route makes: cursor.sort("sequenceNumber", 1).
        self._docs.sort(
            key=lambda doc: (doc.get(key) is None, str(doc.get(key))),
            reverse=direction < 0,
        )
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    """Equality-filter stand-in for the Collection methods the audit route calls.

    pymongo 4.20 ``insert_one`` / ``insert_many`` store the mapping and add
    ``_id`` when it is missing (synchronous/collection.py). ``find`` returns
    documents that match the filter and applies an exclusion projection.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, document):
        if "_id" not in document:
            document["_id"] = len(self.docs) + 1
        self.docs.append(document)
        return _InsertOneResult(document["_id"])

    def insert_many(self, documents):
        inserted = []
        for document in documents:
            if "_id" not in document:
                document["_id"] = len(self.docs) + 1
            self.docs.append(document)
            inserted.append(document["_id"])
        return _InsertManyResult(inserted)

    def find_one(self, query, *args, **kwargs):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        sort = kwargs.get("sort") or []
        for key, direction in reversed(list(sort)):
            matches.sort(
                key=lambda doc: (doc.get(key) is None, str(doc.get(key))),
                reverse=direction < 0,
            )
        return matches[0] if matches else None

    def find(self, query, projection=None, **kwargs):
        matches = []
        for doc in self.docs:
            if not _matches(doc, query):
                continue
            copied = dict(doc)
            if projection and projection.get("_id") == 0:
                copied.pop("_id", None)
            matches.append(copied)
        return _Cursor(matches)


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        self._collections.setdefault(name, _Collection())
        return self._collections[name]


def _matches(doc, query):
    return all(doc.get(key) == value for key, value in query.items())


def _parse_passenger_list(xml_text, flight=None):
    """Parse a SOAP envelope the way SabreClient.get_passenger_list does, then convert."""
    flight = flight or _FLIGHT
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(object(), parsed, "GetPassengerListRS")
    return convert_passenger_list(
        body,
        flight["airline"],
        flight["flightNumber"],
        flight["departureDate"],
        flight["origin"],
    )


def _load_gf2057():
    return _parse_passenger_list(_SAMPLE.read_text(encoding="utf-8"))


def _passenger_xml(
    *,
    last,
    first,
    pnr,
    passenger_type,
    cabin,
    booking_class,
    corp_id=None,
    seniority=None,
    desired_booking_class=None,
    revenue=False,
):
    corp = f"<CorpID>{corp_id}</CorpID>" if corp_id is not None else ""
    seniority_xml = f"<SeniorityDate>{seniority}</SeniorityDate>" if seniority else ""
    desired = (
        f"<DesiredBookingClass>{desired_booking_class}</DesiredBookingClass>"
        if desired_booking_class is not None
        else ""
    )
    indicator = "Revenue" if revenue else "NonRevenue"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <BookingClass>{booking_class}</BookingClass>
          <Cabin>{cabin}</Cabin>
          <PassengerType>{passenger_type}</PassengerType>
          {corp}
          {seniority_xml}
          {desired}
          <Indicators><Indicator>{indicator}</Indicator></Indicators>
        </PassengerInfo>
    """


def _envelope(passenger_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" version="4.0.0">
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
        {passenger_xml}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _manifest(passengers):
    return _parse_passenger_list(_envelope("".join(passengers)))


def _staff_alerts(result):
    return [alert for alert in result["alerts"] if alert["ruleId"] == _STAFF_RULE]


def _alerts_for(result, pnr):
    return [alert for alert in _staff_alerts(result) if alert.get("pnr") == pnr]


def _audit_stored(monkeypatch, doc, changes=None):
    """Store the manifest the way the feeder does, then run the audit route."""
    db = _Database()
    previous = storage._db
    storage.init_db(db)
    try:
        storage.store_passenger_list(doc)
        if changes:
            storage.store_changes(changes)
        monkeypatch.setattr("backend.api.routes.audit.get_db", lambda: db)
        return get_flight_audit(
            doc["flightNumber"],
            origin=doc["origin"],
            date=doc["departureDate"],
        )
    finally:
        storage._db = previous


def _changes_between(before_doc, after_doc):
    return diff_passenger_list(
        {"snapshotId": "before", "data": before_doc},
        {"snapshotId": "after", "data": after_doc},
        {
            "flightNumber": after_doc["flightNumber"],
            "origin": after_doc["origin"],
            "departureDate": after_doc["departureDate"],
        },
    )


def _assert_route_matches_rule(monkeypatch, doc, changes=None):
    direct = run_audit(doc, None, changes or [])
    routed = _audit_stored(monkeypatch, doc, changes)
    assert _staff_alerts(routed) == _staff_alerts(direct)
    return routed


def _premium_ticketed_full_fare(doc):
    return [
        passenger
        for passenger in doc["passengers"]
        if passenger.get("cabin") in ("J", "F")
        and passenger.get("bookingClass") == "D"
        and passenger.get("corpId") == "T"
        and passenger.get("passengerType") == "F"
        and passenger.get("isRevenue") is True
        and not passenger.get("seniorityDate")
    ]


def test_gf2057_ticketed_business_passengers_are_not_reported_as_staff(monkeypatch):
    """Paying business passengers with an e-ticket marker are not staff."""
    doc = _load_gf2057()
    premium = _premium_ticketed_full_fare(doc)
    assert len(premium) == 2
    assert {passenger["cabin"] for passenger in premium} == {"J"}

    routed = _assert_route_matches_rule(monkeypatch, doc, [])
    premium_pnrs = {passenger["pnr"] for passenger in premium}
    assert premium_pnrs
    for pnr in premium_pnrs:
        assert _alerts_for(routed, pnr) == []


def test_gf2057_economy_employee_is_not_a_premium_staff_alert(monkeypatch):
    """The captured employee is in cabin Y, so this rule does not apply."""
    doc = _load_gf2057()
    employees = [
        passenger
        for passenger in doc["passengers"]
        if passenger.get("passengerType") == "E" and passenger.get("seniorityDate")
    ]
    assert len(employees) == 1
    employee = employees[0]
    assert employee["cabin"] == "Y"
    assert employee["corpId"] == "T"
    assert employee["isRevenue"] is False

    routed = _assert_route_matches_rule(monkeypatch, doc, [])
    assert _alerts_for(routed, employee["pnr"]) == []


def test_employee_in_business_without_eticket_marker_is_critical(monkeypatch):
    doc = _manifest([
        _passenger_xml(
            last="EMPLOYEE",
            first="NOETKT",
            pnr="EMPJ01",
            passenger_type="E",
            cabin="J",
            booking_class="Z",
            seniority="2015-02-07",
        )
    ])
    passenger = doc["passengers"][0]
    assert passenger["passengerType"] == "E"
    assert passenger["corpId"] == ""
    assert passenger["cabin"] == "J"
    assert passenger["seniorityDate"] == "2015-02-07"

    routed = _assert_route_matches_rule(monkeypatch, doc, [])
    alerts = _alerts_for(routed, "EMPJ01")
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["details"]["cabin"] == "J"


def test_eticketed_employee_in_first_class_without_upgrade_is_critical(monkeypatch):
    """An employee who also holds an e-ticket is still staff."""
    doc = _manifest([
        _passenger_xml(
            last="EMPLOYEE",
            first="TICKETED",
            pnr="EMPF01",
            passenger_type="E",
            cabin="F",
            booking_class="Z",
            corp_id="T",
            seniority="2015-02-07",
        )
    ])
    passenger = doc["passengers"][0]
    assert passenger["corpId"] == "T"
    assert passenger["passengerType"] == "E"
    assert passenger["cabin"] == "F"

    routed = _assert_route_matches_rule(monkeypatch, doc, [])
    alerts = _alerts_for(routed, "EMPF01")
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["details"]["cabin"] == "F"


def test_seniority_date_marks_premium_staff_without_corp_id_t(monkeypatch):
    doc = _manifest([
        _passenger_xml(
            last="SENIOR",
            first="CREW",
            pnr="SENJ01",
            passenger_type="F",
            cabin="J",
            booking_class="D",
            seniority="2011-06-01",
            revenue=True,
        )
    ])
    passenger = doc["passengers"][0]
    assert passenger["passengerType"] == "F"
    assert passenger["corpId"] == ""
    assert passenger["seniorityDate"] == "2011-06-01"
    assert passenger["cabin"] == "J"

    routed = _assert_route_matches_rule(monkeypatch, doc, [])
    alerts = _alerts_for(routed, "SENJ01")
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"


def test_standby_eticket_in_premium_is_not_a_staff_alert(monkeypatch):
    doc = _manifest([
        _passenger_xml(
            last="STANDBY",
            first="GUEST",
            pnr="SBYJ01",
            passenger_type="S",
            cabin="J",
            booking_class="D",
            corp_id="T",
            revenue=True,
        )
    ])
    passenger = doc["passengers"][0]
    assert passenger["passengerType"] == "S"
    assert passenger["corpId"] == "T"
    assert passenger["cabin"] == "J"
    assert not passenger["seniorityDate"]

    routed = _assert_route_matches_rule(monkeypatch, doc, [])
    assert _alerts_for(routed, "SBYJ01") == []


def test_full_fare_eticket_in_first_class_is_not_a_staff_alert(monkeypatch):
    doc = _manifest([
        _passenger_xml(
            last="PAYING",
            first="FIRST",
            pnr="PAYF01",
            passenger_type="F",
            cabin="F",
            booking_class="A",
            corp_id="T",
            revenue=True,
        )
    ])
    passenger = doc["passengers"][0]
    assert passenger["passengerType"] == "F"
    assert passenger["isRevenue"] is True
    assert passenger["corpId"] == "T"
    assert passenger["cabin"] == "F"

    routed = _assert_route_matches_rule(monkeypatch, doc, [])
    assert _alerts_for(routed, "PAYF01") == []


def test_upgrade_change_for_the_same_locator_suppresses_the_alert(monkeypatch):
    common = dict(
        last="EMPLOYEE",
        first="UPGRADED",
        pnr="EMPUP1",
        passenger_type="E",
        booking_class="Z",
        seniority="2015-02-07",
    )
    before = _manifest([_passenger_xml(**common, cabin="Y")])
    after = _manifest([_passenger_xml(**common, cabin="J")])
    changes = _changes_between(before, after)
    upgrade = [
        change for change in changes
        if change["changeType"] == "CABIN_CHANGE"
        and change["passenger"]["pnr"] == "EMPUP1"
        and change["metadata"]["direction"] == "UPGRADE"
    ]
    assert len(upgrade) == 1

    routed = _assert_route_matches_rule(monkeypatch, after, changes)
    assert _alerts_for(routed, "EMPUP1") == []


def test_upgrade_for_a_different_locator_does_not_clear_the_employee(monkeypatch):
    employee = dict(
        last="EMPLOYEE",
        first="SEATED",
        pnr="EMPJ02",
        passenger_type="E",
        cabin="J",
        booking_class="Z",
        seniority="2015-02-07",
    )
    other_before = _passenger_xml(
        last="PAYING",
        first="MOVER",
        pnr="PAYJ02",
        passenger_type="F",
        cabin="Y",
        booking_class="Y",
        corp_id="T",
        revenue=True,
    )
    other_after = _passenger_xml(
        last="PAYING",
        first="MOVER",
        pnr="PAYJ02",
        passenger_type="F",
        cabin="J",
        booking_class="D",
        corp_id="T",
        revenue=True,
    )
    before = _manifest([_passenger_xml(**employee), other_before])
    after = _manifest([_passenger_xml(**employee), other_after])
    changes = _changes_between(before, after)
    assert any(
        change["changeType"] == "CABIN_CHANGE"
        and change["passenger"]["pnr"] == "PAYJ02"
        and change["metadata"]["direction"] == "UPGRADE"
        for change in changes
    )
    assert not any(change["passenger"].get("pnr") == "EMPJ02" for change in changes if change.get("passenger"))

    routed = _assert_route_matches_rule(monkeypatch, after, changes)
    alerts = _alerts_for(routed, "EMPJ02")
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"
    assert _alerts_for(routed, "PAYJ02") == []


def test_downgrade_does_not_count_as_an_upgrade_record(monkeypatch):
    common = dict(
        last="EMPLOYEE",
        first="DOWN",
        pnr="EMPDG1",
        passenger_type="E",
        booking_class="Z",
        seniority="2015-02-07",
    )
    before = _manifest([_passenger_xml(**common, cabin="F")])
    after = _manifest([_passenger_xml(**common, cabin="J")])
    changes = _changes_between(before, after)
    assert any(
        change["changeType"] == "CABIN_CHANGE"
        and change["metadata"]["direction"] == "DOWNGRADE"
        and change["passenger"]["pnr"] == "EMPDG1"
        for change in changes
    )

    routed = _assert_route_matches_rule(monkeypatch, after, changes)
    alerts = _alerts_for(routed, "EMPDG1")
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"


def test_class_change_alone_does_not_clear_the_staff_alert(monkeypatch):
    common = dict(
        last="EMPLOYEE",
        first="REBOOK",
        pnr="EMPCL1",
        passenger_type="E",
        cabin="J",
        seniority="2015-02-07",
    )
    before = _manifest([_passenger_xml(**common, booking_class="Y")])
    after = _manifest([_passenger_xml(**common, booking_class="Z")])
    changes = _changes_between(before, after)
    assert any(
        change["changeType"] == "CLASS_CHANGE" and change["passenger"]["pnr"] == "EMPCL1"
        for change in changes
    )
    assert not any(change["changeType"] == "CABIN_CHANGE" for change in changes)

    routed = _assert_route_matches_rule(monkeypatch, after, changes)
    alerts = _alerts_for(routed, "EMPCL1")
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"


def test_confirmed_upgrade_for_the_same_locator_suppresses_the_alert(monkeypatch):
    common = dict(
        last="EMPLOYEE",
        first="CONFIRMED",
        pnr="EMPCF1",
        passenger_type="E",
        cabin="J",
        seniority="2015-02-07",
    )
    before = _manifest([
        _passenger_xml(**common, booking_class="Y", desired_booking_class="Z"),
    ])
    after = _manifest([
        _passenger_xml(**common, booking_class="Z"),
    ])
    assert before["passengers"][0]["desiredBookingClass"] == "Z"
    assert after["passengers"][0]["desiredBookingClass"] == ""
    changes = _changes_between(before, after)
    assert any(
        change["changeType"] == "UPGRADE_CONFIRMED"
        and change["passenger"]["pnr"] == "EMPCF1"
        for change in changes
    )

    routed = _assert_route_matches_rule(monkeypatch, after, changes)
    assert _alerts_for(routed, "EMPCF1") == []
