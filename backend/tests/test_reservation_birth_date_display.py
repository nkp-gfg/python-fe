"""Reservations table must show Sabre date-of-birth calendar days.

The tests fail while the table formats a date-only birth date with the
browser Date parser. Run from the repository root, after installing
frontend dependencies (`npm ci` in `frontend/`) and backend dependencies
(`requirements.txt`) plus pytest:

    python3 -m pytest backend/tests/test_reservation_birth_date_display.py -q

`test_trip_search_birth_date_reaches_the_reservations_api` checks that
ingestion and the reservations response model keep the Sabre calendar
date. `test_reservations_table_shows_that_calendar_day_west_of_utc` renders
`ReservationView` in America/New_York and is the case that fails until the
table is fixed. The Bahrain case must keep showing the same calendar day.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import serialize_response

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.routes.reservations import _strip_id
from backend.feeder.converter import _strip_ns, convert_reservations
from backend.sabre.client import SabreClient
from backend.sabre.models import ReservationsResponse

SAMPLE_XML = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)
FRONTEND = REPO_ROOT / "frontend"
UI_TEST = "components/dashboard/reservation-view.birth-date.test.tsx"
CITED_BIRTH_DATE = "1974-12-29"
MONTH_BOUNDARY_BIRTH_DATE = "1977-01-01"


def _parsed_trip_search_body() -> dict:
    """Parse the committed Trip Search response the same way the SOAP client does."""
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not use instance state. SabreClient() reads session
    # settings from the environment, which this display test does not need.
    client = SabreClient.__new__(SabreClient)
    body = client._extract_body(parsed, "Trip_SearchRS")
    if not isinstance(body, dict):
        raise AssertionError("Trip_SearchRS body was not an object")
    return body


def _collect_dates_of_birth(value, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "DateOfBirth" and isinstance(child, str):
                found.append(child)
            else:
                _collect_dates_of_birth(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_dates_of_birth(child, found)


def _api_response(doc: dict) -> dict:
    """Serialize the way GET /flights/{flight_number}/reservations does."""
    stripped = _strip_id(copy.deepcopy(doc))
    app = FastAPI()

    @app.get(
        "/flights/{flight_number}/reservations",
        response_model=ReservationsResponse,
    )
    def get_reservations():
        return stripped

    route = next(
        route
        for route in app.routes
        if getattr(route, "path", "").endswith("/reservations")
    )
    return asyncio.run(
        serialize_response(
            field=route.response_field,
            response_content=stripped,
            is_coroutine=True,
        )
    )


def _ingested_reservations() -> tuple[list[str], dict]:
    body = _parsed_trip_search_body()
    source_dates: list[str] = []
    _collect_dates_of_birth(_strip_ns(body), source_dates)
    converted = convert_reservations(body, "GF", "2057", "BOM", "2026-03-18")
    return source_dates, _api_response(converted)


def _display_fixture(api_doc: dict) -> dict:
    wanted = {CITED_BIRTH_DATE, MONTH_BOUNDARY_BIRTH_DATE}
    selected = []
    expectations = []
    for reservation in api_doc["reservations"]:
        birth_dates = {
            passenger["dateOfBirth"]
            for passenger in reservation["passengers"]
            if passenger["dateOfBirth"]
        }
        if not birth_dates & wanted:
            continue
        passengers = []
        for passenger in reservation["passengers"]:
            passengers.append(
                {
                    "lastName": passenger["lastName"],
                    "firstName": passenger["firstName"],
                    "nameId": passenger.get("nameId", ""),
                    "nameType": passenger.get("nameType", ""),
                    "gender": passenger.get("gender", ""),
                    "dateOfBirth": passenger["dateOfBirth"],
                    "nationality": passenger.get("nationality", ""),
                    "seatNumber": passenger.get("seatNumber", ""),
                    "frequentFlyerNumber": passenger.get("frequentFlyerNumber", ""),
                    "frequentFlyerAirline": passenger.get("frequentFlyerAirline", ""),
                }
            )
        selected.append(
            {
                "pnr": reservation["pnr"],
                "numberInParty": reservation["numberInParty"],
                "numberOfInfants": reservation["numberOfInfants"],
                "createdAt": reservation["createdAt"],
                "updatedAt": reservation["updatedAt"],
                "passengers": passengers,
                "segments": [],
                "tickets": [],
            }
        )
        expectations.append(
            {
                "pnr": reservation["pnr"],
                "createdAt": reservation["createdAt"],
                "updatedAt": reservation["updatedAt"],
                "passengers": [
                    {
                        "lastName": passenger["lastName"],
                        "firstName": passenger["firstName"],
                        "dateOfBirth": passenger["dateOfBirth"],
                    }
                    for passenger in passengers
                ],
            }
        )
    if len(selected) < 2:
        raise AssertionError(
            "expected separate reservations for the cited birth date and 1 January"
        )
    response = {
        "airline": api_doc["airline"],
        "flightNumber": api_doc["flightNumber"],
        "departureAirport": api_doc["departureAirport"],
        "departureDate": api_doc["departureDate"],
        "fetchedAt": api_doc["fetchedAt"],
        "totalResults": len(selected),
        "reservations": selected,
    }
    return {"response": response, "reservations": expectations}


def test_trip_search_birth_date_reaches_the_reservations_api():
    source_dates, api_doc = _ingested_reservations()
    assert CITED_BIRTH_DATE in source_dates
    assert MONTH_BOUNDARY_BIRTH_DATE in source_dates

    api_dates = [
        passenger["dateOfBirth"]
        for reservation in api_doc["reservations"]
        for passenger in reservation["passengers"]
        if passenger["dateOfBirth"]
    ]
    assert CITED_BIRTH_DATE in api_dates
    assert MONTH_BOUNDARY_BIRTH_DATE in api_dates
    assert set(api_dates) <= set(source_dates)

    cited = next(
        reservation
        for reservation in api_doc["reservations"]
        if any(
            passenger["dateOfBirth"] == CITED_BIRTH_DATE
            for passenger in reservation["passengers"]
        )
    )
    assert cited["createdAt"] == "2026-03-16T22:53:00"
    assert cited["updatedAt"] == "2026-03-17T10:48:33"
    assert api_doc["flightNumber"] == "2057"
    assert api_doc["departureAirport"] == "BOM"


def _render_reservations_table(tmp_path: Path, time_zone: str) -> None:
    _, api_doc = _ingested_reservations()
    fixture_path = tmp_path / f"reservations-{time_zone.replace('/', '-')}.json"
    fixture_path.write_text(
        json.dumps(_display_fixture(api_doc)),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["TZ"] = time_zone
    env["FIXTURE_PATH"] = str(fixture_path)
    completed = subprocess.run(
        [
            "npx",
            "vitest",
            "run",
            "--config",
            "vitest.config.ts",
            "--pool=forks",
            UI_TEST,
        ],
        cwd=FRONTEND,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        output = f"{completed.stdout}\n{completed.stderr}".strip()
        pytest.fail(
            f"reservation table in {time_zone} did not show the Sabre calendar dates\n{output}"
        )


def test_reservations_table_shows_that_calendar_day_west_of_utc(tmp_path: Path):
    _render_reservations_table(tmp_path, "America/New_York")


def test_reservations_table_shows_that_calendar_day_east_of_utc(tmp_path: Path):
    _render_reservations_table(tmp_path, "Asia/Bahrain")
