"""Bounds for change-history reads on the passenger timeline and the same bug.

These tests exercise the live route handlers. They fail while a request can
pull every matching change document into the API process. They pass when each
handler either rejects a date-less request before reading, or applies a hard
cap and does not load unused change fields.

The in-memory collection is a stand-in for MongoDB. It follows PyMongo 4.20.1,
which this project allows (``pymongo>=4.6,<5``):

- ``Collection.find`` defaults ``limit`` to 0, and 0 means no limit
  (``pymongo/synchronous/collection.py``).
- ``Cursor.limit`` stores that integer; 0 still means no limit
  (``pymongo/cursor_shared.py``).
- Iterating a cursor pulls documents until the cursor is exhausted, and a
  positive limit is sent with the query (``pymongo/synchronous/cursor.py``).
- A projection list becomes an inclusion dict
  (``pymongo/helpers_shared.py``). An empty projection returns the whole
  document (``Collection.find`` notes for PyMongo 4.0).

Run:

    PYTHONPATH=. python -m pytest backend/tests/test_unbounded_timeline_load.py -q
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import audit as audit_routes
from backend.api.routes import changes as changes_routes

# Same ceiling the change-list routes already advertise: Query(..., le=1000).
MAX_CHANGE_DOCUMENTS = 1000
HISTORY_OVER_THE_CAP = MAX_CHANGE_DOCUMENTS + 1
REFUSAL_STATUSES = {400, 422}
FLIGHT = "2057"
PNR = "ABC123"
OTHER_PNR = "ZZZ999"
DEPARTURE = "2026-11-21"


class _Cursor:
    """Equality-filter cursor with PyMongo's limit(0) == unlimited rule."""

    def __init__(self, docs, projection, skip, limit):
        self._docs = docs
        self._projection = projection
        self._skip = skip
        self._limit = limit
        self._sort = None
        self.fetched = 0
        self.yielded = []
        self._ready = None

    def sort(self, key_or_list, direction=None):
        specs = []
        if isinstance(key_or_list, str):
            specs.append((key_or_list, 1 if direction is None else direction))
        else:
            for item in key_or_list:
                if isinstance(item, str):
                    specs.append((item, 1))
                else:
                    specs.append((item[0], item[1]))
        self._sort = specs
        return self

    def skip(self, skip):
        if not isinstance(skip, int):
            raise TypeError("skip must be an integer")
        self._skip = skip
        return self

    def limit(self, limit):
        if not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        # PyMongo: the last limit wins, and 0 means no limit.
        self._limit = limit
        return self

    def __iter__(self):
        return self

    def __next__(self):
        if self._ready is None:
            docs = list(self._docs)
            if self._sort:
                for field, direction in reversed(self._sort):
                    docs.sort(
                        key=lambda doc, field=field: _sort_value(doc, field),
                        reverse=direction < 0,
                    )
            if self._skip:
                docs = docs[self._skip:]
            if self._limit < 0:
                docs = docs[: abs(self._limit)]
            elif self._limit > 0:
                docs = docs[: self._limit]
            self._ready = iter(docs)
        try:
            raw = next(self._ready)
        except StopIteration:
            raise
        projected = _apply_projection(raw, self._projection)
        self.fetched += 1
        self.yielded.append(projected)
        return projected


class _Collection:
    def __init__(self, name):
        self.name = name
        self.docs = []
        self.cursors = []

    def find(self, filter=None, projection=None, skip=0, limit=0, sort=None, **_kwargs):
        matched = [doc for doc in self.docs if _matches(doc, filter or {})]
        cursor = _Cursor(matched, _normalize_projection(projection), skip, limit)
        if sort is not None:
            cursor.sort(sort)
        self.cursors.append(cursor)
        return cursor

    def find_one(self, filter=None, *args, **kwargs):
        # PyMongo find_one is find().limit(-1) and returns the first document.
        sort = kwargs.pop("sort", None)
        cursor = self.find(filter, *args, **kwargs)
        if sort is not None:
            cursor.sort(sort)
        cursor.limit(-1)
        for result in cursor:
            return result
        return None


class _Db:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection(name)
        return self._collections[name]

    def add(self, name, doc):
        self[name].docs.append(doc)


