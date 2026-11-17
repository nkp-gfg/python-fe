"""Bounds for GET /analytics/network and GET /analytics/filters.

These tests describe the behavior required to stop an unauthenticated caller
from running the network-analytics aggregations over an arbitrary departure-date
span. They fail while that behavior is missing.

Contract the handler must enforce, before it calls Collection.aggregate:

- Authentication. The environment variable FALCONEYE_API_TOKEN is a
  comma-separated list of bearer tokens, read on each request. The caller
  must send ``Authorization: Bearer <token>`` using one of those tokens.
  A missing or unknown token is HTTP 401. These tests set the variable to
  the placeholder tokens named below. Rejected requests must not aggregate.
- Date span. ``date_from`` and ``date_to`` must be real calendar dates.
  The inclusive day count ``(date_to - date_from).days + 1`` must be between
  1 and 31. Anything else, including a reversed range or a regex-shaped
  non-date such as 9999-99-99, is HTTP 400. Rejected requests must not
  aggregate. When the same query key is repeated, the value that is
  validated must be the value that is queried (the last value Starlette
  binds for a scalar query parameter).
- Aggregation deadline. Every ``aggregate`` call made for an accepted
  request passes ``maxTimeMS`` as an integer from 1 through 10000.
- Caller limit. Each distinct bearer token may cause at most 4 requests
  that execute aggregations in any 60-second window, across
  ``/analytics/network``. A request past that limit is HTTP 429 and must
  not aggregate. Requests rejected for authentication or for the date span
  do not consume the allowance. ``/analytics/filters`` has its own
  allowance of 4 aggregating requests per token per 60 seconds.

The database double records every ``aggregate`` call and then runs the
pipeline with mongomock, including ``$unwind``. mongomock 4.3.0 raises
NotImplementedError for ``$lookup`` with ``let`` (see
``mongomock.aggregate._handle_lookup_stage``), so an in-range filters
request is allowed to surface that error after its aggregations have been
issued. Those issued aggregations are still required to be bounded and
time-limited.
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TOKENS = (
    "placeholder-token-day",
    "placeholder-token-month",
    "placeholder-token-dup",
    "placeholder-token-leap",
    "placeholder-token-wide",
    "placeholder-token-rate-a",
    "placeholder-token-rate-b",
    "placeholder-token-filters",
    "placeholder-token-filters-wide",
    "placeholder-token-filters-rate",
    "placeholder-token-parallel",
)
os.environ["FALCONEYE_API_TOKEN"] = ",".join(TOKENS)
os.environ.pop("MONGODB_URI", None)

from fastapi.testclient import TestClient  # noqa: E402
from mongomock import MongoClient  # noqa: E402

import backend.api.routes.analytics as analytics  # noqa: E402
from backend.api.main import app  # noqa: E402

MAX_INCLUSIVE_DAYS = 31
MAX_AGGREGATE_TIME_MS = 10_000
MAX_AGGREGATING_REQUESTS = 4

IN_WINDOW = "2026-03-01"
OUT_WINDOW = "2020-01-01"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _inclusive_days(date_from: str, date_to: str) -> int:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    return (end - start).days + 1


def _seed(db) -> None:
    db.flights.insert_many([
        {
            "flightNumber": "100",
            "origin": "BAH",
            "departureDate": IN_WINDOW,
            "airline": "GF",
        },
        {
            "flightNumber": "200",
            "origin": "LHR",
            "departureDate": OUT_WINDOW,
            "airline": "GF",
        },
    ])
    db.passenger_list.insert_many([
        {
            "flightNumber": "100",
            "origin": "BAH",
            "destination": "LHR",
            "departureDate": IN_WINDOW,
            "fetchedAt": "2026-03-01T10:00:00Z",
            "totalPassengers": 2,
            "adultCount": 2,
            "childCount": 0,
            "infantCount": 0,
            "totalSouls": 2,
            "cabinSummary": [{"cabin": "Y", "count": 2, "authorized": 100}],
            "passengers": [
                {
                    "cabin": "Y",
                    "bookingClass": "V",
                    "isCheckedIn": True,
                    "isBoarded": False,
                    "isRevenue": True,
                    "isStandby": False,
                },
                {
                    "cabin": "Y",
                    "bookingClass": "W",
                    "isCheckedIn": False,
                    "isBoarded": False,
                    "isRevenue": True,
                    "isStandby": False,
                },
            ],
        },
        {
            "flightNumber": "200",
            "origin": "LHR",
            "destination": "BAH",
            "departureDate": OUT_WINDOW,
            "fetchedAt": "2020-01-01T10:00:00Z",
            "totalPassengers": 3,
            "adultCount": 3,
            "childCount": 0,
            "infantCount": 0,
            "totalSouls": 3,
            "cabinSummary": [{"cabin": "Y", "count": 3, "authorized": 100}],
            "passengers": [
                {
                    "cabin": "Y",
                    "bookingClass": "Z",
                    "isCheckedIn": False,
                    "isBoarded": False,
                    "isRevenue": True,
                    "isStandby": False,
                },
                {
                    "cabin": "Y",
                    "bookingClass": "Z",
                    "isCheckedIn": False,
                    "isBoarded": False,
                    "isRevenue": True,
                    "isStandby": False,
                },
                {
                    "cabin": "Y",
                    "bookingClass": "Z",
                    "isCheckedIn": False,
                    "isBoarded": False,
                    "isRevenue": True,
                    "isStandby": False,
                },
            ],
        },
    ])
    db.reservations.insert_many([
        {
            "flightNumber": "100",
            "departureAirport": "BAH",
            "departureDate": IN_WINDOW,
            "fetchedAt": "2026-03-01T10:00:00Z",
            "reservations": [
                {
                    "numberInParty": 2,
                    "pointOfSale": {"isoCountry": "BH"},
                    "passengers": [
                        {
                            "nationality": "BH",
                            "frequentFlyerNumber": "FF1",
                            "ffTierLevel": "1",
                            "specialMeal": "VGML",
                            "wheelchairCode": "WCHR",
                        }
                    ],
                    "segments": [{"segmentBookedDate": "2026-02-01"}],
                }
            ],
        },
        {
            "flightNumber": "200",
            "departureAirport": "LHR",
            "departureDate": OUT_WINDOW,
            "fetchedAt": "2020-01-01T10:00:00Z",
            "reservations": [
                {
                    "numberInParty": 3,
                    "pointOfSale": {"isoCountry": "ZZ"},
                    "passengers": [
                        {
                            "nationality": "ZZ",
                            "frequentFlyerNumber": "FF9",
                            "ffTierLevel": "8",
                            "specialMeal": "XXXX",
                            "wheelchairCode": "WCHC",
                        }
                    ],
                    "segments": [{"segmentBookedDate": "2019-01-01"}],
                }
            ],
        },
    ])
    db.changes.insert_many([
        {
            "departureDate": IN_WINDOW,
            "origin": "BAH",
            "flightNumber": "100",
            "changeType": "CABIN_CHANGE",
        },
        {
            "departureDate": OUT_WINDOW,
            "origin": "LHR",
            "flightNumber": "200",
            "changeType": "UPGRADE_CONFIRMED",
        },
    ])


class _RecordingCollection:
    def __init__(self, inner, name: str, calls: list):
        self._inner = inner
        self._name = name
        self._calls = calls

    def aggregate(self, pipeline, **kwargs):
        self._calls.append({
            "collection": self._name,
            "pipeline": copy.deepcopy(pipeline),
            "kwargs": dict(kwargs),
        })
        return self._inner.aggregate(pipeline, **kwargs)

    def __getattr__(self, item):
        return getattr(self._inner, item)


class _RecordingDatabase:
    def __init__(self, inner, calls: list):
        self._inner = inner
        self._calls = calls

    def __getattr__(self, name: str):
        return _RecordingCollection(getattr(self._inner, name), name, self._calls)


def _date_bounds(pipeline: list) -> list[dict]:
    bounds = []
    for stage in pipeline:
        match = stage.get("$match")
        if isinstance(match, dict) and "departureDate" in match:
            bounds.append(match["departureDate"])
    return bounds


def _assert_no_aggregation(calls: list) -> None:
    assert calls == [], (
        "the handler aggregated before refusing the request: "
        + ", ".join(
            f"{call['collection']} { _date_bounds(call['pipeline']) }"
            for call in calls
        )
    )


def _assert_timed_and_bounded(calls: list, date_from: str, date_to: str) -> None:
    assert calls, "an accepted analytics request did not aggregate"
    expected = {"$gte": date_from, "$lte": date_to}
    for call in calls:
        max_time = call["kwargs"].get("maxTimeMS")
        assert isinstance(max_time, int) and 1 <= max_time <= MAX_AGGREGATE_TIME_MS, (
            f"{call['collection']} aggregate kwargs={call['kwargs']}"
        )
        bounds = _date_bounds(call["pipeline"])
        assert bounds, f"{call['collection']} pipeline has no departureDate match"
        assert all(bound == expected for bound in bounds), (
            f"{call['collection']} queried {bounds}, expected {expected}"
        )


def _assert_in_window_only(body: dict) -> None:
    """The unwind pipelines counted the 2026-03-01 documents and not the 2020 ones."""
    assert body["dateRange"] == {"from": IN_WINDOW, "to": IN_WINDOW}
    assert body["overview"]["totalFlights"] == 1
    assert body["overview"]["totalPassengers"] == 2
    classes = sorted(
        body["bookingClassDistribution"],
        key=lambda item: item["bookingClass"],
    )
    assert classes == [
        {"cabin": "Y", "bookingClass": "V", "count": 1},
        {"cabin": "Y", "bookingClass": "W", "count": 1},
    ]
    assert body["nationalityDistribution"] == [{"nationality": "BH", "count": 1}]
    assert body["loyaltyDistribution"] == [
        {"tier": "1", "tierName": "Gold", "count": 1}
    ]
    assert body["mealDistribution"] == [{"mealCode": "VGML", "count": 1}]
    assert body["wheelchairDistribution"] == [{"code": "WCHR", "count": 1}]
    assert body["partySizeDistribution"] == [{"partySize": 2, "count": 1}]
    assert body["pointOfSaleDistribution"] == [{"country": "BH", "count": 1}]
    assert body["operationalRates"]["totalPassengers"] == 2
    assert body["operationalRates"]["checkedIn"] == 1
    assert body["bookingLeadTime"]["within30d"] == 1
    assert body["bookingLeadTime"]["over90d"] == 0
    assert body["upgradeAnalytics"] == {"cabinChanges": 1, "upgradesConfirmed": 0}
    assert body["totalChanges"] == 1
    assert [change["changeType"] for change in body["changeDistribution"]] == [
        "CABIN_CHANGE"
    ]


@pytest.fixture(scope="module")
def analytics_client():
    calls: list = []
    mongo = MongoClient()
    db = mongo["falconeye"]
    _seed(db)
    analytics.get_db = lambda: _RecordingDatabase(db, calls)
    with TestClient(app) as client:
        yield client, calls


@pytest.fixture
def client(analytics_client):
    client, calls = analytics_client
    calls.clear()
    return client


@pytest.fixture
def calls(analytics_client):
    _, calls = analytics_client
    return calls


def test_wide_range_is_rejected_before_any_aggregation(client, calls):
    response = client.get(
        "/analytics/network",
        params={"date_from": "1900-01-01", "date_to": "2099-12-31"},
        headers=_auth("placeholder-token-wide"),
    )
    assert response.status_code == 400, response.text
    _assert_no_aggregation(calls)
    assert _inclusive_days("1900-01-01", "2099-12-31") > MAX_INCLUSIVE_DAYS


def test_regex_valid_non_date_is_rejected_before_aggregation(client, calls):
    response = client.get(
        "/analytics/network",
        params={"date_from": "0000-00-00", "date_to": "9999-99-99"},
        headers=_auth("placeholder-token-wide"),
    )
    assert response.status_code == 400, response.text
    _assert_no_aggregation(calls)


def test_inverted_range_is_rejected_before_aggregation(client, calls):
    response = client.get(
        "/analytics/network",
        params={"date_from": "2026-03-02", "date_to": "2026-03-01"},
        headers=_auth("placeholder-token-wide"),
    )
    assert response.status_code == 400, response.text
    _assert_no_aggregation(calls)


def test_malformed_date_never_reaches_the_database(client, calls):
    response = client.get(
        "/analytics/network",
        params={"date_from": "01-01-1900", "date_to": "2026-03-01"},
        headers=_auth("placeholder-token-wide"),
    )
    assert response.status_code == 422, response.text
    _assert_no_aggregation(calls)


def test_thirty_two_day_window_is_rejected_before_aggregation(client, calls):
    response = client.get(
        "/analytics/network",
        params={"date_from": "2026-03-01", "date_to": "2026-04-01"},
        headers=_auth("placeholder-token-wide"),
    )
    assert _inclusive_days("2026-03-01", "2026-04-01") == MAX_INCLUSIVE_DAYS + 1
    assert response.status_code == 400, response.text
    _assert_no_aggregation(calls)


def test_leap_year_span_one_day_past_the_cap_is_rejected(client, calls):
    # 2024-02-01 through 2024-03-03 is 32 inclusive days in a leap year.
    response = client.get(
        "/analytics/network",
        params={"date_from": "2024-02-01", "date_to": "2024-03-03"},
        headers=_auth("placeholder-token-wide"),
    )
    assert _inclusive_days("2024-02-01", "2024-03-03") == 32
    assert response.status_code == 400, response.text
    _assert_no_aggregation(calls)


def test_anonymous_request_is_rejected_before_aggregation(client, calls):
    response = client.get(
        "/analytics/network",
        params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
    )
    assert response.status_code == 401, response.text
    _assert_no_aggregation(calls)


def test_unknown_token_is_rejected_before_aggregation(client, calls):
    response = client.get(
        "/analytics/network",
        params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
        headers=_auth("placeholder-token-unknown"),
    )
    assert response.status_code == 401, response.text
    _assert_no_aggregation(calls)


def test_one_day_window_unwinds_only_passengers_inside_it(client, calls):
    response = client.get(
        "/analytics/network",
        params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
        headers=_auth("placeholder-token-day"),
    )
    assert response.status_code == 200, response.text
    _assert_timed_and_bounded(calls, IN_WINDOW, IN_WINDOW)
    _assert_in_window_only(response.json())
    unwound = [
        call["collection"]
        for call in calls
        if any("$unwind" in stage for stage in call["pipeline"])
    ]
    assert unwound.count("passenger_list") >= 2
    assert unwound.count("reservations") >= 6


def test_thirty_one_day_window_is_allowed_and_excludes_older_documents(client, calls):
    date_from = "2026-03-01"
    date_to = "2026-03-31"
    assert _inclusive_days(date_from, date_to) == MAX_INCLUSIVE_DAYS
    response = client.get(
        "/analytics/network",
        params={"date_from": date_from, "date_to": date_to},
        headers=_auth("placeholder-token-month"),
    )
    assert response.status_code == 200, response.text
    _assert_timed_and_bounded(calls, date_from, date_to)
    body = response.json()
    assert body["overview"]["totalFlights"] == 1
    assert body["overview"]["totalPassengers"] == 2
    classes = {item["bookingClass"] for item in body["bookingClassDistribution"]}
    assert classes == {"V", "W"}
    assert body["upgradeAnalytics"]["upgradesConfirmed"] == 0


def test_leap_year_span_at_the_cap_is_allowed(client, calls):
    # 2024-02-01 through 2024-03-02 is 31 inclusive days in a leap year.
    date_from, date_to = "2024-02-01", "2024-03-02"
    assert _inclusive_days(date_from, date_to) == MAX_INCLUSIVE_DAYS
    response = client.get(
        "/analytics/network",
        params={"date_from": date_from, "date_to": date_to},
        headers=_auth("placeholder-token-leap"),
    )
    assert response.status_code == 200, response.text
    _assert_timed_and_bounded(calls, date_from, date_to)
    assert response.json()["overview"]["totalFlights"] == 0
    assert response.json()["bookingClassDistribution"] == []


def test_duplicate_dates_query_the_bound_value_not_an_earlier_one(client, calls):
    # Starlette keeps the last scalar value. An earlier wide value must not
    # be the one that reaches the aggregation.
    response = client.get(
        "/analytics/network"
        "?date_from=1990-01-01&date_from=2026-03-01"
        "&date_to=2099-12-31&date_to=2026-03-01",
        headers=_auth("placeholder-token-dup"),
    )
    assert response.status_code == 200, response.text
    _assert_timed_and_bounded(calls, IN_WINDOW, IN_WINDOW)
    _assert_in_window_only(response.json())


def test_last_duplicate_outside_the_cap_is_rejected_before_aggregation(client, calls):
    # The first copies are inside the cap. The bound (last) copies are not.
    response = client.get(
        "/analytics/network"
        "?date_from=2026-03-01&date_from=1900-01-01"
        "&date_to=2026-03-01&date_to=2099-12-31",
        headers=_auth("placeholder-token-wide"),
    )
    assert response.status_code == 400, response.text
    _assert_no_aggregation(calls)


def test_caller_limit_stops_repeated_aggregations(client, calls):
    statuses = []
    for _ in range(MAX_AGGREGATING_REQUESTS):
        calls.clear()
        response = client.get(
            "/analytics/network",
            params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
            headers=_auth("placeholder-token-rate-a"),
        )
        statuses.append(response.status_code)
        assert response.status_code == 200, response.text
        _assert_timed_and_bounded(calls, IN_WINDOW, IN_WINDOW)

    calls.clear()
    blocked = client.get(
        "/analytics/network",
        params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
        headers=_auth("placeholder-token-rate-a"),
    )
    assert blocked.status_code == 429, blocked.text
    _assert_no_aggregation(calls)

    calls.clear()
    other_caller = client.get(
        "/analytics/network",
        params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
        headers=_auth("placeholder-token-rate-b"),
    )
    assert other_caller.status_code == 200, other_caller.text
    _assert_timed_and_bounded(calls, IN_WINDOW, IN_WINDOW)
    assert statuses == [200] * MAX_AGGREGATING_REQUESTS


def test_repeated_wide_ranges_do_not_start_aggregations(client, calls):
    for _ in range(8):
        response = client.get(
            "/analytics/network",
            params={"date_from": "1900-01-01", "date_to": "2099-12-31"},
            headers=_auth("placeholder-token-parallel"),
        )
        assert response.status_code == 400, response.text
    _assert_no_aggregation(calls)


def test_filters_wide_range_is_rejected_before_aggregation(client, calls):
    response = client.get(
        "/analytics/filters",
        params={"date_from": "1900-01-01", "date_to": "2099-12-31"},
        headers=_auth("placeholder-token-filters-wide"),
    )
    assert response.status_code == 400, response.text
    _assert_no_aggregation(calls)


def test_filters_anonymous_request_is_rejected_before_aggregation(client, calls):
    response = client.get(
        "/analytics/filters",
        params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
    )
    assert response.status_code == 401, response.text
    _assert_no_aggregation(calls)


def test_filters_in_range_aggregations_are_bounded_and_timed(client, calls):
    response = client.get(
        "/analytics/filters",
        params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
        headers=_auth("placeholder-token-filters"),
    )
    _assert_timed_and_bounded(calls, IN_WINDOW, IN_WINDOW)
    # mongomock does not execute $lookup let. The route may return that
    # failure after the bounded aggregations have already been issued.
    if response.status_code == 500:
        assert "lookup" in response.text.lower()
    else:
        assert response.status_code == 200, response.text


def test_filters_caller_limit_is_independent(client, calls):
    for _ in range(MAX_AGGREGATING_REQUESTS):
        calls.clear()
        response = client.get(
            "/analytics/filters",
            params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
            headers=_auth("placeholder-token-filters-rate"),
        )
        assert response.status_code in (200, 500), response.text
        _assert_timed_and_bounded(calls, IN_WINDOW, IN_WINDOW)

    calls.clear()
    blocked = client.get(
        "/analytics/filters",
        params={"date_from": IN_WINDOW, "date_to": IN_WINDOW},
        headers=_auth("placeholder-token-filters-rate"),
    )
    assert blocked.status_code == 429, blocked.text
    _assert_no_aggregation(calls)
