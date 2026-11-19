"""Snapshot restore and compare must not treat one type's sequence as a shared cutoff.

Sequence numbers are counted separately for flight status, the passenger list,
and reservations. The history tab lists passenger-list snapshots and sends that
sequence to restore, compare, and the historical dashboard. These tests drive a
partial ingest through the real feeder, then call those routes.

They fail while a passenger-list sequence is applied with ``sequenceNumber <= N``
to flight status and reservations from other ingests. They pass when restore,
compare, and the historical reads publish only documents from the same ingest
as the passenger list the operator selected (or omit a type that ingest did not
capture).
"""

import os
import time

import pytest

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("SABRE_API_DELAY_SECONDS", "0")

import pytest
from fastapi import HTTPException

from backend.api.database import get_db as get_api_db
from backend.api.routes.changes import (
    compare_snapshot_against_latest,
    get_snapshots,
    restore_snapshot_version,
)
from backend.api.routes.flights import get_flight_dashboard, get_flight_status, get_flight_tree
from backend.api.routes.passengers import get_passengers
from backend.api.routes.reservations import get_reservations
from backend.feeder import runner
from backend.feeder.storage import get_db as get_feeder_db
from backend.sabre.client import SabreClient, SabreError

FLIGHT_DATE = "2026-11-19"
ORIGIN = "BAH"
AIRLINE = "GF"


