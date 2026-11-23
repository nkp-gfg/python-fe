"""Seniority-date changes must be recorded on the passenger timeline.

These tests exercise the ingestion path the product runs after Sabre returns
GetPassengerListRS: parse the SOAP body, merge the passenger-list calls, convert
the manifest, store the snapshot, and diff it against the previous snapshot.

They fail while passenger change detection ignores seniorityDate. A fix should
record a change whose field is seniorityDate, with the old and new dates, on
the matching passenger so the passenger timeline shows it.
"""

import copy
import os
import re
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.audit_rules import rule_upgrade_queue_skipped
from backend.api.routes import audit as audit_routes
from backend.api.routes import changes as changes_routes
from backend.api.routes import passengers as passengers_routes
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient, SabreError
import backend.feeder.converter as converter
import backend.feeder.runner as runner

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GF2006_XML = os.path.join(
    REPO_ROOT,
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2006_LHR_2026-03-18.xml",
)
GF2056_XML = os.path.join(
    REPO_ROOT,
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2056_DMM_2026-03-16.xml",
)

META = {
    "requestXml": "<GetPassengerListRQ/>",
    "httpStatus": 200,
    "durationMs": 1,
    "sessionToken": None,
    "conversationId": None,
}


def _load_manifest(path):
    """Load a captured GetPassengerListRS without keeping the session token."""
    xml = open(path, encoding="utf-8").read()
    return re.sub(
        r"(<wsse:BinarySecurityToken[^>]*>)[^<]*(</wsse:BinarySecurityToken>)",
        r"\1placeholder\2",
        xml,
        count=1,
    )


def _parse_passenger_list(xml):
    """Parse SOAP the same way SabreClient.get_passenger_list does."""
    parsed = SabreClient._parse_xml(xml)
    extractor = SabreClient.__new__(SabreClient)
    return extractor._extract_body(parsed, "GetPassengerListRS")


class _Clock:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"2026-03-18T10:{self.n:02d}:00Z"


def _get_path(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _match(doc, filt):
    for key, expected in (filt or {}).items():
        actual, found = _get_path(doc, key)
        if isinstance(expected, dict):
            if not found:
                return False
            for op, operand in expected.items():
                if op == "$lte":
                    if actual is None or not actual <= operand:
                        return False
                elif op == "$in":
                    if actual not in operand:
                        return False
                else:
                    raise AssertionError(f"unsupported operator {op}")
            continue
        if not found or actual != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return copy.deepcopy(doc)
    if any(value == 0 for value in projection.values()):
        projected = copy.deepcopy(doc)
        for key, value in projection.items():
            if value == 0:
                projected.pop(key, None)
        return projected
    projected = {}
    if projection.get("_id", 1) != 0 and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key, value in projection.items():
        if key == "_id" or not value or key not in doc:
            continue
        projected[key] = copy.deepcopy(doc[key])
    return projected


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, str):
            specs = [(key, 1 if direction is None else direction)]
        else:
            specs = list(key)
        for field, order in reversed(specs):
            self.docs.sort(
                key=lambda doc, field=field: (
                    doc.get(field) is None,
                    "" if doc.get(field) is None else doc.get(field),
                ),
                reverse=order < 0,
            )
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class _Collection:
    def __init__(self):
        self.docs = []
        self._next_id = 1

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = self._next_id
        self._next_id += 1
        self.docs.append(stored)

        class Result:
            inserted_id = stored["_id"]

        return Result()

    def insert_many(self, docs):
        ids = []
        for doc in docs:
            ids.append(self.insert_one(doc).inserted_id)

        class Result:
            inserted_ids = ids

        return Result()

    def find(self, filt=None, projection=None, sort=None, **kwargs):
        docs = [
            _project(doc, projection)
            for doc in self.docs
            if _match(doc, filt)
        ]
        cursor = _Cursor(docs)
        if sort is not None:
            cursor.sort(sort)
        return cursor

    def find_one(self, filt=None, projection=None, sort=None, **kwargs):
        matches = [
            _project(doc, projection)
            for doc in self.docs
            if _match(doc, filt)
        ]
        if sort:
            matches = list(_Cursor(matches).sort(sort))
        return matches[0] if matches else None

    def update_one(self, filt, update, upsert=False):
        matches = [doc for doc in self.docs if _match(doc, filt)]
        if not matches:
            if not upsert:
                return
            created = copy.deepcopy(update.get("$setOnInsert", {}))
            created.update(copy.deepcopy(update.get("$set", {})))
            for key, amount in update.get("$inc", {}).items():
                created[key] = created.get(key, 0) + amount
            created["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(created)
            return
        target = matches[0]
        for key, value in update.get("$set", {}).items():
            target[key] = copy.deepcopy(value)
        for key, amount in update.get("$inc", {}).items():
            target[key] = target.get(key, 0) + amount


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


class _ScriptedSabre:
    """Stand-in for the Sabre HTTP call only.

    Parsing still goes through SabreClient._parse_xml and _extract_body, which
    is the transformation get_passenger_list applies to a SOAP response.
    """

    def __init__(self, holder):
        self.holder = holder

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, *args, **kwargs):
        raise SabreError("flight status is not part of this test")

    def get_passenger_list(self, *args, **kwargs):
        xml = self.holder["xml"]
        return _parse_passenger_list(xml), xml, dict(META)

    def get_reservations(self, *args, **kwargs):
        raise SabreError("reservations are not part of this test")

    def get_trip_report(self, *args, **kwargs):
        raise SabreError("trip reports are not part of this test")

    def verify_flight_details(self, *args, **kwargs):
        raise SabreError("schedule is not part of this test")


