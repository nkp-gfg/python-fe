"""Sabre-versus-OTP comparison must report a passenger-count mismatch when both
flight rows exist and the totals differ, including when the operational total
is a confirmed zero.

A missing ``passenger_counts`` value must stay missing. It must not be stored
as a confirmed zero, and it must not be treated as equal to the Sabre total.

The synchronous ``GET /compare/flights`` route and the Celery worker
``falconeye.compare_flights`` both call ``compare_flights_for_date``. These
tests drive that function through each of those entry points. PostgreSQL and
MongoDB are not running here, so the tests replace only those two query
executions. The stand-ins apply the filters those queries build, decode
``jsonb`` with the psycopg2 caster this project loads, and then run the real
loader and comparison code.
"""

import os

os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:1")

from unittest.mock import patch

import psycopg2.extensions as pg_ext
import pytest
from fastapi.testclient import TestClient
from psycopg2._json import JSONB_OID
from psycopg2.extras import RealDictRow

from backend.feeder.comparison import compare_flights_for_date

DATE = "2026-03-19"
ORIGIN = "BAH"
_JSONB = pg_ext.string_types[JSONB_OID]

_OTP_COLUMNS = (
    "flight_number",
    "scheduled_origin",
    "flight_status",
    "scheduled_departure_utc",
    "actual_block_off_utc",
    "actual_block_on_utc",
    "aircraft_type",
    "passenger_counts",
)


class _Flights:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query, projection=None):
        numbers = None
        flight_filter = query.get("flightNumber")
        if isinstance(flight_filter, dict) and "$in" in flight_filter:
            numbers = set(flight_filter["$in"])
        matched = []
        for doc in self.docs:
            if doc.get("departureDate") != query.get("departureDate"):
                continue
            if numbers is not None and doc.get("flightNumber") not in numbers:
                continue
            matched.append(_project(doc, projection))
        return matched


class _Mongo:
    def __init__(self, flights):
        self._flights = _Flights(flights)

    def __getitem__(self, name):
        if name != "flights":
            raise KeyError(name)
        return self._flights


def _project(doc, projection):
    if not projection:
        return dict(doc)
    included = [key for key, flag in projection.items() if flag and key != "_id"]
    return {key: doc.get(key) for key in included}


def sabre_flight(number, total_pax, *, date=DATE, origin=ORIGIN, status="ON TIME"):
    return {
        "airline": "GF",
        "flightNumber": number,
        "origin": origin,
        "departureDate": date,
        "lastUpdatedAt": "2026-03-19T00:00:00Z",
        "flightSequenceNumber": 1,
        "summary": {
            "status": status,
            "gate": "A1",
            "totalPax": total_pax,
            "checkedIn": 0,
            "boarded": 0,
        },
    }


def otp_flight(
    number,
    passenger_counts,
    *,
    date=DATE,
    origin=ORIGIN,
    status="Scheduled",
    decode_jsonb=True,
):
    """Build one ``otp.flight_xml_current`` row.

    ``passenger_counts`` is the on-the-wire jsonb value (bytes or None) when
    ``decode_jsonb`` is true. Pass a Python str and ``decode_jsonb=False`` to
    hit the loader's non-list branch.
    """
    return {
        "flight_number": number,
        "scheduled_origin": origin,
        "flight_status": status,
        "scheduled_departure_utc": f"{date}T08:00:00+00:00",
        "actual_block_off_utc": None,
        "actual_block_on_utc": None,
        "aircraft_type": "320",
        "passenger_counts": passenger_counts,
        "scheduled_departure_local": f"{date}T11:00:00",
        "scheduled_arrival_local": f"{date}T15:00:00",
        "_decode_jsonb": decode_jsonb,
    }


def _decode_passenger_counts(raw):
    """Apply psycopg2's global jsonb caster (OID 3802) the way fetch does."""
    if isinstance(raw, str):
        raw = raw.encode()
    return _JSONB(raw, None)


