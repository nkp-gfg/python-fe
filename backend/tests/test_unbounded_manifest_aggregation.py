"""Status-distribution and the sibling comparison reads must not sort every
stored passenger-list manifest for a caller-supplied date.

An unauthenticated GET with only a departure date currently matches every
snapshot for that date, sorts the full documents (passengers and the raw
payload) in memory, keeps the latest document per flight, and then expands
every passenger. These tests fail while that pipeline is what the handlers
run. They pass when a date-only request no longer does that work, including
when a rejection happens only after the aggregation has already run.

Run against a local MongoDB (no credentials):

    MONGODB_URI=mongodb://127.0.0.1:27017 PYTHONPATH=. \
        python3 -m pytest backend/tests/test_unbounded_manifest_aggregation.py -q
"""

import os
import threading
from contextlib import contextmanager

os.environ["MONGODB_URI"] = os.environ.get(
    "TEST_MONGODB_URI", "mongodb://127.0.0.1:27017"
)

import pytest
from fastapi.testclient import TestClient
from pymongo.synchronous.collection import Collection

from backend.api.main import app
from backend.feeder import storage
from backend.feeder.aggregations import (
    find_missing_pnrs,
    passenger_status_distribution,
)
from backend.feeder.comparison import compare_passengers_across_flights
from backend.feeder.converter import convert_passenger_list

DATE = "2099-08-17"
OTHER_DATE = "2098-08-17"
RAW_PAD = "R" * 8000


def _passenger(index, cabin, checked_in):
    return {
        "Name_Details": {"LastName": f"Last{index}", "FirstName": "Ada"},
        "PNRLocator": f"PNR{index:04d}",
        "PassengerID": str(index),
        "Cabin": cabin,
        "BookingClass": "Y" if cabin == "Y" else "J",
        "CheckIn_Info": {"CheckInStatus": "true" if checked_in else "false"},
        "Boarding_Info": {"BoardStatus": "false"},
        "Indicators": {"Indicator": "Revenue"},
    }


def _manifest(flight_number, departure_date, fetched_at, passengers, origin="BAH"):
    """Build a document through the production converter, then stamp fetchedAt."""
    raw = {
        "ItineraryInfo": {
            "Itinerary": {"Destination": "LHR", "AircraftType": "32N"},
            "CabinInfoList": {"CabinInfo": []},
        },
        "PassengerInfoList": {"PassengerInfo": passengers},
        "_pad": RAW_PAD,
    }
    doc = convert_passenger_list(raw, "GF", flight_number, departure_date, origin)
    doc["fetchedAt"] = fetched_at
    return doc


def _stage_name(stage):
    return next(iter(stage))


def _walk_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _flight_bounded(match):
    if not isinstance(match, dict):
        return False
    flight_number = match.get("flightNumber")
    if isinstance(flight_number, str) and flight_number.strip():
        return True
    if isinstance(flight_number, dict):
        chosen = flight_number.get("$in")
        if (
            isinstance(chosen, list)
            and chosen
            and all(isinstance(item, str) and item.strip() for item in chosen)
        ):
            return True
    return False


def _apply_projection(op, spec, carries_raw, carries_passengers):
    if op == "$unset":
        fields = spec if isinstance(spec, list) else [spec]
        names = {str(field) for field in fields}
        if "_raw" in names:
            carries_raw = False
        if "passengers" in names:
            carries_passengers = False
        return carries_raw, carries_passengers
    if not isinstance(spec, dict):
        return carries_raw, carries_passengers
    inclusion_values = [
        value for key, value in spec.items() if key != "_id" and value in (1, True)
    ]
    exclusion_values = [
        value for key, value in spec.items() if key != "_id" and value in (0, False)
    ]
    if exclusion_values and not inclusion_values:
        if spec.get("_raw") in (0, False):
            carries_raw = False
        if spec.get("passengers") in (0, False):
            carries_passengers = False
        return carries_raw, carries_passengers
    carries_raw = spec.get("_raw") in (1, True)
    carries_passengers = spec.get("passengers") in (1, True)
    return carries_raw, carries_passengers


def expands_full_manifests(pipeline):
    """True when this pipeline sorts manifests that still carry `_raw` or the
    full `passengers` array and then keeps or unwinds those passengers.

    A flight-number constraint does not make that sort safe. The payload has
    to be gone before `$sort`, otherwise every historical snapshot of the
    named flights is still sorted in memory.
    """
    if not isinstance(pipeline, list):
        return False
    carries_raw = True
    carries_passengers = True
    sorted_full_manifest = False
    expands_passengers = False
    for stage in pipeline:
        if not isinstance(stage, dict) or not stage:
            continue
        op = _stage_name(stage)
        spec = stage[op]
        if op == "$sort":
            if carries_raw or carries_passengers:
                sorted_full_manifest = True
        elif op == "$unwind":
            path = spec.get("path") if isinstance(spec, dict) else spec
            if "passenger" in str(path):
                expands_passengers = True
        elif op == "$group":
            values = list(_walk_values(spec))
            if "$$ROOT" in values:
                expands_passengers = True
            if "$passengers" in values or "$doc.passengers" in values or "$paxDoc.passengers" in values:
                expands_passengers = True
        elif op in ("$project", "$unset"):
            carries_raw, carries_passengers = _apply_projection(
                op, spec, carries_raw, carries_passengers
            )
    # A flight-number constraint does not excuse this: historical snapshots
    # of the named flights still carry `_raw` and the full passenger array
    # into the in-memory sort.
    return sorted_full_manifest and expands_passengers