def _soap_body(inner_xml, response_key):
    """Parse a SOAP body with the client's real XML parser and body extractor."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{inner_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )
    parsed = SabreClient._parse_xml(xml)
    data = SabreClient._extract_body(None, parsed, response_key)
    meta = {
        "requestXml": "<test-request/>",
        "httpStatus": 200,
        "durationMs": 1,
        "sessionToken": "",
        "conversationId": "test-conversation",
    }
    return data, xml, meta


def _flight_status_capture(gate, status):
    inner = (
        "<ns3:ACS_FlightDetailRS>"
        "<ItineraryResponseList><ItineraryInfoResponse>"
        f"<FlightStatus>{status}</FlightStatus>"
        f"<DepartureGate>{gate}</DepartureGate>"
        "<DepartureTerminal>1</DepartureTerminal>"
        "</ItineraryInfoResponse></ItineraryResponseList>"
        "</ns3:ACS_FlightDetailRS>"
    )
    return _soap_body(inner, "ns3:ACS_FlightDetailRS")


def _passenger_list_capture(pnr, last_name, first_name):
    inner = (
        "<GetPassengerListRS>"
        "<ItineraryInfo><Itinerary><Destination>DMM</Destination></Itinerary></ItineraryInfo>"
        "<PassengerInfoList><PassengerInfo>"
        f"<PNRLocator>{pnr}</PNRLocator>"
        "<PassengerID>1</PassengerID>"
        "<Name_Details>"
        f"<LastName>{last_name}</LastName>"
        f"<FirstName>{first_name}</FirstName>"
        "</Name_Details>"
        "<BookingClass>Y</BookingClass>"
        "<Cabin>Y</Cabin>"
        "<Indicators><Indicator>Revenue</Indicator></Indicators>"
        "</PassengerInfo></PassengerInfoList>"
        "</GetPassengerListRS>"
    )
    return _soap_body(inner, "GetPassengerListRS")


def _reservations_capture(pnr, last_name, first_name, nationality, gender):
    inner = (
        '<Trip_SearchRS>'
        '<ReservationsList NumberResults="1"><Reservations>'
        f'<Reservation Locator="{pnr}">'
        "<stl19:GetReservationRS><stl19:Reservation>"
        "<stl19:PassengerReservation><stl19:Passengers><stl19:Passenger>"
        f"<stl19:LastName>{last_name}</stl19:LastName>"
        f"<stl19:FirstName>{first_name}</stl19:FirstName>"
        "<stl19:SpecialRequests><stl19:APISRequest><stl19:DOCSEntry>"
        f"<stl19:DocumentNationalityCountry>{nationality}</stl19:DocumentNationalityCountry>"
        f"<stl19:Gender>{gender}</stl19:Gender>"
        "</stl19:DOCSEntry></stl19:APISRequest></stl19:SpecialRequests>"
        "</stl19:Passenger></stl19:Passengers></stl19:PassengerReservation>"
        "</stl19:Reservation></stl19:GetReservationRS>"
        "</Reservation>"
        "</Reservations></ReservationsList>"
        "</Trip_SearchRS>"
    )
    return _soap_body(inner, "Trip_SearchRS")


class _ScriptedSabre:
    """Stand-in for the SOAP session. Returns parsed bodies or SabreError.

    ``run_feeder`` still converts, sequences, and stores each successful call.
    """

    def __init__(self, outcomes):
        self._outcomes = outcomes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def _next(self, key):
        action = self._outcomes[key]
        if action == "error":
            raise SabreError(f"{key} failed for this capture")
        return action

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        return self._next("flight_status")

    def get_passenger_list(self, airline, flight_number, departure_date, origin, display_codes=None):
        return self._next("passenger_list")

    def get_reservations(self, airline, flight_number, departure_airport, departure_datetime):
        return self._next("reservations")

    def get_trip_report(self, airline, flight_number, departure_date, origin, report_type):
        raise SabreError("trip report unavailable for this capture")

    def verify_flight_details(self, airline, flight_number, departure_date, origin="", destination=""):
        raise SabreError("schedule unavailable for this capture")


def _flight(flight_number):
    return {
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": FLIGHT_DATE,
        "departureDateTime": f"{FLIGHT_DATE}T10:00:00",
        "flightSequenceNumber": 1,
    }


def _ingest(monkeypatch, flight_number, outcomes):
    """Run one feeder pass and return the snapshot ids it stored."""
    db = get_feeder_db()
    before = {
        doc["snapshotId"]
        for doc in db["snapshots"].find(
            {"flightNumber": flight_number},
            {"snapshotId": 1},
        )
    }
    monkeypatch.setattr(runner, "SabreClient", lambda: _ScriptedSabre(outcomes))
    runner.run_feeder([_flight(flight_number)])
    created = []
    for doc in db["snapshots"].find({"flightNumber": flight_number}):
        if doc["snapshotId"] not in before:
            created.append(doc)
    return created


def _clear_flight(flight_number):
    db = get_api_db()
    for name in (
        "snapshots",
        "sabre_requests",
        "changes",
        "flights",
        "flight_status",
        "passenger_list",
        "reservations",
        "trip_reports",
        "flight_schedules",
    ):
        db[name].delete_many({"flightNumber": flight_number})


def _capture(**kwargs):
    outcomes = {}
    if kwargs.get("gate"):
        outcomes["flight_status"] = _flight_status_capture(kwargs["gate"], kwargs.get("status", "OPENCI"))
    else:
        outcomes["flight_status"] = "error"
    if kwargs.get("passenger"):
        pnr, last_name, first_name = kwargs["passenger"]
        outcomes["passenger_list"] = _passenger_list_capture(pnr, last_name, first_name)
    else:
        outcomes["passenger_list"] = "error"
    if kwargs.get("reservation"):
        pnr, last_name, first_name, nationality, gender = kwargs["reservation"]
        outcomes["reservations"] = _reservations_capture(pnr, last_name, first_name, nationality, gender)
    else:
        outcomes["reservations"] = "error"
    return outcomes


def _pnrs(snapshot):
    data = snapshot.get("data") or {}
    if snapshot["snapshotType"] == "passenger_list":
        return {p.get("pnr") for p in data.get("passengers", [])}
    if snapshot["snapshotType"] == "reservations":
        return {r.get("pnr") for r in data.get("reservations", [])}
    return set()


def _passenger_sequence_from_history(flight_number, pnr):
    """Sequence the history tab can send: it lists only passenger_list snapshots."""
    listed = get_snapshots(
        flight_number,
        origin=ORIGIN,
        date=FLIGHT_DATE,
        snapshot_type="passenger_list",
        limit=20,
    )
    db = get_api_db()
    for meta in listed:
        snap = db["snapshots"].find_one(
            {
                "flightNumber": flight_number,
                "snapshotType": "passenger_list",
                "sequenceNumber": meta["sequenceNumber"],
                "origin": ORIGIN,
                "departureDate": FLIGHT_DATE,
            }
        )
        if snap and pnr in _pnrs(snap):
            return meta["sequenceNumber"], snap
    raise AssertionError(f"history list has no passenger-list snapshot for {pnr}")


def _allowed_markers(ingest_snapshots):
    gates = set()
    statuses = set()
    passenger_pnrs = set()
    reservation_pnrs = set()
    nationalities = set()
    for snap in ingest_snapshots:
        data = snap.get("data") or {}
        if snap["snapshotType"] == "flight_status":
            gates.add(data.get("gate", ""))
            statuses.add(data.get("status", ""))
        elif snap["snapshotType"] == "passenger_list":
            passenger_pnrs |= _pnrs(snap)
        elif snap["snapshotType"] == "reservations":
            reservation_pnrs |= _pnrs(snap)
            for res in data.get("reservations", []):
                for pax in res.get("passengers", []):
                    if pax.get("nationality"):
                        nationalities.add(pax["nationality"])
    return {
        "gates": gates,
        "statuses": statuses,
        "passenger_pnrs": passenger_pnrs,
        "reservation_pnrs": reservation_pnrs,
        "nationalities": nationalities,
    }


def _snapshot_for_sequence(flight_number, snapshot_type, sequence):
    return get_api_db()["snapshots"].find_one(
        {
            "flightNumber": flight_number,
            "snapshotType": snapshot_type,
            "sequenceNumber": sequence,
            "origin": ORIGIN,
            "departureDate": FLIGHT_DATE,
        }
    )


def _assert_compare_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed):
    compared = compare_snapshot_against_latest(
        flight_number,
        snapshot_sequence=sequence,
        origin=ORIGIN,
        date=FLIGHT_DATE,
    )
    passenger = compared["types"]["passenger_list"]
    assert passenger.get("available") is True
    assert passenger.get("selectedSequence") == sequence
    selected_pax = _snapshot_for_sequence(flight_number, "passenger_list", sequence)
    assert selected_pax["snapshotId"] in ingest_ids

    flight_status = compared["types"]["flight_status"]
    if flight_status.get("available"):
        selected = _snapshot_for_sequence(
            flight_number, "flight_status", flight_status["selectedSequence"]
        )
        assert selected["snapshotId"] in ingest_ids, (
            f"compare selected flight status sequence {flight_status.get('selectedSequence')} "
            f"gate {flight_status['deltas']['gate']['selected']!r}, a different ingest from the passenger list"
        )
        assert flight_status["deltas"]["gate"]["selected"] in allowed["gates"]
    reservations = compared["types"]["reservations"]
    if reservations.get("available"):
        selected = _snapshot_for_sequence(
            flight_number, "reservations", reservations["selectedSequence"]
        )
        assert selected["snapshotId"] in ingest_ids, (
            f"compare selected reservations sequence {reservations.get('selectedSequence')} "
            "from a different ingest than the passenger list"
        )


def _assert_dashboard_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed):
    dashboard = get_flight_dashboard(
        flight_number,
        origin=ORIGIN,
        date=FLIGHT_DATE,
        snapshot_sequence=sequence,
    )
    flight_status = dashboard.get("flightStatus") or {}
    if flight_status:
        status_seq = flight_status.get("snapshotSequenceNumber")
        selected = _snapshot_for_sequence(flight_number, "flight_status", status_seq)
        assert selected is not None and selected["snapshotId"] in ingest_ids, (
            f"dashboard as-of {sequence} shows gate {flight_status.get('gate')!r} "
            f"from flight-status sequence {status_seq}, a different ingest"
        )
        assert flight_status.get("gate", "") in allowed["gates"]
    nationality_counts = (dashboard.get("analysis") or {}).get("nationalityCounts") or {}
    extra_nationalities = set(nationality_counts) - allowed["nationalities"]
    assert not extra_nationalities, (
        f"dashboard as-of {sequence} counted nationalities {extra_nationalities} "
        "from a reservation capture outside the selected ingest"
    )
    if not allowed["reservation_pnrs"]:
        assert (dashboard.get("analysis") or {}).get("totalMale", 0) == 0


def _assert_tree_uses_selected_ingest(flight_number, sequence, allowed):
    tree = get_flight_tree(
        flight_number,
        origin=ORIGIN,
        date=FLIGHT_DATE,
        snapshot_sequence=sequence,
    )
    if allowed["reservation_pnrs"]:
        return
    male_badges = [
        badge
        for node in tree.get("nodes", [])
        for badge in node.get("badges", [])
        if badge.get("type") == "M" and badge.get("value")
    ]
    assert male_badges == [], (
        "flight tree as-of the passenger-list sequence counted gender from another ingest's reservations"
    )


def _assert_status_route_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed):
    try:
        status_doc = get_flight_status(
            flight_number,
            origin=ORIGIN,
            date=FLIGHT_DATE,
            snapshot_sequence=sequence,
        )
    except HTTPException as exc:
        assert exc.status_code == 404
        return
    status_seq = status_doc.get("snapshotSequenceNumber")
    selected = _snapshot_for_sequence(flight_number, "flight_status", status_seq)
    assert selected is not None and selected["snapshotId"] in ingest_ids, (
        f"flight status as-of {sequence} returned gate {status_doc.get('gate')!r} "
        f"from sequence {status_seq}"
    )
    assert status_doc.get("gate", "") in allowed["gates"]


def _assert_reservations_route_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed):
    try:
        reservation_doc = get_reservations(
            flight_number,
            origin=ORIGIN,
            date=FLIGHT_DATE,
            snapshot_sequence=sequence,
        )
    except HTTPException as exc:
        assert exc.status_code == 404
        return
    status_seq = reservation_doc.get("snapshotSequenceNumber")
    selected = _snapshot_for_sequence(flight_number, "reservations", status_seq)
    assert selected is not None and selected["snapshotId"] in ingest_ids, (
        f"reservations as-of {sequence} returned sequence {status_seq} from another ingest"
    )
    returned = {r.get("pnr") for r in reservation_doc.get("reservations", [])}
    assert returned <= allowed["reservation_pnrs"]


def _assert_passenger_enrichment_uses_selected_ingest(flight_number, sequence, allowed):
    passengers = get_passengers(
        flight_number,
        origin=ORIGIN,
        date=FLIGHT_DATE,
        snapshot_sequence=sequence,
    )
    returned_pnrs = {p.get("pnr") for p in passengers.get("passengers", [])}
    assert returned_pnrs == allowed["passenger_pnrs"]
    for pax in passengers.get("passengers", []):
        nationality = pax.get("nationality") or ""
        if nationality:
            assert nationality in allowed["nationalities"], (
                f"passenger {pax.get('pnr')} was enriched with nationality {nationality} "
                "from a reservation outside the selected ingest"
            )


def _latest_id(collection, flight_number, extra=None):
    query = {"flightNumber": flight_number}
    if extra:
        query.update(extra)
    doc = get_api_db()[collection].find_one(query, sort=[("fetchedAt", -1)])
    return None if doc is None else doc["_id"]


def _assert_restore_does_not_publish_other_ingests(flight_number, sequence, ingest_ids, allowed):
    # Previous captures use a trailing Z. A later clock prefix sorts ahead of them,
    # which is how the live dashboard picks the restored document.
    time.sleep(1.1)
    status_before = _latest_id("flight_status", flight_number)
    reservations_before = _latest_id(
        "reservations",
        flight_number,
        {"departureAirport": ORIGIN, "departureDate": FLIGHT_DATE},
    )

    restored = restore_snapshot_version(
        flight_number,
        sequence,
        origin=ORIGIN,
        date=FLIGHT_DATE,
    )
    restored_types = {item["snapshotType"] for item in restored["restored"]}
    assert "passenger_list" in restored_types
    for item in restored["restored"]:
        source = _snapshot_for_sequence(
            flight_number, item["snapshotType"], item["sourceSequence"]
        )
        assert source is not None and source["snapshotId"] in ingest_ids, (
            f"restore wrote {item['snapshotType']} from sequence {item['sourceSequence']}, "
            "which is not the ingest of the passenger list the operator selected"
        )

    status_after = _latest_id("flight_status", flight_number)
    reservations_after = _latest_id(
        "reservations",
        flight_number,
        {"departureAirport": ORIGIN, "departureDate": FLIGHT_DATE},
    )
    dashboard = get_flight_dashboard(flight_number, origin=ORIGIN, date=FLIGHT_DATE)
    gate = (dashboard.get("flightStatus") or {}).get("gate")

    if not allowed["gates"]:
        assert status_after == status_before, (
            "restore published a flight status from another ingest and stamped it as the newest fetch"
        )
    elif status_after != status_before:
        assert gate in allowed["gates"], (
            f"after restore the live dashboard shows gate {gate!r} from a different capture"
        )

    if not allowed["reservation_pnrs"]:
        assert reservations_after == reservations_before, (
            "restore published reservations from another ingest and stamped them as the newest fetch"
        )
    elif reservations_after != reservations_before:
        live = get_reservations(flight_number, origin=ORIGIN, date=FLIGHT_DATE)
        returned = {r.get("pnr") for r in live.get("reservations", [])}
        assert returned <= allowed["reservation_pnrs"]


def _flight_status_failure_case(monkeypatch):
    """Ingest 1 stores every type. Ingest 2 drops flight status and stores the rest."""
    flight_number = "8801"
    _clear_flight(flight_number)
    first = _ingest(
        monkeypatch,
        flight_number,
        _capture(
            gate="G11",
            status="OPENCI",
            passenger=("ALPHA1", "ALFA", "ONE"),
            reservation=("ALPHA1", "ALFA", "ONE", "BH", "F"),
        ),
    )
    second = _ingest(
        monkeypatch,
        flight_number,
        _capture(
            passenger=("BETA2", "BETA", "TWO"),
            reservation=("BETA2", "BETA", "TWO", "BH", "F"),
        ),
    )
    assert any(s["snapshotType"] == "flight_status" for s in first)
    assert not any(s["snapshotType"] == "flight_status" for s in second)
    sequence, _snap = _passenger_sequence_from_history(flight_number, "BETA2")
    allowed = _allowed_markers(second)
    assert allowed["passenger_pnrs"] == {"BETA2"}
    assert "G11" not in allowed["gates"]
    return flight_number, sequence, {s["snapshotId"] for s in second}, allowed


def _crossed_sequence_case(monkeypatch):
    """Passenger-list sequence 2 collides with flight status and reservations from an earlier ingest."""
    flight_number = "8802"
    _clear_flight(flight_number)
    _ingest(
        monkeypatch,
        flight_number,
        _capture(
            gate="G11",
            status="OPENCI",
            passenger=("ALPHA1", "ALFA", "ONE"),
            reservation=("ALPHA1", "ALFA", "ONE", "BH", "F"),
        ),
    )
    _ingest(
        monkeypatch,
        flight_number,
        _capture(
            gate="G22",
            status="OPENCI",
            reservation=("SAME01", "QADRI", "TEST", "IN", "M"),
        ),
    )
    third = _ingest(
        monkeypatch,
        flight_number,
        _capture(passenger=("SAME01", "QADRI", "TEST")),
    )
    assert [s["snapshotType"] for s in third] == ["passenger_list"]
    sequence, _snap = _passenger_sequence_from_history(flight_number, "SAME01")
    ingest_ids = {s["snapshotId"] for s in third}
    other_flight_status = _snapshot_for_sequence(flight_number, "flight_status", sequence)
    other_reservations = _snapshot_for_sequence(flight_number, "reservations", sequence)
    assert other_flight_status is not None and other_flight_status["snapshotId"] not in ingest_ids
    assert other_reservations is not None and other_reservations["snapshotId"] not in ingest_ids
    allowed = _allowed_markers(third)
    return flight_number, sequence, ingest_ids, allowed


@pytest.fixture
def status_failure(monkeypatch):
    return _flight_status_failure_case(monkeypatch)


@pytest.fixture
def crossed_sequences(monkeypatch):
    return _crossed_sequence_case(monkeypatch)


def test_compare_after_flight_status_failure_uses_the_selected_ingest(status_failure):
    flight_number, sequence, ingest_ids, allowed = status_failure
    _assert_compare_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed)


def test_dashboard_after_flight_status_failure_uses_the_selected_ingest(status_failure):
    flight_number, sequence, ingest_ids, allowed = status_failure
    _assert_dashboard_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed)


def test_flight_status_route_after_flight_status_failure_uses_the_selected_ingest(status_failure):
    flight_number, sequence, ingest_ids, allowed = status_failure
    _assert_status_route_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed)


def test_restore_after_flight_status_failure_does_not_republish_the_other_gate(status_failure):
    flight_number, sequence, ingest_ids, allowed = status_failure
    _assert_restore_does_not_publish_other_ingests(flight_number, sequence, ingest_ids, allowed)


def test_compare_does_not_treat_a_colliding_sequence_as_the_same_ingest(crossed_sequences):
    flight_number, sequence, ingest_ids, allowed = crossed_sequences
    _assert_compare_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed)


def test_dashboard_does_not_treat_a_colliding_sequence_as_the_same_ingest(crossed_sequences):
    flight_number, sequence, ingest_ids, allowed = crossed_sequences
    _assert_dashboard_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed)


def test_tree_does_not_count_gender_from_another_ingest(crossed_sequences):
    flight_number, sequence, _ingest_ids, allowed = crossed_sequences
    _assert_tree_uses_selected_ingest(flight_number, sequence, allowed)


def test_flight_status_route_does_not_treat_a_colliding_sequence_as_the_same_ingest(crossed_sequences):
    flight_number, sequence, ingest_ids, allowed = crossed_sequences
    _assert_status_route_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed)


def test_reservations_route_does_not_treat_a_colliding_sequence_as_the_same_ingest(crossed_sequences):
    flight_number, sequence, ingest_ids, allowed = crossed_sequences
    _assert_reservations_route_uses_selected_ingest(flight_number, sequence, ingest_ids, allowed)


def test_passenger_enrichment_does_not_use_another_ingest(crossed_sequences):
    flight_number, sequence, _ingest_ids, allowed = crossed_sequences
    _assert_passenger_enrichment_uses_selected_ingest(flight_number, sequence, allowed)


def test_restore_does_not_republish_a_colliding_sequence(crossed_sequences):
    flight_number, sequence, ingest_ids, allowed = crossed_sequences
    _assert_restore_does_not_publish_other_ingests(flight_number, sequence, ingest_ids, allowed)
