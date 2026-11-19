"""Serve the real change routes over histories the passenger differ records.

The collection applies the find command the driver sends: filter, then sort,
then skip, then limit. PyMongo 4.20.1 (the 4.x driver this repo allows) puts
those fields on the command in ``pymongo/message.py`` (``_gen_find_command``)
and ``pymongo/cursor_shared.py`` (``sort``, ``skip``, ``limit``). A limit of 0
is no limit, matching ``Cursor.limit``.

This process is a test harness. It does not change product code.
"""

from __future__ import annotations

import copy
import importlib.util
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.api.routes import changes as changes_routes

# Load the differ module itself. Importing backend.feeder runs feeder/__init__.py,
# which imports the Sabre runner, and this harness only needs diff_passenger_list.
_differ_path = Path(__file__).resolve().parents[1] / "feeder" / "differ.py"
_differ_spec = importlib.util.spec_from_file_location("falconeye_differ", _differ_path)
if _differ_spec is None or _differ_spec.loader is None:
    raise RuntimeError(f"could not load {_differ_path}")
_differ = importlib.util.module_from_spec(_differ_spec)
_differ_spec.loader.exec_module(_differ)
diff_passenger_list = _differ.diff_passenger_list

ORIGIN = "BAH"
DATE = "2026-03-19"
PAGE_CAP = 1000


def _pax(pnr, last, seat="1A", checked=False, boarded=False):
    return {
        "pnr": pnr,
        "lastName": last,
        "firstName": "ANN",
        "isCheckedIn": checked,
        "isBoarded": boarded,
        "cabin": "Y",
        "bookingClass": "V",
        "seat": seat,
        "bagCount": 0,
        "passengerType": "F",
    }


def _snap(sid, passengers):
    return {"snapshotId": sid, "data": {"passengers": [dict(p) for p in passengers]}}


def _flight(number):
    return {"flightNumber": number, "origin": ORIGIN, "departureDate": DATE}


