"""Dateless flight reads must not materialize a flight's entire change history.

The operations API accepts GET /flights/{flight_number}/dashboard with no
departure date and no credentials. The handler then queries ``changes`` by
flight number only and iterates the cursor with no limit. These tests load a
history larger than MAX_CHANGE_DOCS_MATERIALIZED and fail while that walk
still happens.

A fix passes when each request below either rejects the missing date before
reading change documents, or materializes at most MAX_CHANGE_DOCS_MATERIALIZED
of them. A cursor ``limit`` and a server-side ``aggregate`` both satisfy the
bound: ``aggregate`` returns grouped results, and those result documents are
not counted as source change documents.

Run:
    python3 -m pytest backend/tests/test_dateless_change_history.py -q
"""

from __future__ import annotations

import copy
import threading
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import audit as audit_routes
from backend.api.routes import changes as changes_routes
from backend.api.routes import flights as flights_routes

# One page, matching the default limit on GET /flights/{flight_number}/changes.
MAX_CHANGE_DOCS_MATERIALIZED = 100
# Larger than the cap, and larger than the changes-list maximum of 1000 is
# unnecessary: 100 already proves the cursor is not exhausted.
HISTORY_SIZE = MAX_CHANGE_DOCS_MATERIALIZED + 150
FLIGHT_NUMBER = "2057"
OTHER_FLIGHT = "9999"
PASSENGER_PNR = "ABC123"
DATED_DEPARTURE = "2024-06-01"

# Routes whose handlers walk ``changes`` with no limit when date is omitted.
# GET /flights/{flight}/passengers/history-badges is the same loop, but the
# earlier /passengers/{pnr} route captures that path, so it is not requested.
DATELESS_CHANGE_ROUTES = [
    f"/flights/{FLIGHT_NUMBER}/dashboard",
    f"/flights/{FLIGHT_NUMBER}/tree",
    f"/flights/{FLIGHT_NUMBER}/changes/summary",
    f"/flights/{FLIGHT_NUMBER}/timeline",
    f"/flights/{FLIGHT_NUMBER}/audit",
    f"/flights/{FLIGHT_NUMBER}/boarding-progress",
    f"/flights/{FLIGHT_NUMBER}/passengers/{PASSENGER_PNR}/timeline",
]


def _day(index: int) -> str:
    return (date(2019, 1, 1) + timedelta(days=index)).isoformat()


def _get_path(doc, path: str):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _set_path(doc: dict, path: str, value) -> None:
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def _del_path(doc: dict, path: str) -> None:
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        current = current.get(part)
        if not isinstance(current, dict):
            return
    current.pop(parts[-1], None)


def _eval_expr(expr, doc):
    """Evaluate the aggregation expressions the flight-list pipelines build."""
    if isinstance(expr, str) and expr.startswith("$$"):
        if expr == "$$ROOT":
            return copy.deepcopy(doc)
        raise NotImplementedError(expr)
    if isinstance(expr, str) and expr.startswith("$"):
        value, _present = _get_path(doc, expr[1:])
        return value
    if isinstance(expr, list):
        return [_eval_expr(item, doc) for item in expr]
    if isinstance(expr, dict):
        if len(expr) == 1:
            op, arg = next(iter(expr.items()))
            if op == "$ifNull":
                value = _eval_expr(arg[0], doc)
                return arg[1] if value is None else value
            if op == "$literal":
                return arg
        # Compound documents, such as a $group _id, evaluate each field.
        return {key: _eval_expr(value, doc) for key, value in expr.items()}
    return expr


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key.startswith("$"):
            raise NotImplementedError(key)
        value, present = _get_path(doc, key)
        if isinstance(expected, dict) and expected and all(
            op.startswith("$") for op in expected
        ):
            for op, arg in expected.items():
                if op == "$exists":
                    if bool(arg) != present:
                        return False
                elif op == "$in":
                    if value not in arg:
                        return False
                elif op == "$ne":
                    if value == arg:
                        return False
                elif op == "$lte":
                    if not present or value > arg:
                        return False
                elif op == "$gte":
                    if not present or value < arg:
                        return False
                else:
                    raise NotImplementedError(op)
        elif value != expected:
            return False
    return True


def _project(doc: dict, projection) -> dict:
    if not projection:
        return copy.deepcopy(doc)
    inclusion = any(value == 1 for key, value in projection.items() if key != "_id")
    if inclusion:
        projected = {}
        if projection.get("_id", 1) != 0 and "_id" in doc:
            projected["_id"] = copy.deepcopy(doc["_id"])
        for key, value in projection.items():
            if key == "_id" or value == 0:
                continue
            field, present = _get_path(doc, key)
            if present:
                _set_path(projected, key, copy.deepcopy(field))
        return projected
    projected = copy.deepcopy(doc)
    for key, value in projection.items():
        if value == 0:
            _del_path(projected, key)
    return projected


