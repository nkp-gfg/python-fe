"""Bounds for analytics date ranges.

GET /analytics/filters and GET /analytics/network take two YYYY-MM-DD query
parameters and, apart from a digit pattern, pass them straight into MongoDB
range matches. These tests are the contract for closing that hole:

- Both endpoints must reject a span longer than 31 inclusive days
  (``(date_to - date_from).days > 30``). The analytics page defaults to a
  25-day lookback, which still fits.
- ``date_from`` must be on or before ``date_to``.
- A rejected request must not aggregate, find, count, or page MongoDB.
  Refusing after the scan has already run is still a failure.
- An allowed window must still return distinct origin, destination, and
  flight number for flights inside that window, using the latest
  passenger-list destination, and must not include departure dates from
  outside the window.
- Callers send no credentials. The refusal is a 400 from the date check,
  not an authentication challenge.

The tests fail while the unbounded scan is still in place.
"""

import os
import sys

import pytest
from pymongo import monitoring

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"

from fastapi.testclient import TestClient

import backend.api.database as database
from backend.api.main import app

_QUERY_COMMANDS = {"aggregate", "find", "count", "distinct", "getMore"}


class _CommandLog(monitoring.CommandListener):
    def __init__(self):
        self.recording = False
        self.events = []

    def started(self, event):
        if self.recording and event.command_name in _QUERY_COMMANDS:
            self.events.append((event.command_name, dict(event.command)))

    def succeeded(self, event):
        return None

    def failed(self, event):
        return None


def _summarize(events):
    parts = []
    for name, command in events:
        if name == "aggregate":
            stages = []
            for stage in command.get("pipeline", []):
                if isinstance(stage, dict) and stage:
                    stages.append(next(iter(stage)))
            parts.append(
                f"aggregate {command.get('aggregate')} stages={stages}"
            )
        else:
            collection = command.get(name) or command.get("collection")
            parts.append(f"{name} {collection}")
    return parts


@pytest.fixture(scope="module")
def api():
    listener = _CommandLog()
    original = database.MongoClient

    def _client(*args, **kwargs):
        listeners = list(kwargs.get("event_listeners") or [])
        listeners.append(listener)
        kwargs["event_listeners"] = listeners
        return original(*args, **kwargs)

    database.MongoClient = _client
    database.close_db()
    try:
        with TestClient(app) as client:
            db = database.get_db()
            for name in ("flights", "passenger_list", "reservations", "changes"):
                db[name].delete_many({})

            flights = []
            passenger_lists = []
            for year in range(2000, 2020):
                departure = f"{year}-06-15"
                flights.append({
                    "airline": "GF",
                    "flightNumber": "2057",
                    "origin": "BAH",
                    "departureDate": departure,
                    "summary": {"totalPax": 100 + year - 2000},
                })
                passenger_lists.append({
                    "airline": "GF",
                    "flightNumber": "2057",
                    "origin": "BAH",
                    "departureDate": departure,
                    "destination": "LHR",
                    "fetchedAt": f"{departure}T00:00:00Z",
                    "totalPassengers": 100,
                })
            # Older passenger-list row for the in-window flight. The route
            # destination must come from the later fetch, not this one.
            passenger_lists.append({
                "airline": "GF",
                "flightNumber": "2057",
                "origin": "BAH",
                "departureDate": "2000-06-15",
                "destination": "OLD",
                "fetchedAt": "2000-06-14T00:00:00Z",
                "totalPassengers": 1,
            })
            flights.append({
                "airline": "GF",
                "flightNumber": "0001",
                "origin": "BAH",
                "departureDate": "2010-01-02",
                "summary": {"totalPax": 3},
            })
            passenger_lists.append({
                "airline": "GF",
                "flightNumber": "0001",
                "origin": "BAH",
                "departureDate": "2010-01-02",
                "destination": "DXB",
                "fetchedAt": "2010-01-02T00:00:00Z",
                "totalPassengers": 3,
            })
            db.flights.insert_many(flights)
            db.passenger_list.insert_many(passenger_lists)
            yield client, listener
    finally:
        database.close_db()
        database.MongoClient = original


