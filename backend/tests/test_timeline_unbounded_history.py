"""Failing spec for unbounded flight-history reads.

An anonymous GET that omits departure date or origin must not read or return
stored changes, snapshot headers, or passenger identities from other
departures. These tests fail while that query is still optional and uncapped.

The in-memory collection implements the PyMongo calls these handlers make:
equality filters (pymongo 4.20 collection.py find), lazy iteration that
returns every match when limit is 0 (cursor_shared.py limit; synchronous
cursor.py _refresh/next), and find_one as find plus limit(-1)
(collection.py find_one).
"""

import copy
import os
import sys
from collections import Counter

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import audit, changes, flights

FLIGHT = "2057"
ORIGIN = "BAH"
OTHER_ORIGIN = "DXB"
DATE = "2026-04-01"
OTHER_DATE = "2026-05-02"
OTHER_LAST = "OTHERDAYNAME"
OTHER_FIRST = "OTHERFIRST"
OTHER_PNR = "OD9Z9Z"
OTHER_SEAT = "SEATOTHER99"
OTHER_ORIGIN_LAST = "DXBONLYNAME"
OTHER_ORIGIN_PNR = "DXB000"
SCOPE_LAST = "SCOPEPAXNAME"
SCOPE_FIRST = "SCOPEFIRST"
SCOPE_PNR = "SC0001"
# Same bound as GET /flights/{flight_number}/changes (default limit).
MAX_PAGE = 100
PAGE_SIZE = 120

_MISSING = object()


def _get_path(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _match_value(actual, expected):
    if isinstance(expected, dict):
        if "$in" in expected:
            return actual is not _MISSING and actual in expected["$in"]
        if "$exists" in expected:
            exists = actual is not _MISSING
            return exists if expected["$exists"] else not exists
        if "$lte" in expected:
            return actual is not _MISSING and actual <= expected["$lte"]
        if "$gte" in expected:
            return actual is not _MISSING and actual >= expected["$gte"]
        raise AssertionError(f"query operator is not implemented by the test double: {expected}")
    return actual == expected


def _matches(doc, query):
    for key, expected in (query or {}).items():
        if not _match_value(_get_path(doc, key), expected):
            return False
    return True


def _project(doc, projection):
    if not projection:
        return doc
    includes = {key for key, value in projection.items() if value in (1, True)}
    excludes = {key for key, value in projection.items() if value in (0, False)}
    if includes:
        projected = {}
        if "_id" not in excludes and "_id" in doc:
            projected["_id"] = doc["_id"]
        for key in includes:
            if key == "_id":
                continue
            value = _get_path(doc, key)
            if value is _MISSING:
                continue
            cursor = projected
            parts = key.split(".")
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = copy.deepcopy(value)
        return projected
    for key in excludes:
        parts = key.split(".")
        cursor = doc
        for part in parts[:-1]:
            if not isinstance(cursor, dict) or part not in cursor:
                cursor = None
                break
            cursor = cursor[part]
        if isinstance(cursor, dict):
            cursor.pop(parts[-1], None)
    return doc


def _sort_value(value):
    if value is _MISSING or value is None:
        return ""
    return value


class MemoryCursor:
    """Lazy cursor. Documents are read only when iteration starts."""

    _ids = 0

    def __init__(self, collection, query, projection):
        MemoryCursor._ids += 1
        self.cursor_id = MemoryCursor._ids
        self.collection = collection
        self.query = copy.deepcopy(query or {})
        self.projection = copy.deepcopy(projection) if projection else None
        self._limit = 0  # PyMongo: 0 means no limit
        self._sort = None
        self._pending = None

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, list):
            specs = []
            for item in key_or_list:
                if isinstance(item, str):
                    specs.append((item, 1))
                else:
                    specs.append((item[0], int(item[1])))
            self._sort = specs
        else:
            if direction in (None, "asc"):
                direction = 1
            elif direction == "desc":
                direction = -1
            self._sort = [(key_or_list, int(direction))]
        return self

    def limit(self, limit):
        if not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        self._limit = limit
        return self

    def __iter__(self):
        return self

    def __next__(self):
        if self._pending is None:
            matched = [doc for doc in self.collection.docs if _matches(doc, self.query)]
            if self._sort:
                for field, direction in reversed(self._sort):
                    matched.sort(
                        key=lambda doc, field=field: _sort_value(_get_path(doc, field)),
                        reverse=direction < 0,
                    )
            # A negative limit still returns abs(limit) documents (find_one uses -1).
            if self._limit not in (None, 0):
                matched = matched[: abs(self._limit)]
            self._pending = list(matched)
        if not self._pending:
            raise StopIteration
        source = self._pending.pop(0)
        self.collection.db.reads.append(
            {
                "collection": self.collection.name,
                "cursor_id": self.cursor_id,
                "filter": self.query,
                "limit": self._limit,
                "doc": source,
            }
        )
        return _project(copy.deepcopy(source), self.projection)


