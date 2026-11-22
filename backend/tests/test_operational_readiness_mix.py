"""Readiness counts must be a partition of the passenger flags.

Checked-in and boarded are independent manifest flags. A passenger can be
boarded without being checked in; the flight audit already treats that as a
real state. Screens that label three slices — boarded, checked in but not
boarded, and not checked in — have to count those flags. Subtracting the two
totals makes the middle slice too small (including negative) and parks boarded
passengers in the slice labeled not checked in.

These tests fail while that subtraction is in place. They pass once every
displayed slice is counted from the flags and clamped at zero.

Run (MongoDB listening, or mongod on PATH / $MONGOD_BIN):

    python -m pip install -r requirements.txt pytest httpx
    cd frontend && npm ci && npm install --no-save vitest@3.2.4 jsdom@26.1.0 @testing-library/react@16.3.0
    cd .. && python -m pytest backend/tests/test_operational_readiness_mix.py -q
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from pymongo import MongoClient

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
MONGO_PORT = 27117
API_PORT = 8765
MONGO_URI = f"mongodb://127.0.0.1:{MONGO_PORT}"
API_BASE = f"http://127.0.0.1:{API_PORT}"

# One closed flight whose latest manifest mixes every flag combination.
MIXED_DATE = "2026-06-15"
MIXED_FLIGHT = "100"
# Two boarded-but-not-checked-in passengers, so checked-in minus boarded is negative.
NEGATIVE_DATE = "2026-06-16"
# An older manifest must not be added to the latest one.
LATEST_DATE = "2026-06-17"
# Cabin filter must count only the passengers in that cabin.
CABIN_DATE = "2026-06-18"
# Two flights on one day must be summed as partitions, not as subtracted totals.
SUM_DATE = "2026-06-19"

EXPECTED_PHASE = "Flight closed — 1 no-show, 1 offload"


def _find_mongod() -> str:
    override = os.environ.get("MONGOD_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("mongod")
    if found:
        return found
    bundled = Path("/tmp/mongo/bin/mongod")
    if bundled.exists():
        return str(bundled)
    raise RuntimeError("mongod is required to exercise the analytics aggregation")


def _wait_for_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"nothing accepted connections on port {port}")


def _passenger(last: str, pnr: str, checked_in: bool, boarded: bool, cabin: str = "Y") -> dict:
    """Sabre GetPassengerList passenger with independent check-in and board flags."""
    return {
        "Name_Details": {"LastName": last, "FirstName": "A"},
        "PNRLocator": {"#text": pnr},
        "PassengerID": pnr,
        "BookingClass": "J" if cabin == "J" else "Y",
        "Cabin": cabin,
        "CheckIn_Info": {"CheckInStatus": "true" if checked_in else "false"},
        "Boarding_Info": {"BoardStatus": "true" if boarded else "false"},
        "PassengerType": "A",
    }


def _store_manifest(db, flight: str, date: str, people: list[dict], fetched_at: str, status: str = "FINAL"):
    from backend.feeder.converter import convert_flight_status, convert_passenger_list

    raw_status = {
        "ItineraryResponseList": {
            "ItineraryInfoResponse": {
                "FlightStatus": status,
                "ScheduledDepartureDate": date,
                "DepartureGate": "A1",
            }
        }
    }
    status_doc = convert_flight_status(raw_status, "GF", flight, "BAH", date)
    status_doc["fetchedAt"] = fetched_at
    db["flight_status"].insert_one(status_doc)

    raw_pax = {
        "PassengerInfoList": {"PassengerInfo": people},
        "ItineraryInfo": {"Itinerary": {"Destination": "LHR"}},
    }
    pax_doc = convert_passenger_list(raw_pax, "GF", flight, date, "BAH")
    pax_doc["fetchedAt"] = fetched_at
    db["passenger_list"].insert_one(pax_doc)
    return pax_doc


def _seed(db) -> None:
    from backend.api.audit_rules import rule_boarded_without_checkin

    mixed = _store_manifest(
        db,
        MIXED_FLIGHT,
        MIXED_DATE,
        [
            _passenger("BOTH", "AAA001", checked_in=True, boarded=True),
            _passenger("BOARDONLY", "AAA002", checked_in=False, boarded=True),
            _passenger("CHECKONLY", "AAA003", checked_in=True, boarded=False),
            _passenger("NEITHER", "AAA004", checked_in=False, boarded=False),
        ],
        "2026-06-15T12:00:00Z",
    )
    # The product keeps this state. The chart must not require the flags to nest.
    boarded_without_checkin = [
        p for p in mixed["passengers"] if p["isBoarded"] and not p["isCheckedIn"]
    ]
    assert len(boarded_without_checkin) == 1
    assert rule_boarded_without_checkin(mixed["passengers"])

    _store_manifest(
        db,
        "101",
        NEGATIVE_DATE,
        [
            _passenger("BOARD1", "BBB001", checked_in=False, boarded=True),
            _passenger("BOARD2", "BBB002", checked_in=False, boarded=True),
            _passenger("NEITHER", "BBB003", checked_in=False, boarded=False),
        ],
        "2026-06-16T12:00:00Z",
    )

    _store_manifest(
        db,
        "102",
        LATEST_DATE,
        [_passenger(f"OLD{i}", f"CCC00{i}", checked_in=True, boarded=False) for i in range(5)],
        "2026-06-17T08:00:00Z",
    )
    _store_manifest(
        db,
        "102",
        LATEST_DATE,
        [
            _passenger("NEWBOARD", "CCC100", checked_in=False, boarded=True),
            _passenger("NEWOPEN", "CCC101", checked_in=False, boarded=False),
        ],
        "2026-06-17T18:00:00Z",
    )

    _store_manifest(
        db,
        "103",
        CABIN_DATE,
        [
            _passenger("ECONBOARD", "DDD001", checked_in=False, boarded=True, cabin="Y"),
            _passenger("BIZCHECK", "DDD002", checked_in=True, boarded=False, cabin="J"),
        ],
        "2026-06-18T12:00:00Z",
    )

    _store_manifest(
        db,
        "190",
        SUM_DATE,
        [_passenger("SUMBOARD", "EEE001", checked_in=False, boarded=True)],
        "2026-06-19T12:00:00Z",
    )
    _store_manifest(
        db,
        "191",
        SUM_DATE,
        [
            _passenger("SUMCHECK", "EEE002", checked_in=True, boarded=False),
            _passenger("SUMOPEN", "EEE003", checked_in=False, boarded=False),
        ],
        "2026-06-19T12:00:00Z",
    )


@pytest.fixture(scope="session")
def api_base():
    """Start MongoDB and the API with manifests the converter itself produced."""
    os.environ["MONGODB_URI"] = MONGO_URI
    dbpath = Path("/tmp/falconeye-readiness-mongo")
    if dbpath.exists():
        shutil.rmtree(dbpath)
    dbpath.mkdir(parents=True)
    mongo_log = open("/tmp/falconeye-mongod.log", "w")
    mongod = subprocess.Popen(
        [
            _find_mongod(),
            "--dbpath", str(dbpath),
            "--bind_ip", "127.0.0.1",
            "--port", str(MONGO_PORT),
            "--wiredTigerCacheSizeGB", "0.25",
        ],
        stdout=mongo_log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_port(MONGO_PORT)
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        db = client["falconeye"]
        db.command("dropDatabase")
        _seed(db)
        client.close()

        env = os.environ.copy()
        env["MONGODB_URI"] = MONGO_URI
        api_log = open("/tmp/falconeye-api.log", "w")
        api = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn",
                "backend.api.main:app",
                "--host", "127.0.0.1",
                "--port", str(API_PORT),
            ],
            cwd=REPO,
            env=env,
            stdout=api_log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_port(API_PORT)
            deadline = time.time() + 30
            last_error = ""
            while time.time() < deadline:
                try:
                    health = httpx.get(f"{API_BASE}/health/db", timeout=2)
                    if health.status_code == 200 and health.json().get("status") == "healthy":
                        break
                    last_error = health.text
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                time.sleep(0.3)
            else:
                raise RuntimeError(f"API did not become healthy: {last_error}")
            yield API_BASE
        finally:
            api.send_signal(signal.SIGTERM)
            try:
                api.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
            api_log.close()
    finally:
        mongod.send_signal(signal.SIGTERM)
        try:
            mongod.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mongod.kill()
        mongo_log.close()


def _get(api_base: str, path: str, **params) -> httpx.Response:
    # No Authorization header: the analytics and flight routes do not require an account.
    response = httpx.get(f"{api_base}{path}", params=params, timeout=60)
    assert response.status_code == 200, response.text
    return response


def test_closed_flight_phase_does_not_call_boarded_passengers_no_shows(api_base):
    """Boarded-without-check-in is not a no-show, and the checked-in passenger who
    did not board is one offload. Both the dashboard banner and the flight list
    compute that sentence."""
    dashboard = _get(
        api_base, f"/flights/{MIXED_FLIGHT}/dashboard",
        origin="BAH", date=MIXED_DATE,
    ).json()
    assert dashboard["flightPhase"]["description"] == EXPECTED_PHASE

    listed = _get(api_base, "/flights", date=MIXED_DATE).json()
    match = [f for f in listed if f["flightNumber"] == MIXED_FLIGHT]
    assert len(match) == 1
    assert match[0]["flightPhase"]["description"] == EXPECTED_PHASE


def test_matrix_not_checked_in_excludes_boarded_passengers(api_base):
    """The pax-matrix status strip shows this card beside Boarded."""
    tree = _get(
        api_base, f"/flights/{MIXED_FLIGHT}/tree",
        origin="BAH", date=MIXED_DATE,
    ).json()
    cards = {card["id"]: card for card in tree["statusCards"]}
    assert cards["boarded"]["value"] == 2
    assert cards["notCheckedIn"]["label"] == "Not Checked-In"
    assert cards["notCheckedIn"]["value"] == 1
    assert cards["noShow"]["value"] == 1
    assert cards["offloaded"]["value"] == 1


def _ensure_frontend_runner():
    if not (FRONTEND / "node_modules" / "vitest").exists():
        subprocess.check_call(["npm", "ci", "--no-audit", "--no-fund"], cwd=FRONTEND)
        subprocess.check_call(
            [
                "npm", "install", "--no-save", "--no-audit", "--no-fund",
                "vitest@3.2.4", "jsdom@26.1.0", "@testing-library/react@16.3.0",
            ],
            cwd=FRONTEND,
        )


def test_displayed_readiness_slices_match_passenger_flags(api_base):
    """Render the analytics chart, the workbench status tile, the boarding funnel,
    and the pax-matrix strip against this API. Each slice is the flag partition."""
    _ensure_frontend_runner()
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_URL"] = api_base
    env["TZ"] = "UTC"
    completed = subprocess.run(
        [
            "npx", "vitest", "run",
            "--config", "vitest.config.ts",
            "tests/operational-readiness.test.tsx",
        ],
        cwd=FRONTEND,
        env=env,
    )
    assert completed.returncode == 0, (
        "displayed readiness slices do not match the passenger flags; "
        "see the vitest output above"
    )
