"""Operational Ready must count only passengers who have all three factors.

The Readiness tab labels readinessPct as "Operational Ready" and describes it
as a composite of seat, check-in, and boarding-pass readiness. A passenger
counts toward that percentage only when the stored manifest row has a seat,
is checked in or boarded, and has a boarding pass. The percentage is
round(ready / passengers * 100, 1), or 0 when the manifest is empty.

These tests follow the dashboard path the product runs: Sabre passenger-list
payload, convert_passenger_list, then GET /flights/{flight_number}/dashboard
(get_flight_dashboard) with no credentials. The Readiness tab is rendered from
the insights object that endpoint returns.

They fail while readinessPct is only the checked-in-or-boarded share.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights
from backend.feeder.converter import convert_passenger_list

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_SCRIPT = Path(__file__).resolve().parent / "render_readiness_tab.mjs"
RENDER_DEPS = Path(os.environ.get("RENDER_DEPS", "/tmp/falconeye-readiness-render"))
HEADLINE_RE = re.compile(
    r"Operational Ready</div>\s*<div[^>]*>([^<]*)</div>\s*<div[^>]*>([^<]*)</div>",
)
COMPOSITE_HELPER = "Composite view of seat, check-in, and boarding-pass readiness."


def _match(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _Collection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find_one(self, query, projection=None, sort=None, **_kwargs):
        matches = [doc for doc in self.docs if _match(doc, query)]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda doc: doc.get(key) is None, reverse=False)
                matches.sort(
                    key=lambda doc, key=key: doc.get(key),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query, projection=None, **_kwargs):
        return [copy.deepcopy(doc) for doc in self.docs if _match(doc, query)]


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection([]))

    def add(self, name, doc):
        self[name].docs.append(doc)


@pytest.fixture(autouse=True)
def _clear_dashboard_cache():
    flights._dashboard_cache.clear()
    yield
    flights._dashboard_cache.clear()


def _sabre_passenger(
    *,
    last_name,
    first_name,
    pnr,
    seat="",
    checked_in=False,
    boarded=False,
    boarding_pass=False,
    thru=False,
):
    """Sabre GetPassengerListRS passenger shape consumed by convert_passenger_list."""
    return {
        "Name_Details": {"LastName": last_name, "FirstName": first_name},
        "PNRLocator": {"#text": pnr},
        "PassengerID": pnr,
        "BookingClass": "Y",
        "Cabin": "Y",
        "Seat": seat,
        "PassengerType": "F",
        "BagCount": "0",
        "CheckIn_Info": {"CheckInStatus": "true" if checked_in else "false"},
        "Boarding_Info": {"BoardStatus": "true" if boarded else "false"},
        "BoardingPassFlag": "true" if boarding_pass else "false",
        "ThruIndicator": "true" if thru else "false",
    }


def _passenger_list(passengers, flight_number):
    raw = {
        "PassengerInfoList": {"PassengerInfo": passengers},
        "ItineraryInfo": {"Itinerary": {"Destination": "LHR", "Origin": "BAH"}},
    }
    return convert_passenger_list(raw, "GF", flight_number, "2026-11-20", "BAH")


def _expected_ready_pct(passengers):
    """Share of manifest rows with a seat, check-in or boarding, and a boarding pass."""
    total = len(passengers)
    if not total:
        return 0
    ready = 0
    for passenger in passengers:
        has_seat = bool(passenger.get("seat", ""))
        has_boarding_pass = bool(passenger.get("boardingPassIssued"))
        checked_in_or_boarded = bool(passenger.get("isCheckedIn") or passenger.get("isBoarded"))
        if has_seat and has_boarding_pass and checked_in_or_boarded:
            ready += 1
    return round(ready / total * 100, 1)


def _expected_gaps(passengers):
    no_seat = 0
    checked_in_no_bp = 0
    not_checked_in = 0
    thru_no_seat = 0
    for passenger in passengers:
        has_seat = bool(passenger.get("seat", ""))
        has_boarding_pass = bool(passenger.get("boardingPassIssued"))
        checked_in_or_boarded = bool(passenger.get("isCheckedIn") or passenger.get("isBoarded"))
        if not has_seat:
            no_seat += 1
        if not has_boarding_pass and checked_in_or_boarded:
            checked_in_no_bp += 1
        if not checked_in_or_boarded:
            not_checked_in += 1
        if passenger.get("isThru") and not has_seat:
            thru_no_seat += 1
    return {
        "noSeat": no_seat,
        "checkedInNoBP": checked_in_no_bp,
        "notCheckedIn": not_checked_in,
        "thruNoSeat": thru_no_seat,
    }


def _check_in_or_boarded_pct(passengers):
    total = len(passengers)
    if not total:
        return 0
    ready = sum(
        1 for passenger in passengers
        if passenger.get("isCheckedIn") or passenger.get("isBoarded")
    )
    return round(ready / total * 100, 1)


def _open_dashboard(passenger_doc):
    """GET the dashboard route with no Authorization header.

    FastAPI resolves the omitted snapshot_sequence query to None. Calling the
    endpoint function directly would leave that default as a Query object.
    """
    flight_number = passenger_doc["flightNumber"]
    origin = passenger_doc["origin"]
    departure_date = passenger_doc["departureDate"]
    database = _Database()
    database.add("passenger_list", passenger_doc)
    database.add("flight_status", {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "status": "OPEN",
        "fetchedAt": passenger_doc.get("fetchedAt", ""),
    })

    app = FastAPI()
    app.include_router(flights.router)
    original_get_db = flights.get_db
    flights.get_db = lambda: database
    try:
        response = TestClient(app).get(
            f"/flights/{flight_number}/dashboard",
            params={"origin": origin, "date": departure_date},
        )
    finally:
        flights.get_db = original_get_db
    assert response.status_code == 200, response.text
    return response.json()


def _assert_operational_ready(result, passengers):
    readiness = result["insights"]["operationalReadiness"]
    expected = _expected_ready_pct(passengers)
    gaps = _expected_gaps(passengers)
    assert readiness["noSeat"] == gaps["noSeat"]
    assert readiness["checkedInNoBP"] == gaps["checkedInNoBP"]
    assert readiness["notCheckedIn"] == gaps["notCheckedIn"]
    assert readiness["thruNoSeat"] == gaps["thruNoSeat"]
    assert readiness["readinessPct"] == expected
    check_in_rate = _check_in_or_boarded_pct(passengers)
    if expected != check_in_rate:
        assert readiness["readinessPct"] != check_in_rate


def _manifest(passengers, flight_number):
    document = _passenger_list(passengers, flight_number)
    stored = document["passengers"]
    assert len(stored) == len(passengers)
    return document, stored


def test_checked_in_without_seat_or_boarding_pass_is_not_operationally_ready():
    """Every traveler is checked in, and none has a seat or a boarding pass."""
    document, stored = _manifest([
        _sabre_passenger(last_name="SMITH", first_name="JOHN", pnr="AAA111", checked_in=True),
        _sabre_passenger(last_name="JONES", first_name="MARY", pnr="BBB222", checked_in=True),
        _sabre_passenger(
            last_name="ALI", first_name="SARA", pnr="CCC333", checked_in=True, boarded=True, thru=True,
        ),
    ], "2057")
    for passenger in stored:
        assert passenger["isCheckedIn"] is True
        assert passenger["seat"] == ""
        assert passenger["boardingPassIssued"] is False

    result = _open_dashboard(document)
    _assert_operational_ready(result, stored)


def test_checked_in_and_seated_without_boarding_pass_is_not_ready():
    document, stored = _manifest([
        _sabre_passenger(
            last_name="SMITH", first_name="JOHN", pnr="AAA111",
            seat="12A", checked_in=True, boarding_pass=False,
        ),
        _sabre_passenger(
            last_name="JONES", first_name="MARY", pnr="BBB222",
            seat="12B", checked_in=True, boarding_pass=False,
        ),
    ], "2058")
    result = _open_dashboard(document)
    _assert_operational_ready(result, stored)


def test_checked_in_with_boarding_pass_but_no_seat_is_not_ready():
    document, stored = _manifest([
        _sabre_passenger(
            last_name="SMITH", first_name="JOHN", pnr="AAA111",
            seat="", checked_in=True, boarding_pass=True,
        ),
    ], "2059")
    assert stored[0]["boardingPassIssued"] is True
    assert stored[0]["seat"] == ""
    result = _open_dashboard(document)
    _assert_operational_ready(result, stored)


def test_seat_and_boarding_pass_without_check_in_is_not_ready():
    document, stored = _manifest([
        _sabre_passenger(
            last_name="SMITH", first_name="JOHN", pnr="AAA111",
            seat="14C", checked_in=False, boarded=False, boarding_pass=True,
        ),
    ], "2060")
    assert stored[0]["isCheckedIn"] is False
    assert stored[0]["isBoarded"] is False
    result = _open_dashboard(document)
    _assert_operational_ready(result, stored)


def test_ready_share_is_the_per_passenger_conjunction():
    """Gaps on different passengers must not average out to a ready flight."""
    document, stored = _manifest([
        _sabre_passenger(
            last_name="NOSEAT", first_name="A", pnr="AAA111",
            seat="", checked_in=True, boarding_pass=True,
        ),
        _sabre_passenger(
            last_name="NOBP", first_name="B", pnr="BBB222",
            seat="21A", checked_in=True, boarding_pass=False,
        ),
        _sabre_passenger(
            last_name="NOCI", first_name="C", pnr="CCC333",
            seat="21B", checked_in=False, boarded=False, boarding_pass=True,
        ),
        _sabre_passenger(
            last_name="READY", first_name="D", pnr="DDD444",
            seat="1A", checked_in=False, boarded=True, boarding_pass=True,
        ),
    ], "2061")
    assert _expected_ready_pct(stored) == 25.0
    assert _check_in_or_boarded_pct(stored) == 75.0
    result = _open_dashboard(document)
    _assert_operational_ready(result, stored)


def test_one_of_three_ready_rounds_to_one_decimal():
    document, stored = _manifest([
        _sabre_passenger(
            last_name="READY", first_name="A", pnr="AAA111",
            seat="1A", checked_in=True, boarding_pass=True,
        ),
        _sabre_passenger(last_name="OPEN", first_name="B", pnr="BBB222", checked_in=True),
        _sabre_passenger(last_name="OPEN", first_name="C", pnr="CCC333", checked_in=True),
    ], "2062")
    assert _expected_ready_pct(stored) == 33.3
    result = _open_dashboard(document)
    _assert_operational_ready(result, stored)


def test_all_three_factors_reports_100():
    document, stored = _manifest([
        _sabre_passenger(
            last_name="SMITH", first_name="JOHN", pnr="AAA111",
            seat="12A", checked_in=True, boarded=True, boarding_pass=True,
        ),
        _sabre_passenger(
            last_name="JONES", first_name="MARY", pnr="BBB222",
            seat="4F", checked_in=True, boarding_pass=True,
        ),
    ], "2063")
    result = _open_dashboard(document)
    _assert_operational_ready(result, stored)
    assert result["insights"]["operationalReadiness"]["readinessPct"] == 100.0


def _ensure_render_deps():
    marker = RENDER_DEPS / "node_modules" / "react" / "package.json"
    if marker.exists():
        return
    RENDER_DEPS.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "npm", "install", "--silent", "--no-fund", "--no-audit",
            "react@19.2.4", "react-dom@19.2.4", "esbuild@0.25.12",
            "clsx@2.1.1", "tailwind-merge@3.5.0",
        ],
        cwd=RENDER_DEPS,
        check=True,
    )


def _render_readiness_tab(insights, total_passengers):
    _ensure_render_deps()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(insights, handle)
        insights_path = handle.name
    env = os.environ.copy()
    env["RENDER_DEPS"] = str(RENDER_DEPS)
    completed = subprocess.run(
        ["node", str(RENDER_SCRIPT), insights_path, str(total_passengers)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Readiness tab render failed\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout


def _headline(html):
    match = HEADLINE_RE.search(html)
    assert match, f"Operational Ready headline not found in:\n{html[:2000]}"
    return match.group(1).strip(), match.group(2).strip()


def test_readiness_tab_prints_the_composite_not_the_check_in_rate():
    document, stored = _manifest([
        _sabre_passenger(last_name="SMITH", first_name="JOHN", pnr="AAA111", checked_in=True),
        _sabre_passenger(last_name="JONES", first_name="MARY", pnr="BBB222", checked_in=True, boarded=True),
    ], "2065")
    result = _open_dashboard(document)
    expected = _expected_ready_pct(stored)
    assert expected == 0.0
    html = _render_readiness_tab(
        result["insights"],
        result["passengerSummary"]["totalPassengers"],
    )
    value, helper = _headline(html)
    reported = result["insights"]["operationalReadiness"]["readinessPct"]
    assert helper == COMPOSITE_HELPER
    assert value == f"{reported:g}%"
    assert value == f"{expected:g}%"
    assert reported == expected