class MemoryCollection:
    def __init__(self, name, db):
        self.name = name
        self.db = db
        self.docs = []

    def find(self, query=None, projection=None, *args, **kwargs):
        cursor = MemoryCursor(self, query, projection)
        if kwargs.get("sort") is not None:
            cursor.sort(kwargs["sort"])
        if kwargs.get("limit") not in (None, 0):
            cursor.limit(kwargs["limit"])
        return cursor

    def find_one(self, query=None, *args, **kwargs):
        # pymongo Collection.find_one iterates find(...).limit(-1).
        cursor = self.find(query, *args, **kwargs)
        for result in cursor.limit(-1):
            return result
        return None


class MemoryDatabase:
    def __init__(self):
        self._collections = {}
        self.reads = []

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection(name, self)
        return self._collections[name]


def _change(origin, date, detected_at, pnr, last_name, first_name, change_type, old_value=None, new_value=None):
    doc = {
        "flightNumber": FLIGHT,
        "origin": origin,
        "departureDate": date,
        "changeType": change_type,
        "detectedAt": detected_at,
        "passenger": {
            "pnr": pnr,
            "lastName": last_name,
            "firstName": first_name,
        },
    }
    if old_value is not None:
        doc["oldValue"] = old_value
    if new_value is not None:
        doc["newValue"] = new_value
    return doc


def _snapshot(origin, date, sequence, fetched_at, pnr, last_name, first_name):
    return {
        "flightNumber": FLIGHT,
        "origin": origin,
        "departureDate": date,
        "snapshotType": "passenger_list",
        "sequenceNumber": sequence,
        "fetchedAt": fetched_at,
        "capturedAt": fetched_at,
        "data": {
            "totalPassengers": 1,
            "passengers": [
                {
                    "pnr": pnr,
                    "lastName": last_name,
                    "firstName": first_name,
                    "isCheckedIn": False,
                    "isBoarded": False,
                    "cabin": "Y",
                }
            ],
        },
    }


def _seed_pair(db):
    """In-scope departure, plus another date and another origin that sort first."""
    db["changes"].docs.append(
        _change(
            ORIGIN,
            OTHER_DATE,
            "2020-01-01T00:00:00Z",
            OTHER_PNR,
            OTHER_LAST,
            OTHER_FIRST,
            "CHECKED_IN",
        )
    )
    db["changes"].docs.append(
        _change(
            ORIGIN,
            OTHER_DATE,
            "2020-01-01T00:00:01Z",
            OTHER_PNR,
            OTHER_LAST,
            OTHER_FIRST,
            "SEAT_CHANGE",
            old_value="1A",
            new_value=OTHER_SEAT,
        )
    )
    db["changes"].docs.append(
        _change(
            OTHER_ORIGIN,
            DATE,
            "2020-01-01T00:00:02Z",
            OTHER_ORIGIN_PNR,
            OTHER_ORIGIN_LAST,
            "DXBFIRST",
            "CHECKED_IN",
        )
    )
    db["changes"].docs.append(
        _change(
            ORIGIN,
            DATE,
            "2026-04-01T12:00:00Z",
            SCOPE_PNR,
            SCOPE_LAST,
            SCOPE_FIRST,
            "CHECKED_IN",
        )
    )
    db["snapshots"].docs.append(
        _snapshot(ORIGIN, OTHER_DATE, 1, "2020-01-01T00:00:00Z", OTHER_PNR, OTHER_LAST, OTHER_FIRST)
    )
    db["snapshots"].docs.append(
        _snapshot(OTHER_ORIGIN, DATE, 2, "2020-01-01T00:00:02Z", OTHER_ORIGIN_PNR, OTHER_ORIGIN_LAST, "DXBFIRST")
    )
    db["snapshots"].docs.append(
        _snapshot(ORIGIN, DATE, 3, "2026-04-01T12:00:00Z", SCOPE_PNR, SCOPE_LAST, SCOPE_FIRST)
    )


