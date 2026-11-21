"""Delay changes for Sabre estimated departure and arrival.

These tests exercise the flight-status ingest path: parse an ACS Flight Detail
response, store two snapshots through the feeder pipeline, then read the
changes collection, the flight timeline, and the activity feed.

They fail while estimated times are omitted from change detection. A fix
should record one change per slipped estimate and surface it as a flight event.

Contract for each slipped estimate:
  changeType: DELAY
  field: schedule.estimatedDeparture or schedule.estimatedArrival
  oldValue / newValue: the previous and new combined date-time strings
    (date + "T" + time, as stored on the flight status schedule)
  timeline category: flight
  activity feed categories=flight includes the same change

An estimate that did not change must not produce DELAY. A gate change must
still produce GATE_CHANGE.

Not covered here: passenger-list estimatedDeparture, which is a different
snapshot and is not part of this flight-status comparison.
"""

import re
import time
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from pymongo import MongoClient

from backend.api.routes.changes import get_activity_feed, get_changes, get_flight_timeline
from backend.api.routes.flights import get_flight_status
from backend.feeder.converter import convert_flight_status
from backend.feeder.runner import _process_api_call, _validate_live_flight_payload
from backend.feeder import storage
from backend.sabre.client import SabreClient

DELAY = "DELAY"
REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_XML = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml"
)
FLIGHT = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}


def _redact_sample_token(xml_text):
    """Drop the credential carried in the captured sample before it is parsed."""
    return re.sub(
        r"(<wsse:BinarySecurityToken[^>]*>)[^<]*(</wsse:BinarySecurityToken>)",
        r"\1placeholder\2",
        xml_text,
        count=1,
    )


def _sample_xml():
    return _redact_sample_token(SAMPLE_XML.read_text())


def _replace_tag(xml_text, tag, value):
    pattern = rf"(<{tag}>)[^<]*(</{tag}>)"
    updated, count = re.subn(pattern, rf"\g<1>{value}\2", xml_text, count=1)
    if count != 1:
        raise AssertionError(f"expected one {tag} element to replace")
    return updated


def _minimal_flight_detail(
    *,
    flight_number="2058",
    estimated_departure_date="2026-03-19",
    estimated_departure_time="05:40PM",
    estimated_arrival_date="2026-03-19",
    estimated_arrival_time="09:25PM",
    include_estimated_departure=True,
    gate="67A",
    status="FINAL",
):
    """ACS Flight Detail body with the elements convert_flight_status reads."""
    if include_estimated_departure:
        estimated_departure = (
            f"<EstimatedDepartureDate>{estimated_departure_date}</EstimatedDepartureDate>"
            f"<EstimatedDepartureTime>{estimated_departure_time}</EstimatedDepartureTime>"
        )
    else:
        estimated_departure = ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>{flight_number}</Flight>
          <Origin>BOM</Origin>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <ScheduledDepartureTime>05:40PM</ScheduledDepartureTime>
          {estimated_departure}
          <DepartureGate>{gate}</DepartureGate>
          <DepartureTerminal>2</DepartureTerminal>
          <BoardingTime>05:00PM</BoardingTime>
          <ScheduledArrivalDate>2026-03-19</ScheduledArrivalDate>
          <ScheduledArrivalTime>08:10PM</ScheduledArrivalTime>
          <EstimatedArrivalDate>{estimated_arrival_date}</EstimatedArrivalDate>
          <EstimatedArrivalTime>{estimated_arrival_time}</EstimatedArrivalTime>
          <FlightStatus>{status}</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <Result>
        <Status>Success</Status>
      </Result>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _flight_for(flight_number):
    return {**FLIGHT, "flightNumber": flight_number}


def _parse_flight_detail(xml_text):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(object(), parsed, "ns3:ACS_FlightDetailRS")


def _ingest(xml_text, flight):
    raw = _parse_flight_detail(xml_text)
    _validate_live_flight_payload(flight, None, raw)
    meta = {
        "requestXml": "<ACS_FlightDetailRQ/>",
        "httpStatus": 200,
        "durationMs": 1,
    }
    return _process_api_call(
        "FlightStatus",
        "flight_status",
        flight,
        raw,
        xml_text,
        meta,
        convert_flight_status,
        (
            raw,
            flight["airline"],
            flight["flightNumber"],
            flight["origin"],
            flight["departureDate"],
        ),
    )


def _delay_rows(db, flight_number):
    return list(
        db["changes"].find(
            {"flightNumber": flight_number, "changeType": DELAY},
        )
    )


