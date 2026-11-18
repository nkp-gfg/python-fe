"""Comparison routes must not load a whole departure day into the API process.

These tests are the spec for the missing bound. They fail while the
vulnerability is present and pass once a comparison names at most
MAX_COMPARISON_FLIGHTS flights and a missing, blank, or longer list is
refused before any passenger, flight, or OTP read.

Run from the repository root:

    PYTHONPATH=. python3 -m pytest backend/tests/test_comparison_unbounded_load.py -q

Left untested, because they do not copy one Python row per passenger into a
Polars frame: GET /compare/pnr-mismatches, GET /compare/status-distribution,
GET /compare/change-summary, POST /compare/pnr-mismatches/async, and
GET /data-audit/{flight_number}/passengers (that route already requires one
flight number).
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

MAX_COMPARISON_FLIGHTS = 20
DATE = "2026-04-01"
OTHER_DATE = "2026-04-02"
ORIGIN = "BAH"

# Refusal statuses a fix may use: explicit HTTPException or a required/capped query.
REFUSED = (400, 422)


def _passenger(pnr, last_name, first_name):
    return {
        "pnr": pnr,
        "lastName": last_name,
        "firstName": first_name,
        "cabin": "Y",
        "bookingClass": "Y",
        "isCheckedIn": True,
        "isBoarded": False,
        "seat": "1A",
    }


def _passenger_list_doc(flight_number, fetched_at, passengers, date=DATE):
    return {
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": date,
        "fetchedAt": fetched_at,
        "passengers": passengers,
    }


def _flight_doc(flight_number, total_pax, date=DATE):
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": date,
        "lastUpdatedAt": f"{date}T00:00:00Z",
        "summary": {
            "status": "ON_TIME",
            "gate": "A1",
            "totalPax": total_pax,
            "checkedIn": total_pax,
            "boarded": 0,
        },
    }


def _otp_row(flight_number, total_pax, date=DATE):
    return {
        "flight_number": flight_number,
        "scheduled_origin": ORIGIN,
        "flight_status": "SCH",
        "scheduled_departure_utc": f"{date}T01:00:00Z",
        "actual_block_off_utc": None,
        "actual_block_on_utc": None,
        "aircraft_type": "320",
        "passenger_counts": [{"Count": total_pax}],
        "scheduled_departure_local": date,
        "scheduled_arrival_local": date,
    }


def _flight_number(index):
    return f"GF{index:03d}"


def _build_day(flight_count):
    """Latest passenger list plus an older snapshot that must not be loaded."""
    passenger_docs = []
    flight_docs = []
    otp_rows = []
    expected_names = {}
    for index in range(1, flight_count + 1):
        flight_number = _flight_number(index)
        latest = [
            _passenger(f"{flight_number}A", f"LATE{index}", "ONE"),
            _passenger(f"{flight_number}B", f"LATE{index}", "TWO"),
        ]
        older = [_passenger(f"{flight_number}Z", "GHOST", str(index))]
        passenger_docs.append(
            _passenger_list_doc(flight_number, "2026-04-01T02:00:00Z", latest)
        )
        passenger_docs.append(
            _passenger_list_doc(flight_number, "2026-04-01T01:00:00Z", older)
        )
        passenger_docs.append(
            _passenger_list_doc(
                flight_number,
                "2026-04-02T02:00:00Z",
                [_passenger(f"{flight_number}X", "OTHERDAY", "NO")],
                date=OTHER_DATE,
            )
        )
        flight_docs.append(_flight_doc(flight_number, len(latest)))
        flight_docs.append(_flight_doc(flight_number, 9, date=OTHER_DATE))
        otp_rows.append(_otp_row(flight_number, len(latest)))
        otp_rows.append(_otp_row(flight_number, 9, date=OTHER_DATE))
        expected_names[flight_number] = {("LATE" + str(index), "ONE"), ("LATE" + str(index), "TWO")}
    return passenger_docs, flight_docs, otp_rows, expected_names


def _matches(doc, query):
    for key, expected in query.items():
        if isinstance(expected, dict) and "$in" in expected:
            if doc.get(key) not in expected["$in"]:
                return False
        elif doc.get(key) != expected:
            return False
    return True


def _apply_passenger_pipeline(docs, pipeline):
    """Apply the stages load_sabre_passengers sends to MongoDB.

    The pipeline is $match, $sort by fetchedAt descending, then $group taking
    the first document per flight number and origin. Later stages are rejected
    so a rewrite that still asks the API process to read passenger rows cannot
    hide behind an unimplemented stage.
    """
    current = list(docs)
    for stage in pipeline:
        if "$match" in stage:
            current = [doc for doc in current if _matches(doc, stage["$match"])]
        elif "$sort" in stage:
            for key, direction in reversed(list(stage["$sort"].items())):
                current.sort(key=lambda doc, field=key: doc.get(field) or "", reverse=direction < 0)
        elif "$group" in stage:
            spec = stage["$group"]
            grouped = {}
            order = []
            for doc in current:
                identity = tuple(
                    (field, doc.get(expr[1:]) if isinstance(expr, str) and expr.startswith("$") else expr)
                    for field, expr in spec["_id"].items()
                )
                if identity not in grouped:
                    if spec.get("doc") != {"$first": "$$ROOT"}:
                        raise AssertionError(f"passenger group must keep the first root document, got {spec}")
                    grouped[identity] = {"_id": {field: value for field, value in identity}, "doc": doc}
                    order.append(identity)
            current = [grouped[identity] for identity in order]
        else:
            raise AssertionError(f"unexpected passenger aggregation stage: {stage}")
    return current


class RecordingCollection:
    def __init__(self, docs, kind):
        self.docs = docs
        self.kind = kind
        self.find_calls = []
        self.aggregate_calls = []
        self.materialized_names = []
        self.materialized_flights = []

    def find(self, query, projection=None):
        self.find_calls.append({"query": query, "projection": projection})
        matched = [doc for doc in self.docs if _matches(doc, query)]
        self.materialized_flights.extend(doc.get("flightNumber") for doc in matched)
        return matched

    def aggregate(self, pipeline):
        self.aggregate_calls.append(pipeline)
        grouped = _apply_passenger_pipeline(self.docs, pipeline)

        def generate():
            for item in grouped:
                doc = item["doc"]
                for passenger in doc.get("passengers") or []:
                    self.materialized_names.append(
                        (passenger.get("lastName"), passenger.get("firstName"))
                    )
                yield item

        return generate()


class RecordingDB:
    def __init__(self, passenger_docs, flight_docs):
        self.passenger_list = RecordingCollection(passenger_docs, "passengers")
        self.flights = RecordingCollection(flight_docs, "flights")

    def __getitem__(self, name):
        if name == "passenger_list":
            return self.passenger_list
        if name == "flights":
            return self.flights
        raise KeyError(name)


class OtpStore:
    """Stand-in for the OTP query built in load_otp_flights.

    The product query filters scheduled departure or arrival date, then an
    optional flight_number IN list. Rows are returned only after that filter.
    """

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query_all(self, sql, params=None):
        self.calls.append((sql, params))
        if not params or len(params) < 2:
            raise AssertionError("OTP comparison query did not receive both date parameters")
        departure_date, arrival_date = params[0], params[1]
        named = [str(value).strip() for value in params[2:]] if len(params) > 2 else None
        if named is None:
            if " IN " in sql.upper():
                raise AssertionError("OTP query has an IN list without flight parameters")
        elif "flight_number IN" not in sql:
            raise AssertionError("OTP query dropped the flight_number filter")
        selected = []
        for row in self.rows:
            departure = str(row.get("scheduled_departure_local") or "")[:10]
            arrival = str(row.get("scheduled_arrival_local") or "")[:10]
            if departure != departure_date and arrival != arrival_date:
                continue
            if named is not None and str(row.get("flight_number")).strip() not in named:
                continue
            selected.append(row)
        return selected


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://127.0.0.1:1")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("POSTGRES_URI", "postgresql://127.0.0.1:1/none")

    import backend.api.main as main
    import backend.feeder.storage as storage

    def unavailable(service):
        raise RuntimeError(f"{service} is not used by these tests")

    monkeypatch.setattr(main, "get_db", lambda: unavailable("MongoDB"))
    monkeypatch.setattr(main, "get_redis", lambda: unavailable("Redis"))

    passenger_docs, flight_docs, otp_rows, expected_names = _build_day(30)
    database = RecordingDB(passenger_docs, flight_docs)
    otp = OtpStore(otp_rows)
    jobs = []
    enqueued = []

    monkeypatch.setattr("backend.feeder.comparison.query_all", otp.query_all)
    monkeypatch.setattr(
        "backend.api.routes.comparison.create_job",
        lambda *args, **kwargs: jobs.append((args, kwargs)) or {"jobId": args[0]},
    )

    import backend.tasks.comparison as comparison_tasks

    monkeypatch.setattr(
        comparison_tasks.run_flight_comparison,
        "delay",
        lambda *args, **kwargs: enqueued.append((args, kwargs)),
    )

    storage._db = None
    with TestClient(main.app) as client:
        storage.init_db(database)
        yield {
            "client": client,
            "db": database,
            "otp": otp,
            "jobs": jobs,
            "enqueued": enqueued,
            "expected_names": expected_names,
        }
    storage._db = None


def _read_evidence(world):
    return {
        "passenger_names": list(world["db"].passenger_list.materialized_names),
        "passenger_aggregates": len(world["db"].passenger_list.aggregate_calls),
        "flight_numbers_read": list(world["db"].flights.materialized_flights),
        "otp_queries": len(world["otp"].calls),
        "jobs_created": len(world["jobs"]),
        "tasks_enqueued": len(world["enqueued"]),
    }


def _assert_refused_without_reads(response, world):
    evidence = _read_evidence(world)
    assert response.status_code in REFUSED, evidence
    assert evidence["passenger_names"] == []
    assert evidence["passenger_aggregates"] == 0
    assert evidence["flight_numbers_read"] == []
    assert evidence["otp_queries"] == 0
    assert evidence["jobs_created"] == 0
    assert evidence["tasks_enqueued"] == 0
    assert world["db"].passenger_list.find_calls == []
    assert world["db"].flights.find_calls == []
    assert world["db"].flights.aggregate_calls == []


def _names_for(expected_names, flight_numbers):
    names = set()
    for flight_number in flight_numbers:
        names |= expected_names[flight_number]
    return names


def test_passengers_without_flight_numbers_are_refused_before_load(world):
    response = world["client"].get("/compare/passengers", params={"date": DATE})

    _assert_refused_without_reads(response, world)


def test_passengers_blank_flight_numbers_are_refused_before_load(world):
    response = world["client"].get(
        "/compare/passengers",
        params={"date": DATE, "flight_numbers": " , , "},
    )

    _assert_refused_without_reads(response, world)


def test_passengers_over_cap_are_refused_before_load(world):
    flight_numbers = ",".join(_flight_number(index) for index in range(1, MAX_COMPARISON_FLIGHTS + 2))
    response = world["client"].get(
        "/compare/passengers",
        params={"date": DATE, "flight_numbers": flight_numbers},
    )

    _assert_refused_without_reads(response, world)


def test_passengers_duplicate_list_over_cap_is_refused_before_load(world):
    flight_numbers = ",".join(["GF001"] * (MAX_COMPARISON_FLIGHTS + 1))
    response = world["client"].get(
        "/compare/passengers",
        params={"date": DATE, "flight_numbers": flight_numbers},
    )

    _assert_refused_without_reads(response, world)


def test_unauthenticated_bounded_passenger_comparison_loads_only_named_flights(world):
    named = [_flight_number(index) for index in range(1, 3)]
    response = world["client"].get(
        "/compare/passengers",
        params={"date": DATE, "flight_numbers": " GF001, GF002 "},
        headers={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["totalPax"] == 4
    assert {flight["flightNumber"] for flight in body["flights"]} == set(named)
    assert set(world["db"].passenger_list.materialized_names) == _names_for(
        world["expected_names"], named
    )
    assert ("GHOST", "1") not in world["db"].passenger_list.materialized_names
    assert ("OTHERDAY", "NO") not in world["db"].passenger_list.materialized_names
    assert world["db"].flights.find_calls == []
    assert world["otp"].calls == []
    assert len(world["db"].passenger_list.aggregate_calls) == 1
    match = world["db"].passenger_list.aggregate_calls[0][0]["$match"]
    assert match["departureDate"] == DATE
    assert match["flightNumber"]["$in"] == named


def test_passenger_comparison_accepts_the_flight_cap(world):
    named = [_flight_number(index) for index in range(1, MAX_COMPARISON_FLIGHTS + 1)]
    response = world["client"].get(
        "/compare/passengers",
        params={"date": DATE, "flight_numbers": ",".join(named)},
    )

    assert response.status_code == 200
    assert response.json()["summary"]["totalPax"] == MAX_COMPARISON_FLIGHTS * 2
    assert set(world["db"].passenger_list.materialized_names) == _names_for(
        world["expected_names"], named
    )


def test_flights_without_flight_numbers_are_refused_before_load(world):
    response = world["client"].get("/compare/flights", params={"date": DATE})

    _assert_refused_without_reads(response, world)


def test_flights_blank_flight_numbers_are_refused_before_load(world):
    response = world["client"].get(
        "/compare/flights",
        params={"date": DATE, "flight_numbers": ""},
    )

    _assert_refused_without_reads(response, world)


def test_flights_over_cap_are_refused_before_load(world):
    flight_numbers = ",".join(_flight_number(index) for index in range(1, MAX_COMPARISON_FLIGHTS + 2))
    response = world["client"].get(
        "/compare/flights",
        params={"date": DATE, "flight_numbers": flight_numbers},
    )

    _assert_refused_without_reads(response, world)


def test_unauthenticated_bounded_flight_comparison_reads_only_named_flights(world):
    response = world["client"].get(
        "/compare/flights",
        params={"date": DATE, "flight_numbers": "GF001,GF002"},
        headers={},
    )

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["totalSabreFlights"] == 2
    assert summary["totalOtpFlights"] == 2
    assert world["db"].flights.materialized_flights == ["GF001", "GF002"]
    assert world["db"].passenger_list.aggregate_calls == []
    assert len(world["otp"].calls) == 1
    _sql, params = world["otp"].calls[0]
    assert params[0] == DATE and params[1] == DATE
    assert list(params[2:]) == ["GF001", "GF002"]
    match = world["db"].flights.find_calls[0]["query"]
    assert match["departureDate"] == DATE
    assert match["flightNumber"]["$in"] == ["GF001", "GF002"]


def test_async_flights_without_flight_numbers_are_refused_before_enqueue(world):
    response = world["client"].post("/compare/flights/async", params={"date": DATE})

    _assert_refused_without_reads(response, world)


def test_async_flights_over_cap_are_refused_before_enqueue(world):
    flight_numbers = ",".join(_flight_number(index) for index in range(1, MAX_COMPARISON_FLIGHTS + 2))
    response = world["client"].post(
        "/compare/flights/async",
        params={"date": DATE, "flight_numbers": flight_numbers},
    )

    _assert_refused_without_reads(response, world)


def test_async_bounded_flight_comparison_enqueues_only_named_flights(world):
    response = world["client"].post(
        "/compare/flights/async",
        params={"date": DATE, "flight_numbers": "GF001,GF002"},
    )

    assert response.status_code == 202
    assert world["db"].passenger_list.aggregate_calls == []
    assert world["db"].flights.find_calls == []
    assert world["otp"].calls == []
    assert len(world["jobs"]) == 1
    assert len(world["enqueued"]) == 1
    _job_id, date, flight_numbers = world["enqueued"][0][0]
    assert date == DATE
    assert flight_numbers == ["GF001", "GF002"]
