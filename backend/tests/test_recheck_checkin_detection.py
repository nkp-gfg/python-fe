"""Recheck-in change detection.

Passenger manifests already store checkInSequence, checkInDate, and checkInTime.
Change detection must record a change when any of those stored values differ
between two passenger-list snapshots, including when isCheckedIn stays true.

These tests fail while that comparison is missing. They drive the ingestion
pipeline used in production:

  SabreClient._parse_xml / _extract_body
  -> feeder.runner._process_api_call
  -> convert_passenger_list
  -> storage.store_snapshot / get_previous_snapshot
  -> detect_changes
  -> storage.store_changes
  -> passenger timeline, flight timeline, and activity feed

Not covered here:
- Trip-report checkInNumber. convert_trip_report stores it, and run_feeder
  never calls detect_changes for trip reports.
- The four display-code merge in run_feeder. _pax_dedup_key keeps the first
  copy of a traveler and does not compare CheckIn_Info inside one ingest.
- The boarding-progress chart. It counts CHECKED_IN events and does not show
  a check-in sequence. A later sequence for someone already checked in must
  not increase that headcount.

Run:
  PYTHONPATH=. python -m pytest backend/tests/test_recheck_checkin_detection.py -q
"""

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException

import backend.api.database as api_database
import backend.feeder.storage as storage
from backend.api.routes.changes import (
    get_activity_feed,
    get_flight_timeline,
    get_passenger_timeline,
)
from backend.feeder.converter import convert_passenger_list
from backend.feeder.runner import _process_api_call
from backend.sabre.client import SabreClient

SAMPLE_LIST = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)

CHECKIN_FIELDS = ("checkInSequence", "checkInDate", "checkInTime")


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


def _path_present(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _matches(doc, query):
    for key, expected in (query or {}).items():
        value, present = _path_present(doc, key)
        if isinstance(expected, dict) and any(
            op in expected for op in ("$exists", "$lte", "$in")
        ):
            if "$exists" in expected and bool(expected["$exists"]) != present:
                return False
            if "$lte" in expected and (not present or value > expected["$lte"]):
                return False
            if "$in" in expected and (not present or value not in expected["$in"]):
                return False
            continue
        if value != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return dict(doc)
    if any(flag == 0 for flag in projection.values()):
        projected = dict(doc)
        for key, flag in projection.items():
            if flag == 0:
                projected.pop(key, None)
        return projected
    projected = {}
    if projection.get("_id", 1) != 0 and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key, flag in projection.items():
        if key == "_id" or not flag or "." in key:
            continue
        if key in doc:
            projected[key] = doc[key]
    return projected


def _sort_docs(docs, sort):
    if not sort:
        return docs
    if isinstance(sort, str):
        keys = [(sort, 1)]
    else:
        keys = list(sort)

    ordered = list(docs)
    for key, direction in reversed(keys):
        ordered.sort(
            key=lambda doc, key=key: _path_present(doc, key)[0],
            reverse=direction in (-1, "desc", "descending"),
        )
    return ordered


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=None):
        if direction is None:
            self._docs = _sort_docs(self._docs, key)
        else:
            self._docs = _sort_docs(self._docs, [(key, direction)])
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = f"id-{len(self.docs) + 1}"
        self.docs.append(doc)
        return _InsertResult(doc["_id"])

    def insert_many(self, docs):
        return _InsertManyResult([self.insert_one(doc).inserted_id for doc in docs])

    def find_one(self, query=None, projection=None, sort=None):
        matches = _sort_docs(
            [doc for doc in self.docs if _matches(doc, query)], sort
        )
        if not matches:
            return None
        return _project(matches[0], projection)

    def find(self, query=None, projection=None, sort=None):
        matches = [
            _project(doc, projection) for doc in self.docs if _matches(doc, query)
        ]
        return _Cursor(_sort_docs(matches, sort))

    def update_one(self, query, update, upsert=False):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if matches:
            target = matches[0]
            created = False
        elif upsert:
            target = {}
            self.docs.append(target)
            created = True
        else:
            return
        if created:
            for key, value in update.get("$setOnInsert", {}).items():
                target[key] = deepcopy(value)
        for key, value in update.get("$set", {}).items():
            target[key] = deepcopy(value)
        for key, value in update.get("$inc", {}).items():
            target[key] = target.get(key, 0) + value


class _MemoryDatabase:
    """In-memory stand-in for the Mongo collections this pipeline writes.

    The check-in values are chosen by convert_passenger_list and
    diff_passenger_list. This object only stores and returns those documents
    with the filters, sorts, projections, and upserts those two modules use.
    """

    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection())

    def command(self, *_args, **_kwargs):
        return {"ok": 1}


