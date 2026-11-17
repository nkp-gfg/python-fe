"""GET /flights must not scan every stored flight when date is omitted.

These tests are the spec for the missing-date scan. They fail while the
handler still accepts an omitted date and asks MongoDB to sort whole
collections with disk use enabled. They pass once a departure date is
required, every read is limited to that day, and disk use is explicitly off.

The stand-in records the command PyMongo 4.20 would send. Collection.aggregate
puts keyword options onto the aggregate command after the pipeline
(pymongo/synchronous/aggregation.py, _AggregationCommand.get_cursor).
Collection.find stores the filter as the find command's filter
(pymongo/cursor_shared.py and pymongo/message.py _gen_find_command). An empty
filter selects every document (Collection.find docstring).

Not covered here, because they are different operations:
- GET /activity/feed can omit date, but it limits the changes read.
- GET /flights/{flight_number}/... can omit date, but the flight number is required.
- GET /analytics/network, GET /compare/flights, and GET /otp/flights require a date.
"""

import os
import sys
import threading

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.main import app  # noqa: E402


DAY = "2026-03-19"
OTHER_DAY = "2026-01-02"


class _RecordingCollection:
    """Record the aggregate/find command the handler hands to the driver."""

    def __init__(self, name, calls, lock):
        self.name = name
        self._calls = calls
        self._lock = lock

    def aggregate(self, pipeline, session=None, let=None, comment=None, **kwargs):
        options = dict(kwargs)
        if let is not None:
            options["let"] = let
        if comment is not None:
            options["comment"] = comment
        # Same shape as _AggregationCommand.get_cursor: pipeline, then options.
        command = {"aggregate": self.name, "pipeline": list(pipeline)}
        command.update(options)
        with self._lock:
            self._calls.append(command)
        return []

    def find(self, filter=None, projection=None, *args, **kwargs):
        # Cursor stores `filter or {}` and _gen_find_command sends that as filter.
        spec = filter or {}
        command = {"find": self.name, "filter": spec, "projection": projection}
        with self._lock:
            self._calls.append(command)
        return []

    def index_information(self):
        # Startup index check must not look like a flight-list read.
        return {"flight_lookup": {}}

    def create_index(self, *args, **kwargs):
        return None

    def find_one(self, *args, **kwargs):
        return None


class _RecordingDb:
    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()
        self._collections = {}

    def __getitem__(self, name):
        collection = self._collections.get(name)
        if collection is None:
            collection = _RecordingCollection(name, self.calls, self._lock)
            self._collections[name] = collection
        return collection

    def command(self, *args, **kwargs):
        return {"ok": 1}


class _Redis:
    def ping(self):
        return True


@pytest.fixture
def api(monkeypatch):
    database = _RecordingDb()
    monkeypatch.setattr("backend.api.main.get_db", lambda: database)
    monkeypatch.setattr("backend.api.routes.flights.get_db", lambda: database)
    monkeypatch.setattr("backend.api.main.get_redis", lambda: _Redis())
    with TestClient(app) as client:
        database.calls.clear()
        yield client, database


def _assert_scoped_to_day(command, day):
    if "aggregate" in command:
        pipeline = command["pipeline"]
        assert pipeline, command
        match = pipeline[0].get("$match")
        assert match is not None, command
        assert match.get("departureDate") == day, command
        assert command.get("allowDiskUse") is False, command
        return
    assert "find" in command, command
    assert command["filter"].get("departureDate") == day, command


def test_omitted_date_is_rejected_before_any_read(api):
    client, database = api
    response = client.get("/flights")
    assert response.status_code in (400, 422), (response.status_code, database.calls)
    assert database.calls == [], database.calls


def test_unauthenticated_caller_cannot_scan_history(api):
    """No credential is required to reach the handler, so refusal must happen there."""
    client, database = api
    response = client.get("/flights", headers={})
    assert response.status_code not in (401, 403), response.status_code
    assert response.status_code in (400, 422), (response.status_code, database.calls)
    assert database.calls == [], database.calls


def test_invalid_date_is_rejected_before_any_read(api):
    client, database = api
    response = client.get("/flights", params={"date": "yesterday"})
    assert response.status_code in (400, 422), response.text
    assert database.calls == []


def test_blank_date_is_rejected_before_any_read(api):
    client, database = api
    response = client.get("/flights", params={"date": ""})
    assert response.status_code in (400, 422), response.text
    assert database.calls == []


def test_dated_list_reads_only_that_day_with_disk_use_off(api):
    client, database = api
    response = client.get("/flights", params={"date": DAY})
    assert response.status_code == 200, response.text
    assert response.json() == []

    by_collection = {}
    for command in database.calls:
        name = command.get("aggregate") or command.get("find")
        by_collection.setdefault(name, []).append(command)
        _assert_scoped_to_day(command, DAY)

    assert set(by_collection) == {
        "flight_status",
        "passenger_list",
        "flight_schedules",
        "flights",
    }
    for name, commands in by_collection.items():
        assert len(commands) == 1, (name, commands)

    for name in ("flight_status", "passenger_list", "flight_schedules"):
        command = by_collection[name][0]
        assert "aggregate" in command
        assert command["allowDiskUse"] is False
        stages = [next(iter(stage)) for stage in command["pipeline"]]
        assert stages[0] == "$match"
        if "$sort" in stages:
            assert stages.index("$sort") > stages.index("$match")


def test_dated_list_does_not_reuse_another_days_filter(api):
    client, database = api
    first = client.get("/flights", params={"date": DAY})
    assert first.status_code == 200, first.text
    first_calls = list(database.calls)
    database.calls.clear()

    second = client.get("/flights", params={"date": OTHER_DAY})
    assert second.status_code == 200, second.text
    assert database.calls, "dated list issued no reads"
    for command in first_calls:
        _assert_scoped_to_day(command, DAY)
    for command in database.calls:
        _assert_scoped_to_day(command, OTHER_DAY)
        if "aggregate" in command:
            assert command["pipeline"][0]["$match"]["departureDate"] != DAY
        else:
            assert command["filter"]["departureDate"] != DAY
