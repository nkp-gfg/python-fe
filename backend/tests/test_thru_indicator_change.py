"""Thru-indicator changes must be recorded in flight history.

Sabre GetPassengerListRS sets ThruIndicator on the onward leg when a
connecting traveler was checked in on the previous leg. Conversion stores
that as isThru. Consecutive passenger-list snapshots are diffed by
detect_changes, and the activity feed, flight timeline, and passenger
timeline are built only from the stored change documents.

These tests drive that path: parse a manifest the way SabreClient parses a
GetPassengerListRS body, store it through _process_api_call, then read the
history endpoints. They fail while a thru-only transition produces no
isThru change.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from unittest.mock import patch

from backend.api.routes.changes import (
    get_activity_feed,
    get_changes,
    get_flight_timeline,
    get_passenger_timeline,
)
from backend.feeder.converter import convert_passenger_list
from backend.feeder.runner import _process_api_call
from backend.sabre.client import SabreClient

FLIGHT = {
    "airline": "GF",
    "flightNumber": "9001",
    "origin": "BAH",
    "departureDate": "2026-03-18",
}
def _manifest_xml(passengers: list[str]) -> str:
    """SOAP body shaped like a GetPassengerListRS the client extracts.

    No session token is included. Passenger fragments are the caller-built
    PassengerInfo elements.
    """
    body = "\n".join(passengers)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>9001</Flight>
          <DepartureDate>2026-03-18</DepartureDate>
          <Origin>BAH</Origin>
          <Destination>DOH</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger_xml(
    *,
    last_name: str,
    first_name: str,
    thru: str | None,
    pnr: str | None = None,
    line_number: int = 1,
    seat: str = "21A",
    checked_in: str = "false",
    name_association_id: str = "1",
) -> str:
    thru_el = f"<ThruIndicator>{thru}</ThruIndicator>" if thru is not None else ""
    if pnr is None:
        pnr_el = ""
    else:
        pnr_el = (
            f'<PNRLocator nameAssociationID="{name_association_id}">'
            f"{pnr}</PNRLocator>"
        )
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>{first_name}</FirstName>
          </Name_Details>
          {pnr_el}
          <PassengerID>PAX{line_number:04d}</PassengerID>
          {thru_el}
          <LineNumber>{line_number}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{seat}</Seat>
          <Destination>DOH</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <CheckIn_Info>
            <CheckInStatus>{checked_in}</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
        </PassengerInfo>
    """