@pytest.fixture
def db():
    memory = _MemoryDatabase()
    storage.init_db(memory)
    api_database._db = memory
    yield memory
    storage._db = None
    storage._owns_connection = False
    api_database._db = None


def _flight(number="2057"):
    return {
        "airline": "GF",
        "flightNumber": number,
        "origin": "BOM",
        "departureDate": "2026-03-19",
    }


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


def _passenger_xml(
    *,
    last,
    first,
    line,
    pnr="ABC123",
    checked_in=True,
    number="4",
    checkin_date="2026-03-18",
    checkin_time="12:53",
    seat="22A",
):
    status = "true" if checked_in else "false"
    locator = f"<PNRLocator>{pnr}</PNRLocator>" if pnr is not None else ""
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          {locator}
          <LineNumber>{line}</LineNumber>
          <BookingClass>V</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>{seat}</Seat>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <CheckIn_Info>
            <CheckInNumber>{number}</CheckInNumber>
            <CheckInDate>{checkin_date}</CheckInDate>
            <CheckInTime>{checkin_time}</CheckInTime>
            <CheckInStatus>{status}</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
        </PassengerInfo>
    """


def _parse_passenger_list(xml_text):
    """Same two calls get_passenger_list makes after Sabre returns XML."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _ingest(db, xml_text, flight=None):
    del db  # bound by the fixture; the pipeline reads storage.get_db()
    flight = flight or _flight()
    raw = _parse_passenger_list(xml_text)
    meta = {"requestXml": "<GetPassengerListRQ/>", "httpStatus": 200, "durationMs": 1}
    return _process_api_call(
        "PassengerList",
        "passenger_list",
        flight,
        raw,
        "<GetPassengerListRS/>",
        meta,
        convert_passenger_list,
        (
            raw,
            flight["airline"],
            flight["flightNumber"],
            flight["departureDate"],
            flight["origin"],
        ),
    )


def _snapshots(db, flight):
    cursor = db["snapshots"].find(
        {
            "flightNumber": flight["flightNumber"],
            "origin": flight["origin"],
            "departureDate": flight["departureDate"],
            "snapshotType": "passenger_list",
        }
    ).sort("sequenceNumber", 1)
    return list(cursor)


def _stored_passenger(snapshot, *, pnr=None, last=None):
    for passenger in snapshot["data"]["passengers"]:
        if pnr is not None and passenger.get("pnr") == pnr:
            return passenger
        if last is not None and passenger.get("lastName") == last:
            return passenger
    raise AssertionError(f"passenger pnr={pnr!r} last={last!r} missing from snapshot")


def _changes(db, flight):
    return list(
        db["changes"].find(
            {
                "flightNumber": flight["flightNumber"],
                "origin": flight["origin"],
                "departureDate": flight["departureDate"],
            }
        )
    )


def _has_field_change(changes, *, pnr, last, field, old, new):
    for change in changes:
        passenger = change.get("passenger") or {}
        if pnr is not None and passenger.get("pnr") != pnr:
            continue
        if last is not None and passenger.get("lastName") != last:
            continue
        if change.get("field") != field:
            continue
        if str(change.get("oldValue")) == str(old) and str(change.get("newValue")) == str(new):
            return True
    return False


def _assert_stored_transition(db, flight, *, pnr=None, last=None, field):
    snaps = _snapshots(db, flight)
    assert len(snaps) >= 2
    before = _stored_passenger(snaps[-2], pnr=pnr, last=last)
    after = _stored_passenger(snaps[-1], pnr=pnr, last=last)
    assert before[field] != after[field], (
        f"{field} did not change in the stored snapshots "
        f"({before[field]!r} -> {after[field]!r})"
    )
    assert before.get("isCheckedIn") is True
    assert after.get("isCheckedIn") is True
    assert _has_field_change(
        _changes(db, flight),
        pnr=pnr,
        last=last,
        field=field,
        old=before[field],
        new=after[field],
    ), (
        f"no change records {field} {before[field]!r} -> {after[field]!r} "
        f"for pnr={pnr!r} last={last!r}"
    )
    return before[field], after[field]


def _mentions_transition(text, old, new):
    text = text or ""
    old_s, new_s = str(old), str(new)
    return (
        f"{old_s} → {new_s}" in text
        or f"{old_s} -> {new_s}" in text
        or (f"from {old_s}" in text and f"to {new_s}" in text)
    )