def _get(api, path, params):
    client, listener = api
    listener.events.clear()
    listener.recording = True
    try:
        response = client.get(path, params=params)
    finally:
        listener.recording = False
    return response, list(listener.events)


def _assert_refused_before_query(response, events, path):
    body = response.json()
    message = (
        f"unauthenticated GET {path} returned {response.status_code} "
        f"after {_summarize(events)}; body={body}"
    )
    assert response.status_code == 400, message
    assert events == [], message


def test_filters_non_date_query_never_reaches_the_database(api):
    response, events = _get(
        api, "/analytics/filters",
        {"date_from": "yesterday", "date_to": "2026-03-01"},
    )
    assert response.status_code == 422
    assert events == []


def test_filters_unauthenticated_full_history_is_rejected_before_any_query(api):
    response, events = _get(
        api, "/analytics/filters",
        {"date_from": "0001-01-01", "date_to": "9999-12-31"},
    )
    _assert_refused_before_query(response, events, "/analytics/filters")


def test_filters_inverted_range_is_rejected_before_any_query(api):
    response, events = _get(
        api, "/analytics/filters",
        {"date_from": "9999-12-31", "date_to": "0001-01-01"},
    )
    _assert_refused_before_query(response, events, "/analytics/filters")


def test_filters_span_longer_than_31_days_is_rejected_before_any_query(api):
    # 2000-06-01 through 2000-07-02 is 32 inclusive days and contains a flight.
    response, events = _get(
        api, "/analytics/filters",
        {"date_from": "2000-06-01", "date_to": "2000-07-02"},
    )
    _assert_refused_before_query(response, events, "/analytics/filters")


def test_filters_31_day_window_returns_distinct_route_without_other_dates(api):
    # 2000-06-01 through 2000-07-01 is 31 inclusive days.
    response, events = _get(
        api, "/analytics/filters",
        {"date_from": "2000-06-01", "date_to": "2000-07-01"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["origins"] == ["BAH"]
    assert body["flightNumbers"] == ["2057"]
    assert len(body["routes"]) == 1
    route = body["routes"][0]
    assert route["flightNumber"] == "2057"
    assert route["origin"] == "BAH"
    assert route["destination"] == "LHR"
    dates = route.get("dates", [])
    assert dates == [] or dates == ["2000-06-15"]
    assert "2010-01-02" not in dates
    assert "2019-06-15" not in dates
    assert events, "an allowed window should still query MongoDB"


def test_filters_single_day_window_is_allowed(api):
    response, events = _get(
        api, "/analytics/filters",
        {"date_from": "2010-01-02", "date_to": "2010-01-02"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["flightNumbers"] == ["0001"]
    assert len(body["routes"]) == 1
    assert body["routes"][0]["destination"] == "DXB"
    assert body["routes"][0]["origin"] == "BAH"
    dates = body["routes"][0].get("dates", [])
    assert dates == [] or dates == ["2010-01-02"]
    assert events


def test_network_unauthenticated_full_history_is_rejected_before_any_query(api):
    response, events = _get(
        api, "/analytics/network",
        {"date_from": "0001-01-01", "date_to": "9999-12-31"},
    )
    _assert_refused_before_query(response, events, "/analytics/network")


def test_network_span_longer_than_31_days_is_rejected_before_any_query(api):
    response, events = _get(
        api, "/analytics/network",
        {"date_from": "2000-06-01", "date_to": "2000-07-02"},
    )
    _assert_refused_before_query(response, events, "/analytics/network")


def test_network_inverted_range_is_rejected_before_any_query(api):
    response, events = _get(
        api, "/analytics/network",
        {"date_from": "2019-06-15", "date_to": "2000-06-15"},
    )
    _assert_refused_before_query(response, events, "/analytics/network")


def test_network_31_day_window_returns_only_in_range_dates(api):
    response, events = _get(
        api, "/analytics/network",
        {"date_from": "2000-06-01", "date_to": "2000-07-01"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    trend_dates = [row["date"] for row in body["dailyTrends"]]
    assert trend_dates == ["2000-06-15"]
    assert body["overview"]["daysTracked"] == 1
    assert events
