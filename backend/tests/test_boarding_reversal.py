"""Boarding and check-in reversals must be stored and must lower published counts.

These tests follow a passenger from a Sabre GetPassengerList payload through the
same merge, conversion, snapshot diff, and change storage the feeder runs, then
read the boarding-progress, activity-feed, and flight-timeline endpoints.

They fail while a true-to-false isCheckedIn or isBoarded transition stores no
reversal and those endpoints keep counting historical CHECKED_IN and BOARDED
events. They pass once that transition stores CHECK_IN_REVERSED or OFFLOADED
and the published current counts and series follow the latest manifest flags,
including a series that can decrease.

Run from the repository root:

    python3 -m pytest backend/tests/test_boarding_reversal.py -vv
"""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")

import pytest

from backend.api.database import close_db, get_db
from backend.api.routes.changes import (
    get_activity_feed,
    get_boarding_progress,
    get_changes,
    get_flight_timeline,
)
from backend.feeder import storage
from backend.feeder.converter import convert_passenger_list
from backend.feeder.runner import (
    _extract_passenger_info_list,
    _pax_dedup_key,
    _process_api_call,
    _set_passenger_info_list,
)
from backend.sabre.client import SabreClient
import backend.feeder.differ as differ_mod

ORIGIN = "BAH"
DATE = "2026-03-19"
CHECK_IN_REVERSED = "CHECK_IN_REVERSED"
OFFLOADED = "OFFLOADED"