def _parse_passenger_list(xml_text: str) -> dict:
    """Same parse and body extract get_passenger_list uses after the HTTP call."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _ingest(flight_info: dict, xml_text: str) -> dict:
    raw = _parse_passenger_list(xml_text)
    meta = {
        "requestXml": "<GetPassengerListRQ/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": None,
        "conversationId": None,
    }
    return _process_api_call(
        "PassengerList",
        "passenger_list",
        flight_info,
        raw,
        xml_text,
        meta,
        convert_passenger_list,
        (
            raw,
            flight_info["airline"],
            flight_info["flightNumber"],
            flight_info["departureDate"],
            flight_info["origin"],
        ),
    )


def _stored_passengers(db, flight_info: dict) -> list[dict]:
    snaps = [
        doc
        for doc in db["snapshots"].docs
        if doc.get("flightNumber") == flight_info["flightNumber"]
        and doc.get("snapshotType") == "passenger_list"
    ]
    snaps.sort(key=lambda doc: doc.get("sequenceNumber", 0))
    assert snaps, "ingestion stored no passenger_list snapshot"
    return snaps[-1]["data"]["passengers"]


def _passenger(passengers: list[dict], last_name: str) -> dict:
    matches = [pax for pax in passengers if pax.get("lastName") == last_name]
    assert len(matches) == 1, last_name
    return matches[0]


def _history(flight_info: dict) -> list[dict]:
    return get_changes(
        flight_info["flightNumber"],
        origin=flight_info["origin"],
        date=flight_info["departureDate"],
        change_type=None,
        limit=100,
    )


def _thru_changes(changes: list[dict], last_name: str) -> list[dict]:
    return [
        change
        for change in changes
        if change.get("field") == "isThru"
        and (change.get("passenger") or {}).get("lastName") == last_name
    ]


def _value(change: dict, key: str) -> str:
    return str(change.get(key))


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            spec = list(key)
        else:
            spec = [(key, 1 if direction is None else direction)]

        def _ordering(value):
            if value is None:
                return (0, "")
            return (1, value)

        for field, direc in reversed(spec):
            self._docs.sort(
                key=lambda doc, field=field: _ordering(_path(doc, field)),
                reverse=direc == -1,
            )
        return self

    def limit(self, count: int):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _InsertResult:
    def __init__(self, inserted_id=None, inserted_ids=None):
        self.inserted_id = inserted_id
        self.inserted_ids = inserted_ids or []


class _Collection:
    def __init__(self):
        self.docs: list[dict] = []
        self._ids = 0

    def insert_one(self, doc: dict):
        self._ids += 1
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", self._ids)
        doc["_id"] = stored["_id"]
        self.docs.append(stored)
        return _InsertResult(inserted_id=stored["_id"])

    def insert_many(self, docs: list[dict]):
        inserted = []
        for doc in docs:
            inserted.append(self.insert_one(doc).inserted_id)
        return _InsertResult(inserted_ids=inserted)

    def find_one(self, filter=None, projection=None, sort=None, **_kwargs):
        matched = [doc for doc in self.docs if _match(doc, filter or {})]
        if sort:
            matched = list(_Cursor(matched).sort(sort))
        if not matched:
            return None
        return _project(matched[0], projection)

    def find(self, filter=None, projection=None, sort=None, **_kwargs):
        matched = [
            _project(doc, projection)
            for doc in self.docs
            if _match(doc, filter or {})
        ]
        cursor = _Cursor(matched)
        if sort:
            cursor.sort(sort)
        return cursor

    def update_one(self, filter, update, upsert=False):
        matches = [doc for doc in self.docs if _match(doc, filter or {})]
        if not matches:
            if not upsert:
                return
            created = {}
            created.update(copy.deepcopy(update.get("$setOnInsert", {})))
            created.update(copy.deepcopy(update.get("$set", {})))
            for field, amount in update.get("$inc", {}).items():
                created[field] = amount
            self.insert_one(created)
            return
        target = matches[0]
        for field, value in update.get("$set", {}).items():
            target[field] = copy.deepcopy(value)
        for field, amount in update.get("$inc", {}).items():
            target[field] = target.get(field, 0) + amount


class _MemoryDB:
    def __init__(self):
        self._collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


def _path(doc: dict, dotted: str):
    current = doc
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _match(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_match(doc, item) for item in expected):
                return False
            continue
        actual = _path(doc, key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$in" not in expected and "$lte" not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _project(doc: dict, projection: dict | None) -> dict:
    copied = copy.deepcopy(doc)
    if not projection:
        return copied
    excluding = any(value == 0 for value in projection.values())
    if excluding:
        for field, value in projection.items():
            if value == 0:
                copied.pop(field, None)
        return copied
    included = {}
    if projection.get("_id", 1) != 0 and "_id" in copied:
        included["_id"] = copied["_id"]
    for field, value in projection.items():
        if field == "_id" or not value:
            continue
        if field in copied:
            included[field] = copied[field]
    return included


@contextmanager
def _history_db():
    database = _MemoryDB()
    with (
        patch("backend.feeder.storage.get_db", return_value=database),
        patch("backend.api.routes.changes.get_db", return_value=database),
    ):
        yield database


def _onward_manifest(
    *,
    traveler_thru: str,
    crew_thru: str,
    traveler_line: int,
    mover_seat: str,
    local_thru: str | None,
) -> str:
    return _manifest_xml(
        [
            _passenger_xml(
                last_name="TRAVELER",
                first_name="CONNECTING",
                thru=traveler_thru,
                pnr="ABC123",
                line_number=traveler_line,
                seat="21A",
            ),
            _passenger_xml(
                last_name="CREW",
                first_name="MEMBER",
                thru=crew_thru,
                pnr=None,
                line_number=4,
                seat="3A",
            ),
            _passenger_xml(
                last_name="LOCAL",
                first_name="STABLE",
                thru=local_thru,
                pnr="STB456",
                line_number=5,
                seat="22B",
            ),
            _passenger_xml(
                last_name="MOVER",
                first_name="SEAT",
                thru="false",
                pnr="MOV789",
                line_number=6,
                seat=mover_seat,
            ),
        ]
    )


def _onward_leg_pair() -> tuple[str, str, str]:
    """Three onward-leg polls.

    The second poll turns ThruIndicator on and moves one other seat.
    The third poll turns only the booked traveler's indicator back off.
    """
    before = _onward_manifest(
        traveler_thru="false",
        crew_thru="false",
        traveler_line=1,
        mover_seat="12A",
        local_thru=None,
    )
    after = _onward_manifest(
        traveler_thru="True",
        crew_thru="true",
        traveler_line=8,
        mover_seat="14C",
        local_thru="false",
    )
    turned_off = _onward_manifest(
        traveler_thru="false",
        crew_thru="true",
        traveler_line=8,
        mover_seat="14C",
        local_thru="false",
    )
    return before, after, turned_off


def test_thru_indicator_transition_is_stored_in_history():
    before_xml, after_xml, _turned_off = _onward_leg_pair()
    with _history_db() as database:
        first = _ingest(FLIGHT, before_xml)
        assert first["isDuplicate"] is False
        assert first["changesStored"] == 0
        stored = _stored_passengers(database, FLIGHT)
        assert _passenger(stored, "TRAVELER")["isThru"] is False
        assert _passenger(stored, "TRAVELER")["isCheckedIn"] is False
        assert _passenger(stored, "CREW")["pnr"] == ""
        assert _passenger(stored, "CREW")["isThru"] is False

        second = _ingest(FLIGHT, after_xml)
        assert second["isDuplicate"] is False
        stored = _stored_passengers(database, FLIGHT)
        traveler = _passenger(stored, "TRAVELER")
        crew = _passenger(stored, "CREW")
        local = _passenger(stored, "LOCAL")
        assert traveler["isThru"] is True
        assert traveler["isCheckedIn"] is False
        assert traveler["seat"] == "21A"
        assert crew["isThru"] is True
        assert crew["isCheckedIn"] is False
        assert local["isThru"] is False

        changes = _history(FLIGHT)
        traveler_thru = _thru_changes(changes, "TRAVELER")
        crew_thru = _thru_changes(changes, "CREW")
        assert traveler_thru, (
            "history has no isThru change after ThruIndicator turned on "
            "while check-in, cabin, class, seat, bags, and type stayed the same"
        )
        assert len(traveler_thru) == 1
        assert _value(traveler_thru[0], "oldValue") == "False"
        assert _value(traveler_thru[0], "newValue") == "True"
        assert traveler_thru[0]["passenger"]["pnr"] == "ABC123"
        assert traveler_thru[0]["passenger"]["firstName"] == "CONNECTING"
        assert traveler_thru[0]["changeType"] != "CHECKED_IN"
        assert crew_thru, (
            "history has no isThru change for a traveler matched by line number"
        )
        assert len(crew_thru) == 1
        assert _value(crew_thru[0], "oldValue") == "False"
        assert _value(crew_thru[0], "newValue") == "True"
        assert crew_thru[0]["passenger"]["pnr"] == ""
        assert not _thru_changes(changes, "LOCAL")
        assert not any(
            change.get("changeType") == "CHECKED_IN"
            and (change.get("passenger") or {}).get("lastName")
            in {"TRAVELER", "CREW", "LOCAL"}
            for change in changes
        )
        assert any(
            change.get("changeType") == "SEAT_CHANGE"
            and (change.get("passenger") or {}).get("pnr") == "MOV789"
            and _value(change, "oldValue") == "12A"
            and _value(change, "newValue") == "14C"
            for change in changes
        )
        assert not any(
            change.get("changeType") in {"PASSENGER_ADDED", "PASSENGER_REMOVED"}
            for change in changes
        )

        feed = get_activity_feed(date=FLIGHT["departureDate"], limit=50, categories=None)
        for last_name, pnr in (("TRAVELER", "ABC123"), ("CREW", "")):
            recorded = _thru_changes(changes, last_name)[0]
            visible = [
                event
                for event in feed["events"]
                if event.get("flightNumber") == FLIGHT["flightNumber"]
                and event.get("eventType") == recorded["changeType"]
                and (event.get("passenger") or {}).get("lastName") == last_name
                and (event.get("passenger") or {}).get("pnr") == pnr
            ]
            assert len(visible) == 1
            assert recorded["oldValue"] in visible[0]["description"]
            assert recorded["newValue"] in visible[0]["description"]

        timeline = get_flight_timeline(
            FLIGHT["flightNumber"],
            origin=FLIGHT["origin"],
            date=FLIGHT["departureDate"],
        )
        thru_events = [
            event
            for event in timeline["events"]
            if (event.get("details") or {}).get("field") == "isThru"
        ]
        assert {
            (
                (event.get("passenger") or {}).get("lastName"),
                (event.get("details") or {}).get("oldValue"),
                (event.get("details") or {}).get("newValue"),
            )
            for event in thru_events
        } >= {
            ("TRAVELER", "False", "True"),
            ("CREW", "False", "True"),
        }

        passenger_timeline = get_passenger_timeline(
            FLIGHT["flightNumber"],
            "ABC123",
            date=FLIGHT["departureDate"],
            origin=FLIGHT["origin"],
        )
        assert any(
            event["details"]["field"] == "isThru"
            and event["details"]["oldValue"] == "False"
            and event["details"]["newValue"] == "True"
            for event in passenger_timeline["events"]
        )


def test_thru_indicator_turning_off_is_stored_in_history():
    before_xml, after_xml, turned_off = _onward_leg_pair()
    with _history_db():
        _ingest(FLIGHT, before_xml)
        _ingest(FLIGHT, after_xml)
        third = _ingest(FLIGHT, turned_off)
        assert third["isDuplicate"] is False
        changes = _history(FLIGHT)
        offs = [
            change
            for change in _thru_changes(changes, "TRAVELER")
            if _value(change, "oldValue") == "True"
            and _value(change, "newValue") == "False"
        ]
        assert len(offs) == 1, (
            "history has no isThru change after ThruIndicator turned back off"
        )
        assert not [
            change
            for change in _thru_changes(changes, "CREW")
            if _value(change, "newValue") == "False"
        ]
        timeline = get_passenger_timeline(
            FLIGHT["flightNumber"],
            "ABC123",
            date=FLIGHT["departureDate"],
            origin=FLIGHT["origin"],
        )
        assert any(
            event["details"]["field"] == "isThru"
            and event["details"]["oldValue"] == "True"
            and event["details"]["newValue"] == "False"
            for event in timeline["events"]
        )


def test_equivalent_thru_text_does_not_record_a_change():
    """'True' and 'true' are the same indicator after conversion."""
    flight = dict(FLIGHT, flightNumber="9002")
    first = _manifest_xml(
        [
            _passenger_xml(
                last_name="TRAVELER",
                first_name="CONNECTING",
                thru="True",
                pnr="ABC123",
            )
        ]
    )
    second = _manifest_xml(
        [
            _passenger_xml(
                last_name="TRAVELER",
                first_name="CONNECTING",
                thru="true",
                pnr="ABC123",
            )
        ]
    )
    with _history_db() as database:
        _ingest(flight, first)
        follow_up = _ingest(flight, second)
        assert follow_up["isDuplicate"] is False
        assert _passenger(_stored_passengers(database, flight), "TRAVELER")["isThru"] is True
        assert follow_up["changesStored"] == 0


def test_check_in_and_thru_indicator_are_both_recorded():
    flight = dict(FLIGHT, flightNumber="9003")
    before = _manifest_xml(
        [
            _passenger_xml(
                last_name="TRAVELER",
                first_name="CONNECTING",
                thru="false",
                pnr="ABC123",
                checked_in="false",
            )
        ]
    )
    after = _manifest_xml(
        [
            _passenger_xml(
                last_name="TRAVELER",
                first_name="CONNECTING",
                thru="true",
                pnr="ABC123",
                checked_in="true",
            )
        ]
    )
    with _history_db():
        _ingest(flight, before)
        _ingest(flight, after)
        changes = _history(flight)
        assert any(change.get("changeType") == "CHECKED_IN" for change in changes)
        thru = _thru_changes(changes, "TRAVELER")
        assert len(thru) == 1, (
            "a check-in change was stored but the thru-indicator change was not"
        )
        assert _value(thru[0], "oldValue") == "False"
        assert _value(thru[0], "newValue") == "True"
        assert thru[0]["changeType"] != "CHECKED_IN"


if __name__ == "__main__":
    test_thru_indicator_transition_is_stored_in_history()
    test_thru_indicator_turning_off_is_stored_in_history()
    test_equivalent_thru_text_does_not_record_a_change()
    test_check_in_and_thru_indicator_are_both_recorded()
    print("ALL TESTS PASS")