def test_sequence_recheck_is_stored_while_checked_in_flag_stays_true(db):
    flight = _flight("3101")
    first = _envelope(
        _passenger_xml(last="SMITH", first="JOHN", pnr="ABC123", line=1, number="4", seat="22A")
        + _passenger_xml(
            last="CREW", first="ANN", pnr=None, line=9, number="2", seat="2A"
        )
        + _passenger_xml(
            last="JONES", first="MARY", pnr="NEW999", line=3,
            checked_in=False, number="", checkin_date="", checkin_time="", seat="14C",
        )
    )
    second = _envelope(
        _passenger_xml(last="SMITH", first="JOHN", pnr="ABC123", line=1, number="48", seat="22A")
        + _passenger_xml(
            last="CREW", first="ANN", pnr=None, line=9, number="7", seat="2A"
        )
        + _passenger_xml(
            last="JONES", first="MARY", pnr="NEW999", line=3,
            checked_in=True, number="1", checkin_date="2026-03-19",
            checkin_time="04:10", seat="14C",
        )
    )

    first_result = _ingest(db, first, flight)
    second_result = _ingest(db, second, flight)

    assert first_result["isDuplicate"] is False
    assert second_result["isDuplicate"] is False
    assert second_result["changesStored"] >= 1

    _assert_stored_transition(
        db, flight, pnr="ABC123", field="checkInSequence"
    )
    _assert_stored_transition(
        db, flight, last="CREW", field="checkInSequence"
    )

    changes = _changes(db, flight)
    assert _has_field_change(
        changes, pnr="NEW999", last=None, field="isCheckedIn", old=False, new=True
    )
    assert any(
        change.get("changeType") == "CHECKED_IN"
        and (change.get("passenger") or {}).get("pnr") == "NEW999"
        for change in changes
    )
    # The traveler whose sequence did not move must not gain a sequence event.
    assert not any(
        change.get("field") == "checkInSequence"
        and (change.get("passenger") or {}).get("pnr") == "NEW999"
        and str(change.get("oldValue")) not in {"", "0", "None"}
        for change in changes
    )


def test_checkin_date_recheck_is_stored_when_sequence_and_status_stay_the_same(db):
    flight = _flight("3102")
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        number="4", checkin_date="2026-03-18", checkin_time="12:53",
    )), flight)
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        number="4", checkin_date="2026-03-19", checkin_time="12:53",
    )), flight)
    _assert_stored_transition(db, flight, pnr="ABC123", field="checkInDate")
    assert not any(
        change.get("changeType") == "CHECKED_IN" for change in _changes(db, flight)
    )


def test_checkin_time_recheck_is_stored_when_sequence_and_status_stay_the_same(db):
    flight = _flight("3103")
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        number="4", checkin_date="2026-03-18", checkin_time="12:53",
    )), flight)
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        number="4", checkin_date="2026-03-18", checkin_time="13:05",
    )), flight)
    _assert_stored_transition(db, flight, pnr="ABC123", field="checkInTime")
    assert not any(
        change.get("changeType") == "CHECKED_IN" for change in _changes(db, flight)
    )


def test_sequence_date_and_time_recheck_are_all_stored_together(db):
    flight = _flight("3104")
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        number="4", checkin_date="2026-03-18", checkin_time="12:53", seat="22A",
    )), flight)
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        number="48", checkin_date="2026-03-19", checkin_time="04:30", seat="11F",
    )), flight)

    for field in CHECKIN_FIELDS:
        _assert_stored_transition(db, flight, pnr="ABC123", field=field)
    assert any(
        change.get("changeType") == "SEAT_CHANGE"
        and (change.get("passenger") or {}).get("pnr") == "ABC123"
        for change in _changes(db, flight)
    )


def test_unchanged_checkin_fields_do_not_emit_a_checkin_change(db, monkeypatch):
    flight = _flight("3105")
    stamps = iter(["2026-03-19T00:00:01Z", "2026-03-19T00:00:02Z"])
    monkeypatch.setattr(
        "backend.feeder.converter._now_iso", lambda: next(stamps)
    )
    xml = _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        number="4", checkin_date="2026-03-18", checkin_time="12:53",
    ))
    first = _ingest(db, xml, flight)
    second = _ingest(db, xml, flight)
    assert first["isDuplicate"] is False
    assert second["isDuplicate"] is False

    snaps = _snapshots(db, flight)
    before = _stored_passenger(snaps[0], pnr="ABC123")
    after = _stored_passenger(snaps[1], pnr="ABC123")
    for field in CHECKIN_FIELDS:
        assert before[field] == after[field]
    assert not any(
        change.get("field") in CHECKIN_FIELDS or change.get("changeType") == "CHECKED_IN"
        for change in _changes(db, flight)
    )