@pytest.fixture
def db(monkeypatch):
    database = _Database()
    storage._client = None
    storage._db = None
    storage._owns_connection = False
    storage.init_db(database)
    monkeypatch.setattr(changes_routes, "get_db", lambda: database)
    monkeypatch.setattr(passengers_routes, "get_db", lambda: database)
    monkeypatch.setattr(audit_routes, "get_db", lambda: database)
    monkeypatch.setattr(converter, "_now_iso", _Clock())
    holder = {"xml": ""}
    monkeypatch.setattr(runner, "SabreClient", lambda: _ScriptedSabre(holder))
    yield database, holder
    storage._db = None
    storage._client = None
    storage._owns_connection = False


def _flight(**overrides):
    payload = {
        "airline": "GF",
        "flightNumber": "2006",
        "origin": "LHR",
        "departureDate": "2026-03-18",
        "departureDateTime": "2026-03-18T21:50:00",
    }
    payload.update(overrides)
    return payload


def _ingest_pair(holder, before_xml, after_xml, flight):
    """Run the feeder twice, the same entry ingest_flight uses."""
    holder["xml"] = before_xml
    first = run_feeder([flight])
    holder["xml"] = after_xml
    second = run_feeder([flight])
    return first, second


def _passenger_list_result(feeder_result):
    return feeder_result["results"][0]["apis"]["passengerList"]


def _latest_passengers(database, flight):
    doc = database["passenger_list"].find_one(
        {
            "flightNumber": flight["flightNumber"],
            "origin": flight["origin"],
            "departureDate": flight["departureDate"],
        },
        sort=[("fetchedAt", -1)],
    )
    assert doc is not None
    return doc["passengers"]


def _changes(database, flight):
    return list(database["changes"].find(
        {
            "flightNumber": flight["flightNumber"],
            "origin": flight["origin"],
            "departureDate": flight["departureDate"],
        }
    ))


def _seniority_changes(changes, pnr=None, last_name=None):
    matched = []
    for change in changes:
        if change.get("field") != "seniorityDate":
            continue
        passenger = change.get("passenger") or {}
        if pnr is not None and passenger.get("pnr") != pnr:
            continue
        if last_name is not None and passenger.get("lastName") != last_name:
            continue
        matched.append(change)
    return matched


