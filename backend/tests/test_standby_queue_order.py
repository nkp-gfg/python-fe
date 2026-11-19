"""Upgrade and standby clearance order must follow Sabre priority, not response position.

Flight ingestion asks GetPassengerList to sort by last name
(backend/sabre/templates.py PASSENGER_LIST SortSequence). Sabre documents
LineNumber as the passenger's sequence in that response, not as a priority
rank. These tests send a name-sorted response through the SOAP parser and
convert_passenger_list, then through the standby-list and flight-audit
endpoints.

Clearance order the endpoints must return:

- Upgrade queue (PriorityCode UPG): ascending UpgradeCode. A lower code is
  cleared first (J01 before J07), including when the better code belongs to
  a later surname and a higher LineNumber.
- Standby queue: earliest SeniorityDate first. A missing seniority date sorts
  after every recorded date. Passengers who share a seniority date are ordered
  by ascending PriorityCode (B01 before B02), not by LineNumber.
- Audit rule UPGRADE_QUEUE_SKIPPED uses that same upgrade-code rank. It must
  not treat a lower LineNumber as a higher priority.

LineNumber stays on each row as the response position. It must not decide
who is offered the seat.
"""

import copy

from backend.api.routes.audit import get_flight_audit
from backend.api.routes.passengers import get_standby_list
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import diff_passenger_list
from backend.feeder.runner import _extract_passenger_info_list
from backend.sabre.client import SabreClient
from backend.sabre import templates


FLIGHT = "2057"
ORIGIN = "BOM"
DATE = "2026-03-19"
AIRLINE = "GF"

# Last-name order, which is the order SortSequence Name produces. LineNumber
# is that response position. Upgrade and standby codes disagree with it.
_PASSENGERS = (
    # surname, first, pnr, line, priority, upgrade, seniority, desired, cabin
    ("AARDVARK", "ANN", "PNR001", 1, "B01", "", "", "", "Y"),
    ("AARON", "AMY", "PNR002", 2, "UPG", "J07", "2010-01-01", "J", "Y"),
    ("BAKER", "BEN", "PNR003", 3, "B02", "", "2010-06-01", "", "Y"),
    ("MIDDLE", "MO", "PNR004", 4, "B09", "", "2001-01-01", "", "Y"),
    ("YOUNG", "YAN", "PNR005", 5, "B01", "", "2010-06-01", "", "Y"),
    ("ZULU", "ZED", "PNR006", 6, "UPG", "J01", "2010-01-01", "J", "Y"),
)


