"""Phase journey must pair passenger manifests with the flight status captured
at the same time.

Sequence numbers are allocated separately for each snapshot type. These tests
ingest through the real feeder, with one Sabre call failing, and then read the
phase journey, snapshot compare, and snapshot
restore. They fail while a passenger snapshot is labeled from a flight-status
snapshot chosen by sequence number.

The status that belongs to a passenger snapshot is the flight-status snapshot
with the latest capturedAt that is still at or before that passenger
snapshot's capturedAt. When none exists, the phase is inferred from the
passenger snapshot alone. The journey keeps the passenger snapshot that was
current when the phase actually changed.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo import MongoClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import changes, flights
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreError

ORIGIN = "BAH"
DEPARTURE_DATE = "2026-03-19"
AIRLINE = "GF"


class _Clock:
    """Deterministic capturedAt values, one minute apart, in sortable ISO form."""

    def __init__(self):
        self._n = 0

    def __call__(self):
        self._n += 1
        moment = datetime(2026, 3, 19, 6, 0, tzinfo=timezone.utc) + timedelta(minutes=self._n)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class ScriptedSabre:
    """Stand-in for SabreClient. Returns scripted bodies or SabreError.

    run_feeder still converts, stores, and sequences whatever this returns.
    The queue holds one script per feeder run.
    """

    queue = []

    def __init__(self):
        self._script = ScriptedSabre.queue.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_flight_status(self, *args, **kwargs):
        return self._result("flight_status")

    def get_passenger_list(self, *args, **kwargs):
        return self._result("passenger_list")

    def get_reservations(self, *args, **kwargs):
        raise SabreError("reservations unavailable")

    def get_trip_report(self, *args, **kwargs):
        raise SabreError("trip report unavailable")

    def verify_flight_details(self, *args, **kwargs):
        raise SabreError("schedule unavailable")

    def _result(self, name):
        action = self._script[name]
        if action == "error":
            raise SabreError(f"{name} unavailable")
        meta = {
            "requestXml": "<test/>",
            "httpStatus": 200,
            "durationMs": 1,
            "sessionToken": None,
            "conversationId": "phase-journey-test",
        }
        return action, "<test/>", meta


def _status_body(status):
    return {
        "ItineraryResponseList": {
            "ItineraryInfoResponse": {
                "FlightStatus": status,
                "DepartureGate": "A1",
                "ScheduledDepartureDate": DEPARTURE_DATE,
            }
        }
    }


def _passenger_body(passengers):
    infos = []
    for passenger in passengers:
        infos.append({
            "Name_Details": {
                "LastName": passenger["last"],
                "FirstName": passenger["first"],
            },
            "PNRLocator": {"#text": passenger["pnr"]},
            "PassengerID": passenger["pnr"],
            "Cabin": "Y",
            "PassengerType": "F",
            "Indicators": {"Indicator": ["Revenue"]},
            "CheckIn_Info": {
                "CheckInStatus": "true" if passenger["checked_in"] else "false",
            },
            "Boarding_Info": {
                "BoardStatus": "true" if passenger["boarded"] else "false",
            },
        })
    return {
        "ItineraryInfo": {
            "Itinerary": {"Destination": "DXB"},
            "DepartureArrival_Dates": {},
        },
        "PassengerInfoList": {"PassengerInfo": infos},
    }


def _checked_in(*names):
    return [
        {"pnr": pnr, "last": last, "first": first, "checked_in": True, "boarded": False}
        for pnr, last, first in names
    ]


def _boarded(*names):
    return [
        {"pnr": pnr, "last": last, "first": first, "checked_in": True, "boarded": True}
        for pnr, last, first in names
    ]


PAX_A = ("AAA111", "SMITH", "JOHN")
PAX_B = ("BBB222", "JONES", "MARY")


def _flight(number):
    return {
        "airline": AIRLINE,
        "flightNumber": number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": f"{DEPARTURE_DATE}T08:00:00",
    }


def _ingest(monkeypatch, scripts, flight_number):
    clock = _Clock()
    monkeypatch.setattr(storage, "_now_iso", clock)
    monkeypatch.setattr("backend.feeder.runner.SabreClient", ScriptedSabre)
    ScriptedSabre.queue = list(scripts)
    flight = _flight(flight_number)
    for _ in scripts:
        run_feeder([flight])
    return flight


def _passenger_snapshots(db, flight_number):
    return list(db["snapshots"].find(
        {
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "departureDate": DEPARTURE_DATE,
            "snapshotType": "passenger_list",
        }
    ).sort("capturedAt", 1))


def _sequence_with_boarding(db, flight_number, boarded, checked_in_only):
    for snap in _passenger_snapshots(db, flight_number):
        passengers = snap["data"]["passengers"]
        boarded_count = sum(1 for p in passengers if p.get("isBoarded"))
        checked_only = sum(
            1 for p in passengers if p.get("isCheckedIn") and not p.get("isBoarded")
        )
        if boarded_count == boarded and checked_only == checked_in_only:
            return snap["sequenceNumber"]
    raise AssertionError(
        f"no passenger snapshot with boarded={boarded} checked_in_only={checked_in_only}"
    )


def _phase_by_name(journey):
    return {phase["phase"]: phase for phase in journey["phases"]}


@pytest.fixture(scope="module")
def mongo_db():
    from pymongo_inmemory.context import Context
    from pymongo_inmemory.downloader import download

    context = Context(os_name="ubuntu", os_ver="24", version="8.0.4")
    mongod = os.path.join(download(context), "mongod")
    data_dir = tempfile.mkdtemp(prefix="phase-journey-mongo-")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    log_path = os.path.join(data_dir, "mongod.log")
    proc = subprocess.Popen(
        [
            mongod,
            "--dbpath", data_dir,
            "--logpath", log_path,
            "--port", str(port),
            "--bind_ip", "127.0.0.1",
            "--nounixsocket",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = MongoClient(
        f"127.0.0.1:{port}",
        serverSelectionTimeoutMS=500,
        connectTimeoutMS=500,
    )
    deadline = time.time() + 30
    last_error = None
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            client.admin.command("ping")
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    else:
        proc.terminate()
        raise RuntimeError(f"mongod did not start: {last_error}")
    if proc.poll() is not None:
        raise RuntimeError(f"mongod exited {proc.returncode}; see {log_path}")

    database = client["falconeye_phase_journey_test"]
    storage.init_db(database)
    yield database
    client.close()
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def db(mongo_db, monkeypatch):
    monkeypatch.setattr(flights, "get_db", lambda: mongo_db)
    monkeypatch.setattr(changes, "get_db", lambda: mongo_db)
    flights._dashboard_cache.clear()
    storage.init_db(mongo_db)
    return mongo_db


def _early_manifests_then_departed_status():
    """Passenger lists stored while flight status fails, then one PDC status."""
    check_in = _passenger_body(_checked_in(PAX_A, PAX_B))
    boarding = _passenger_body([
        {"pnr": "AAA111", "last": "SMITH", "first": "JOHN", "checked_in": True, "boarded": True},
        {"pnr": "BBB222", "last": "JONES", "first": "MARY", "checked_in": True, "boarded": False},
    ])
    departed = _passenger_body(_boarded(PAX_A, PAX_B))
    return [
        {"flight_status": "error", "passenger_list": check_in},
        {"flight_status": "error", "passenger_list": boarding},
        {"flight_status": _status_body("PDC"), "passenger_list": departed},
    ]


def test_phase_journey_does_not_apply_a_later_status_to_earlier_manifests(db, monkeypatch):
    flight = _ingest(monkeypatch, _early_manifests_then_departed_status(), "9051")

    journey = flights.get_phase_journey(
        flight["flightNumber"], origin=ORIGIN, date=DEPARTURE_DATE,
    )
    phases = _phase_by_name(journey)

    assert set(phases) == {"CHECK_IN", "BOARDING", "DEPARTED"}
    assert phases["CHECK_IN"]["summary"]["boarded"]["totalPassengers"] == 0
    assert phases["CHECK_IN"]["summary"]["checkedIn"]["totalPassengers"] == 2
    assert phases["BOARDING"]["summary"]["boarded"]["totalPassengers"] == 1
    assert phases["BOARDING"]["summary"]["checkedIn"]["totalPassengers"] == 1
    assert phases["DEPARTED"]["summary"]["boarded"]["totalPassengers"] == 2
    assert [(item["fromPhase"], item["toPhase"]) for item in journey["transitions"]] == [
        ("CHECK_IN", "BOARDING"),
        ("BOARDING", "DEPARTED"),
    ]


def test_phase_journey_http_is_unauthenticated_and_keeps_the_check_in_phase(db, monkeypatch):
    flight = _ingest(monkeypatch, _early_manifests_then_departed_status(), "9054")
    app = FastAPI()
    app.include_router(flights.router)
    response = TestClient(app).get(
        f"/flights/{flight['flightNumber']}/phase-journey",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )

    assert response.status_code == 200
    phases = {phase["phase"] for phase in response.json()["phases"]}
    assert "CHECK_IN" in phases
    assert "BOARDING" in phases
    departed = next(phase for phase in response.json()["phases"] if phase["phase"] == "DEPARTED")
    assert departed["summary"]["boarded"]["totalPassengers"] == 2


def _status_runs_then_partial_boarding():
    """Two flight-status snapshots, then one partially boarded manifest."""
    partial = _passenger_body([
        {"pnr": "AAA111", "last": "SMITH", "first": "JOHN", "checked_in": True, "boarded": True},
        {"pnr": "BBB222", "last": "JONES", "first": "MARY", "checked_in": True, "boarded": False},
    ])
    return [
        {"flight_status": _status_body("OPENCI"), "passenger_list": "error"},
        {"flight_status": _status_body("PDC"), "passenger_list": "error"},
        {"flight_status": "error", "passenger_list": partial},
    ]


def test_phase_journey_uses_the_status_already_captured_for_a_later_manifest(db, monkeypatch):
    flight = _ingest(monkeypatch, _status_runs_then_partial_boarding(), "9052")

    journey = flights.get_phase_journey(
        flight["flightNumber"], origin=ORIGIN, date=DEPARTURE_DATE,
    )

    # Half the manifest is still unboarded, so passenger inference alone is
    # BOARDING. PDC was already captured, so the phase is DEPARTED.
    assert [phase["phase"] for phase in journey["phases"]] == ["DEPARTED"]
    assert journey["phases"][0]["summary"]["boarded"]["totalPassengers"] == 1
    assert journey["phases"][0]["summary"]["checkedIn"]["totalPassengers"] == 1


def test_aligned_ingestion_still_reports_check_in_boarding_and_departed(db, monkeypatch):
    check_in = _passenger_body(_checked_in(PAX_A, PAX_B))
    boarding = _passenger_body([
        {"pnr": "AAA111", "last": "SMITH", "first": "JOHN", "checked_in": True, "boarded": True},
        {"pnr": "BBB222", "last": "JONES", "first": "MARY", "checked_in": True, "boarded": False},
    ])
    departed = _passenger_body(_boarded(PAX_A, PAX_B))
    flight = _ingest(
        monkeypatch,
        [
            {"flight_status": _status_body("OPENCI"), "passenger_list": check_in},
            {"flight_status": _status_body("OPENCI"), "passenger_list": boarding},
            {"flight_status": _status_body("PDC"), "passenger_list": departed},
        ],
        "9053",
    )

    journey = flights.get_phase_journey(
        flight["flightNumber"], origin=ORIGIN, date=DEPARTURE_DATE,
    )
    phases = _phase_by_name(journey)

    assert set(phases) == {"CHECK_IN", "BOARDING", "DEPARTED"}
    assert phases["CHECK_IN"]["summary"]["boarded"]["totalPassengers"] == 0
    assert phases["BOARDING"]["summary"]["boarded"]["totalPassengers"] == 1
    assert phases["DEPARTED"]["summary"]["boarded"]["totalPassengers"] == 2


def test_snapshot_compare_does_not_select_a_status_from_another_time(db, monkeypatch):
    flight = _ingest(monkeypatch, _early_manifests_then_departed_status(), "9056")
    number = flight["flightNumber"]
    check_in_sequence = _sequence_with_boarding(db, number, boarded=0, checked_in_only=2)
    departed_sequence = _sequence_with_boarding(db, number, boarded=2, checked_in_only=0)

    late = changes.compare_snapshot_against_latest(
        number, snapshot_sequence=departed_sequence, origin=ORIGIN, date=DEPARTURE_DATE,
    )
    assert late["types"]["flight_status"]["available"] is True
    assert late["types"]["flight_status"]["deltas"]["status"]["selected"] == "PDC"

    early = changes.compare_snapshot_against_latest(
        number, snapshot_sequence=check_in_sequence, origin=ORIGIN, date=DEPARTURE_DATE,
    )
    early_status = early["types"]["flight_status"]
    if early_status["available"]:
        assert early_status["deltas"]["status"]["selected"] != "PDC"


def test_snapshot_compare_uses_departed_status_captured_before_the_manifest(db, monkeypatch):
    flight = _ingest(monkeypatch, _status_runs_then_partial_boarding(), "9057")
    manifest_sequence = _sequence_with_boarding(
        db, flight["flightNumber"], boarded=1, checked_in_only=1,
    )
    compared = changes.compare_snapshot_against_latest(
        flight["flightNumber"],
        snapshot_sequence=manifest_sequence,
        origin=ORIGIN,
        date=DEPARTURE_DATE,
    )
    assert compared["types"]["flight_status"]["deltas"]["status"]["selected"] == "PDC"


def test_restore_does_not_publish_a_status_from_another_time(db, monkeypatch):
    flight = _ingest(monkeypatch, _early_manifests_then_departed_status(), "9058")
    number = flight["flightNumber"]
    check_in_sequence = _sequence_with_boarding(db, number, boarded=0, checked_in_only=2)

    restored = changes.restore_snapshot_version(
        number, check_in_sequence, origin=ORIGIN, date=DEPARTURE_DATE,
    )
    for item in restored["restored"]:
        if item["snapshotType"] != "flight_status":
            continue
        snap = db["snapshots"].find_one({
            "flightNumber": number,
            "snapshotType": "flight_status",
            "sequenceNumber": item["sourceSequence"],
        })
        assert snap["data"]["status"] != "PDC"


def test_restore_publishes_the_departed_status_captured_before_the_manifest(db, monkeypatch):
    later_flight = _ingest(monkeypatch, _status_runs_then_partial_boarding(), "9059")
    manifest_sequence = _sequence_with_boarding(
        db, later_flight["flightNumber"], boarded=1, checked_in_only=1,
    )
    restored_late = changes.restore_snapshot_version(
        later_flight["flightNumber"],
        manifest_sequence,
        origin=ORIGIN,
        date=DEPARTURE_DATE,
    )
    status_restores = [
        item for item in restored_late["restored"] if item["snapshotType"] == "flight_status"
    ]
    assert status_restores
    source = db["snapshots"].find_one({
        "flightNumber": later_flight["flightNumber"],
        "snapshotType": "flight_status",
        "sequenceNumber": status_restores[0]["sourceSequence"],
    })
    assert source["data"]["status"] == "PDC"