def _stamp(changes, type_order, oldest_name_by_type):
    """Assign increasing timestamps so earlier cycles sort after the newest page."""
    grouped = {change_type: [] for change_type in type_order}
    for change in changes:
        grouped.setdefault(change["changeType"], []).append(change)
    unknown = [change_type for change_type in grouped if change_type not in type_order]
    if unknown:
        raise AssertionError(f"unexpected change types: {unknown}")

    ordered = []
    for change_type in type_order:
        group = grouped[change_type]
        oldest_name = oldest_name_by_type.get(change_type)
        group.sort(
            key=lambda change: 0
            if (change.get("passenger") or {}).get("lastName") == oldest_name
            else 1
        )
        ordered.extend(group)

    base = datetime(2026, 3, 19, tzinfo=timezone.utc)
    for index, change in enumerate(ordered):
        change["detectedAt"] = (base + timedelta(seconds=index)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return ordered


def _busy_departure():
    """Adds, then seat moves, then check-ins, then boardings. Total is past 100."""
    flight = _flight("2057")
    fillers = [
        _pax(f"F{i:04d}", f"FILL{i:04d}", seat=f"{(i % 40) + 1}A") for i in range(1, 51)
    ]
    base = [
        _pax("EAD001", "EARLYADD", seat="10A"),
        _pax("EST001", "EARLYSEAT", seat="11A"),
        _pax("ECK001", "EARLYCKIN", seat="12A"),
        *fillers,
    ]
    added = diff_passenger_list(_snap("s0", []), _snap("s1", base), flight)

    seated_source = []
    for passenger in base:
        updated = dict(passenger)
        if passenger["lastName"] == "EARLYSEAT":
            updated["seat"] = "22B"
        elif passenger["lastName"].startswith("FILL"):
            updated["seat"] = "33C"
        seated_source.append(updated)
    seated = diff_passenger_list(_snap("s1", base), _snap("s2", seated_source), flight)

    checked_source = []
    for passenger in seated_source:
        updated = dict(passenger)
        if passenger["lastName"] == "EARLYCKIN" or passenger["lastName"].startswith("FILL"):
            updated["isCheckedIn"] = True
        checked_source.append(updated)
    checked = diff_passenger_list(
        _snap("s2", seated_source), _snap("s3", checked_source), flight
    )

    boarded_source = []
    for passenger in checked_source:
        updated = dict(passenger)
        if passenger["lastName"].startswith("FILL"):
            updated["isBoarded"] = True
        boarded_source.append(updated)
    boarded = diff_passenger_list(
        _snap("s3", checked_source), _snap("s4", boarded_source), flight
    )

    changes = _stamp(
        added + seated + checked + boarded,
        ["PASSENGER_ADDED", "SEAT_CHANGE", "CHECKED_IN", "BOARDED"],
        {
            "PASSENGER_ADDED": "EARLYADD",
            "SEAT_CHANGE": "EARLYSEAT",
            "CHECKED_IN": "EARLYCKIN",
        },
    )
    counts = {}
    for change in changes:
        counts[change["changeType"]] = counts.get(change["changeType"], 0) + 1
    expected = {
        "PASSENGER_ADDED": 53,
        "SEAT_CHANGE": 51,
        "CHECKED_IN": 51,
        "BOARDED": 50,
    }
    if counts != expected:
        raise AssertionError(f"busy departure counts {counts} != {expected}")
    return changes


def _checkin_page():
    """More check-ins than one page, plus a handful of newer seat moves."""
    flight = _flight("2060")
    checkins = [_pax("CK0000", "CKINOLD")] + [
        _pax(f"CK{i:04d}", f"CK{i:04d}") for i in range(1, 130)
    ]
    seats = [_pax(f"ST{i:04d}", f"SEATONLY{i:04d}", seat="1A") for i in range(1, 21)]
    before = checkins + seats
    after = []
    for passenger in before:
        updated = dict(passenger)
        if passenger["pnr"].startswith("CK"):
            updated["isCheckedIn"] = True
        else:
            updated["seat"] = "9Z"
        after.append(updated)
    changes = diff_passenger_list(_snap("c0", before), _snap("c1", after), flight)
    changes = _stamp(
        changes,
        ["CHECKED_IN", "SEAT_CHANGE"],
        {"CHECKED_IN": "CKINOLD"},
    )
    counts = {}
    for change in changes:
        counts[change["changeType"]] = counts.get(change["changeType"], 0) + 1
    if counts != {"CHECKED_IN": 130, "SEAT_CHANGE": 20}:
        raise AssertionError(f"check-in page counts {counts}")
    return changes


def _over_server_cap():
    """One more passenger-added event than the route's maximum page size."""
    flight = _flight("2070")
    passengers = [_pax("FAR0001", "FAR0001")] + [
        _pax(f"FAR{i:04d}", f"FAR{i:04d}") for i in range(2, 1002)
    ]
    changes = diff_passenger_list(_snap("o0", []), _snap("o1", passengers), flight)
    changes = _stamp(changes, ["PASSENGER_ADDED"], {"PASSENGER_ADDED": "FAR0001"})
    if len(changes) != PAGE_CAP + 1:
        raise AssertionError(f"expected {PAGE_CAP + 1} adds, got {len(changes)}")
    return changes


def _single_page():
    flight = _flight("2040")
    passengers = [_pax("FUL001", "FULL001")] + [
        _pax(f"OK{i:04d}", f"OK{i:04d}") for i in range(1, 100)
    ]
    changes = diff_passenger_list(_snap("p0", []), _snap("p1", passengers), flight)
    changes = _stamp(changes, ["PASSENGER_ADDED"], {"PASSENGER_ADDED": "FULL001"})
    if len(changes) != 100:
        raise AssertionError(f"expected 100 adds, got {len(changes)}")
    return changes


def build_fixture():
    flights = {
        "2057": _busy_departure(),
        "2060": _checkin_page(),
        "2070": _over_server_cap(),
        "2040": _single_page(),
    }
    return {
        "origin": ORIGIN,
        "date": DATE,
        "fullPage": {
            "flight": "2040",
            "total": len(flights["2040"]),
            "required": ["FULL001"],
        },
        "truncated": {
            "flight": "2057",
            "total": len(flights["2057"]),
            "required": ["EARLYADD", "EARLYSEAT", "EARLYCKIN"],
        },
        "checkins": {
            "flight": "2060",
            "totalCheckins": 130,
            "required": ["CKINOLD"],
            "forbidden": ["SEATONLY"],
            "chip": "CHECKED IN",
        },
        "overCap": {
            "flight": "2070",
            "total": len(flights["2070"]),
            "required": ["FAR0001"],
            "serverPageCap": PAGE_CAP,
        },
    }, [change for batch in flights.values() for change in batch]


class _Cursor:
    def __init__(self, docs, projection):
        self._docs = docs
        self._projection = projection
        self._ordering = None
        self._skip = 0
        self._limit = 0

    def sort(self, key_or_list, direction=None):
        if direction is not None:
            if not isinstance(key_or_list, str):
                raise TypeError("sort field must be a string when direction is set")
            pairs = [(key_or_list, direction)]
        elif isinstance(key_or_list, str):
            pairs = [(key_or_list, 1)]
        else:
            pairs = list(key_or_list)
        self._ordering = pairs
        return self

    def skip(self, count):
        if not isinstance(count, int):
            raise TypeError("skip must be an integer")
        if count < 0:
            raise ValueError("skip must be >= 0")
        self._skip = count
        return self

    def limit(self, count):
        if not isinstance(count, int):
            raise TypeError("limit must be an integer")
        self._limit = count
        return self

    def __iter__(self):
        docs = list(self._docs)
        if self._ordering:
            for key, direction in reversed(self._ordering):
                descending = direction in (-1, "desc", "descending")
                docs.sort(key=lambda doc, key=key: doc.get(key) or "", reverse=descending)
        if self._skip:
            docs = docs[self._skip :]
        if self._limit:
            docs = docs[: abs(self._limit)]
        for doc in docs:
            yield _project(copy.deepcopy(doc), self._projection)


def _match_value(actual, expected):
    if isinstance(expected, dict):
        for operator, operand in expected.items():
            if operator == "$lt":
                ok = actual is not None and actual < operand
            elif operator == "$lte":
                ok = actual is not None and actual <= operand
            elif operator == "$gt":
                ok = actual is not None and actual > operand
            elif operator == "$gte":
                ok = actual is not None and actual >= operand
            elif operator == "$eq":
                ok = actual == operand
            elif operator == "$ne":
                ok = actual != operand
            elif operator == "$in":
                ok = actual in operand
            else:
                raise AssertionError(f"test store has no {operator} operator")
            if not ok:
                return False
        return True
    return actual == expected


def _match(doc, query):
    return all(_match_value(doc.get(key), expected) for key, expected in query.items())


def _project(doc, projection):
    if not projection:
        return doc
    included = [key for key, flag in projection.items() if flag]
    if not included:
        return doc
    projected = {key: doc.get(key) for key in included if key in doc}
    if "_id" not in projection or projection.get("_id"):
        if "_id" in doc:
            projected["_id"] = doc["_id"]
    return projected


class _Collection:
    def __init__(self):
        self._docs = []

    def insert_many(self, docs):
        self._docs.extend(copy.deepcopy(doc) for doc in docs)

    def find(self, query=None, projection=None):
        query = query or {}
        matched = [doc for doc in self._docs if _match(doc, query)]
        return _Cursor(matched, projection)


class _Database:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        collection = self._collections.get(name)
        if collection is None:
            collection = _Collection()
            self._collections[name] = collection
        return collection


def _last_names(payload):
    return [(doc.get("passenger") or {}).get("lastName") for doc in payload]


def _assert_api_pages(client, fixture):
    """The harness must actually cap pages, or the UI test would not be able to fail."""
    params = {"origin": ORIGIN, "date": DATE}
    truncated = client.get("/flights/2057/changes", params={**params, "limit": 100})
    assert truncated.status_code == 200, truncated.text
    page = truncated.json()
    assert len(page) == 100
    visible = set(_last_names(page))
    for marker in fixture["truncated"]["required"]:
        assert marker not in visible, marker
    summary = client.get("/flights/2057/changes/summary", params=params)
    assert summary.status_code == 200, summary.text
    assert summary.json()["totalChanges"] == fixture["truncated"]["total"]
    assert summary.json()["totalChanges"] > len(page)

    checkins = client.get(
        "/flights/2060/changes",
        params={**params, "change_type": "CHECKED_IN", "limit": 100},
    )
    assert checkins.status_code == 200, checkins.text
    checkin_page = checkins.json()
    assert len(checkin_page) == 100
    assert "CKINOLD" not in _last_names(checkin_page)
    checkin_summary = client.get("/flights/2060/changes/summary", params=params)
    assert checkin_summary.json()["changeTypes"]["CHECKED_IN"] == 130
    assert checkin_summary.json()["totalChanges"] == 150

    over = client.get("/flights/2070/changes", params={**params, "limit": PAGE_CAP})
    assert over.status_code == 200, over.text
    over_page = over.json()
    assert len(over_page) == PAGE_CAP
    assert "FAR0001" not in _last_names(over_page)
    rejected = client.get("/flights/2070/changes", params={**params, "limit": PAGE_CAP + 1})
    assert rejected.status_code == 422, rejected.text

    full = client.get("/flights/2040/changes", params={**params, "limit": 100})
    assert full.status_code == 200, full.text
    full_page = full.json()
    assert len(full_page) == 100
    assert "FULL001" in _last_names(full_page)
    full_summary = client.get("/flights/2040/changes/summary", params=params)
    assert full_summary.json()["totalChanges"] == 100


def build_app(database, fixture):
    changes_routes.get_db = lambda: database
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(changes_routes.router)

    @app.get("/__fixture")
    def read_fixture():
        return fixture

    @app.middleware("http")
    async def log_change_reads(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if "/changes" in path:
            print(
                f"{request.method} {path}?{request.url.query} -> {response.status_code}",
                flush=True,
            )
        return response

    return app


def main():
    fixture, documents = build_fixture()
    database = _Database()
    database["changes"].insert_many(documents)
    app = build_app(database, fixture)
    _assert_api_pages(TestClient(app), fixture)

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if port == 0:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
    print(f"READY http://127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