def _seed_page(db):
    """More than one page of in-scope rows, plus an earlier out-of-scope row."""
    _seed_pair(db)
    for index in range(PAGE_SIZE - 1):
        db["changes"].docs.append(
            _change(
                ORIGIN,
                DATE,
                f"2026-04-01T13:{index // 60:02d}:{index % 60:02d}Z",
                SCOPE_PNR,
                SCOPE_LAST,
                SCOPE_FIRST,
                "CHECKED_IN",
            )
        )
        db["snapshots"].docs.append(
            _snapshot(
                ORIGIN,
                DATE,
                4 + index,
                f"2026-04-01T13:{index // 60:02d}:{index % 60:02d}Z",
                SCOPE_PNR,
                SCOPE_LAST,
                SCOPE_FIRST,
            )
        )


def _markers():
    return [OTHER_LAST, OTHER_FIRST, OTHER_PNR, OTHER_SEAT, OTHER_ORIGIN_LAST, OTHER_ORIGIN_PNR]


def _assert_absent(text, markers):
    for marker in markers:
        assert marker not in text


def _assert_refused(response, db):
    assert response.status_code in (400, 422), response.text
    assert db.reads == []
    _assert_absent(response.text, _markers())


def _cursor_sizes(db, collection):
    counts = Counter(
        read["cursor_id"] for read in db.reads if read["collection"] == collection
    )
    return list(counts.values())


def _assert_reads_stay_on_departure(db):
    assert db.reads, "scoped request did not read the stored departure"
    for read in db.reads:
        doc = read["doc"]
        if "departureDate" in doc:
            assert doc["departureDate"] == DATE
        if "origin" in doc:
            assert doc["origin"] == ORIGIN
        assert read["filter"].get("departureDate") == DATE
        assert read["filter"].get("origin") == ORIGIN
        assert read["filter"].get("flightNumber") == FLIGHT


@pytest.fixture
def client():
    db = MemoryDatabase()
    app = FastAPI()
    app.include_router(changes.router)
    app.include_router(audit.router)
    app.include_router(flights.router)

    def get_db():
        return db

    changes.get_db = get_db
    audit.get_db = get_db
    flights.get_db = get_db
    with TestClient(app) as test_client:
        yield test_client, db


def _get(client, url):
    return client.get(url, headers={})


def test_anonymous_timeline_without_date_or_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/timeline")
    assert "authorization" not in {key.lower() for key in response.request.headers}
    _assert_refused(response, db)


def test_timeline_without_date_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/timeline?origin={ORIGIN}")
    _assert_refused(response, db)


def test_timeline_without_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/timeline?date={DATE}")
    _assert_refused(response, db)


def test_scoped_timeline_pages_changes_and_snapshots(client):
    http, db = client
    _seed_page(db)
    response = _get(http, f"/flights/{FLIGHT}/timeline?origin={ORIGIN}&date={DATE}")
    assert response.status_code == 200, response.text
    assert "authorization" not in {key.lower() for key in response.request.headers}
    body = response.json()
    _assert_absent(response.text, _markers())
    _assert_reads_stay_on_departure(db)
    assert _cursor_sizes(db, "changes")
    assert _cursor_sizes(db, "snapshots")
    assert all(size <= MAX_PAGE for size in _cursor_sizes(db, "changes"))
    assert all(size <= MAX_PAGE for size in _cursor_sizes(db, "snapshots"))
    events = body["events"]
    snapshot_events = [event for event in events if event.get("category") == "snapshot"]
    change_events = [event for event in events if event.get("category") != "snapshot"]
    assert 0 < len(change_events) <= MAX_PAGE
    assert len(snapshot_events) <= MAX_PAGE
    assert body["hasMore"] is True
    assert SCOPE_PNR in response.text or SCOPE_LAST in response.text


def test_anonymous_boarding_progress_without_date_or_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/boarding-progress")
    assert "authorization" not in {key.lower() for key in response.request.headers}
    _assert_refused(response, db)