def _iter_stages(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_stages(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_stages(item)


def explain_summary(explain):
    scans = []
    sorts = []
    unwind_rows = []
    for stage in _iter_stages(explain):
        name = stage.get("stage")
        if name in ("COLLSCAN", "IXSCAN", "scan"):
            scans.append(
                {
                    "stage": name,
                    "nReturned": stage.get("nReturned"),
                    "direction": stage.get("direction"),
                }
            )
        if name == "sort":
            sorts.append(
                {
                    "nReturned": stage.get("nReturned"),
                    "memLimit": stage.get("memLimit"),
                    "usedDisk": stage.get("usedDisk"),
                    "spills": stage.get("spills"),
                    "totalDataSizeSortedBytesEstimate": stage.get(
                        "totalDataSizeSortedBytesEstimate"
                    ),
                }
            )
        if name in ("$unwind", "unwind") and "nReturned" in stage:
            unwind_rows.append(stage.get("nReturned"))
    blocking = [item for item in sorts if item.get("memLimit")]
    return {
        "scans": scans,
        "sorts": sorts,
        "blockingSortInput": max(
            (item["nReturned"] or 0 for item in blocking), default=None
        ),
        "unwindRows": unwind_rows,
    }


def _pipeline_sorts_fetched_at(pipeline):
    for stage in pipeline:
        if isinstance(stage, dict) and "$sort" in stage:
            spec = stage["$sort"]
            if isinstance(spec, dict) and "fetchedAt" in spec:
                return True
    return False


def _allowed_flight_keys(pipeline, date_flight_count):
    match = None
    for stage in pipeline:
        if isinstance(stage, dict) and "$match" in stage:
            match = stage["$match"]
            break
    if isinstance(match, dict) and _flight_bounded(match):
        flight_number = match.get("flightNumber")
        if isinstance(flight_number, dict) and isinstance(flight_number.get("$in"), list):
            return len(flight_number["$in"])
        return 1
    return date_flight_count


def call_sorts_historical_snapshots(call, date_flight_count):
    """True when a fetchedAt sort's input is every snapshot, not one per flight."""
    summary = call.get("explain")
    if not summary or not _pipeline_sorts_fetched_at(call["pipeline"]):
        return False
    blocking = summary.get("blockingSortInput")
    if blocking is None:
        return False
    return blocking > _allowed_flight_keys(call["pipeline"], date_flight_count)


def _offending(call, date_flight_count):
    pipeline = call["pipeline"]
    reasons = []
    if call["collection"] == "passenger_list" and expands_full_manifests(pipeline):
        reasons.append("sorts full manifests and expands passengers")
    if call["collection"] == "passenger_list" and call_sorts_historical_snapshots(
        call, date_flight_count
    ):
        reasons.append(
            "fetchedAt sort input "
            f"{call['explain'].get('blockingSortInput')} exceeds "
            f"{_allowed_flight_keys(pipeline, date_flight_count)} flight keys"
        )
    return reasons


@contextmanager
def recording_aggregates():
    """Forward to MongoDB, and keep the pipeline the product actually sent."""
    original = Collection.aggregate
    calls = []
    lock = threading.Lock()
    inflight = {"current": 0, "max": 0}

    def wrapped(self, pipeline, *args, **kwargs):
        explain = None
        if self.name == "passenger_list":
            try:
                explained = self.database.command(
                    {
                        "explain": {
                            "aggregate": self.name,
                            "pipeline": pipeline,
                            "cursor": {},
                        },
                        "verbosity": "executionStats",
                    }
                )
                explain = explain_summary(explained)
            except Exception as exc:  # explain is evidence; the aggregate still runs
                explain = {"error": type(exc).__name__}
        call = {
            "collection": self.name,
            "pipeline": pipeline,
            "kwargs": dict(kwargs),
            "allowDiskUse": kwargs.get("allowDiskUse", False),
            "thread": threading.current_thread().name,
            "explain": explain,
        }
        with lock:
            inflight["current"] += 1
            inflight["max"] = max(inflight["max"], inflight["current"])
            calls.append(call)
        try:
            return original(self, pipeline, *args, **kwargs)
        finally:
            with lock:
                inflight["current"] -= 1

    Collection.aggregate = wrapped
    try:
        yield calls, inflight
    finally:
        Collection.aggregate = original


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        db = storage.get_db()
        db["passenger_list"].delete_many(
            {"departureDate": {"$in": [DATE, OTHER_DATE]}}
        )
        older = _manifest(
            "100",
            DATE,
            "2099-08-17T00:00:00Z",
            [_passenger(1, "Y", checked_in=False)],
        )
        latest = _manifest(
            "100",
            DATE,
            "2099-08-17T03:00:00Z",
            [
                _passenger(1, "J", checked_in=True),
                _passenger(2, "J", checked_in=True),
            ],
        )
        second_flight = _manifest(
            "200",
            DATE,
            "2099-08-17T03:00:00Z",
            [_passenger(3, "Y", checked_in=False)],
        )
        other_date = _manifest(
            "300",
            OTHER_DATE,
            "2098-08-17T03:00:00Z",
            [_passenger(4, "Y", checked_in=False)],
        )
        for doc in (older, latest, second_flight, other_date):
            assert "_raw" in doc and doc["passengers"]
            storage.store_passenger_list(doc)
        date_flights = {
            (doc["flightNumber"], doc["origin"])
            for doc in db["passenger_list"].find({"departureDate": DATE})
        }
        yield {
            "client": client,
            "db": db,
            "date_flight_count": len(date_flights),
            "snapshot_count": db["passenger_list"].count_documents({}),
            "indexes": sorted(db["passenger_list"].index_information().keys()),
        }
        db["passenger_list"].delete_many(
            {"departureDate": {"$in": [DATE, OTHER_DATE]}}
        )


def _response_body(response):
    if response is None:
        return None
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        return response.json()
    return response.text


def _assert_safe(calls, date_flight_count, response=None, overlap=None):
    offending = []
    for call in calls:
        reasons = _offending(call, date_flight_count)
        if reasons:
            offending.append({**call, "reasons": reasons})
    details = {
        "httpStatus": None if response is None else response.status_code,
        "httpBody": _response_body(response),
        "overlap": overlap,
        "offending": [
            {
                "reasons": item["reasons"],
                "collection": item["collection"],
                "allowDiskUse": item["allowDiskUse"],
                "thread": item["thread"],
                "kwargs": item["kwargs"],
                "pipeline": item["pipeline"],
                "explain": item["explain"],
            }
            for item in offending
        ],
    }
    assert not offending, details
    if response is not None:
        assert response.status_code < 500, details


def test_status_distribution_date_only_does_not_sort_and_unwind_every_manifest(api):
    """GET /compare/status-distribution?date= with no credentials and no flight set."""
    with recording_aggregates() as (calls, inflight):
        response = api["client"].get(
            "/compare/status-distribution",
            params={"date": DATE},
        )
        try:
            passenger_status_distribution(DATE)
        except (ValueError,):
            pass
    _assert_safe(
        calls,
        api["date_flight_count"],
        response=response,
        overlap={
            "max": inflight["max"],
            "snapshots": api["snapshot_count"],
            "dateFlights": api["date_flight_count"],
            "indexes": api["indexes"],
        },
    )


def test_status_distribution_named_flights_does_not_sort_full_manifests(api):
    """A flight list on the query string must not be ignored, and must not
    select the pipeline that sorts every stored snapshot's raw payload."""
    with recording_aggregates() as (calls, _inflight):
        response = api["client"].get(
            "/compare/status-distribution",
            params={"date": DATE, "flight_numbers": "100"},
        )
    _assert_safe(calls, api["date_flight_count"], response=response)


def test_pnr_mismatch_date_only_does_not_sort_and_unwind_every_manifest(api):
    """GET /compare/pnr-mismatches uses the same match, sort, $$ROOT, unwind shape."""
    with recording_aggregates() as (calls, _inflight):
        response = api["client"].get(
            "/compare/pnr-mismatches",
            params={"date": DATE},
        )
        try:
            find_missing_pnrs(DATE)
        except (ValueError,):
            pass
    _assert_safe(calls, api["date_flight_count"], response=response)


def test_passenger_comparison_date_only_does_not_sort_full_manifests(api):
    """GET /compare/passengers with no flight_numbers sorts $$ROOT for the date
    and then walks every passenger of the latest snapshot in the API process.
    """
    with recording_aggregates() as (calls, _inflight):
        response = api["client"].get(
            "/compare/passengers",
            params={"date": DATE},
        )
        try:
            compare_passengers_across_flights(DATE, None)
        except (ValueError,):
            pass
    _assert_safe(calls, api["date_flight_count"], response=response)


def test_concurrent_status_distribution_calls_do_not_run_the_full_sort(api):
    """Repeated callers must not each enter the full-manifest sort together."""
    errors = []

    def _call():
        try:
            passenger_status_distribution(DATE)
        except ValueError:
            return
        except Exception as exc:  # a fixed handler may reject; a crash is not a fix
            errors.append(exc)

    with recording_aggregates() as (calls, inflight):
        threads = [threading.Thread(target=_call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    assert not errors, errors
    _assert_safe(
        calls,
        api["date_flight_count"],
        overlap={"maxInAggregate": inflight["max"]},
    )
