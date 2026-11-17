"""Bounds for change-history reads.

These tests fail while GET handlers load every matching change document
for a flight. They pass once each handler:

- rejects a missing departure date before it reads ``changes``
- still reads that flight's history when a date is supplied
- caps the read at ``MAX_CHANGE_DOCUMENTS`` (1000, the same ceiling as
  ``GET /flights/{flight_number}/changes``)
- puts that cap on the MongoDB query (``find`` limit or aggregate
  ``$limit``) so the server stops, instead of slicing a fully loaded list

The cursor stand-in follows PyMongo 4.20.0, the version installed from
``requirements.txt`` (``pymongo>=4.6,<5``):

- ``Cursor.__init__`` defaults ``limit`` to 0
  (``pymongo/cursor_shared.py``)
- ``Cursor.limit`` stores that integer and treats 0 as no limit
  (``pymongo/cursor_shared.py``)
- ``_gen_find_command`` adds a ``limit`` field only when the value is
  non-zero, and uses ``abs(limit)`` (``pymongo/message.py``)
- ``Cursor.next`` keeps pulling documents until the cursor is exhausted
  (``pymongo/synchronous/cursor.py``)
- ``find`` ``batchSize`` limits a batch, not the cursor. The server
  default is no limit when ``limit`` is omitted, and further batches
  arrive through ``getMore`` (MongoDB find command).

``batch_size`` on this stand-in therefore does not cap how many
documents the handler receives. A client-side ``break`` after the
handler has already asked for an unlimited cursor still leaves the
recorded command without a limit.

Not covered here:

- ``GET /flights/{flight_number}/changes`` and
  ``GET /flights/{flight_number}/changes/passenger/{pnr}`` already call
  ``Cursor.limit`` (default 100, max 1000).
- ``GET /activity/feed`` already calls ``Cursor.limit`` (max 200).
- ``GET /flights/{flight_number}/passengers/history-badges`` has the
  same unlimited ``changes`` scan, but ``passengers.router`` is mounted
  first and ``GET /flights/{flight_number}/passengers/{pnr}`` handles
  that path. The badges function does not run for the HTTP request.
- ``GET /flights/{flight_number}/timeline`` and
  ``GET /flights/{flight_number}/phase-journey`` also load snapshot
  documents with no cap. Those reads are not change-history reads.
- Analytics and ``GET /comparison/change-summary`` aggregate on the
  server and require a date. They do not build the change list this
  finding describes.
"""

import os
import sys
import threading
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import InvalidOperation

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.audit_rules import run_audit as real_run_audit
from backend.api.routes import audit as audit_routes
from backend.api.routes import changes as changes_routes
from backend.api.routes import flights as flights_routes

MAX_CHANGE_DOCUMENTS = 1000
FLIGHT = "2057"
OTHER_FLIGHT = "9999"
DATE = "2026-03-19"
OTHER_DATE = "2026-03-18"
ORIGIN = "BAH"
OTHER_ORIGIN = "LHR"
PNR = "ABC123"
# More than the cap for every change type the boarding endpoint loads.
PER_TYPE = MAX_CHANGE_DOCUMENTS + 200

# Paths that share the audit handler's optional date and unlimited
# ``changes`` cursor. Each one is a route the app actually mounts.
UNBOUNDED_PATHS = [
    f"/flights/{FLIGHT}/audit",
    f"/flights/{FLIGHT}/timeline",
    f"/flights/{FLIGHT}/passengers/{PNR}/timeline",
    f"/flights/{FLIGHT}/boarding-progress",
    f"/flights/{FLIGHT}/changes/summary",
    f"/flights/{FLIGHT}/dashboard",
    f"/flights/{FLIGHT}/tree",
]