def _assert_seniority_event(database, flight, pnr, old, new, last_name=None):
    changes = _changes(database, flight)
    matched = _seniority_changes(changes, pnr=pnr, last_name=last_name)
    described = [
        (
            change.get("changeType"),
            change.get("field"),
            change.get("oldValue"),
            change.get("newValue"),
            (change.get("passenger") or {}).get("pnr"),
            (change.get("passenger") or {}).get("lastName"),
        )
        for change in changes
    ]
    assert matched, (
        "expected a seniorityDate change "
        f"for pnr={pnr!r} last={last_name!r} {old!r} -> {new!r}, "
        f"stored changes were {described}"
    )
    assert matched[0]["oldValue"] == old
    assert matched[0]["newValue"] == new
    assert matched[0].get("passenger", {}).get("pnr") == pnr

    timeline = changes_routes.get_passenger_timeline(
        flight["flightNumber"],
        pnr,
        date=flight["departureDate"],
        origin=flight["origin"],
    )
    events = [
        event for event in timeline["events"]
        if (event.get("details") or {}).get("field") == "seniorityDate"
        and (event.get("details") or {}).get("oldValue") == old
        and (event.get("details") or {}).get("newValue") == new
    ]
    assert events, timeline["events"]

    flight_timeline = changes_routes.get_flight_timeline(
        flight["flightNumber"],
        origin=flight["origin"],
        date=flight["departureDate"],
    )
    flight_events = [
        event for event in flight_timeline["events"]
        if (event.get("details") or {}).get("field") == "seniorityDate"
        and (event.get("passenger") or {}).get("pnr") == pnr
        and (event.get("details") or {}).get("oldValue") == old
        and (event.get("details") or {}).get("newValue") == new
    ]
    assert flight_events, flight_timeline["events"]