def _apply_sort(docs: list, spec) -> list:
    if not spec:
        return docs
    if isinstance(spec, str):
        items = [(spec, 1)]
    elif isinstance(spec, dict):
        items = list(spec.items())
    else:
        items = list(spec)
    ordered = list(docs)
    for key, direction in reversed(items):
        ordered.sort(
            key=lambda document, key=key: _get_path(document, key)[0],
            reverse=int(direction) < 0,
        )
    return ordered


class _Cursor:
    """Find cursor with PyMongo's default: limit 0 means return every match.

    PyMongo 4.20 ``Collection.find`` builds a cursor whose ``limit`` defaults
    to 0 (``pymongo/cursor_shared.py``), and ``Cursor.limit`` documents 0 as
    no limit. ``Cursor.__next__`` then refreshes until the server cursor is
    exhausted. This cursor yields every match unless ``limit`` is non-zero.
    """

    def __init__(self, store, collection: str, query: dict, projection, sort_spec):
        self._store = store
        self._collection = collection
        self._query = query
        self._projection = projection
        self._sort = sort_spec
        self._limit = 0

    def sort(self, key, direction=None):
        if direction is None:
            self._sort = key
        else:
            self._sort = [(key, direction)]
        return self

    def limit(self, limit: int):
        if not isinstance(limit, int):
            raise TypeError(limit)
        self._limit = limit
        return self

    def __iter__(self):
        docs = _apply_sort(
            [doc for doc in self._store.documents(self._collection) if _matches(doc, self._query)],
            self._sort,
        )
        # 0 is "no limit", matching Cursor.limit in pymongo 4.20.
        if self._limit:
            docs = docs[: abs(self._limit)]
        scan = None
        if self._collection == "changes":
            scan = self._store.open_change_scan(self._query, self._limit)
        for index, doc in enumerate(docs, start=1):
            if scan is not None:
                self._store.note_change_yielded(scan)
                if self._store.block_after and index > self._store.block_after:
                    self._store.entered_block.set()
                    self._store.block_event.wait(timeout=30)
            yield _project(doc, self._projection)


class _Collection:
    def __init__(self, store, name: str):
        self._store = store
        self._name = name

    def find(self, filter=None, projection=None, **kwargs):
        query = {} if filter is None else filter
        return _Cursor(self._store, self._name, query, projection, kwargs.get("sort"))

    def find_one(self, filter=None, *args, **kwargs):
        projection = None
        if args and isinstance(args[0], dict):
            projection = args[0]
        projection = kwargs.get("projection", projection)
        cursor = self.find(filter, projection, sort=kwargs.get("sort"))
        for doc in cursor:
            # find_one is a single-document read. The unbounded walk is the
            # multi-document find() iteration, so undo that one yield.
            if self._name == "changes":
                self._store.undo_last_change_yield()
            return doc
        return None

    def aggregate(self, pipeline, **kwargs):
        """Server-side aggregation. Callers see stage output, not each source row.

        PyMongo's ``Collection.aggregate`` returns a command cursor of the
        pipeline result. Source documents that stay inside the pipeline are
        not materialized to the route, so they are not counted.
        """
        docs = self._store.documents(self._name)
        for stage in pipeline:
            if not docs:
                docs = []
                continue
            name, body = next(iter(stage.items()))
            if name == "$match":
                docs = [doc for doc in docs if _matches(doc, body)]
            elif name == "$sort":
                docs = _apply_sort(docs, body)
            elif name == "$limit":
                docs = docs[: body]
            elif name == "$group":
                docs = _group(docs, body)
            elif name == "$project":
                docs = [_project(doc, body) for doc in docs]
            else:
                raise NotImplementedError(name)
        return docs


def _group(docs: list, spec: dict) -> list:
    grouped = {}
    order = []
    id_expr = spec["_id"]
    for doc in docs:
        raw_id = _eval_expr(id_expr, doc)
        key = _freeze(raw_id)
        if key not in grouped:
            grouped[key] = {"_id": raw_id}
            order.append(key)
            first = True
        else:
            first = False
        bucket = grouped[key]
        for field, accumulator in spec.items():
            if field == "_id":
                continue
            if "$first" in accumulator and first:
                bucket[field] = _eval_expr(accumulator["$first"], doc)
            elif "$sum" in accumulator:
                addend = accumulator["$sum"]
                addend = 1 if addend == 1 else _eval_expr(addend, doc) or 0
                bucket[field] = bucket.get(field, 0) + addend
            elif "$first" not in accumulator:
                raise NotImplementedError(accumulator)
    return [grouped[key] for key in order]


