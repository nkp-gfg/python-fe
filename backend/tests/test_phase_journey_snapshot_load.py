"""Failing spec for unbounded historical manifest loads in phase journey.

GET /flights/{flight_number}/phase-journey currently accepts a flight number
with no departure date and no origin, then materializes every passenger_list
snapshot and every flight_status snapshot for that number. These tests call
that route on the real FastAPI app, against a real MongoDB, and fail until:

* a request that omits departure date or origin is rejected before any
  snapshots collection read, and
* a request that supplies both decodes at most MAX_SNAPSHOTS_PER_TYPE
  snapshot documents for each snapshot type and does not decode passenger
  arrays into the API process.

Run against a local mongod (default mongodb://127.0.0.1:27017):

    PYTHONPATH=. python3 -m pytest backend/tests/test_phase_journey_snapshot_load.py -vv

Reviewed and not the same bug (left untested here):
* GET /flights/{flight_number}/snapshots limits to 100 and projects away data
* GET /flights/{flight_number}/timeline projects snapshot data away
* GET /flights/{flight_number}/passengers and the phase-journey fallback load
  one current passenger_list document, not every historical snapshot
* dashboard and boarding-progress snapshot reads are find_one
"""

import os
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.collection import Collection

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# One phase-journey request may decode at most this many snapshot documents
# of a single snapshotType. The handler only needs the first snapshot of each
# operational phase (there are five phases).
MAX_SNAPSHOTS_PER_TYPE = 8

FLIGHT_NUMBER = "84821"
ORIGIN = "BAH"
HEAVY_DATE = "2026-06-15"
HEAVY_SNAPSHOTS_PER_TYPE = 12
OTHER_DATES = [f"2026-06-{day:02d}" for day in range(1, 7)]
PASSENGERS_PER_SNAPSHOT = 25
MANIFEST_MARKER = "historical-manifest-body-84821"
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")

_ORIGINAL_FIND = Collection.find
_ORIGINAL_AGGREGATE = Collection.aggregate


def _passenger_records(document):
    """Count passenger elements on a document decoded into the API process.

    Any non-empty passengers array counts, including a projection that keeps
    only a few passenger fields. Counts have to be computed in MongoDB.
    """
    found = 0

    def walk(node):
        nonlocal found
        if isinstance(node, dict):
            passengers = node.get("passengers")
            if isinstance(passengers, list):
                found += len(passengers)
            for key, value in node.items():
                if key != "passengers":
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    return found