def _query_otp(sql, params, rows):
    """Stand-in for ``backend.api.postgres.query_all`` on this SELECT only."""
    assert "FROM otp.flight_xml_current" in sql
    assert "passenger_counts" in sql
    params = tuple(params or ())
    assert len(params) >= 2
    departure_date, arrival_date, *flight_numbers = params
    assert departure_date == arrival_date
    if "flight_number IN" in sql:
        assert flight_numbers
        assert sql.count("%s") == len(params)
    else:
        assert flight_numbers == []
    wanted = set(flight_numbers) if flight_numbers else None

    matched = []
    for row in rows:
        departure = str(row["scheduled_departure_local"])[:10]
        arrival = str(row["scheduled_arrival_local"])[:10]
        if departure != departure_date and arrival != arrival_date:
            continue
        number = str(row["flight_number"]).strip()
        if wanted is not None and number not in wanted:
            continue
        projected = {column: row.get(column) for column in _OTP_COLUMNS}
        if row.get("_decode_jsonb", True):
            projected["passenger_counts"] = _decode_passenger_counts(
                projected["passenger_counts"]
            )
        matched.append(dict(RealDictRow(projected)))
    return matched


def install_sources(monkeypatch, sabre_rows, otp_rows):
    database = _Mongo(sabre_rows)

    def query_all(sql, params=None):
        return _query_otp(sql, params, otp_rows)

    monkeypatch.setattr("backend.feeder.comparison.get_db", lambda: database)
    monkeypatch.setattr("backend.feeder.comparison.query_all", query_all)
    monkeypatch.setattr("backend.feeder.storage.get_db", lambda: database)
    return database


def pax_mismatches(report, flight_number=None):
    found = [
        anomaly
        for anomaly in report["anomalies"]
        if anomaly["type"] == "PAX_COUNT_MISMATCH"
    ]
    if flight_number is None:
        return found
    return [anomaly for anomaly in found if anomaly["flightNumber"] == flight_number]


def assert_mismatch(report, flight_number, sabre_total, otp_total):
    found = pax_mismatches(report, flight_number)
    assert found, (
        f"flight {flight_number} was omitted from passenger-count anomalies; "
        f"summary={report['summary']} anomalies={report['anomalies']}"
    )
    assert len(found) == 1
    anomaly = found[0]
    assert anomaly["origin"] == ORIGIN
    assert anomaly["sabre_totalPax"] == sabre_total
    assert anomaly["otp_totalPax"] == otp_total
    if otp_total is None:
        assert anomaly["otp_totalPax"] is None
    else:
        assert anomaly["difference"] == sabre_total - otp_total
    assert report["summary"]["paxCountMismatches"] == len(pax_mismatches(report))
    return anomaly


def assert_no_mismatch(report, flight_number):
    assert pax_mismatches(report, flight_number) == []


@pytest.fixture(scope="module")
def client():
    """Real application, with startup probes pointed at local stand-ins.

    Lifespan pings MongoDB and Redis before serving. Those probes are not part
    of the comparison. The comparison queries are patched per test.
    """

    class _StartupDB:
        def command(self, _name):
            return {"ok": 1}

    class _StartupRedis:
        def ping(self):
            return True

    with (
        patch("backend.api.main.get_db", return_value=_StartupDB()),
        patch("backend.api.main.get_redis", return_value=_StartupRedis()),
        patch("backend.feeder.storage.ensure_indexes", lambda: None),
    ):
        from backend.api.main import app

        with TestClient(app) as test_client:
            yield test_client


def test_compare_routes_require_no_credentials(client):
    from backend.api.main import app
    from backend.api.routes.comparison import router as comparison_router

    assert app.router.dependencies == []
    assert comparison_router.dependencies == []
    routes = [
        route
        for route in comparison_router.routes
        if getattr(route, "path", None) in {"/compare/flights", "/compare/flights/async"}
    ]
    assert {route.path for route in routes} == {
        "/compare/flights",
        "/compare/flights/async",
    }
    for route in routes:
        assert route.dependencies == []
        assert route.dependant.dependencies == []


def test_sync_get_reports_null_empty_and_zero_counts(client, monkeypatch):
    """One unauthenticated request for the departure date in the attack path."""
    install_sources(
        monkeypatch,
        [
            sabre_flight("2057", 42),
            sabre_flight("2058", 17),
            sabre_flight("2059", 8),
            sabre_flight("2061", 11),
            sabre_flight("2062", 9),
        ],
        [
            otp_flight("2057", None),
            otp_flight("2058", b"[]"),
            otp_flight("2059", b'[{"Count": 0}]'),
            otp_flight("2061", b"null"),
            otp_flight(
                "2062",
                '[{"Count": 0}, {"Count": 0}]',
                decode_jsonb=False,
            ),
        ],
    )

    response = client.get(
        "/compare/flights",
        params={"date": DATE, "flight_numbers": "2057,2058,2059,2061,2062"},
    )
    assert response.status_code == 200
    body = response.json()
    assert_mismatch(body, "2057", 42, None)
    assert_mismatch(body, "2058", 17, 0)
    assert_mismatch(body, "2059", 8, 0)
    assert_mismatch(body, "2061", 11, None)
    assert_mismatch(body, "2062", 9, 0)
    assert body["summary"]["matchedFlights"] == 5
    assert body["summary"]["paxCountMismatches"] == 5


