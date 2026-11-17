"""Spec tests for the network-analytics date window.

GET /analytics/network and GET /analytics/filters take date_from and date_to
from the caller. A window is legal only when both values are real calendar
dates and the inclusive span is at most 31 days:

    inclusive_days = (date_to - date_from).days + 1
    reject when inclusive_days > 31

A longer window must be rejected with HTTP 400 before MongoDB receives any
aggregate command. These tests fail until that happens.

Run:
    MONGODB_URI=mongodb://127.0.0.1:27017 REDIS_URL=redis://127.0.0.1:6379/0 \\
        python3 -m pytest backend/tests/test_analytics_date_range.py -q
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"

from backend.api.database import close_db, get_db  # noqa: E402
from backend.api.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MAX_INCLUSIVE_DAYS = 31
WINDOW_START = "2026-03-01"
WINDOW_END = "2026-03-31"  # inclusive 31-day dashboard window
DAY_PAST_WINDOW = "2026-04-01"  # 32nd day from WINDOW_START
HISTORY_DAY = "2020-01-15"
HISTORY_FROM = "0001-01-01"
HISTORY_TO = "9999-12-31"

# Latest snapshot for the in-window flight. An older snapshot on the same key
# carries different totals so a sort-then-first aggregation is observable.
IN_WINDOW_PASSENGERS = 10
IN_WINDOW_ECONOMY = 7
SECOND_DAY_PASSENGERS = 4
SECOND_DAY_ECONOMY = 3
PAST_WINDOW_PASSENGERS = 80
PAST_WINDOW_ECONOMY = 55
HISTORY_PASSENGERS = 200
HISTORY_ECONOMY = 111
STALE_PASSENGERS = 999


def _inclusive_days(date_from: str, date_to: str) -> int:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    return (end - start).days + 1


def _flight(flight_number: str, departure_date: str) -> dict:
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": departure_date,
    }


def _passenger_list(
    flight_number: str,
    departure_date: str,
    fetched_at: str,
    total_passengers: int,
    economy: int,
) -> dict:
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "destination": "LHR",
        "departureDate": departure_date,
        "fetchedAt": fetched_at,
        "totalPassengers": total_passengers,
        "adultCount": total_passengers,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": total_passengers,
        "cabinSummary": [
            {"cabin": "Y", "count": economy, "authorized": 100},
        ],
        "passengers": [],
    }


def _reservation(flight_number: str, departure_date: str, nationality: str) -> dict:
    return {
        "flightNumber": flight_number,
        "departureAirport": "BAH",
        "departureDate": departure_date,
        "fetchedAt": f"{departure_date}T12:00:00Z",
        "reservations": [
            {
                "numberInParty": 1,
                "pointOfSale": {"isoCountry": nationality},
                "passengers": [
                    {
                        "nationality": nationality,
                        "frequentFlyerNumber": "",
                        "ffTierLevel": "",
                        "specialMeal": "",
                        "wheelchairCode": "",
                    }
                ],
                "segments": [{"segmentBookedDate": departure_date}],
            }
        ],
    }


def _change(flight_number: str, departure_date: str, change_type: str) -> dict:
    return {
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": departure_date,
        "changeType": change_type,
    }


@pytest.fixture(scope="module")
def client():
    close_db()
    with TestClient(app) as test_client:
        db = get_db()
        for name in ("flights", "passenger_list", "reservations", "changes"):
            db[name].delete_many({})
        db.flights.insert_many(
            [
                _flight("100", WINDOW_START),
                _flight("100", WINDOW_END),
                _flight("200", DAY_PAST_WINDOW),
                _flight("300", HISTORY_DAY),
            ]
        )
        db.passenger_list.insert_many(
            [
                _passenger_list(
                    "100", WINDOW_START, f"{WINDOW_START}T00:00:00Z",
                    STALE_PASSENGERS, STALE_PASSENGERS,
                ),
                _passenger_list(
                    "100", WINDOW_START, f"{WINDOW_START}T12:00:00Z",
                    IN_WINDOW_PASSENGERS, IN_WINDOW_ECONOMY,
                ),
                _passenger_list(
                    "100", WINDOW_END, f"{WINDOW_END}T12:00:00Z",
                    SECOND_DAY_PASSENGERS, SECOND_DAY_ECONOMY,
                ),
                _passenger_list(
                    "200", DAY_PAST_WINDOW, f"{DAY_PAST_WINDOW}T12:00:00Z",
                    PAST_WINDOW_PASSENGERS, PAST_WINDOW_ECONOMY,
                ),
                _passenger_list(
                    "300", HISTORY_DAY, f"{HISTORY_DAY}T12:00:00Z",
                    HISTORY_PASSENGERS, HISTORY_ECONOMY,
                ),
            ]
        )
        db.reservations.insert_many(
            [
                _reservation("100", WINDOW_START, "BH"),
                _reservation("300", HISTORY_DAY, "XX"),
            ]
        )
        db.changes.insert_many(
            [
                _change("100", WINDOW_START, "SEAT_CHANGE"),
                _change("300", HISTORY_DAY, "CABIN_CHANGE"),
            ]
        )
        yield test_client
    close_db()


def _aggregates_during(call):
    """Record aggregate commands MongoDB receives while `call` runs.

    The profiler is MongoDB's own command log. A refusal that still scans
    flights, passenger lists, reservations, or changes leaves an entry here.
    """
    db = get_db()
    db.command("profile", 0)
    db.system.profile.drop()
    db.command("profile", 2)
    try:
        response = call()
    finally:
        db.command("profile", 0)
    ops = list(db.system.profile.find({"command.aggregate": {"$exists": True}}))
    described = [
        {
            "collection": op.get("command", {}).get("aggregate"),
            "match": next(
                (
                    stage.get("$match")
                    for stage in op.get("command", {}).get("pipeline", [])
                    if isinstance(stage, dict) and "$match" in stage
                ),
                None,
            ),
        }
        for op in ops
    ]
    return response, described


def _get(client: TestClient, path: str, date_from: str, date_to: str):
    return client.get(
        path,
        params={"date_from": date_from, "date_to": date_to},
        headers={"X-Request-ID": "analytics-range-test"},
    )


def test_dashboard_window_is_thirty_one_inclusive_days():
    assert _inclusive_days(WINDOW_START, WINDOW_END) == MAX_INCLUSIVE_DAYS
    assert _inclusive_days(WINDOW_START, DAY_PAST_WINDOW) == MAX_INCLUSIVE_DAYS + 1
    assert _inclusive_days(HISTORY_FROM, HISTORY_TO) > MAX_INCLUSIVE_DAYS


def test_network_accepts_thirty_one_day_window_without_credentials(client):
    """A staff-sized window still runs, and only documents inside it count."""
    response, ops = _aggregates_during(
        lambda: _get(client, "/analytics/network", WINDOW_START, WINDOW_END)
    )
    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    body = response.json()
    assert body["dateRange"] == {"from": WINDOW_START, "to": WINDOW_END}
    overview = body["overview"]
    assert overview["totalPassengers"] == IN_WINDOW_PASSENGERS + SECOND_DAY_PASSENGERS
    assert overview["economyPassengers"] == IN_WINDOW_ECONOMY + SECOND_DAY_ECONOMY
    assert overview["totalPassengers"] != STALE_PASSENGERS
    assert body["totalChanges"] == 1
    assert body["upgradeAnalytics"]["cabinChanges"] == 0
    nationalities = {row["nationality"] for row in body["nationalityDistribution"]}
    assert nationalities == {"BH"}
    assert ops, "the legal window never reached MongoDB"


def test_filters_accept_thirty_one_day_window_without_credentials(client):
    response, ops = _aggregates_during(
        lambda: _get(client, "/analytics/filters", WINDOW_START, WINDOW_END)
    )
    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    body = response.json()
    assert body["flightNumbers"] == ["100"]
    assert body["origins"] == ["BAH"]
    assert ops, "the legal window never reached MongoDB"


@pytest.mark.parametrize(
    "path,date_from,date_to",
    [
        ("/analytics/network", WINDOW_START, DAY_PAST_WINDOW),
        ("/analytics/network", HISTORY_FROM, HISTORY_TO),
        ("/analytics/filters", WINDOW_START, DAY_PAST_WINDOW),
        ("/analytics/filters", HISTORY_FROM, HISTORY_TO),
    ],
)
def test_range_longer_than_thirty_one_days_is_refused_before_aggregation(
    client, path, date_from, date_to
):
    response, ops = _aggregates_during(
        lambda: _get(client, path, date_from, date_to)
    )
    assert response.status_code == 400 and ops == [], (
        f"{path} {date_from}..{date_to} status={response.status_code} "
        f"body={response.text} aggregates={ops}"
    )


def test_parallel_history_windows_are_refused_before_aggregation(client):
    """Repeating the history window concurrently must not start aggregations."""

    def one_call(_index: int):
        return _get(client, "/analytics/network", HISTORY_FROM, HISTORY_TO)

    def burst():
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(one_call, range(4)))

    responses, ops = _aggregates_during(burst)
    statuses = [response.status_code for response in responses]
    assert statuses == [400, 400, 400, 400] and ops == [], (
        f"parallel history windows statuses={statuses} aggregates={ops} "
        f"bodies={[response.text for response in responses]}"
    )


def test_single_day_window_uses_latest_passenger_snapshot(client):
    response = _get(client, "/analytics/network", WINDOW_START, WINDOW_START)
    assert response.status_code == 200, response.text
    overview = response.json()["overview"]
    assert overview["totalPassengers"] == IN_WINDOW_PASSENGERS
    assert overview["economyPassengers"] == IN_WINDOW_ECONOMY
    assert overview["daysTracked"] == 1
