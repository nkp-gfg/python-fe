"""Bounds for boarding-progress and the flight timeline.

These tests exercise the real route functions through HTTP. They fail while
the vulnerability is present: date and origin are optional, each matching
change document is pulled into the API process, and the response repeats the
passenger object once per event.

They pass once both routes:

- reject a call that omits date or origin before reading the changes
  collection
- apply departureDate and origin on the queries they do run
- pull at most SERIES_DOCUMENT_CAP change documents per query into the
  process, and return at most that many series points
- leave passenger identity (pnr, lastName, firstName) out of those points

The cursor stand-in follows PyMongo 4.20, the release installed from
requirements.txt (pymongo>=4.6,<5): Collection.find returns a cursor whose
default limit is 0, and a limit of 0 is omitted from the find command, so
iteration yields every equality match. limit() applied before iteration is
honored. See pymongo/cursor_shared.py (limit default and Cursor.limit) and
pymongo/message.py (_gen_find_command).

Not covered here, because they do not build a per-event check-in or boarding
series with the passenger attached:

- get_changes_summary and the dashboard change summary scan every change but
  return counts only
- get_passengers_history_badges aggregates per PNR
- get_flight_audit loads the change history for rules and does not return
  that series
- get_passenger_timeline is scoped to one PNR
- get_activity_feed is capped by its limit parameter (maximum 200)
"""

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import changes as changes_routes

# One flight-day of check-in events is larger than this on purpose.
SERIES_DOCUMENT_CAP = 64
EVENTS_PER_GROUP = 180

FLIGHT = "2057"
REQUEST_ORIGIN = "BAH"
REQUEST_DATE = "2026-04-02"
OTHER_ORIGIN = "DXB"
OTHER_DATE = "2024-01-01"

# Last-name prefixes used to spot a leaked passenger object.
HIST_NAME = "HIST"
OTHER_ORIGIN_NAME = "DXBPAX"
REQUEST_NAME = "DAYPAX"


def _change(flight_number, origin, departure_date, change_type, detected_at,
            last_name, pnr, new_value="true"):
    """Document shape written by backend.feeder.differ._make_change."""
    doc = {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "changeType": change_type,
        "beforeSnapshotId": "before",
        "afterSnapshotId": "after",
        "detectedAt": detected_at,
        "field": "isCheckedIn" if change_type == "CHECKED_IN" else change_type,
        "oldValue": "false",
        "newValue": new_value,
    }
    if change_type in ("CHECKED_IN", "BOARDED"):
        doc["passenger"] = {
            "pnr": pnr,
            "lastName": last_name,
            "firstName": "A",
        }
    return doc