def test_compare_flights_for_date_reports_each_suppressed_shape(monkeypatch):
    install_sources(
        monkeypatch,
        [
            sabre_flight("2057", 42),
            sabre_flight("2058", 17),
            sabre_flight("2059", 8),
        ],
        [
            otp_flight("2057", None),
            otp_flight("2058", b"[]"),
            otp_flight("2059", b'[{"@classOfService": "Y", "Count": 0}]'),
        ],
    )

    report = compare_flights_for_date(DATE, ["2057", "2058", "2059"])
    assert_mismatch(report, "2057", 42, None)
    assert_mismatch(report, "2058", 17, 0)
    assert_mismatch(report, "2059", 8, 0)


def test_missing_count_is_not_equal_to_a_sabre_zero(monkeypatch):
    install_sources(
        monkeypatch,
        [sabre_flight("2057", 0)],
        [otp_flight("2057", None)],
    )
    report = compare_flights_for_date(DATE)
    assert_mismatch(report, "2057", 0, None)


def test_async_worker_returns_the_same_mismatches(monkeypatch):
    install_sources(
        monkeypatch,
        [sabre_flight("2057", 42), sabre_flight("2059", 8)],
        [otp_flight("2057", None), otp_flight("2059", b'[{"Count": 0}]')],
    )
    stored = {}

    def set_job_completed(job_id, processed, results):
        stored["job_id"] = job_id
        stored["processed"] = processed
        stored["results"] = results

    monkeypatch.setattr("backend.tasks.comparison.set_job_running", lambda job_id: None)
    monkeypatch.setattr(
        "backend.tasks.comparison.set_job_progress", lambda *args, **kwargs: None
    )
    monkeypatch.setattr("backend.tasks.comparison.set_job_completed", set_job_completed)
    monkeypatch.setattr(
        "backend.tasks.comparison.set_job_failed", lambda *args, **kwargs: None
    )

    from backend.tasks.comparison import run_flight_comparison

    outcome = run_flight_comparison.apply(
        args=["job-otp-count", DATE, ["2057", "2059"]],
        throw=True,
    )
    body = outcome.get()
    assert_mismatch(body, "2057", 42, None)
    assert_mismatch(body, "2059", 8, 0)
    assert stored["results"]["summary"]["paxCountMismatches"] == 2
    assert_mismatch(stored["results"], "2057", 42, None)
    assert_mismatch(stored["results"], "2059", 8, 0)


def test_positive_mismatch_is_still_reported(monkeypatch):
    install_sources(
        monkeypatch,
        [sabre_flight("3001", 30)],
        [otp_flight("3001", b'[{"Count": 20}, {"Count": 3}]')],
    )
    response_report = compare_flights_for_date(DATE, ["3001"])
    assert_mismatch(response_report, "3001", 30, 23)


def test_equal_counts_are_not_mismatches(monkeypatch):
    install_sources(
        monkeypatch,
        [sabre_flight("3002", 15), sabre_flight("3003", 0)],
        [
            otp_flight("3002", b'[{"Count": 10}, {"Count": 5}]'),
            otp_flight("3003", b'[{"Count": 0}, {"Count": 0}]'),
        ],
    )
    report = compare_flights_for_date(DATE)
    assert_no_mismatch(report, "3002")
    assert_no_mismatch(report, "3003")
    assert report["summary"]["matchedFlights"] == 2
    assert report["summary"]["paxCountMismatches"] == 0
    assert pax_mismatches(report) == []


def test_other_date_and_other_flight_are_not_compared(monkeypatch):
    install_sources(
        monkeypatch,
        [
            sabre_flight("2057", 42),
            sabre_flight("9999", 42, date="2026-03-18"),
        ],
        [
            otp_flight("2057", None),
            otp_flight("8888", None),
            otp_flight("2057", b'[{"Count": 1}]', date="2026-03-18"),
        ],
    )
    report = compare_flights_for_date(DATE, ["2057"])
    assert_mismatch(report, "2057", 42, None)
    assert {anomaly["flightNumber"] for anomaly in report["anomalies"]} == {"2057"}