class MemoryDatabase:
    """In-memory collections for the read paths these routes actually use."""

    def __init__(self):
        self._collections: dict[str, list] = {}
        self._lock = threading.Lock()
        self.scans: list[dict] = []
        self.changes_yielded = 0
        self.block_after = 0
        self.block_event = threading.Event()
        self.entered_block = threading.Event()

    def __getitem__(self, name: str) -> _Collection:
        self._collections.setdefault(name, [])
        return _Collection(self, name)

    def documents(self, name: str) -> list:
        return list(self._collections.get(name, []))

    def insert(self, name: str, doc: dict) -> None:
        self._collections.setdefault(name, []).append(copy.deepcopy(doc))

    def command(self, name: str):
        if name == "ping":
            return {"ok": 1}
        raise NotImplementedError(name)

    def open_change_scan(self, query: dict, limit: int) -> dict:
        scan = {"filter": copy.deepcopy(query), "limit": limit, "yielded": 0}
        with self._lock:
            self.scans.append(scan)
        return scan

    def note_change_yielded(self, scan: dict) -> None:
        with self._lock:
            scan["yielded"] += 1
            self.changes_yielded += 1

    def undo_last_change_yield(self) -> None:
        with self._lock:
            if self.scans and self.scans[-1]["yielded"]:
                self.scans[-1]["yielded"] -= 1
                self.changes_yielded -= 1
                if self.scans[-1]["yielded"] == 0:
                    self.scans.pop()

    def reset_reads(self) -> None:
        with self._lock:
            self.scans = []
            self.changes_yielded = 0
        self.entered_block.clear()


def _seed(db: MemoryDatabase) -> None:
    db.insert(
        "flight_status",
        {
            "_id": "fs-2057",
            "airline": "GF",
            "flightNumber": FLIGHT_NUMBER,
            "origin": "BAH",
            "departureDate": DATED_DEPARTURE,
            "status": "OPENCI",
            "gate": "A1",
            "fetchedAt": "2024-06-01T00:00:00Z",
            "boarding": {"indicator": ""},
            "jumpSeat": {"cockpit": 0, "cabin": 0},
            "codeshareInfo": [],
            "freeTextRemarks": [],
        },
    )
    for index in range(HISTORY_SIZE):
        # Two of this flight's rows share the dated departure used below.
        # The rest are other dates, so a dateless query is the whole history.
        departure = DATED_DEPARTURE if index < 2 else _day(index)
        db.insert(
            "changes",
            {
                "_id": f"chg-{index}",
                "flightNumber": FLIGHT_NUMBER,
                "origin": "BAH" if index % 2 == 0 else "DXB",
                "departureDate": departure,
                "changeType": "CHECKED_IN",
                "detectedAt": f"{departure}T00:00:{index % 60:02d}Z",
                "sequenceNumber": index + 1,
                "passenger": {"pnr": PASSENGER_PNR, "lastName": "DOE"},
                "metadata": {},
                "field": "isCheckedIn",
                "oldValue": False,
                "newValue": True,
            },
        )
    for index in range(5):
        db.insert(
            "changes",
            {
                "_id": f"other-{index}",
                "flightNumber": OTHER_FLIGHT,
                "origin": "BAH",
                "departureDate": _day(index),
                "changeType": "BOARDED",
                "detectedAt": f"{_day(index)}T01:00:00Z",
                "passenger": {"pnr": "ZZZZZZ"},
                "metadata": {},
            },
        )


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(monkeypatch):
    memory = MemoryDatabase()
    _seed(memory)
    monkeypatch.setattr(flights_routes, "get_db", lambda: memory)
    monkeypatch.setattr(changes_routes, "get_db", lambda: memory)
    monkeypatch.setattr(audit_routes, "get_db", lambda: memory)
    flights_routes._dashboard_cache.clear()
    yield memory
    flights_routes._dashboard_cache.clear()


def _assert_change_read_is_bounded(response, database: MemoryDatabase, path: str) -> None:
    assert response.status_code < 500, response.text
    filters = [scan["filter"] for scan in database.scans]
    assert database.changes_yielded <= MAX_CHANGE_DOCS_MATERIALIZED, (
        f"{path} returned {response.status_code} after materializing "
        f"{database.changes_yielded} change documents (history={HISTORY_SIZE}). "
        f"filters={filters}. A missing departure date must be rejected before "
        f"the read, or the read must stop at {MAX_CHANGE_DOCS_MATERIALIZED} "
        f"documents."
    )