def _passenger_xml(row):
    last, first, pnr, line, priority, upgrade, seniority, desired, cabin = row
    upgrade_xml = f"<UpgradeCode>{upgrade}</UpgradeCode>" if upgrade else ""
    seniority_xml = f"<SeniorityDate>{seniority}</SeniorityDate>" if seniority else ""
    desired_xml = f"<DesiredBookingClass>{desired}</DesiredBookingClass>" if desired else ""
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>ID{line:04d}</PassengerID>
          <LineNumber>{line}</LineNumber>
          <PriorityCode>{priority}</PriorityCode>
          {upgrade_xml}
          <BookingClass>Y</BookingClass>
          {desired_xml}
          <Cabin>{cabin}</Cabin>
          <Destination>DMM</Destination>
          <BoardingPassFlag>false</BoardingPassFlag>
          <PassengerType>P</PassengerType>
          {seniority_xml}
          <Indicators><Indicator>NotBoarded</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
        </PassengerInfo>"""


def _response_xml():
    passengers = "\n".join(_passenger_xml(row) for row in _PASSENGERS)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT}</Flight>
          <DepartureDate>{DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
        <CabinInfoList>
          <CabinInfo>
            <Destination>DMM</Destination>
            <Cabin>J</Cabin>
            <Count>0</Count>
            <Authorized>12</Authorized>
            <Available>12</Available>
          </CabinInfo>
        </CabinInfoList>
      </ItineraryInfo>
      <PassengerInfoList>
        {passengers}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _name_sorted_request():
    """Build the GetPassengerList body the same way SabreClient.get_passenger_list does."""
    body = templates.PASSENGER_LIST.format(
        cpaid="GF",
        conversation_id="uuid-test",
        message_id="mid:test@clientofsabre.com",
        timestamp="2026-03-19T00:00:00",
        token="TOKEN",
        airline=AIRLINE,
        flight_number=FLIGHT,
        departure_date=DATE,
        origin=ORIGIN,
        display_codes=templates.DISPLAY_CODES_BOOKED,
    )
    assert "<v4:SortSequence>Name</v4:SortSequence>" in body
    return body


def _ingested_passenger_list():
    """Parse a name-sorted GetPassengerListRS and convert it the way ingestion does."""
    _name_sorted_request()
    parsed = SabreClient._parse_xml(_response_xml())
    raw = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    # The first successful passenger-list call is stored as the base list.
    # Line numbers are left as Sabre sent them.
    assert _extract_passenger_info_list(raw)
    return convert_passenger_list(raw, AIRLINE, FLIGHT, DATE, ORIGIN)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda doc: doc.get(key, 0), reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc, field=field: doc.get(field, ""),
                    reverse=direction < 0,
                )
        return copy.deepcopy(matches[0]) if matches else None

    def find(self, query, projection=None):
        return _Cursor(copy.deepcopy(doc) for doc in self.docs if _matches(doc, query))


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection())


def _matches(doc, query):
    for key, expected in query.items():
        value = doc.get(key)
        if isinstance(expected, dict):
            if "$lte" in expected and (value is None or value > expected["$lte"]):
                return False
        elif value != expected:
            return False
    return True


def _install_db(monkeypatch, passenger_doc, changes=None, snapshot_sequence=1):
    db = _Database()
    db["passenger_list"].docs.append(passenger_doc)
    db["snapshots"].docs.append({
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "snapshotType": "passenger_list",
        "sequenceNumber": snapshot_sequence,
        "capturedAt": passenger_doc.get("fetchedAt", ""),
        "data": copy.deepcopy(passenger_doc),
    })
    for change in changes or []:
        db["changes"].docs.append(change)
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: db)
    monkeypatch.setattr("backend.api.routes.audit.get_db", lambda: db)
    return db


def _names(rows):
    return [row["lastName"] for row in rows]


def test_upgrade_queue_follows_upgrade_code_not_line_number(monkeypatch):
    doc = _ingested_passenger_list()
    stored = {p["lastName"]: p["lineNumber"] for p in doc["passengers"]}
    assert stored["AARON"] < stored["ZULU"]
    _install_db(monkeypatch, doc)

    body = get_standby_list(FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None)

    # J01 clears before J07 even though ZULU is later in the name-sorted response.
    assert _names(body["upgrade"]["passengers"]) == ["ZULU", "AARON"]
    assert [row["lineNumber"] for row in body["upgrade"]["passengers"]] == [
        stored["ZULU"],
        stored["AARON"],
    ]


def test_standby_tie_follows_priority_code_not_line_number(monkeypatch):
    doc = _ingested_passenger_list()
    _install_db(monkeypatch, doc)

    body = get_standby_list(FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None)

    # MIDDLE has the earliest seniority. YOUNG and BAKER share 2010-06-01, so
    # B01 clears before B02 even though BAKER has the lower line number.
    # AARDVARK has no seniority date and waits behind every dated passenger.
    assert _names(body["standby"]["passengers"]) == [
        "MIDDLE",
        "YOUNG",
        "BAKER",
        "AARDVARK",
    ]


def test_snapshot_standby_list_uses_the_same_priority_order(monkeypatch):
    doc = _ingested_passenger_list()
    _install_db(monkeypatch, doc, snapshot_sequence=4)

    body = get_standby_list(
        FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=4)

    assert _names(body["upgrade"]["passengers"]) == ["ZULU", "AARON"]
    assert _names(body["standby"]["passengers"]) == [
        "MIDDLE",
        "YOUNG",
        "BAKER",
        "AARDVARK",
    ]


def _audit_after_cabin_change(upgraded_last_name):
    """Run the flight-audit endpoint after one upgrade candidate's cabin changes.

    Both upgrade candidates keep the same seniority date and still request J.
    Only the cabin of the named passenger moves Y to J, which the differ records
    as CABIN_CHANGE direction UPGRADE.
    """
    current = _ingested_passenger_list()
    before_passengers = copy.deepcopy(current["passengers"])
    after_passengers = copy.deepcopy(current["passengers"])
    for passenger in after_passengers:
        if passenger["lastName"] == upgraded_last_name:
            passenger["cabin"] = "J"
    flight_info = {
        "airline": AIRLINE,
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
    }
    changes = diff_passenger_list(
        {"snapshotId": "before", "data": {"passengers": before_passengers}},
        {"snapshotId": "after", "data": {"passengers": after_passengers}},
        flight_info,
    )
    current["passengers"] = after_passengers
    return current, changes


def _skipped_rules(body):
    return [
        alert for alert in body["alerts"]
        if alert["ruleId"] == "UPGRADE_QUEUE_SKIPPED"
    ]


def test_audit_does_not_treat_earlier_line_as_higher_upgrade_priority(monkeypatch):
    current, changes = _audit_after_cabin_change("ZULU")
    assert any(change["changeType"] == "CABIN_CHANGE" for change in changes)
    _install_db(monkeypatch, current, changes=changes)

    body = get_flight_audit(FLIGHT, origin=ORIGIN, date=DATE)

    # ZULU (J01) was upgraded ahead of AARON (J07). AARON's lower line number
    # is not a stronger upgrade claim.
    assert _skipped_rules(body) == []


def test_audit_flags_skipped_passenger_with_better_upgrade_code(monkeypatch):
    current, changes = _audit_after_cabin_change("AARON")
    _install_db(monkeypatch, current, changes=changes)

    body = get_flight_audit(FLIGHT, origin=ORIGIN, date=DATE)

    skipped = _skipped_rules(body)
    assert [alert["pnr"] for alert in skipped] == ["PNR006"]
    assert skipped[0]["passengerName"]