class _SnapshotLoadMonitor:
    """Records snapshot documents the real driver yields to the handler."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.records = []
            self.commands = []

    def command(self, summary):
        with self._lock:
            self.commands.append(summary)

    def observe(self, summary, document):
        snapshot_type = (summary.get("filter") or {}).get("snapshotType")
        if snapshot_type is None and isinstance(document, dict):
            snapshot_type = document.get("snapshotType")
        record = {
            "op": summary.get("op"),
            "snapshotType": snapshot_type,
            "limit": summary.get("limit") or 0,
            "passengers": _passenger_records(document),
        }
        with self._lock:
            self.records.append(record)

    def snapshot(self):
        with self._lock:
            return list(self.commands), list(self.records)


class _ObservedCursor:
    """Cursor proxy that yields the same documents the real cursor yields."""

    def __init__(self, cursor, monitor, summary):
        self._cursor = cursor
        self._monitor = monitor
        self._summary = summary

    def limit(self, limit):
        self._summary["limit"] = limit
        self._cursor.limit(limit)
        return self

    def __iter__(self):
        for document in self._cursor:
            self._monitor.observe(self._summary, document)
            yield document

    def __getattr__(self, name):
        attr = getattr(self._cursor, name)
        if not callable(attr):
            return attr

        def method(*args, **kwargs):
            result = attr(*args, **kwargs)
            if result is self._cursor:
                return self
            return result

        return method


def _install_monitor(monitor):
    def find(self, *args, **kwargs):
        cursor = _ORIGINAL_FIND(self, *args, **kwargs)
        if self.name != "snapshots":
            return cursor
        filt = kwargs.get("filter")
        if filt is None and args:
            filt = args[0]
        projection = kwargs.get("projection")
        if projection is None and len(args) > 1:
            projection = args[1]
        summary = {
            "op": "find",
            "filter": filt or {},
            "projection": projection,
            "limit": kwargs.get("limit") or 0,
        }
        monitor.command(summary)
        return _ObservedCursor(cursor, monitor, summary)

    def aggregate(self, pipeline, *args, **kwargs):
        cursor = _ORIGINAL_AGGREGATE(self, pipeline, *args, **kwargs)
        if self.name != "snapshots":
            return cursor
        summary = {"op": "aggregate", "pipeline": pipeline, "limit": 0, "filter": {}}
        for stage in pipeline:
            if isinstance(stage, dict) and "$limit" in stage:
                summary["limit"] = stage["$limit"]
            if isinstance(stage, dict) and "$match" in stage:
                summary["filter"] = stage["$match"]
        monitor.command(summary)
        return _ObservedCursor(cursor, monitor, summary)

    Collection.find = find
    Collection.aggregate = aggregate


def _restore_collection_methods():
    Collection.find = _ORIGINAL_FIND
    Collection.aggregate = _ORIGINAL_AGGREGATE


def _passenger(index):
    checked_in = index % 3 != 0
    boarded = index % 5 == 0
    return {
        "pnr": f"HX{index:04d}",
        "lastName": "SMITH",
        "firstName": "JORDAN",
        "cabin": "Y",
        "bookingClass": "V",
        "seat": f"{index % 30 + 1}A",
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "isChild": False,
        "hasInfant": False,
        "isRevenue": True,
        "passengerType": "F",
        "bagCount": 1,
        "manifestMarker": MANIFEST_MARKER,
    }


def _snapshot(flight_number, origin, departure_date, snapshot_type, sequence, passengers):
    status_cycle = ["", "OPENCI", "OPENCI", "FINAL", "PDC"]
    status = status_cycle[sequence % len(status_cycle)]
    if snapshot_type == "passenger_list":
        data = {
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "totalPassengers": len(passengers),
            "passengers": passengers,
        }
    else:
        data = {
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "status": status,
            "boarding": {"indicator": "BDG" if sequence % 5 == 2 else ""},
            "gate": "A1",
        }
    return {
        "snapshotId": f"{flight_number}-{origin}-{departure_date}-{snapshot_type}-{sequence}",
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "snapshotType": snapshot_type,
        "capturedAt": f"{departure_date}T{sequence % 24:02d}:00:00Z",
        "sequenceNumber": sequence,
        "data": data,
    }


def _seed(db):
    db["snapshots"].delete_many({"flightNumber": FLIGHT_NUMBER})
    db["flight_status"].delete_many({"flightNumber": FLIGHT_NUMBER})
    db["passenger_list"].delete_many({"flightNumber": FLIGHT_NUMBER})
    db["reservations"].delete_many({"flightNumber": FLIGHT_NUMBER})

    passengers = [_passenger(index) for index in range(PASSENGERS_PER_SNAPSHOT)]
    documents = []
    sequence = 1
    for _ in range(HEAVY_SNAPSHOTS_PER_TYPE):
        for snapshot_type in ("passenger_list", "flight_status"):
            documents.append(
                _snapshot(FLIGHT_NUMBER, ORIGIN, HEAVY_DATE, snapshot_type, sequence, passengers)
            )
        sequence += 1
    for departure_date in OTHER_DATES:
        for snapshot_type in ("passenger_list", "flight_status"):
            documents.append(
                _snapshot(
                    FLIGHT_NUMBER, ORIGIN, departure_date, snapshot_type, sequence, passengers
                )
            )
        sequence += 1
    db["snapshots"].insert_many(documents)
    db["flight_status"].insert_one(
        {
            "airline": "GF",
            "flightNumber": FLIGHT_NUMBER,
            "origin": ORIGIN,
            "departureDate": HEAVY_DATE,
            "fetchedAt": f"{HEAVY_DATE}T12:00:00Z",
            "status": "OPENCI",
            "gate": "A1",
        }
    )


def _counts(records):
    by_type = {"passenger_list": 0, "flight_status": 0, "other": 0}
    passengers = 0
    for record in records:
        passengers += record["passengers"]
        snapshot_type = record["snapshotType"]
        if snapshot_type in by_type:
            by_type[snapshot_type] += 1
        else:
            by_type["other"] += 1
    return by_type, passengers


@pytest.fixture(scope="module")
def api():
    monitor = _SnapshotLoadMonitor()
    _install_monitor(monitor)
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client["falconeye_phase_journey_test"]
    client.admin.command("ping")

    import backend.api.database as database_module
    import backend.api.main as main
    import backend.api.routes.flights as flights

    database_module.get_db = lambda: database
    main.get_db = lambda: database
    flights.get_db = lambda: database

    with TestClient(main.app) as http:
        _seed(database)
        monitor.reset()
        yield http, monitor

    _restore_collection_methods()
    database["snapshots"].delete_many({"flightNumber": FLIGHT_NUMBER})
    database["flight_status"].delete_many({"flightNumber": FLIGHT_NUMBER})
    client.close()


def test_unauthenticated_caller_cannot_load_every_historical_manifest(api):
    """Attack path: learn the flight number, then request phase journey with no date or origin."""
    http, monitor = api
    monitor.reset()

    listed = http.get("/flights")
    assert listed.status_code == 200, listed.text
    numbers = {row.get("flightNumber") for row in listed.json()}
    assert FLIGHT_NUMBER in numbers

    monitor.reset()
    response = http.get(f"/flights/{FLIGHT_NUMBER}/phase-journey")
    commands, records = monitor.snapshot()
    by_type, passengers = _counts(records)
    assert response.status_code in (400, 422) and not records, (
        "phase journey with no date and no origin must be rejected before any "
        f"snapshot document is decoded; status={response.status_code} "
        f"body={response.text[:400]} commands={commands} "
        f"decoded={by_type} passenger_records={passengers}"
    )


@pytest.mark.parametrize(
    "query",
    [
        f"?date={HEAVY_DATE}",
        f"?origin={ORIGIN}",
    ],
)
def test_partial_flight_identity_is_rejected_before_snapshot_reads(api, query):
    http, monitor = api
    monitor.reset()
    response = http.get(f"/flights/{FLIGHT_NUMBER}/phase-journey{query}")
    commands, records = monitor.snapshot()
    by_type, passengers = _counts(records)
    assert response.status_code in (400, 422) and not records, (
        "phase journey must reject a request that omits departure date or origin "
        f"before reading snapshots; query={query} status={response.status_code} "
        f"body={response.text[:400]} commands={commands} "
        f"decoded={by_type} passenger_records={passengers}"
    )


def test_scoped_phase_journey_does_not_decode_every_manifest(api):
    """Date and origin are present, and more than MAX_SNAPSHOTS_PER_TYPE snapshots are stored."""
    http, monitor = api
    monitor.reset()
    response = http.get(
        f"/flights/{FLIGHT_NUMBER}/phase-journey?origin={ORIGIN}&date={HEAVY_DATE}"
    )
    commands, records = monitor.snapshot()
    by_type, passengers = _counts(records)
    assert passengers == 0, (
        "phase journey decoded historical passenger arrays into the API process; "
        f"passenger_records={passengers} decoded={by_type} commands={commands} "
        f"status={response.status_code} body={response.text[:400]}"
    )
    assert by_type["passenger_list"] <= MAX_SNAPSHOTS_PER_TYPE, (
        "passenger_list snapshot reads are not capped at "
        f"{MAX_SNAPSHOTS_PER_TYPE}; decoded={by_type} commands={commands}"
    )
    assert by_type["flight_status"] <= MAX_SNAPSHOTS_PER_TYPE, (
        "flight_status snapshot reads are not capped at "
        f"{MAX_SNAPSHOTS_PER_TYPE}; decoded={by_type} commands={commands}"
    )
    assert by_type["other"] == 0, (
        f"decoded snapshot documents with no snapshot type: {by_type} commands={commands}"
    )
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert "phases" in body and "transitions" in body