def _normalize_projection(projection):
    if projection is None:
        return None
    if isinstance(projection, dict):
        return projection
    if isinstance(projection, (list, tuple, set)):
        if not all(isinstance(field, str) for field in projection):
            raise TypeError("projection must be a list of key names")
        # PyMongo _fields_list_to_dict: ["a", "b"] -> {"a": 1, "b": 1}.
        return dict.fromkeys(projection, 1)
    raise TypeError("projection must be a mapping or list of key names")


def _get_path(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _set_path(dest, path, value):
    parts = path.split(".")
    current = dest
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = copy.deepcopy(value)


def _del_path(dest, path):
    parts = path.split(".")
    current = dest
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _apply_projection(doc, projection):
    if not projection:
        # None or {} returns the entire document (PyMongo 4.0).
        return copy.deepcopy(doc)
    non_id = {key: value for key, value in projection.items() if key != "_id"}
    if not non_id:
        projected = copy.deepcopy(doc)
        if projection.get("_id") in (0, False):
            projected.pop("_id", None)
        return projected
    inclusion = [value in (1, True) for value in non_id.values()]
    if any(inclusion) and not all(inclusion):
        raise ValueError("projection cannot mix inclusion and exclusion")
    if all(inclusion):
        projected = {}
        if projection.get("_id", 1) not in (0, False) and "_id" in doc:
            projected["_id"] = copy.deepcopy(doc["_id"])
        for key, flag in non_id.items():
            if flag in (1, True):
                value, present = _get_path(doc, key)
                if present:
                    _set_path(projected, key, value)
        return projected
    projected = copy.deepcopy(doc)
    for key, flag in projection.items():
        if flag in (0, False):
            _del_path(projected, key)
    return projected


def _matches(doc, query):
    for key, expected in query.items():
        value, present = _get_path(doc, key)
        if not _value_matches(value, present, expected):
            return False
    return True


def _value_matches(value, present, expected):
    if not isinstance(expected, dict):
        return present and value == expected
    for op, operand in expected.items():
        if op == "$eq":
            if value != operand:
                return False
        elif op == "$in":
            if value not in operand:
                return False
        elif op == "$exists":
            if bool(present) != bool(operand):
                return False
        elif op == "$gt":
            if not present or not value > operand:
                return False
        elif op == "$gte":
            if not present or not value >= operand:
                return False
        elif op == "$lt":
            if not present or not value < operand:
                return False
        elif op == "$lte":
            if not present or not value <= operand:
                return False
        else:
            raise AssertionError(f"test stand-in has no query operator {op}")
    return True


def _sort_value(doc, field):
    value, present = _get_path(doc, field)
    if not present or value is None:
        return ("",)
    return (str(value),)


def _change(index, *, flight=FLIGHT, pnr=PNR, departure=DEPARTURE, change_type="CHECKED_IN"):
    return {
        "_id": f"chg-{flight}-{pnr}-{departure}-{index}",
        "flightNumber": flight,
        "origin": "BAH",
        "departureDate": departure,
        "changeType": change_type,
        "passenger": {"pnr": pnr, "lastName": "DOE", "firstName": "JANE"},
        "detectedAt": f"2026-11-21T{(index // 3600) % 24:02d}:{(index // 60) % 60:02d}:{index % 60:02d}+00:00",
        "sequenceNumber": index + 1,
        "field": "seat",
        "oldValue": "1A",
        "newValue": "2B",
        "metadata": {"originalCabin": "Y", "originalClass": "Y"},
        "beforeSnapshotId": f"before-{index}",
        "afterSnapshotId": f"after-{index}",
        # Stored on the change document and unused by the timeline response.
        "archiveBlob": f"unused-history-{index}",
    }


def _snapshot(index, *, flight=FLIGHT, departure=DEPARTURE):
    return {
        "_id": f"snap-{index}",
        "flightNumber": flight,
        "origin": "BAH",
        "departureDate": departure,
        "snapshotType": "passenger_list",
        "sequenceNumber": index + 1,
        "fetchedAt": f"2026-11-21T00:{(index // 60) % 60:02d}:{index % 60:02d}+00:00",
        "data": {"passengers": [{"pnr": PNR, "cabin": "Y", "seat": "1A"}]},
    }


def _dates(count):
    start = date(2020, 1, 1)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(count)]