def test_boarding_progress_without_date_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/boarding-progress?origin={ORIGIN}")
    _assert_refused(response, db)


def test_boarding_progress_without_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/boarding-progress?date={DATE}")
    _assert_refused(response, db)


def test_scoped_boarding_progress_pages_passenger_series(client):
    http, db = client
    _seed_page(db)
    response = _get(http, f"/flights/{FLIGHT}/boarding-progress?origin={ORIGIN}&date={DATE}")
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_absent(response.text, _markers())
    _assert_reads_stay_on_departure(db)
    assert all(size <= MAX_PAGE for size in _cursor_sizes(db, "changes"))
    series = body["checkinProgress"]["series"]
    assert 0 < len(series) <= MAX_PAGE
    assert len(body["boardingProgress"]["series"]) <= MAX_PAGE
    assert body["hasMore"] is True


def test_passenger_timeline_without_date_or_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/passengers/{OTHER_PNR}/timeline")
    _assert_refused(response, db)


def test_passenger_timeline_without_date_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/passengers/{OTHER_PNR}/timeline?origin={ORIGIN}")
    _assert_refused(response, db)


def test_passenger_timeline_without_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/passengers/{SCOPE_PNR}/timeline?date={DATE}")
    _assert_refused(response, db)


def test_scoped_passenger_timeline_pages_one_record(client):
    http, db = client
    _seed_page(db)
    for index in range(PAGE_SIZE):
        db["changes"].docs.append(
            _change(
                ORIGIN,
                DATE,
                f"2026-04-01T14:{index // 60:02d}:{index % 60:02d}Z",
                SCOPE_PNR,
                SCOPE_LAST,
                SCOPE_FIRST,
                "SEAT_CHANGE",
                old_value="10A",
                new_value=f"1{index:02d}A",
            )
        )
    response = _get(
        http,
        f"/flights/{FLIGHT}/passengers/{SCOPE_PNR}/timeline?origin={ORIGIN}&date={DATE}",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_absent(response.text, _markers())
    _assert_reads_stay_on_departure(db)
    assert all(size <= MAX_PAGE for size in _cursor_sizes(db, "changes"))
    assert 0 < body["eventCount"] <= MAX_PAGE
    assert len(body["events"]) <= MAX_PAGE
    assert body["hasMore"] is True


def test_history_badges_without_date_or_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/passengers/history-badges")
    _assert_refused(response, db)


def test_history_badges_without_date_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/passengers/history-badges?origin={ORIGIN}")
    _assert_refused(response, db)


def test_history_badges_without_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/passengers/history-badges?date={DATE}")
    _assert_refused(response, db)


def test_scoped_history_badges_return_only_that_departure(client):
    http, db = client
    _seed_pair(db)
    response = _get(
        http,
        f"/flights/{FLIGHT}/passengers/history-badges?origin={ORIGIN}&date={DATE}",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert SCOPE_PNR in body
    assert OTHER_PNR not in body
    _assert_reads_stay_on_departure(db)


def test_audit_without_date_or_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/audit")
    _assert_refused(response, db)


def test_audit_without_date_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/audit?origin={ORIGIN}")
    _assert_refused(response, db)


def test_audit_without_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/audit?date={DATE}")
    _assert_refused(response, db)


def test_scoped_audit_does_not_read_other_departures(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/audit?origin={ORIGIN}&date={DATE}")
    assert response.status_code == 200, response.text
    _assert_absent(response.text, _markers())
    _assert_reads_stay_on_departure(db)


def test_phase_journey_without_date_or_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/phase-journey")
    _assert_refused(response, db)


def test_phase_journey_without_date_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/phase-journey?origin={ORIGIN}")
    _assert_refused(response, db)


def test_phase_journey_without_origin_reads_nothing(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/phase-journey?date={DATE}")
    _assert_refused(response, db)


def test_scoped_phase_journey_does_not_read_other_departures(client):
    http, db = client
    _seed_pair(db)
    response = _get(http, f"/flights/{FLIGHT}/phase-journey?origin={ORIGIN}&date={DATE}")
    assert response.status_code == 200, response.text
    _assert_absent(response.text, _markers())
    _assert_reads_stay_on_departure(db)