def _stamp(day, second):
    hour = second // 3600
    minute = (second // 60) % 60
    sec = second % 60
    return f"{day}T{hour:02d}:{minute:02d}:{sec:02d}Z"


def _seed_documents():
    changes = []
    groups = [
        (OTHER_DATE, REQUEST_ORIGIN, HIST_NAME, 0),
        (REQUEST_DATE, OTHER_ORIGIN, OTHER_ORIGIN_NAME, 3600),
        (REQUEST_DATE, REQUEST_ORIGIN, REQUEST_NAME, 12 * 3600),
    ]
    for departure_date, origin, name_prefix, start in groups:
        for index in range(EVENTS_PER_GROUP):
            detected = _stamp(departure_date, start + index)
            pnr = f"{name_prefix}{index:04d}"
            changes.append(_change(
                FLIGHT, origin, departure_date, "CHECKED_IN", detected,
                f"{name_prefix}{index:04d}", pnr,
            ))
            changes.append(_change(
                FLIGHT, origin, departure_date, "BOARDED", detected,
                f"{name_prefix}{index:04d}", pnr, new_value="boarded",
            ))
            if index < EVENTS_PER_GROUP // 2:
                changes.append(_change(
                    FLIGHT, origin, departure_date, "STATUS_CHANGE", detected,
                    "", "", new_value="BOARDING",
                ))
    snapshots = [{
        "flightNumber": FLIGHT,
        "origin": REQUEST_ORIGIN,
        "departureDate": REQUEST_DATE,
        "snapshotType": "passenger_list",
        "sequenceNumber": 4,
        "fetchedAt": f"{REQUEST_DATE}T12:00:00Z",
        "data": {"totalPassengers": EVENTS_PER_GROUP},
    }]
    return changes, snapshots


def _matches(doc, query):
    """Equality match, which is the only filter these routes send."""
    for key, expected in (query or {}).items():
        if isinstance(expected, dict):
            raise AssertionError(f"unexpected query operator: {key}={expected}")
        if doc.get(key) != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return doc
    if isinstance(projection, dict) and any(value == 0 for value in projection.values()):
        projected = deepcopy(doc)
        for key, value in projection.items():
            if value == 0:
                projected.pop(key, None)
        return projected
    if isinstance(projection, dict):
        projected = {}
        for key, value in projection.items():
            if value and key in doc:
                projected[key] = deepcopy(doc[key])
        if "_id" not in projection and "_id" in doc:
            projected["_id"] = doc["_id"]
        return projected
    return doc


class _Cursor:
    """PyMongo cursor stand-in: nothing is read until iteration, after limit()."""

    def __init__(self, collection, query, projection, limit, sort):
        self._collection = collection
        self._query = query
        self._projection = projection
        self._limit = limit
        self._sort = sort

    def sort(self, key_or_list, direction=None):
        if direction is None and isinstance(key_or_list, str):
            direction = 1
        if isinstance(key_or_list, str):
            self._sort = [(key_or_list, direction)]
        else:
            normalized = []
            for item in key_or_list:
                if isinstance(item, str):
                    normalized.append((item, 1))
                else:
                    normalized.append((item[0], item[1]))
            self._sort = normalized
        return self

    def limit(self, limit):
        # Cursor.limit: a limit of 0 is equivalent to no limit.
        self._limit = limit
        return self

    def _selected(self):
        docs = [doc for doc in self._collection.docs if _matches(doc, self._query)]
        if self._sort:
            for key, direction in reversed(self._sort):
                reverse = direction in (-1, "desc", "descending")
                docs.sort(
                    key=lambda doc, field=key: (doc.get(field) is None, str(doc.get(field))),
                    reverse=reverse,
                )
        # message.py omits limit when it is 0; negative limit is abs(limit).
        if isinstance(self._limit, int) and self._limit != 0:
            docs = docs[:abs(self._limit)]
        return docs

    def __iter__(self):
        selected = self._selected()
        with self._collection.lock:
            self._collection.queries.append({
                "filter": deepcopy(self._query),
                "limit": self._limit,
                "yielded": len(selected),
            })
            self._collection.materialized += len(selected)
        for doc in selected:
            yield _project(deepcopy(doc), self._projection)


class _Collection:
    def __init__(self, docs):
        self.docs = docs
        self.queries = []
        self.materialized = 0
        self.lock = threading.Lock()

    def find(self, filter=None, projection=None, skip=0, limit=0, sort=None, **kwargs):
        return _Cursor(self, filter or {}, projection, limit, sort)

    def find_one(self, filter=None, *args, **kwargs):
        # Collection.find_one iterates find(...).limit(-1) and returns one doc.
        projection = args[0] if args else kwargs.get("projection")
        cursor = self.find(
            filter,
            projection,
            limit=-1,
            sort=kwargs.get("sort"),
        )
        for doc in cursor:
            return doc
        return None


class _Database:
    def __init__(self, changes, snapshots):
        self._collections = {
            "changes": _Collection(changes),
            "snapshots": _Collection(snapshots),
        }

    def __getitem__(self, name):
        return self._collections[name]


@pytest.fixture()
def api():
    changes, snapshots = _seed_documents()
    database = _Database(changes, snapshots)
    app = FastAPI()
    app.include_router(changes_routes.router)
    app.dependency_overrides.clear()

    def _db():
        return database

    changes_routes_get_db = changes_routes.get_db
    changes_routes.get_db = _db
    client = TestClient(app)
    try:
        yield client, database
    finally:
        changes_routes.get_db = changes_routes_get_db


def _assert_refused_without_reading_changes(response, database):
    changes = database["changes"]
    leaked = {
        HIST_NAME: HIST_NAME in response.text,
        OTHER_ORIGIN_NAME: OTHER_ORIGIN_NAME in response.text,
        REQUEST_NAME: REQUEST_NAME in response.text,
    }
    assert response.status_code in (400, 422) and changes.materialized == 0, (
        f"status={response.status_code} materialized={changes.materialized} "
        f"queries={changes.queries} leaked_names={leaked}"
    )
    assert not any(leaked.values())


def _assert_no_passenger_identity(points):
    for point in points:
        passenger = point.get("passenger")
        assert not passenger, point
        blob = repr(point)
        assert HIST_NAME not in blob
        assert OTHER_ORIGIN_NAME not in blob
        assert REQUEST_NAME not in blob


def _assert_changes_queries_bounded(database):
    queries = database["changes"].queries
    assert queries, "expected the route to read changes for a fully qualified flight"
    for query in queries:
        assert query["yielded"] <= SERIES_DOCUMENT_CAP, query
        filt = query["filter"]
        assert filt.get("flightNumber") == FLIGHT
        assert filt.get("origin") == REQUEST_ORIGIN
        assert filt.get("departureDate") == REQUEST_DATE


def test_boarding_progress_route_is_mounted_without_credentials(api):
    """The product router serves this path with no Authorization header."""
    client, _database = api
    response = client.get(
        f"/flights/{FLIGHT}/boarding-progress",
        params={"origin": REQUEST_ORIGIN, "date": REQUEST_DATE},
    )
    assert response.status_code != 401
    assert response.status_code != 403
    assert response.status_code == 200


@pytest.mark.parametrize("params", [
    {},
    {"date": REQUEST_DATE},
    {"origin": REQUEST_ORIGIN},
])
def test_boarding_progress_requires_date_and_origin_before_loading(api, params):
    client, database = api
    response = client.get(f"/flights/{FLIGHT}/boarding-progress", params=params)
    _assert_refused_without_reading_changes(response, database)


def test_boarding_progress_bounds_series_and_omits_passengers(api):
    client, database = api
    response = client.get(
        f"/flights/{FLIGHT}/boarding-progress",
        params={"origin": REQUEST_ORIGIN, "date": REQUEST_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    checkins = body["checkinProgress"]["series"]
    boardings = body["boardingProgress"]["series"]
    milestones = body["milestones"]

    assert len(checkins) <= SERIES_DOCUMENT_CAP
    assert len(boardings) <= SERIES_DOCUMENT_CAP
    assert len(milestones) <= SERIES_DOCUMENT_CAP
    # The fixture has more events than the cap, so an unbounded series is visible.
    assert EVENTS_PER_GROUP > SERIES_DOCUMENT_CAP
    _assert_no_passenger_identity(checkins)
    _assert_no_passenger_identity(boardings)
    _assert_changes_queries_bounded(database)

    text = response.text
    assert OTHER_DATE not in text
    assert "T01:" not in text
    assert HIST_NAME not in text
    assert OTHER_ORIGIN_NAME not in text
    assert REQUEST_NAME not in text


def test_parallel_boarding_progress_calls_each_stay_bounded(api):
    """Overlapping calls must not each retain the full history."""
    client, database = api

    def one_call():
        return client.get(
            f"/flights/{FLIGHT}/boarding-progress",
            params={"origin": REQUEST_ORIGIN, "date": REQUEST_DATE},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = [future.result() for future in [pool.submit(one_call) for _ in range(4)]]

    for response in responses:
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["checkinProgress"]["series"]) <= SERIES_DOCUMENT_CAP
        assert len(body["boardingProgress"]["series"]) <= SERIES_DOCUMENT_CAP
        _assert_no_passenger_identity(body["checkinProgress"]["series"])
        _assert_no_passenger_identity(body["boardingProgress"]["series"])

    queries = database["changes"].queries
    assert len(queries) >= 4
    for query in queries:
        assert query["yielded"] <= SERIES_DOCUMENT_CAP, query


@pytest.mark.parametrize("params", [
    {},
    {"date": REQUEST_DATE},
    {"origin": REQUEST_ORIGIN},
])
def test_flight_timeline_requires_date_and_origin_before_loading(api, params):
    client, database = api
    response = client.get(f"/flights/{FLIGHT}/timeline", params=params)
    _assert_refused_without_reading_changes(response, database)


def test_flight_timeline_bounds_events_and_omits_passengers(api):
    client, database = api
    response = client.get(
        f"/flights/{FLIGHT}/timeline",
        params={"origin": REQUEST_ORIGIN, "date": REQUEST_DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    events = body["events"]
    change_events = [event for event in events if event.get("eventType") != "SNAPSHOT_TAKEN"]
    assert len(change_events) <= SERIES_DOCUMENT_CAP
    _assert_no_passenger_identity(events)
    _assert_changes_queries_bounded(database)
    text = response.text
    assert OTHER_DATE not in text
    assert HIST_NAME not in text
    assert OTHER_ORIGIN_NAME not in text
    assert REQUEST_NAME not in text