def _assert_delay(rows, field, old, new):
    matches = [row for row in rows if row.get("field") == field]
    assert matches, (
        f"missing {DELAY} for {field}; "
        f"stored fields={[row.get('field') for row in rows]}"
    )
    assert len(matches) == 1
    assert matches[0]["oldValue"] == old
    assert matches[0]["newValue"] == new
    assert matches[0]["origin"] == "BOM"
    assert matches[0]["departureDate"] == "2026-03-19"


@pytest.fixture()
def db(monkeypatch):
    client = MongoClient(
        "mongodb://127.0.0.1:27017",
        serverSelectionTimeoutMS=3000,
    )
    client.admin.command("ping")
    name = "falconeye_delay_" + uuid.uuid4().hex
    database = client[name]
    storage.init_db(database)
    monkeypatch.setattr(
        "backend.api.routes.changes.get_db", lambda: database)
    monkeypatch.setattr(
        "backend.api.routes.flights.get_db", lambda: database)
    yield database
    storage.close()
    client.drop_database(name)
    client.close()


def test_later_sample_estimates_are_delay_events_on_timeline_and_flight_feed(db):
    """GF2057's captured flight detail, then a later estimate for the same flight."""
    original = _sample_xml()
    later = _replace_tag(original, "EstimatedDepartureTime", "06:15PM")
    later = _replace_tag(later, "EstimatedArrivalTime", "10:05PM")

    first = _ingest(original, FLIGHT)
    # fetchedAt is stored at one-second resolution, and the status API
    # returns the newest document by that field.
    time.sleep(1.1)
    second = _ingest(later, FLIGHT)

    status = get_flight_status(
        "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None)
    assert status["schedule"]["scheduledDeparture"] == "2026-03-19T05:40PM"
    assert status["schedule"]["scheduledArrival"] == "2026-03-19T08:10PM"
    assert status["schedule"]["estimatedDeparture"] == "2026-03-19T06:15PM"
    assert status["schedule"]["estimatedArrival"] == "2026-03-19T10:05PM"

    assert first["changesStored"] == 0
    assert second["isDuplicate"] is False
    assert second["changesStored"] == 2

    rows = _delay_rows(db, "2057")
    _assert_delay(
        rows, "schedule.estimatedDeparture",
        "2026-03-19T05:40PM", "2026-03-19T06:15PM",
    )
    _assert_delay(
        rows, "schedule.estimatedArrival",
        "2026-03-19T09:25PM", "2026-03-19T10:05PM",
    )

    try:
        stored = get_changes(
            "2057", origin="BOM", date="2026-03-19",
            change_type=DELAY, limit=100)
    except HTTPException as exc:
        pytest.fail(
            f"changes API omitted the delay: {exc.status_code} {exc.detail}")
    fields = {row["field"]: row for row in stored}
    assert fields["schedule.estimatedDeparture"]["oldValue"] == "2026-03-19T05:40PM"
    assert fields["schedule.estimatedDeparture"]["newValue"] == "2026-03-19T06:15PM"
    assert fields["schedule.estimatedArrival"]["oldValue"] == "2026-03-19T09:25PM"
    assert fields["schedule.estimatedArrival"]["newValue"] == "2026-03-19T10:05PM"

    try:
        timeline = get_flight_timeline("2057", origin="BOM", date="2026-03-19")
    except HTTPException as exc:
        pytest.fail(
            f"timeline omitted the delay: {exc.status_code} {exc.detail}")
    delay_events = [
        event for event in timeline["events"] if event["eventType"] == DELAY
    ]
    by_field = {event["details"]["field"]: event for event in delay_events}
    departure = by_field["schedule.estimatedDeparture"]
    arrival = by_field["schedule.estimatedArrival"]
    assert departure["category"] == "flight"
    assert departure["details"]["oldValue"] == "2026-03-19T05:40PM"
    assert departure["details"]["newValue"] == "2026-03-19T06:15PM"
    assert "2026-03-19T05:40PM" in departure["description"]
    assert "2026-03-19T06:15PM" in departure["description"]
    assert arrival["category"] == "flight"
    assert arrival["details"]["oldValue"] == "2026-03-19T09:25PM"
    assert arrival["details"]["newValue"] == "2026-03-19T10:05PM"
    assert "2026-03-19T09:25PM" in arrival["description"]
    assert "2026-03-19T10:05PM" in arrival["description"]

    feed = get_activity_feed(date="2026-03-19", limit=50, categories="flight")
    feed_delays = [event for event in feed["events"] if event["eventType"] == DELAY]
    assert len(feed_delays) == 2
    for event in feed_delays:
        assert event["category"] == "flight"
        assert event["flightNumber"] == "2057"
    descriptions = " ".join(event["description"] for event in feed_delays)
    assert "2026-03-19T06:15PM" in descriptions
    assert "2026-03-19T10:05PM" in descriptions
    assert "2026-03-19T05:40PM" in descriptions
    assert "2026-03-19T09:25PM" in descriptions


def test_arrival_slip_alone_is_one_delay(db):
    flight = _flight_for("2058")
    before = _minimal_flight_detail(flight_number="2058")
    after = _minimal_flight_detail(
        flight_number="2058", estimated_arrival_time="10:40PM")

    _ingest(before, flight)
    second = _ingest(after, flight)

    assert second["isDuplicate"] is False
    assert second["changesStored"] == 1
    rows = _delay_rows(db, "2058")
    assert len(rows) == 1
    _assert_delay(
        rows, "schedule.estimatedArrival",
        "2026-03-19T09:25PM", "2026-03-19T10:40PM",
    )

    timeline = get_flight_timeline("2058", origin="BOM", date="2026-03-19")
    delays = [event for event in timeline["events"] if event["eventType"] == DELAY]
    assert len(delays) == 1
    assert delays[0]["category"] == "flight"
    assert delays[0]["details"]["field"] == "schedule.estimatedArrival"
    assert delays[0]["details"]["oldValue"] == "2026-03-19T09:25PM"
    assert delays[0]["details"]["newValue"] == "2026-03-19T10:40PM"

    feed = get_activity_feed(date="2026-03-19", limit=50, categories="flight")
    assert [event["eventType"] for event in feed["events"]] == [DELAY]
    assert feed["events"][0]["category"] == "flight"
    assert "2026-03-19T09:25PM" in feed["events"][0]["description"]
    assert "2026-03-19T10:40PM" in feed["events"][0]["description"]


def test_departure_slip_alone_is_one_delay(db):
    flight = _flight_for("2059")
    before = _minimal_flight_detail(flight_number="2059")
    after = _minimal_flight_detail(
        flight_number="2059", estimated_departure_time="06:15PM")

    _ingest(before, flight)
    second = _ingest(after, flight)

    assert second["changesStored"] == 1
    rows = _delay_rows(db, "2059")
    assert len(rows) == 1
    _assert_delay(
        rows, "schedule.estimatedDeparture",
        "2026-03-19T05:40PM", "2026-03-19T06:15PM",
    )


def test_first_published_estimate_is_a_delay(db):
    """An estimate that was blank, then published, is still a delay change."""
    flight = _flight_for("2060")
    before = _minimal_flight_detail(
        flight_number="2060", include_estimated_departure=False)
    after = _minimal_flight_detail(
        flight_number="2060",
        estimated_departure_date="2026-03-19",
        estimated_departure_time="06:15PM",
    )

    _ingest(before, flight)
    second = _ingest(after, flight)

    assert second["changesStored"] == 1
    rows = _delay_rows(db, "2060")
    assert len(rows) == 1
    _assert_delay(
        rows, "schedule.estimatedDeparture",
        "", "2026-03-19T06:15PM",
    )


def test_unchanged_estimate_does_not_emit_delay(db):
    original = _sample_xml()
    _ingest(original, FLIGHT)
    second = _ingest(original, FLIGHT)

    assert second["changesStored"] == 0
    assert _delay_rows(db, "2057") == []
    with pytest.raises(HTTPException) as raised:
        get_flight_timeline("2057", origin="BOM", date="2026-03-19")
    assert raised.value.status_code == 404


def test_gate_change_is_still_recorded_without_a_delay(db):
    original = _sample_xml()
    moved = original.replace(
        "<DepartureGate>67A</DepartureGate>",
        "<DepartureGate>70B</DepartureGate>",
        1,
    )
    _ingest(original, FLIGHT)
    second = _ingest(moved, FLIGHT)

    assert second["isDuplicate"] is False
    assert second["changesStored"] == 1
    assert _delay_rows(db, "2057") == []
    stored = get_changes(
        "2057", origin="BOM", date="2026-03-19", change_type=None, limit=100)
    assert [row["changeType"] for row in stored] == ["GATE_CHANGE"]
    assert stored[0]["oldValue"] == "67A"
    assert stored[0]["newValue"] == "70B"

    timeline = get_flight_timeline("2057", origin="BOM", date="2026-03-19")
    change_events = [
        event for event in timeline["events"]
        if event["eventType"] != "SNAPSHOT_TAKEN"
    ]
    assert [event["eventType"] for event in change_events] == ["GATE_CHANGE"]
    assert change_events[0]["category"] == "flight"