def _client_for(db):
    app = FastAPI()
    app.include_router(changes_routes.router)
    app.include_router(audit_routes.router)

    def get_db():
        return db

    changes_routes.get_db = get_db
    audit_routes.get_db = get_db
    return TestClient(app)


@pytest.fixture
def api():
    db = _Db()
    client = _client_for(db)
    yield client, db
    client.close()


def _assert_no_full_documents(collection):
    for cursor in collection.cursors:
        for doc in cursor.yielded:
            assert "archiveBlob" not in doc, (
                "the handler loaded a change field the timeline response does not use"
            )


def _assert_each_cursor_capped(collection):
    for cursor in collection.cursors:
        assert cursor.fetched <= MAX_CHANGE_DOCUMENTS, (
            f"{collection.name} cursor materialized {cursor.fetched} documents; "
            f"cap is {MAX_CHANGE_DOCUMENTS}"
        )


def _assert_refused_before_read(response, *collections):
    assert response.status_code in REFUSAL_STATUSES, response.text
    for collection in collections:
        fetched = sum(cursor.fetched for cursor in collection.cursors)
        assert fetched == 0, (
            f"refused with HTTP {response.status_code} after reading {fetched} "
            f"{collection.name} documents"
        )


def _bounded_or_refused(response, *collections, allow_refusal):
    if allow_refusal and response.status_code in REFUSAL_STATUSES:
        _assert_refused_before_read(response, *collections)
        return None
    assert response.status_code == 200, response.text
    for collection in collections:
        _assert_each_cursor_capped(collection)
        if collection.name == "changes":
            _assert_no_full_documents(collection)
    return response.json()


def test_changes_list_limit_is_enforced_by_cursor(api):
    """Harness check: the sibling changes list already calls Cursor.limit."""
    client, db = api
    for index in range(HISTORY_OVER_THE_CAP):
        db.add("changes", _change(index))
    response = client.get(f"/flights/{FLIGHT}/changes?limit=10")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 10
    assert db["changes"].cursors[0].fetched == 10


def test_small_passenger_timeline_still_returns_its_events(api):
    client, db = api
    db.add("changes", _change(0, change_type="PASSENGER_ADDED"))
    db.add("changes", _change(1, change_type="CHECKED_IN"))
    db.add("changes", _change(2, pnr=OTHER_PNR, change_type="BOARDED"))
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline?date={DEPARTURE}"
    )
    body = _bounded_or_refused(response, db["changes"], allow_refusal=False)
    assert body["eventCount"] == 2
    assert len(body["events"]) == 2
    descriptions = {event["description"] for event in body["events"]}
    assert "Passenger added to manifest" in descriptions
    assert "Passenger checked in" in descriptions
    assert all(event["changeType"] != "BOARDED" for event in body["events"])


def test_passenger_timeline_without_date_does_not_load_every_change(api):
    client, db = api
    for index, departure in enumerate(_dates(HISTORY_OVER_THE_CAP)):
        db.add("changes", _change(index, departure=departure))
    response = client.get(f"/flights/{FLIGHT}/passengers/{PNR}/timeline")
    body = _bounded_or_refused(response, db["changes"], allow_refusal=True)
    if body is None:
        return
    assert body["eventCount"] <= MAX_CHANGE_DOCUMENTS
    assert len(body["events"]) <= MAX_CHANGE_DOCUMENTS
    assert body["departureDate"] in (None, "")


def test_passenger_timeline_with_date_does_not_load_every_change(api):
    client, db = api
    for index in range(HISTORY_OVER_THE_CAP):
        db.add("changes", _change(index, change_type="SEAT_CHANGE"))
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/timeline?date={DEPARTURE}"
    )
    body = _bounded_or_refused(response, db["changes"], allow_refusal=False)
    assert body["eventCount"] <= MAX_CHANGE_DOCUMENTS
    assert len(body["events"]) <= MAX_CHANGE_DOCUMENTS