def test_flight_list_is_unauthenticated_and_returns_flight_numbers(client, db):
    """Attack step 1: the flight list names a flight without credentials."""
    response = client.get("/flights")
    assert response.status_code == 200, response.text
    numbers = [item["flightNumber"] for item in response.json()]
    assert FLIGHT_NUMBER in numbers


def test_dateless_dashboard_does_not_walk_the_flight_change_history(client, db):
    """Attack steps 2-3: dashboard with no date and no origin."""
    response = client.get(f"/flights/{FLIGHT_NUMBER}/dashboard")
    _assert_change_read_is_bounded(response, db, "/flights/2057/dashboard")
    if response.status_code == 200:
        # The tally is whatever the bounded read produced. It must not be the
        # full history unless that total was computed without pulling each row.
        assert db.changes_yielded <= MAX_CHANGE_DOCS_MATERIALIZED


@pytest.mark.parametrize("path", DATELESS_CHANGE_ROUTES)
def test_dateless_route_does_not_materialize_full_change_history(client, db, path):
    response = client.get(path)
    _assert_change_read_is_bounded(response, db, path)


def test_repeated_dateless_dashboard_reads_stay_bounded(client, db):
    """snapshot_sequence is not cached, so each value repeats the change walk."""
    for sequence in (1, 2):
        db.reset_reads()
        response = client.get(
            f"/flights/{FLIGHT_NUMBER}/dashboard",
            params={"snapshot_sequence": sequence},
        )
        _assert_change_read_is_bounded(
            response, db, f"dashboard?snapshot_sequence={sequence}"
        )


def test_concurrent_dateless_dashboards_each_stay_bounded(client, db):
    """Cache is filled only after a request finishes, so overlapping calls each scan."""
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def issue(sequence: int) -> None:
        try:
            barrier.wait(timeout=5)
            response = client.get(
                f"/flights/{FLIGHT_NUMBER}/dashboard",
                params={"snapshot_sequence": sequence},
            )
            results.append(response)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [
        threading.Thread(target=issue, args=(sequence,), daemon=True)
        for sequence in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors, errors
    assert len(results) == 2
    for response in results:
        assert response.status_code < 500, response.text
    assert db.changes_yielded <= MAX_CHANGE_DOCS_MATERIALIZED * 2, (
        f"two overlapping dateless dashboards materialized {db.changes_yielded} "
        f"change documents; each request must stay within "
        f"{MAX_CHANGE_DOCS_MATERIALIZED}"
    )


def test_dateless_dashboard_returns_without_blocking_in_the_change_scan(client, db):
    """The sync route holds its worker until the change cursor is exhausted.

    The cursor pauses once the handler has pulled more than the cap. A bounded
    handler never reaches that pause and returns. A handler that walks the
    whole history is still inside the cursor when the timeout fires.
    """
    db.block_after = MAX_CHANGE_DOCS_MATERIALIZED
    done = threading.Event()
    holder = {}

    def issue() -> None:
        holder["response"] = client.get(f"/flights/{FLIGHT_NUMBER}/dashboard")
        done.set()

    worker = threading.Thread(target=issue, daemon=True)
    worker.start()
    returned = done.wait(timeout=2)
    yielded_while_waiting = db.changes_yielded
    db.block_event.set()
    worker.join(timeout=5)
    assert returned, (
        f"GET /flights/{FLIGHT_NUMBER}/dashboard did not return within 2s; "
        f"it had already materialized {yielded_while_waiting} change documents "
        f"and was blocked in the changes cursor"
    )
    _assert_change_read_is_bounded(holder["response"], db, "/flights/2057/dashboard")


def test_dated_dashboard_counts_only_that_departure(client, db):
    """A supplied departure date keeps the tally on that date's changes."""
    response = client.get(
        f"/flights/{FLIGHT_NUMBER}/dashboard",
        params={"date": DATED_DEPARTURE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changeSummary"] == {"CHECKED_IN": 2}
    assert body["overview"]["trackedChanges"] == 2
    for scan in db.scans:
        assert scan["filter"].get("departureDate") == DATED_DEPARTURE


def test_changes_list_is_already_capped(client, db):
    """GET /changes applies cursor.limit (default 100) and is not this bug."""
    response = client.get(f"/flights/{FLIGHT_NUMBER}/changes")
    assert response.status_code == 200, response.text
    assert len(response.json()) <= MAX_CHANGE_DOCS_MATERIALIZED
    assert db.changes_yielded <= MAX_CHANGE_DOCS_MATERIALIZED
