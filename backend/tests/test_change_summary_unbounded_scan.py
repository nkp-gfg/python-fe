"""Failing specs for unbounded change-history scans.

An unauthenticated caller can omit `date` on several flight routes. Those
handlers then iterate every stored change for that flight number. The cursor
they use has no limit (PyMongo treats limit 0 as unlimited), so the API
process materializes one document per historical change on every request.

These tests exercise the live FastAPI routes. They fail while that scan is
still reachable and pass once a missing departure date is rejected before any
change document is read, and once change-type totals are computed without
delivering every matching change document into the API process.

Not covered here, and why:
- GET /flights/{flight_number}/changes and
  GET /flights/{flight_number}/changes/passenger/{pnr} call Cursor.limit
  (default 100, max 1000), so the handler does not walk the full history.
- GET /activity/feed applies Cursor.limit (default 50, max 200).
- GET /analytics/* requires date_from and date_to and already aggregates.
- GET /flights/{flight_number}/phase-journey walks snapshots, not changes.
- GET /flights/{flight_number}/passengers/history-badges repeats the same
  unlimited loop, but it is registered after
  GET /flights/{flight_number}/passengers/{pnr}, which claims the path.
  test_history_badges_route_is_not_the_passenger_lookup records that.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

# A summary is one row per change type. Anything larger means the handler
# pulled stored change documents into the API process.
MAX_SUMMARY_ROWS = 16

TARGET_DATE = "2026-04-01"
OTHER_DATES = ("2026-01-15", "2026-02-15", "2026-03-15")
ORIGIN = "BAH"
OTHER_ORIGIN = "LHR"


# The stand-in below follows PyMongo 4.20.0, the version installed from
# requirements.txt (pymongo>=4.6,<5.0):
# - Collection.find returns a Cursor and defaults limit to 0, which the
#   driver documents as no limit
#   (pymongo/synchronous/collection.py:1735-1780 and :1942).
# - Cursor stores that limit (pymongo/cursor_shared.py:372-378 and :466) and
#   notes the server treats limit 0 as no limit (cursor_shared.py:488-490).
# - Cursor.__iter__ returns self and next() yields until the server has no
#   further batch (pymongo/synchronous/cursor.py:378-406).
# A find() that never calls limit() therefore delivers every match to the
# caller. limit(0) does not stop that.


def _path(doc: dict, dotted: str) -> tuple[Any, bool]:
    current: Any = doc
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _matches(doc: dict, filt: dict | None) -> bool:
    if not filt:
        return True
    for key, expected in filt.items():
        value, found = _path(doc, key)
        if isinstance(expected, dict) and any(str(op).startswith("$") for op in expected):
            if "$exists" in expected and bool(expected["$exists"]) != found:
                return False
            if "$in" in expected and value not in expected["$in"]:
                return False
            if "$ne" in expected and value == expected["$ne"]:
                return False
            if "$gte" in expected and (not found or value < expected["$gte"]):
                return False
            if "$lte" in expected and (not found or value > expected["$lte"]):
                return False
            continue
        if not found or value != expected:
            return False
    return True


def _project(doc: dict, projection: Any) -> dict:
    if not projection or not isinstance(projection, dict):
        return doc
    excluding = all(value == 0 for value in projection.values())
    if excluding:
        projected = copy.deepcopy(doc)
        for key in projection:
            projected.pop(key, None)
        return projected
    projected = {}
    if projection.get("_id", 1) != 0 and "_id" in doc:
        projected["_id"] = doc["_id"]
    for key, include in projection.items():
        if key == "_id" or not include:
            continue
        value, found = _path(doc, key)
        if not found:
            continue
        if "." not in key:
            projected[key] = value
            continue
        cursor = projected
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return projected


def _sort_value(doc: dict, field: str) -> tuple:
    value, found = _path(doc, field)
    if not found or value is None:
        return (1, "")
    return (0, value)


class _Cursor:
    """Yields every match unless limit(n) is called with n > 0."""

    def __init__(self, collection: "MemoryCollection", filt: dict, projection: Any):
        self._collection = collection
        self._filter = filt
        self._projection = projection
        self._sort: list[tuple[str, int]] | None = None
        self._limit = 0

    def sort(self, key, direction=None):
        if isinstance(key, str):
            self._sort = [(key, 1 if direction is None else direction)]
        else:
            self._sort = [(field, order) for field, order in key]
        return self

    def limit(self, limit: int):
        # PyMongo: a limit of 0 is equivalent to no limit.
        if not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        self._limit = limit
        return self

    def __iter__(self) -> Iterator[dict]:
        docs = [copy.deepcopy(doc) for doc in self._collection.docs if _matches(doc, self._filter)]
        if self._sort:
            for field, direction in reversed(self._sort):
                docs.sort(key=lambda doc, field=field: _sort_value(doc, field), reverse=direction < 0)
        if self._limit > 0:
            docs = docs[: self._limit]
        delivered = [_project(doc, self._projection) for doc in docs]
        self._collection.note(self._filter, examined=len(delivered), materialized=len(delivered))
        yield from delivered

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class MemoryCollection:
    def __init__(self, name: str):
        self.name = name
        self.docs: list[dict] = []
        self.examined = 0
        self.materialized = 0
        self._lock = threading.Lock()

    def insert_one(self, doc: dict):
        with self._lock:
            stored = copy.deepcopy(doc)
            stored.setdefault("_id", f"{self.name}-{len(self.docs)}")
            self.docs.append(stored)
        return stored

    def note(self, filt: dict, examined: int, materialized: int):
        with self._lock:
            self.examined += examined
            self.materialized += materialized

    def reset_counters(self):
        with self._lock:
            self.examined = 0
            self.materialized = 0

    def find(self, *args, **kwargs) -> _Cursor:
        filt: dict = {}
        projection = None
        if args:
            filt = args[0] or {}
            if len(args) > 1:
                projection = args[1]
        if "filter" in kwargs:
            filt = kwargs["filter"] or {}
        if "projection" in kwargs:
            projection = kwargs["projection"]
        return _Cursor(self, filt, projection)

    def find_one(self, filt=None, projection=None, *args, sort=None, **kwargs):
        cursor = self.find(filt, projection)
        if sort:
            cursor.sort(sort)
        cursor.limit(1)
        found = list(cursor)
        return found[0] if found else None

    def count_documents(self, filt, **kwargs) -> int:
        matched = [doc for doc in self.docs if _matches(doc, filt or {})]
        self.note(filt or {}, examined=len(matched), materialized=0)
        return len(matched)

    def distinct(self, key: str, filt=None, **kwargs):
        values = []
        matched = 0
        for doc in self.docs:
            if filt and not _matches(doc, filt):
                continue
            matched += 1
            value, found = _path(doc, key)
            if found and value not in values:
                values.append(value)
        self.note(filt or {}, examined=matched, materialized=0)
        return values

    def aggregate(self, pipeline, **kwargs):
        docs = [copy.deepcopy(doc) for doc in self.docs]
        scanned = 0
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _matches(doc, stage["$match"])]
                scanned = len(docs)
            elif "$group" in stage:
                scanned = len(docs)
                docs = _group(docs, stage["$group"])
            elif "$sort" in stage:
                for field, direction in reversed(list(stage["$sort"].items())):
                    docs.sort(
                        key=lambda doc, field=field: _sort_value(doc, field),
                        reverse=direction < 0,
                    )
            elif "$project" in stage:
                docs = [_project(doc, stage["$project"]) for doc in docs]
            elif "$count" in stage:
                scanned = len(docs)
                docs = [{stage["$count"]: len(docs)}]
            else:
                raise NotImplementedError(f"unsupported aggregate stage: {stage}")
        self.note({}, examined=scanned, materialized=len(docs))
        return iter(docs)

    def index_information(self):
        if self.name == "flight_status":
            return {"flight_lookup": {"key": [("flightNumber", 1)]}}
        return {}

    def create_index(self, *args, **kwargs):
        return None

    def drop_index(self, *args, **kwargs):
        return None


def _group(docs: list[dict], spec: dict) -> list[dict]:
    id_expr = spec["_id"]
    grouped: dict[Any, dict] = {}
    order: list[Any] = []
    for doc in docs:
        if isinstance(id_expr, str) and id_expr.startswith("$"):
            key, _found = _path(doc, id_expr[1:])
        else:
            key = id_expr
        if key not in grouped:
            grouped[key] = {"_id": key}
            order.append(key)
        bucket = grouped[key]
        for field, accumulator in spec.items():
            if field == "_id":
                continue
            if not isinstance(accumulator, dict) or "$sum" not in accumulator:
                raise NotImplementedError(accumulator)
            addend = accumulator["$sum"]
            if addend == 1:
                increment = 1
            elif isinstance(addend, str) and addend.startswith("$"):
                raw, found = _path(doc, addend[1:])
                increment = raw if found and isinstance(raw, (int, float)) else 0
            else:
                raise NotImplementedError(accumulator)
            bucket[field] = bucket.get(field, 0) + increment
    return [grouped[key] for key in order]


class MemoryDatabase:
    def __init__(self):
        self._collections: dict[str, MemoryCollection] = {}

    def __getitem__(self, name: str) -> MemoryCollection:
        collection = self._collections.get(name)
        if collection is None:
            collection = MemoryCollection(name)
            self._collections[name] = collection
        return collection

    def command(self, command_name, *args, **kwargs):
        if command_name == "ping":
            return {"ok": 1}
        raise NotImplementedError(command_name)


@pytest.fixture
def api(monkeypatch):
    database = MemoryDatabase()

    def _get_db():
        return database

    for target in (
        "backend.api.database.get_db",
        "backend.api.routes.changes.get_db",
        "backend.api.routes.flights.get_db",
        "backend.api.routes.audit.get_db",
        "backend.api.routes.passengers.get_db",
    ):
        monkeypatch.setattr(target, _get_db)

    from backend.api.main import app
    from backend.api.routes import flights as flights_routes

    flights_routes._dashboard_cache.clear()
    with TestClient(app) as client:
        yield client, database
    flights_routes._dashboard_cache.clear()


def _change(flight: str, departure_date: str, change_type: str, origin: str = ORIGIN, pnr: str | None = None, detected_at: str = "2026-04-01T00:00:00Z") -> dict:
    doc = {
        "flightNumber": flight,
        "origin": origin,
        "departureDate": departure_date,
        "changeType": change_type,
        "detectedAt": detected_at,
        "field": "status",
        "oldValue": "OPEN",
        "newValue": "BOARDING",
    }
    if pnr:
        doc["passenger"] = {"pnr": pnr, "lastName": "SMITH", "firstName": "JANE"}
    return doc


def _seed_flight_history(changes: MemoryCollection, flight: str = "2057") -> dict[str, int]:
    """History large enough that a page-sized limit cannot cover it."""
    counts = {"target_boarded": 40, "target_checked_in": 30, "other_origin": 15, "other_dates_each": 40}
    for _ in range(counts["target_boarded"]):
        changes.insert_one(_change(flight, TARGET_DATE, "BOARDED", pnr="AAA111"))
    for _ in range(counts["target_checked_in"]):
        changes.insert_one(_change(flight, TARGET_DATE, "CHECKED_IN", pnr="BBB222"))
    for _ in range(counts["other_origin"]):
        changes.insert_one(_change(flight, TARGET_DATE, "BOARDED", origin=OTHER_ORIGIN))
    for departure_date in OTHER_DATES:
        for change_type in ("BOARDED", "CHECKED_IN"):
            for index in range(counts["other_dates_each"]):
                changes.insert_one(
                    _change(
                        flight,
                        departure_date,
                        change_type,
                        detected_at=f"{departure_date}T01:{index:02d}:00Z",
                        pnr="CCC333",
                    )
                )
    for _ in range(25):
        changes.insert_one(_change("9999", TARGET_DATE, "BOARDED"))
    return counts


def _seed_flight_status(db: MemoryDatabase, flight: str = "2057"):
    db["flight_status"].insert_one(
        {
            "flightNumber": flight,
            "origin": ORIGIN,
            "departureDate": TARGET_DATE,
            "status": "BOARDING",
            "fetchedAt": "2026-04-01T12:00:00Z",
            "gate": "A1",
            "legs": [{"city": ORIGIN}, {"city": "LHR"}],
        }
    )


def _rejected(response) -> bool:
    return response.status_code in (400, 422)


def _assert_omitted_date_does_not_scan(response, changes: MemoryCollection, label: str):
    assert _rejected(response) and changes.examined == 0 and changes.materialized == 0, (
        f"{label} without a departure date returned {response.status_code} "
        f"after examining {changes.examined} change documents and delivering "
        f"{changes.materialized} of them to the API process: {response.text[:400]}"
    )


def test_summary_without_date_scans_every_change_for_the_flight_on_every_call(api):
    client, db = api
    counts = _seed_flight_history(db["changes"])
    history = (
        counts["target_boarded"]
        + counts["target_checked_in"]
        + counts["other_origin"]
        + counts["other_dates_each"] * 2 * len(OTHER_DATES)
    )

    db["changes"].reset_counters()
    first = client.get("/flights/2057/changes/summary")
    examined_once = db["changes"].examined
    materialized_once = db["changes"].materialized
    assert _rejected(first) and examined_once == 0 and materialized_once == 0, (
        f"the first unauthenticated call returned {first.status_code} after "
        f"examining {examined_once} of {history} stored changes for flight 2057 "
        f"and delivering {materialized_once} of them to the API process: {first.text[:400]}"
    )

    db["changes"].reset_counters()
    second = client.get("/flights/2057/changes/summary")
    _assert_omitted_date_does_not_scan(second, db["changes"], "GET /changes/summary")


def test_summary_with_date_counts_on_the_server(api):
    client, db = api
    counts = _seed_flight_history(db["changes"])
    db["changes"].reset_counters()

    response = client.get(
        "/flights/2057/changes/summary",
        params={"date": TARGET_DATE, "origin": ORIGIN},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["flightNumber"] == "2057"
    assert body["changeTypes"] == {
        "BOARDED": counts["target_boarded"],
        "CHECKED_IN": counts["target_checked_in"],
    }
    assert body["totalChanges"] == counts["target_boarded"] + counts["target_checked_in"]
    assert db["changes"].materialized <= MAX_SUMMARY_ROWS, (
        "summary delivered "
        f"{db['changes'].materialized} change documents into the API process; "
        f"a server-side group returns at most one row per change type "
        f"(<= {MAX_SUMMARY_ROWS}), not one document per stored change"
    )


def test_invalid_date_is_rejected_before_any_change_is_read(api):
    client, db = api
    _seed_flight_history(db["changes"])
    db["changes"].reset_counters()

    response = client.get("/flights/2057/changes/summary", params={"date": "04-01-2026"})

    _assert_omitted_date_does_not_scan(response, db["changes"], "GET /changes/summary")


def test_dashboard_without_date_does_not_walk_change_history(api):
    client, db = api
    _seed_flight_history(db["changes"])
    _seed_flight_status(db)
    db["changes"].reset_counters()

    response = client.get("/flights/2057/dashboard")

    _assert_omitted_date_does_not_scan(response, db["changes"], "GET /dashboard")


def test_dashboard_with_date_summarizes_changes_without_streaming_them(api):
    client, db = api
    counts = _seed_flight_history(db["changes"])
    _seed_flight_status(db)
    db["changes"].reset_counters()

    response = client.get(
        "/flights/2057/dashboard",
        params={"date": TARGET_DATE, "origin": ORIGIN},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changeSummary"] == {
        "BOARDED": counts["target_boarded"],
        "CHECKED_IN": counts["target_checked_in"],
    }
    assert body["overview"]["trackedChanges"] == counts["target_boarded"] + counts["target_checked_in"]
    assert db["changes"].materialized <= MAX_SUMMARY_ROWS, (
        f"dashboard change summary materialized {db['changes'].materialized} documents"
    )


def test_tree_without_date_does_not_scan_changes_it_discards(api):
    client, db = api
    _seed_flight_history(db["changes"])
    _seed_flight_status(db)
    db["changes"].reset_counters()

    response = client.get("/flights/2057/tree")

    _assert_omitted_date_does_not_scan(response, db["changes"], "GET /tree")


def test_tree_with_date_does_not_stream_every_change(api):
    client, db = api
    _seed_flight_history(db["changes"])
    _seed_flight_status(db)
    db["changes"].reset_counters()

    response = client.get("/flights/2057/tree", params={"date": TARGET_DATE, "origin": ORIGIN})

    assert response.status_code == 200, response.text
    assert db["changes"].materialized <= MAX_SUMMARY_ROWS, (
        f"GET /tree materialized {db['changes'].materialized} change documents "
        "while building a payload that discards the change summary"
    )


def test_audit_without_date_does_not_load_every_change(api):
    client, db = api
    _seed_flight_history(db["changes"])
    db["changes"].reset_counters()

    response = client.get("/flights/2057/audit")

    _assert_omitted_date_does_not_scan(response, db["changes"], "GET /audit")


def test_audit_with_date_reads_only_that_departure(api):
    client, db = api
    counts = _seed_flight_history(db["changes"])
    expected = counts["target_boarded"] + counts["target_checked_in"]
    db["changes"].reset_counters()

    response = client.get("/flights/2057/audit", params={"date": TARGET_DATE, "origin": ORIGIN})

    assert response.status_code == 200, response.text
    assert "alerts" in response.json()
    assert db["changes"].examined == expected, (
        f"audit examined {db['changes'].examined} changes; the requested departure has {expected}"
    )


def test_flight_timeline_without_date_does_not_load_every_change(api):
    client, db = api
    _seed_flight_history(db["changes"])
    db["changes"].reset_counters()

    response = client.get("/flights/2057/timeline")

    _assert_omitted_date_does_not_scan(response, db["changes"], "GET /timeline")


def test_flight_timeline_with_date_returns_that_departure_only(api):
    client, db = api
    counts = _seed_flight_history(db["changes"])
    expected = counts["target_boarded"] + counts["target_checked_in"]
    db["changes"].reset_counters()

    response = client.get("/flights/2057/timeline", params={"date": TARGET_DATE, "origin": ORIGIN})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["eventCount"] == expected
    assert body["departureDate"] == TARGET_DATE
    assert db["changes"].examined == expected


def test_boarding_progress_without_date_does_not_load_every_change(api):
    client, db = api
    _seed_flight_history(db["changes"])
    db["changes"].reset_counters()

    response = client.get("/flights/2057/boarding-progress")

    _assert_omitted_date_does_not_scan(response, db["changes"], "GET /boarding-progress")


def test_boarding_progress_with_date_counts_only_that_departure(api):
    client, db = api
    counts = _seed_flight_history(db["changes"])
    db["changes"].reset_counters()

    response = client.get(
        "/flights/2057/boarding-progress",
        params={"date": TARGET_DATE, "origin": ORIGIN},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["checkinProgress"]["current"] == counts["target_checked_in"]
    assert body["boardingProgress"]["current"] == counts["target_boarded"]
    # Three unlimited finds: check-ins, boardings, and status changes (none seeded).
    assert db["changes"].examined == counts["target_checked_in"] + counts["target_boarded"]


def test_passenger_timeline_without_date_does_not_load_every_date(api):
    client, db = api
    for _ in range(6):
        db["changes"].insert_one(_change("2057", TARGET_DATE, "CHECKED_IN", pnr="XYZ789"))
    for departure_date in OTHER_DATES:
        for _ in range(6):
            db["changes"].insert_one(_change("2057", departure_date, "BOARDED", pnr="XYZ789"))
    db["changes"].reset_counters()

    response = client.get("/flights/2057/passengers/XYZ789/timeline")

    _assert_omitted_date_does_not_scan(response, db["changes"], "GET /passengers/{pnr}/timeline")


def test_passenger_timeline_with_date_returns_that_departure_only(api):
    client, db = api
    for _ in range(6):
        db["changes"].insert_one(_change("2057", TARGET_DATE, "CHECKED_IN", pnr="XYZ789"))
    for departure_date in OTHER_DATES:
        for _ in range(6):
            db["changes"].insert_one(_change("2057", departure_date, "BOARDED", pnr="XYZ789"))
    db["changes"].reset_counters()

    response = client.get(
        "/flights/2057/passengers/XYZ789/timeline",
        params={"date": TARGET_DATE},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["eventCount"] == 6
    assert body["departureDate"] == TARGET_DATE
    assert db["changes"].examined == 6


def test_summary_request_needs_no_credentials(api):
    """The route has no auth dependency. A caller with no credentials reaches it."""
    client, db = api
    _seed_flight_history(db["changes"])
    db["changes"].reset_counters()

    response = client.get("/flights/2057/changes/summary", headers={})

    assert "authorization" not in {key.lower() for key in response.request.headers}
    _assert_omitted_date_does_not_scan(response, db["changes"], "unauthenticated GET /changes/summary")


def test_history_badges_route_is_not_the_passenger_lookup(api):
    """history-badges is only asserted when that handler is the one mounted."""
    client, db = api
    _seed_flight_history(db["changes"])
    db["changes"].reset_counters()

    response = client.get("/flights/2057/passengers/history-badges")

    if response.status_code == 404 and "PNR" in response.text:
        pytest.skip(
            "GET /passengers/history-badges is claimed by "
            "GET /passengers/{pnr} and does not run the badge scan"
        )
    _assert_omitted_date_does_not_scan(response, db["changes"], "GET /passengers/history-badges")