def test_first_checkin_still_records_checked_in_and_the_new_checkin_values(db):
    flight = _flight("3106")
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        checked_in=False, number="", checkin_date="", checkin_time="",
    )), flight)
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1,
        checked_in=True, number="4", checkin_date="2026-03-18", checkin_time="12:53",
    )), flight)

    changes = _changes(db, flight)
    assert any(
        change.get("changeType") == "CHECKED_IN"
        and (change.get("passenger") or {}).get("pnr") == "ABC123"
        for change in changes
    )
    snaps = _snapshots(db, flight)
    before = _stored_passenger(snaps[0], pnr="ABC123")
    after = _stored_passenger(snaps[1], pnr="ABC123")
    assert before["isCheckedIn"] is False
    assert after["isCheckedIn"] is True
    for field in CHECKIN_FIELDS:
        assert before[field] != after[field]
        assert _has_field_change(
            changes, pnr="ABC123", last=None, field=field,
            old=before[field], new=after[field],
        )


def test_passenger_timeline_shows_the_new_checkin_sequence(db):
    flight = _flight("3107")
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1, number="4",
    )), flight)
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1, number="48",
    )), flight)
    old, new = _assert_stored_transition(
        db, flight, pnr="ABC123", field="checkInSequence"
    )

    try:
        timeline = get_passenger_timeline(
            flight["flightNumber"], "ABC123",
            date=flight["departureDate"], origin=flight["origin"],
        )
    except HTTPException as exc:
        pytest.fail(f"passenger timeline omitted the recheck: {exc.detail}")

    assert any(
        (event.get("details") or {}).get("field") == "checkInSequence"
        and str((event.get("details") or {}).get("oldValue")) == str(old)
        and str((event.get("details") or {}).get("newValue")) == str(new)
        for event in timeline["events"]
    )


def test_flight_timeline_shows_the_new_checkin_sequence(db):
    flight = _flight("3108")
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1, number="4",
    )), flight)
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1, number="48",
    )), flight)
    old, new = _assert_stored_transition(
        db, flight, pnr="ABC123", field="checkInSequence"
    )

    try:
        timeline = get_flight_timeline(
            flight["flightNumber"], origin=flight["origin"], date=flight["departureDate"],
        )
    except HTTPException as exc:
        pytest.fail(f"flight timeline omitted the recheck: {exc.detail}")

    assert any(
        (event.get("details") or {}).get("field") == "checkInSequence"
        and str((event.get("details") or {}).get("oldValue")) == str(old)
        and str((event.get("details") or {}).get("newValue")) == str(new)
        and (event.get("passenger") or {}).get("pnr") == "ABC123"
        for event in timeline["events"]
    )


def test_activity_feed_records_the_new_checkin_sequence(db):
    flight = _flight("3109")
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1, number="4",
    )), flight)
    _ingest(db, _envelope(_passenger_xml(
        last="SMITH", first="JOHN", pnr="ABC123", line=1, number="48",
    )), flight)
    old, new = _assert_stored_transition(
        db, flight, pnr="ABC123", field="checkInSequence"
    )

    feed = get_activity_feed(
        date=flight["departureDate"], limit=50, categories=None
    )
    assert any(
        (event.get("passenger") or {}).get("pnr") == "ABC123"
        and _mentions_transition(event.get("description"), old, new)
        for event in feed["events"]
    )


def test_sample_manifest_recheck_sequence_is_recorded(db):
    """The captured GF2057 list has CheckInStatus true and CheckInNumber 4.

    A second ingest of that same list with only that number changed must
    produce a checkInSequence change for the traveler who held number 4.
    """
    original = SAMPLE_LIST.read_text(encoding="utf-8")
    old_number = "<CheckInNumber>4</CheckInNumber>"
    assert original.count(old_number) == 1
    updated = original.replace(old_number, "<CheckInNumber>49</CheckInNumber>", 1)

    flight = _flight("2057")
    _ingest(db, original, flight)
    second = _ingest(db, updated, flight)
    assert second["isDuplicate"] is False

    snaps = _snapshots(db, flight)
    before = _stored_passenger(snaps[0], pnr="ESMMQK")
    after = _stored_passenger(snaps[1], pnr="ESMMQK")
    assert before["isCheckedIn"] is True
    assert after["isCheckedIn"] is True
    assert before["checkInSequence"] == 4
    assert after["checkInSequence"] == 49
    assert before["checkInDate"] == after["checkInDate"]
    assert before["checkInTime"] == after["checkInTime"]
    assert _has_field_change(
        _changes(db, flight),
        pnr="ESMMQK",
        last="BABOO",
        field="checkInSequence",
        old=4,
        new=49,
    )