def test_flight_timeline_without_date_does_not_load_every_change(api):
    client, db = api
    for index, departure in enumerate(_dates(HISTORY_OVER_THE_CAP)):
        db.add("changes", _change(index, departure=departure, pnr=f"P{index:04d}"))
        db.add("snapshots", _snapshot(index, departure=departure))
    response = client.get(f"/flights/{FLIGHT}/timeline")
    body = _bounded_or_refused(
        response, db["changes"], db["snapshots"], allow_refusal=True
    )
    if body is None:
        return
    change_events = [
        event for event in body["events"] if event.get("eventType") != "SNAPSHOT_TAKEN"
    ]
    snapshot_events = [
        event for event in body["events"] if event.get("eventType") == "SNAPSHOT_TAKEN"
    ]
    assert len(change_events) <= MAX_CHANGE_DOCUMENTS
    assert len(snapshot_events) <= MAX_CHANGE_DOCUMENTS


def test_flight_timeline_with_date_does_not_load_every_change(api):
    client, db = api
    for index in range(HISTORY_OVER_THE_CAP):
        db.add("changes", _change(index, pnr=f"P{index:04d}"))
        db.add("snapshots", _snapshot(index))
    response = client.get(f"/flights/{FLIGHT}/timeline?date={DEPARTURE}")
    body = _bounded_or_refused(
        response, db["changes"], db["snapshots"], allow_refusal=False
    )
    change_events = [
        event for event in body["events"] if event.get("eventType") != "SNAPSHOT_TAKEN"
    ]
    snapshot_events = [
        event for event in body["events"] if event.get("eventType") == "SNAPSHOT_TAKEN"
    ]
    assert len(change_events) <= MAX_CHANGE_DOCUMENTS
    assert len(snapshot_events) <= MAX_CHANGE_DOCUMENTS
    for doc in db["snapshots"].cursors[0].yielded:
        assert "data" not in doc


def test_boarding_progress_without_date_does_not_load_every_change(api):
    client, db = api
    for index, departure in enumerate(_dates(HISTORY_OVER_THE_CAP)):
        db.add(
            "changes",
            _change(index, departure=departure, pnr=f"P{index:04d}", change_type="CHECKED_IN"),
        )
    response = client.get(f"/flights/{FLIGHT}/boarding-progress")
    body = _bounded_or_refused(response, db["changes"], allow_refusal=True)
    if body is None:
        return
    assert len(body["checkinProgress"]["series"]) <= MAX_CHANGE_DOCUMENTS
    assert len(body["boardingProgress"]["series"]) <= MAX_CHANGE_DOCUMENTS
    assert len(body["milestones"]) <= MAX_CHANGE_DOCUMENTS


def test_boarding_progress_with_date_does_not_load_every_change(api):
    client, db = api
    for index in range(HISTORY_OVER_THE_CAP):
        db.add("changes", _change(index, pnr=f"P{index:04d}", change_type="CHECKED_IN"))
    db.add("changes", _change(HISTORY_OVER_THE_CAP, change_type="BOARDED"))
    db.add("changes", _change(HISTORY_OVER_THE_CAP + 1, change_type="STATUS_CHANGE"))
    response = client.get(f"/flights/{FLIGHT}/boarding-progress?date={DEPARTURE}")
    body = _bounded_or_refused(response, db["changes"], allow_refusal=False)
    assert len(body["checkinProgress"]["series"]) <= MAX_CHANGE_DOCUMENTS
    assert len(body["boardingProgress"]["series"]) <= MAX_CHANGE_DOCUMENTS
    assert len(body["milestones"]) <= MAX_CHANGE_DOCUMENTS


def test_flight_audit_without_date_does_not_load_every_change(api):
    client, db = api
    for index, departure in enumerate(_dates(HISTORY_OVER_THE_CAP)):
        db.add("changes", _change(index, departure=departure))
    response = client.get(f"/flights/{FLIGHT}/audit")
    _bounded_or_refused(response, db["changes"], allow_refusal=True)


def test_flight_audit_with_date_does_not_load_every_change(api):
    client, db = api
    for index in range(HISTORY_OVER_THE_CAP):
        db.add("changes", _change(index))
    response = client.get(f"/flights/{FLIGHT}/audit?date={DEPARTURE}")
    _bounded_or_refused(response, db["changes"], allow_refusal=False)