def _lookup(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _match(doc, query):
    for key, expected in query.items():
        value, found = _lookup(doc, key)
        if isinstance(expected, dict) and (
            "$exists" in expected or "$in" in expected or "$eq" in expected
        ):
            if "$exists" in expected and bool(found) != bool(expected["$exists"]):
                return False
            if "$eq" in expected and value != expected["$eq"]:
                return False
            if "$in" in expected and value not in expected["$in"]:
                return False
            continue
        if value != expected:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return doc
    inclusion = any(value == 1 for value in projection.values())
    if not inclusion:
        projected = deepcopy(doc)
        for key, value in projection.items():
            if value == 0 and key in projected:
                projected.pop(key, None)
        return projected
    projected = {}
    if projection.get("_id", 1) != 0 and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key, flag in projection.items():
        if key == "_id" or flag != 1:
            continue
        value, found = _lookup(doc, key)
        if not found:
            continue
        parts = key.split(".")
        cursor = projected
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return projected


class MemoryCursor:
    """Find cursor with PyMongo's limit rules, and no server behind it.

    Documents past the limit are never returned. With no limit, iteration
    returns every match, which is what ``Cursor.next`` does when the find
    command omits ``limit``.
    """

    def __init__(self, database, name, docs, query, projection, limit, batch_size):
        self._database = database
        self._name = name
        self._docs = docs
        self._filter = deepcopy(query or {})
        self._projection = projection
        self._limit = limit
        self._batch_size = batch_size
        self._sort = None
        self._executed = False
        self._queue = []
        self._record = None

    def sort(self, key_or_list, direction=None):
        if self._executed:
            raise InvalidOperation("cannot set options after executing query")
        if isinstance(key_or_list, str):
            self._sort = [(key_or_list, 1 if direction is None else direction)]
        else:
            keys = []
            for item in key_or_list:
                if isinstance(item, str):
                    keys.append((item, 1))
                else:
                    keys.append((item[0], item[1]))
            self._sort = keys
        return self

    def limit(self, limit):
        if not isinstance(limit, int):
            raise TypeError(f"limit must be an integer, not {type(limit)}")
        if self._executed:
            raise InvalidOperation("cannot set options after executing query")
        # 0 means no limit, matching Cursor.limit and the find command.
        self._limit = limit
        return self

    def _execute(self):
        if self._executed:
            return
        self._executed = True
        matched = [doc for doc in self._docs if _match(doc, self._filter)]
        if self._sort:
            for field, direction in reversed(self._sort):
                matched.sort(
                    key=lambda doc, field=field: (
                        not _lookup(doc, field)[1],
                        _lookup(doc, field)[0],
                    ),
                    reverse=direction < 0,
                )
        command_limit = abs(self._limit) if self._limit else None
        if command_limit is not None:
            matched = matched[:command_limit]
        self._queue = matched
        self._record = {
            "op": "find",
            "collection": self._name,
            "filter": deepcopy(self._filter),
            "limit": command_limit,
            "batch_size": self._batch_size or None,
            "delivered": 0,
        }
        self._database.commands.append(self._record)

    def __iter__(self):
        return self

    def __next__(self):
        self._execute()
        if not self._queue:
            raise StopIteration
        doc = deepcopy(self._queue.pop(0))
        self._record["delivered"] += 1
        return _project(doc, self._projection)


class MemoryCollection:
    def __init__(self, database, name):
        self._database = database
        self._name = name
        self._docs = database.documents.setdefault(name, [])

    def find(self, filter=None, projection=None, skip=0, limit=0, **kwargs):
        cursor = MemoryCursor(
            self._database,
            self._name,
            self._docs,
            filter if isinstance(filter, dict) else {},
            projection,
            kwargs.get("limit", limit),
            kwargs.get("batch_size", 0),
        )
        if kwargs.get("sort"):
            cursor.sort(kwargs["sort"])
        return cursor

    def find_one(self, filter=None, *args, **kwargs):
        # Collection.find_one iterates ``cursor.limit(-1)`` and returns
        # the first document (pymongo/synchronous/collection.py).
        cursor = self.find(filter, *args, **kwargs)
        for result in cursor.limit(-1):
            return result
        return None

    def aggregate(self, pipeline, **kwargs):
        query = {}
        projection = None
        sort = None
        limit = 0
        for stage in pipeline:
            if "$match" in stage:
                query.update(stage["$match"])
            elif "$project" in stage:
                projection = stage["$project"]
            elif "$sort" in stage:
                sort = list(stage["$sort"].items())
            elif "$limit" in stage:
                limit = stage["$limit"]
        cursor = MemoryCursor(
            self._database,
            self._name,
            self._docs,
            query,
            projection,
            limit if isinstance(limit, int) else 0,
            0,
        )
        cursor._record_op = "aggregate"
        if sort:
            cursor.sort(sort)
        return _AggregateCursor(cursor)


class _AggregateCursor:
    """Records aggregate reads as aggregate, not find."""

    def __init__(self, cursor):
        self._cursor = cursor

    def __iter__(self):
        self._cursor._execute()
        self._cursor._record["op"] = "aggregate"
        return self._cursor


class MemoryDatabase:
    def __init__(self):
        self.documents = {}
        self.commands = []

    def __getitem__(self, name):
        return MemoryCollection(self, name)


def _change(flight, date, origin, sequence, change_type):
    return {
        "_id": f"{flight}-{date}-{origin}-{sequence}",
        "flightNumber": flight,
        "origin": origin,
        "departureDate": date,
        "sequenceNumber": sequence,
        "detectedAt": f"2026-03-19T00:00:{sequence % 60:02d}",
        "changeType": change_type,
        "field": "status",
        "oldValue": "OPEN",
        "newValue": "CLOSED",
        "passenger": {"pnr": PNR, "lastName": "SMITH", "firstName": "JOHN"},
        "payload": "x" * 256,
    }


def _seed(database):
    changes = database.documents.setdefault("changes", [])
    sequence = 1
    for change_type in ("CHECKED_IN", "BOARDED", "STATUS_CHANGE", "SEAT_CHANGE"):
        count = PER_TYPE if change_type != "SEAT_CHANGE" else 100
        for _ in range(count):
            changes.append(_change(FLIGHT, DATE, ORIGIN, sequence, change_type))
            sequence += 1
    for _ in range(PER_TYPE):
        changes.append(_change(FLIGHT, DATE, OTHER_ORIGIN, sequence, "CHECKED_IN"))
        sequence += 1
    for _ in range(PER_TYPE):
        changes.append(_change(FLIGHT, OTHER_DATE, ORIGIN, sequence, "CHECKED_IN"))
        sequence += 1
    for _ in range(50):
        changes.append(_change(OTHER_FLIGHT, DATE, ORIGIN, sequence, "CHECKED_IN"))
        sequence += 1

    database.documents["passenger_list"] = [
        {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "fetchedAt": "2026-03-19T01:00:00Z",
            "passengers": [],
            "cabinSummary": [],
            "totalPassengers": 0,
        }
    ]
    database.documents["reservations"] = [
        {
            "flightNumber": FLIGHT,
            "departureDate": DATE,
            "fetchedAt": "2026-03-19T01:00:00Z",
            "reservations": [],
        }
    ]


def _changes_reads(database):
    return [command for command in database.commands if command["collection"] == "changes"]


def _scoped(command):
    query = command["filter"]
    assert query.get("flightNumber") == FLIGHT, query
    date_filter = query.get("departureDate")
    assert date_filter == DATE or (
        isinstance(date_filter, dict) and date_filter.get("$eq") == DATE
    ), query
    limit = command["limit"]
    delivered = command["delivered"]
    assert isinstance(limit, int) and 1 <= limit <= MAX_CHANGE_DOCUMENTS, (
        f"changes query delivered {delivered} documents with server limit {limit}"
    )
    assert delivered <= limit
    assert delivered > 0


@pytest.fixture
def database(monkeypatch):
    database = MemoryDatabase()
    _seed(database)
    monkeypatch.setattr(audit_routes, "get_db", lambda: database)
    monkeypatch.setattr(changes_routes, "get_db", lambda: database)
    monkeypatch.setattr(flights_routes, "get_db", lambda: database)
    flights_routes._dashboard_cache.clear()
    captured = []

    def wrapped_run_audit(passenger_doc, reservations_doc, changes_list):
        captured.append(changes_list)
        return real_run_audit(passenger_doc, reservations_doc, changes_list)

    monkeypatch.setattr(audit_routes, "run_audit", wrapped_run_audit)
    database.audit_lists = captured
    return database


@pytest.fixture
def client(database):
    app = FastAPI()
    # Same mount order as backend.api.main for these routers.
    app.include_router(flights_routes.router)
    app.include_router(changes_routes.router)
    app.include_router(audit_routes.router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("path", UNBOUNDED_PATHS)
def test_missing_date_does_not_read_change_history(client, database, path):
    response = client.get(path)

    reads = _changes_reads(database)
    delivered = [command["delivered"] for command in reads]
    limits = [command["limit"] for command in reads]
    assert response.status_code in (400, 422) and reads == [], (
        f"status {response.status_code}, delivered {delivered}, limits {limits}"
    )


@pytest.mark.parametrize("path", UNBOUNDED_PATHS)
def test_dated_change_history_is_capped_in_the_query(client, database, path):
    response = client.get(path, params={"date": DATE, "origin": ORIGIN})

    assert response.status_code == 200, response.status_code
    reads = _changes_reads(database)
    assert reads, "dated request never read the change history"
    for command in reads:
        _scoped(command)
        origin_filter = command["filter"].get("origin")
        assert origin_filter == ORIGIN or (
            isinstance(origin_filter, dict) and origin_filter.get("$eq") == ORIGIN
        ), command["filter"]


def test_audit_without_origin_still_scopes_and_caps(client, database):
    response = client.get(f"/flights/{FLIGHT}/audit", params={"date": DATE})

    assert response.status_code == 200, response.status_code
    reads = _changes_reads(database)
    assert reads
    for command in reads:
        _scoped(command)
    assert database.audit_lists, "audit rules did not run"
    for changes_list in database.audit_lists:
        assert isinstance(changes_list, list)
        assert 0 < len(changes_list) <= MAX_CHANGE_DOCUMENTS


def test_audit_holds_only_the_capped_list_across_overlapping_requests(database):
    barrier = threading.Barrier(2)
    lengths = []
    errors = []

    def wrapped(passenger_doc, reservations_doc, changes_list):
        lengths.append(len(changes_list))
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError as exc:
            errors.append(exc)
        return real_run_audit(passenger_doc, reservations_doc, changes_list)

    original = audit_routes.run_audit
    audit_routes.run_audit = wrapped
    try:
        def call():
            try:
                audit_routes.get_flight_audit(FLIGHT, origin=ORIGIN, date=DATE)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        audit_routes.run_audit = original

    assert errors == [], errors
    assert lengths, "overlapping audits did not reach run_audit"
    assert all(0 < length <= MAX_CHANGE_DOCUMENTS for length in lengths), lengths
    assert _changes_reads(database)
    for command in _changes_reads(database):
        _scoped(command)