def _passenger_xml(
    last_name,
    first_name,
    *,
    pnr=None,
    line="1",
    seniority=None,
    priority="B01",
    cabin="Y",
    booking_class="Y",
    desired="",
    seat="",
    passenger_id="PAX1",
):
    pnr_xml = (
        f"<PNRLocator nameAssociationID=\"1\">{pnr}</PNRLocator>"
        if pnr is not None else ""
    )
    seniority_xml = (
        f"<SeniorityDate>{seniority}</SeniorityDate>" if seniority is not None else ""
    )
    desired_xml = (
        f"<DesiredBookingClass>{desired}</DesiredBookingClass>" if desired else ""
    )
    seat_xml = f"<Seat>{seat}</Seat>" if seat else ""
    priority_xml = f"<PriorityCode>{priority}</PriorityCode>" if priority else ""
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>{first_name}</FirstName>
          </Name_Details>
          {pnr_xml}
          <PassengerID>{passenger_id}</PassengerID>
          <LineNumber>{line}</LineNumber>
          {priority_xml}
          <BookingClass>{booking_class}</BookingClass>
          {desired_xml}
          <Cabin>{cabin}</Cabin>
          {seat_xml}
          <Destination>DMM</Destination>
          <PassengerType>P</PassengerType>
          <BagCount>0</BagCount>
          {seniority_xml}
          <Indicators><Indicator>NotBoarded</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
        </PassengerInfo>
    """


def _envelope(body, flight_number="2006", origin="LHR", departure_date="2026-03-18"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{flight_number}</Flight>
          <DepartureDate>{departure_date}</DepartureDate>
          <Origin>{origin}</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def test_gf2006_sample_seniority_move_is_recorded(db):
    """The committed GF2006 list stores 2022-11-01 for OWNPDE.

    A later list that changes only that SeniorityDate must produce a timeline
    event. This traveler has no PriorityCode, so the standby queue does not
    include them; the missed event is still on their history.
    """
    database, holder = db
    flight = _flight()
    before = _load_manifest(GF2006_XML)
    assert before.count("<SeniorityDate>2022-11-01</SeniorityDate>") == 1
    after = before.replace(
        "<SeniorityDate>2022-11-01</SeniorityDate>",
        "<SeniorityDate>2010-01-15</SeniorityDate>",
        1,
    )

    parsed = _parse_passenger_list(before)
    converted = converter.convert_passenger_list(
        parsed, "GF", "2006", "2026-03-18", "LHR")
    original = next(p for p in converted["passengers"] if p["pnr"] == "OWNPDE")
    assert original["lastName"] == "ALANSARI"
    assert original["seniorityDate"] == "2022-11-01"
    assert original["priorityCode"] == ""

    _ingest_pair(holder, before, after, flight)

    latest = _latest_passengers(database, flight)
    moved = next(p for p in latest if p["pnr"] == "OWNPDE")
    assert moved["seniorityDate"] == "2010-01-15"
    assert moved["cabin"] == original["cabin"]
    assert moved["seat"] == original["seat"]
    assert moved["bookingClass"] == original["bookingClass"]

    standby = passengers_routes.get_standby_list(
        flight["flightNumber"],
        origin=flight["origin"],
        date=flight["departureDate"],
        snapshot_sequence=None,
    )
    standby_pnrs = [p["pnr"] for p in standby["standby"]["passengers"]]
    assert "OWNPDE" not in standby_pnrs

    _assert_seniority_event(
        database, flight, "OWNPDE", "2022-11-01", "2010-01-15",
        last_name="ALANSARI",
    )


def test_standby_queue_reorder_is_recorded(db):
    """GF2056 staff with priority B3 are ordered by seniorityDate.

    Moving ALBAHRI from 2009-08-16 to 2026-01-01 places ABDULLA ahead of them
    on the standby queue. The timeline has to record that date change.
    """
    database, holder = db
    flight = _flight(
        flightNumber="2056",
        origin="DMM",
        departureDate="2026-03-16",
        departureDateTime="2026-03-16T09:30:00",
    )
    before = _load_manifest(GF2056_XML)
    assert before.count("<SeniorityDate>2009-08-16</SeniorityDate>") == 1
    after = before.replace(
        "<SeniorityDate>2009-08-16</SeniorityDate>",
        "<SeniorityDate>2026-01-01</SeniorityDate>",
        1,
    )

    holder["xml"] = before
    run_feeder([flight])
    before_order = [
        p["pnr"] for p in passengers_routes.get_standby_list(
            flight["flightNumber"],
            origin=flight["origin"],
            date=flight["departureDate"],
            snapshot_sequence=None,
        )["standby"]["passengers"]
    ]
    assert before_order.index("MYHANC") < before_order.index("ELYJWM")

    holder["xml"] = after
    run_feeder([flight])
    after_order = [
        p["pnr"] for p in passengers_routes.get_standby_list(
            flight["flightNumber"],
            origin=flight["origin"],
            date=flight["departureDate"],
            snapshot_sequence=None,
        )["standby"]["passengers"]
    ]
    assert after_order.index("ELYJWM") < after_order.index("MYHANC")

    latest = _latest_passengers(database, flight)
    moved = next(p for p in latest if p["pnr"] == "MYHANC")
    assert moved["seniorityDate"] == "2026-01-01"
    assert moved["priorityCode"] == "B3"

    _assert_seniority_event(
        database, flight, "MYHANC", "2009-08-16", "2026-01-01",
        last_name="ALBAHRI",
    )


def test_upgrade_skip_audit_uses_new_seniority_and_history_records_it(db):
    """Skipped-upgrade ranking reads the latest seniorityDate.

    The earlier staff member's date moves from 2000-01-01 to 2020-01-01 while
    a later colleague is upgraded. The audit stops calling the first person
    skipped, and the timeline has to show the date that caused that.
    """
    database, holder = db
    flight = _flight(flightNumber="2110")
    early = dict(
        last_name="EARLY", first_name="STAFF", pnr="SKIP01", line="1",
        seniority="2000-01-01", desired="J", passenger_id="SKIP",
    )
    later = dict(
        last_name="LATER", first_name="STAFF", pnr="UPGR01", line="2",
        seniority="2010-01-01", desired="J", passenger_id="UPGR",
    )
    before = _envelope(
        _passenger_xml(**early) + _passenger_xml(**later),
        flight_number="2110",
    )
    early["seniority"] = "2020-01-01"
    later["cabin"] = "J"
    after = _envelope(
        _passenger_xml(**early) + _passenger_xml(**later),
        flight_number="2110",
    )

    _ingest_pair(holder, before, after, flight)
    changes = _changes(database, flight)
    latest = _latest_passengers(database, flight)

    with_old_date = copy.deepcopy(latest)
    for passenger in with_old_date:
        if passenger["pnr"] == "SKIP01":
            passenger["seniorityDate"] = "2000-01-01"
    alerts_if_old = rule_upgrade_queue_skipped(with_old_date, changes)
    assert any(
        alert["ruleId"] == "UPGRADE_QUEUE_SKIPPED" and alert["pnr"] == "SKIP01"
        for alert in alerts_if_old
    )

    alerts_now = rule_upgrade_queue_skipped(latest, changes)
    assert not any(
        alert["ruleId"] == "UPGRADE_QUEUE_SKIPPED" for alert in alerts_now
    )
    assert next(p["seniorityDate"] for p in latest if p["pnr"] == "SKIP01") == "2020-01-01"

    _assert_seniority_event(
        database, flight, "SKIP01", "2000-01-01", "2020-01-01",
        last_name="EARLY",
    )


def test_added_cleared_and_pnrless_seniority_are_recorded(db):
    """Cover the other seniority transitions on a matched manifest row.

    A date can appear, disappear, or change for staff who have no PNR and are
    matched by line number. A seat change on the same row must still be recorded.
    """
    database, holder = db
    flight = _flight(flightNumber="2111")

    def body(added, cleared, staff_date, staff_seat):
        return _envelope(
            _passenger_xml(
                "ADDED", "DATE", pnr="ADD001", line="1", seniority=added,
                priority="B01", passenger_id="ADD",
            )
            + _passenger_xml(
                "CLEARED", "DATE", pnr="CLR001", line="2", seniority=cleared,
                priority="B01", passenger_id="CLR",
            )
            + _passenger_xml(
                "NOLINE", "STAFF", pnr=None, line="7", seniority=staff_date,
                priority="B02", seat=staff_seat, passenger_id="NOLINE",
            )
            + _passenger_xml(
                "SAME", "DATE", pnr="SAME01", line="4", seniority="2018-08-08",
                priority="B01", passenger_id="SAME",
            ),
            flight_number="2111",
        )

    before = body(None, "2019-09-09", "2011-05-05", "12A")
    after = body("2024-04-04", None, "2011-06-06", "14C")
    _ingest_pair(holder, before, after, flight)

    changes = _changes(database, flight)
    seat_changes = [
        change for change in changes
        if change.get("field") == "seat"
        and (change.get("passenger") or {}).get("lastName") == "NOLINE"
    ]
    assert seat_changes, [
        (c.get("field"), (c.get("passenger") or {}).get("lastName"))
        for c in changes
    ]
    assert seat_changes[0]["oldValue"] == "12A"
    assert seat_changes[0]["newValue"] == "14C"

    _assert_seniority_event(
        database, flight, "ADD001", "", "2024-04-04", last_name="ADDED")
    _assert_seniority_event(
        database, flight, "CLR001", "2019-09-09", "", last_name="CLEARED")
    _assert_seniority_event(
        database, flight, "", "2011-05-05", "2011-06-06", last_name="NOLINE")

    unchanged = _seniority_changes(changes, pnr="SAME01")
    assert unchanged == []


def test_unchanged_seniority_does_not_emit_a_change(db):
    database, holder = db
    flight = _flight(flightNumber="2112")
    xml = _envelope(
        _passenger_xml(
            "SAME", "DATE", pnr="SAME01", seniority="2018-08-08",
            passenger_id="SAME",
        ),
        flight_number="2112",
    )
    first, second = _ingest_pair(holder, xml, xml, flight)
    assert _passenger_list_result(first)["changesStored"] == 0
    assert _passenger_list_result(second)["isDuplicate"] is False
    assert _passenger_list_result(second)["changesStored"] == 0
    assert _seniority_changes(_changes(database, flight)) == []
    with pytest.raises(HTTPException) as caught:
        changes_routes.get_passenger_timeline(
            flight["flightNumber"],
            "SAME01",
            date=flight["departureDate"],
            origin=flight["origin"],
        )
    assert caught.value.status_code == 404