class _FrozenDateTime(datetime):
    """Clock for differ.detectedAt so each ingest sorts after the previous one."""

    current = datetime(2026, 3, 19, 8, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current


differ_mod.datetime = _FrozenDateTime


def _advance_clock(minutes=1):
    _FrozenDateTime.current = _FrozenDateTime.current + timedelta(minutes=minutes)


@pytest.fixture(autouse=True)
def _fresh_db():
    _FrozenDateTime.current = datetime(2026, 3, 19, 8, 0, 0, tzinfo=timezone.utc)
    close_db()
    storage.close()
    db = get_db()
    storage.init_db(db)
    db.client.drop_database(db.name)
    yield db


def _passenger_xml(pax):
    pnr_xml = ""
    if pax.get("pnr"):
        pnr_xml = f'<PNRLocator nameAssociationID="1">{pax["pnr"]}</PNRLocator>'
    checked = "true" if pax["checked_in"] else "false"
    boarded = "true" if pax["boarded"] else "false"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{pax["last"]}</LastName>
            <FirstName>{pax["first"]}</FirstName>
          </Name_Details>
          {pnr_xml}
          <PassengerID>{pax["id"]}</PassengerID>
          <LineNumber>{pax["line"]}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>10A</Seat>
          <Destination>BAH</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <CheckIn_Info><CheckInStatus>{checked}</CheckInStatus></CheckIn_Info>
          <Boarding_Info><BoardStatus>{boarded}</BoardStatus></Boarding_Info>
        </PassengerInfo>
    """


def _parse_passenger_list(passengers):
    """Parse a list body the way SabreClient.get_passenger_list does after HTTP."""
    inner = "".join(_passenger_xml(p) for p in passengers)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
      <soap-env:Body>
        <GetPassengerListRS>
          <ItineraryInfo>
            <Itinerary><Airline>GF</Airline><Flight>9100</Flight></Itinerary>
          </ItineraryInfo>
          <PassengerInfoList>{inner}</PassengerInfoList>
        </GetPassengerListRS>
      </soap-env:Body>
    </soap-env:Envelope>"""
    parsed = SabreClient._parse_xml(xml)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS"), xml


def _merge_passenger_calls(raw_responses):
    """Merge the four display-code responses the way run_feeder does.

    None is a failed call and is skipped. A later call cannot replace a
    passenger already kept from an earlier call.
    """
    merged_raw = None
    merged_xml = ""
    for raw, xml in raw_responses:
        if raw is None:
            continue
        if merged_raw is None:
            merged_raw = raw
            merged_xml = xml
            continue
        subsequent_pax = _extract_passenger_info_list(raw)
        if not subsequent_pax:
            continue
        base_pax_list = _extract_passenger_info_list(merged_raw)
        existing_keys = {_pax_dedup_key(p) for p in base_pax_list}
        for pax in subsequent_pax:
            key = _pax_dedup_key(pax)
            if key not in existing_keys:
                base_pax_list.append(pax)
                existing_keys.add(key)
        _set_passenger_info_list(merged_raw, base_pax_list)
    return merged_raw, merged_xml


def _with_flags(pax, checked_in, boarded):
    updated = dict(pax)
    updated["checked_in"] = checked_in
    updated["boarded"] = boarded
    return updated


def ingest(flight_number, calls):
    """Ingest one passenger-list cycle.

    calls is the four GetPassengerList results in feeder order:
    booked, checked-in, no-show/offload, catch-all. Each entry is a list of
    passengers or None when that call fails.
    """
    _advance_clock()
    raw_responses = []
    for call in calls:
        if call is None:
            raw_responses.append((None, ""))
        else:
            raw_responses.append(_parse_passenger_list(call))
    merged_raw, merged_xml = _merge_passenger_calls(raw_responses)
    assert merged_raw is not None, "at least one passenger-list call must succeed"
    flight_info = {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DATE,
    }
    meta = {
        "requestXml": "<GetPassengerListRQ/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": "test-session-token",
        "conversationId": "test-conversation",
    }
    return _process_api_call(
        "PassengerList",
        "passenger_list",
        flight_info,
        merged_raw,
        merged_xml,
        meta,
        convert_passenger_list,
        (merged_raw, "GF", flight_number, DATE, ORIGIN),
    )


def _only(call_index, passengers):
    calls = [None, None, None, None]
    calls[call_index] = passengers
    return calls


def _changes(flight_number):
    return get_changes(flight_number, origin=ORIGIN, date=DATE, change_type=None, limit=200)


def _named(changes, last_name, change_type):
    return [
        change for change in changes
        if change.get("changeType") == change_type
        and (change.get("passenger") or {}).get("lastName") == last_name
    ]


def _assert_reversal(changes, last_name, change_type, field):
    matches = _named(changes, last_name, change_type)
    assert matches, (
        f"expected {change_type} for {last_name}, saw "
        f"{sorted({c.get('changeType') for c in changes})}"
    )
    assert matches[-1].get("field") == field
    assert str(matches[-1].get("oldValue")).lower() == "true"
    assert str(matches[-1].get("newValue")).lower() == "false"


def _manifest_counts(db, flight_number):
    snapshot = db["snapshots"].find_one(
        {
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "departureDate": DATE,
            "snapshotType": "passenger_list",
        },
        sort=[("sequenceNumber", -1)],
    )
    passengers = snapshot["data"]["passengers"]
    return {
        "boarded": sum(1 for pax in passengers if pax.get("isBoarded")),
        "checked_in": sum(1 for pax in passengers if pax.get("isCheckedIn")),
        "by_name": {pax["lastName"]: pax for pax in passengers},
    }


def _series_counts(progress):
    points = progress.get("series") or progress.get("data") or []
    counts = []
    for point in points:
        if "count" in point:
            counts.append(point["count"])
        elif "cumulativeCount" in point:
            counts.append(point["cumulativeCount"])
    return counts


def _assert_progress_matches_manifest(flight_number, counts, *, boarding_drops, checkin_drops):
    body = get_boarding_progress(flight_number, origin=ORIGIN, date=DATE)
    boarding = _series_counts(body["boardingProgress"])
    checkin = _series_counts(body["checkinProgress"])
    assert body["boardingProgress"]["current"] == counts["boarded"]
    assert body["checkinProgress"]["current"] == counts["checked_in"]

    def _curve(series, expected, must_drop, label):
        if not series:
            assert expected == 0 and not must_drop, (
                f"{label} series is empty while the manifest count is {expected}"
            )
            return
        assert series[-1] == expected
        if must_drop:
            assert any(series[i] < series[i - 1] for i in range(1, len(series))), (
                f"{label} series never decreases: {series}"
            )

    _curve(boarding, counts["boarded"], boarding_drops, "boarding")
    _curve(checkin, counts["checked_in"], checkin_drops, "check-in")

    timeline = get_flight_timeline(flight_number, origin=ORIGIN, date=DATE)
    assert timeline["stats"]["boarded"] == counts["boarded"]
    assert timeline["stats"]["checkedIn"] == counts["checked_in"]
    return body


def _feed_has(categories, last_name, event_type):
    feed = get_activity_feed(date=DATE, limit=50, categories=categories)
    return any(
        event.get("eventType") == event_type
        and (event.get("passenger") or {}).get("lastName") == last_name
        for event in feed["events"]
    )


STAYER = {"pnr": "STAY01", "last": "STAYER", "first": "SARA", "id": "P1", "line": 1}
GONE = {"pnr": "GONE01", "last": "GONE", "first": "OMAR", "id": "P2", "line": 2}
STAFF = {"pnr": "", "last": "CREW", "first": "NINA", "id": "P3", "line": 9}


def test_offload_and_checkin_undo_record_reversals_and_lower_the_curve(_fresh_db):
    """A later manifest with the flags false must reverse both events.

    The offloaded revenue passenger and the offloaded staff passenger (no PNR)
    are absent from the checked-in display and arrive on the no-show/offload
    display. The stayer remains boarded. Published counts must follow the
    latest flags, and the boarding and check-in feeds must show the reversal.
    """
    flight = "9101"
    party = [STAYER, GONE, STAFF]
    booked = [_with_flags(pax, False, False) for pax in party]
    boarded = [_with_flags(pax, True, True) for pax in party]
    still_on_board = [_with_flags(STAYER, True, True)]
    offloaded = [_with_flags(GONE, False, False), _with_flags(STAFF, False, False)]

    ingest(flight, _only(0, booked))
    ingest(flight, _only(1, boarded))
    ingest(flight, [None, still_on_board, offloaded, None])

    counts = _manifest_counts(_fresh_db, flight)
    assert counts["by_name"]["GONE"]["isBoarded"] is False
    assert counts["by_name"]["GONE"]["isCheckedIn"] is False
    assert counts["by_name"]["CREW"]["isBoarded"] is False
    assert counts["by_name"]["CREW"]["isCheckedIn"] is False
    assert counts["by_name"]["STAYER"]["isBoarded"] is True
    assert counts["boarded"] == 1
    assert counts["checked_in"] == 1

    changes = _changes(flight)
    assert _named(changes, "GONE", "BOARDED")
    assert _named(changes, "GONE", "CHECKED_IN")
    assert _named(changes, "CREW", "BOARDED")
    assert not _named(changes, "STAYER", OFFLOADED)
    for last_name in ("GONE", "CREW"):
        _assert_reversal(changes, last_name, OFFLOADED, "isBoarded")
        _assert_reversal(changes, last_name, CHECK_IN_REVERSED, "isCheckedIn")

    _assert_progress_matches_manifest(
        flight, counts, boarding_drops=True, checkin_drops=True,
    )
    for last_name in ("GONE", "CREW"):
        assert _feed_has("boarding", last_name, OFFLOADED)
        assert _feed_has("checkin", last_name, CHECK_IN_REVERSED)
        assert _feed_has(None, last_name, OFFLOADED)


def test_offload_while_still_checked_in_reverses_boarding_only(_fresh_db):
    flight = "9102"
    person = _with_flags(GONE, False, False)
    ingest(flight, _only(0, [person]))
    ingest(flight, _only(1, [_with_flags(GONE, True, True)]))
    ingest(flight, _only(1, [_with_flags(GONE, True, False)]))

    counts = _manifest_counts(_fresh_db, flight)
    assert counts["boarded"] == 0
    assert counts["checked_in"] == 1
    changes = _changes(flight)
    _assert_reversal(changes, "GONE", OFFLOADED, "isBoarded")
    assert not _named(changes, "GONE", CHECK_IN_REVERSED)
    _assert_progress_matches_manifest(
        flight, counts, boarding_drops=True, checkin_drops=False,
    )
    assert _feed_has("boarding", "GONE", OFFLOADED)


def test_undone_checkin_reverses_without_a_boarding_event(_fresh_db):
    flight = "9103"
    ingest(flight, _only(0, [_with_flags(GONE, False, False)]))
    ingest(flight, _only(1, [_with_flags(GONE, True, False)]))
    ingest(flight, _only(0, [_with_flags(GONE, False, False)]))

    counts = _manifest_counts(_fresh_db, flight)
    assert counts["boarded"] == 0
    assert counts["checked_in"] == 0
    changes = _changes(flight)
    _assert_reversal(changes, "GONE", CHECK_IN_REVERSED, "isCheckedIn")
    assert not _named(changes, "GONE", "BOARDED")
    assert not _named(changes, "GONE", OFFLOADED)
    _assert_progress_matches_manifest(
        flight, counts, boarding_drops=False, checkin_drops=True,
    )
    assert _feed_has("checkin", "GONE", CHECK_IN_REVERSED)


def test_reboard_after_offload_does_not_keep_the_high_water_mark(_fresh_db):
    flight = "9104"
    ingest(flight, _only(0, [_with_flags(GONE, False, False)]))
    ingest(flight, _only(1, [_with_flags(GONE, True, True)]))
    ingest(flight, _only(2, [_with_flags(GONE, False, False)]))
    ingest(flight, _only(1, [_with_flags(GONE, True, True)]))

    counts = _manifest_counts(_fresh_db, flight)
    assert counts["boarded"] == 1
    assert counts["checked_in"] == 1
    changes = _changes(flight)
    assert len(_named(changes, "GONE", "BOARDED")) == 2
    assert len(_named(changes, "GONE", "CHECKED_IN")) == 2
    _assert_reversal(changes, "GONE", OFFLOADED, "isBoarded")
    _assert_reversal(changes, "GONE", CHECK_IN_REVERSED, "isCheckedIn")
    boarded_events = [c for c in changes if c.get("changeType") == "BOARDED"]
    assert len(boarded_events) > counts["boarded"]
    _assert_progress_matches_manifest(
        flight, counts, boarding_drops=True, checkin_drops=True,
    )


def test_passenger_who_leaves_the_manifest_is_not_still_counted_as_boarded(_fresh_db):
    """Removal already stores PASSENGER_REMOVED. The progress curve must still fall."""
    flight = "9105"
    ingest(flight, _only(0, [_with_flags(GONE, False, False)]))
    ingest(flight, _only(1, [_with_flags(GONE, True, True)]))
    ingest(flight, _only(0, [_with_flags(STAYER, False, False)]))

    counts = _manifest_counts(_fresh_db, flight)
    assert "GONE" not in counts["by_name"]
    assert counts["boarded"] == 0
    assert counts["checked_in"] == 0
    changes = _changes(flight)
    assert _named(changes, "GONE", "PASSENGER_REMOVED")
    assert _named(changes, "GONE", "BOARDED")
    _assert_progress_matches_manifest(
        flight, counts, boarding_drops=True, checkin_drops=True,
    )


def test_flags_that_stay_true_do_not_emit_a_reversal(_fresh_db):
    flight = "9106"
    ingest(flight, _only(0, [_with_flags(STAYER, False, False)]))
    ingest(flight, _only(1, [_with_flags(STAYER, True, True)]))
    ingest(flight, _only(1, [_with_flags(STAYER, True, True)]))

    changes = _changes(flight)
    assert _named(changes, "STAYER", "BOARDED")
    assert not _named(changes, "STAYER", OFFLOADED)
    assert not _named(changes, "STAYER", CHECK_IN_REVERSED)
    counts = _manifest_counts(_fresh_db, flight)
    assert counts["boarded"] == 1
    assert counts["checked_in"] == 1
    body = get_boarding_progress(flight, origin=ORIGIN, date=DATE)
    assert body["boardingProgress"]["current"] == 1
    assert body["checkinProgress"]["current"] == 1
