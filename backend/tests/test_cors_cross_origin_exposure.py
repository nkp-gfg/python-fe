"""Cross-origin reads and state changes must not be granted to an arbitrary site.

These tests exercise the live FastAPI app, including CORSMiddleware and the
passenger, reservation, ingestion, schedule, restore, and comparison routes.
They fail while any website can read those responses or clear a preflight for
the JSON calls the browser client sends. A fix should make them pass.

Run from the repository root:

    PYTHONPATH=. python3 -m pytest backend/tests/test_cors_cross_origin_exposure.py -q
"""

import os
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient

from backend.api.main import app
import backend.api.main as main_module
import backend.api.routes.changes as changes_routes
import backend.api.routes.comparison as comparison_routes
import backend.api.routes.ingestion as ingestion_routes
import backend.api.routes.passengers as passengers_routes
import backend.api.routes.reservations as reservations_routes
import backend.api.routes.schedule as schedule_routes
import backend.tasks.comparison as comparison_tasks
import backend.tasks.ingestion as ingestion_tasks

# An origin the FalconEye web app does not own. fetch() from a page on this
# origin uses credentials mode "same-origin", so a cross-origin request does
# not include cookies.
EVIL_ORIGIN = "https://evil.example"
FLIGHT = "2057"
AIRPORT = "BAH"
DATE = "2026-04-01"
PNR = "ZZZ999"
PASSENGER_LAST = "DOE"
DOCUMENT_NUMBER = "X00000000"
RESTORED_LAST = "OLDER"


def _sort_key(value):
    return (value is None, "" if value is None else value)


def _dotted_match(node, parts, expected):
    if isinstance(node, list):
        return any(_dotted_match(item, parts, expected) for item in node)
    if not isinstance(node, dict):
        return False
    head, *rest = parts
    if head not in node:
        return False
    child = node[head]
    if not rest:
        if isinstance(child, list):
            return any(item == expected for item in child)
        return child == expected
    return _dotted_match(child, rest, expected)


def _matches(doc, query):
    for key, expected in query.items():
        if isinstance(expected, dict) and set(expected) <= {"$lte"}:
            value = doc.get(key)
            if value is None or value > expected["$lte"]:
                return False
            continue
        if "." in key:
            if not _dotted_match(doc, key.split("."), expected):
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction):
        self.docs.sort(key=lambda doc: _sort_key(doc.get(key)), reverse=direction < 0)
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find_one(self, query=None, sort=None, **kwargs):
        matches = [doc for doc in self.docs if _matches(doc, query or {})]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: _sort_key(doc.get(field)),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return deepcopy(matches[0])

    def find(self, query=None, **kwargs):
        matches = [deepcopy(doc) for doc in self.docs if _matches(doc, query or {})]
        return FakeCursor(matches)

    def insert_one(self, doc):
        stored = deepcopy(doc)
        stored.setdefault("_id", f"inserted-{len(self.docs) + 1}")
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def create_index(self, keys, **kwargs):
        return kwargs.get("name")


class FakeDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())

    def command(self, *args, **kwargs):
        return {"ok": 1}


def _seed(db: FakeDB):
    db["passenger_list"].docs.append({
        "_id": "passenger-list-1",
        "_raw": {"mustNotLeak": True},
        "flightNumber": FLIGHT,
        "origin": AIRPORT,
        "departureDate": DATE,
        "fetchedAt": "2026-04-01T01:00:00Z",
        "passengers": [{
            "pnr": PNR,
            "lastName": PASSENGER_LAST,
            "firstName": "JANE",
            "priorityCode": "B01",
            "isCheckedIn": False,
            "groupCode": "GRP1",
            "lineNumber": 1,
            "cabin": "Y",
        }],
    })
    db["reservations"].docs.append({
        "_id": "reservations-1",
        "airline": "GF",
        "flightNumber": FLIGHT,
        "departureAirport": AIRPORT,
        "departureDate": DATE,
        "fetchedAt": "2026-04-01T01:00:00Z",
        "totalResults": 1,
        "reservations": [{
            "pnr": PNR,
            "passengers": [{
                "lastName": PASSENGER_LAST,
                "firstName": "JANE",
                "nationality": "BH",
            }],
        }],
    })
    db["changes"].docs.append({
        "_id": "change-1",
        "flightNumber": FLIGHT,
        "departureDate": DATE,
        "detectedAt": "2026-04-01T02:00:00Z",
        "changeType": "SEAT_CHANGE",
        "passenger": {"pnr": PNR, "lastName": PASSENGER_LAST},
    })
    restored_passenger = {
        "flightNumber": FLIGHT,
        "origin": AIRPORT,
        "departureDate": DATE,
        "passengers": [{"pnr": PNR, "lastName": RESTORED_LAST}],
        "_raw": "snapshot-raw",
    }
    for snapshot_type, data in (
        ("passenger_list", restored_passenger),
        ("flight_status", {"flightNumber": FLIGHT, "status": "OLD"}),
        ("reservations", {
            "airline": "GF",
            "flightNumber": FLIGHT,
            "departureDate": DATE,
            "reservations": [{"pnr": PNR}],
        }),
    ):
        db["snapshots"].docs.append({
            "flightNumber": FLIGHT,
            "origin": AIRPORT,
            "departureDate": DATE,
            "snapshotType": snapshot_type,
            "sequenceNumber": 1,
            "data": data,
        })


@pytest.fixture
def api(monkeypatch):
    db = FakeDB()
    _seed(db)

    def db_factory():
        return db

    def redis_down():
        raise RuntimeError("redis is not used by these tests")

    monkeypatch.setattr(main_module, "get_db", db_factory)
    monkeypatch.setattr(main_module, "get_redis", redis_down)
    for module in (passengers_routes, reservations_routes, changes_routes):
        monkeypatch.setattr(module, "get_db", db_factory)

    with TestClient(app) as client:
        yield client, db


def cross_origin_readable(response, origin):
    """Whether fetch() from `origin` can read this response body.

    Matches the Fetch standard CORS check for a request whose credentials
    mode is not "include": Access-Control-Allow-Origin of * or the request
    origin succeeds. Any other value, including a missing header, fails.
    """
    allow = response.headers.get("access-control-allow-origin")
    return allow == "*" or allow == origin


def preflight_authorizes(response, origin, unsafe_headers):
    """Whether a browser would send the real request after this preflight.

    POST is a CORS-safelisted method, so a non-credentialed preflight succeeds
    when the CORS check passes, the status is ok, and every unsafe request
    header (Content-Type: application/json) is listed.
    """
    if not cross_origin_readable(response, origin):
        return False
    if not (200 <= response.status_code <= 299):
        return False
    allowed = {
        header.strip().lower()
        for header in response.headers.get("access-control-allow-headers", "").split(",")
        if header.strip()
    }
    if "*" in allowed:
        return True
    return all(header.lower() in allowed for header in unsafe_headers)


def assert_not_shared(response, origin, marker):
    if response.status_code < 400:
        assert marker in response.text
        assert not cross_origin_readable(response, origin), (
            f"{origin} can read the response "
            f"(access-control-allow-origin="
            f"{response.headers.get('access-control-allow-origin')!r}, "
            f"body={response.text[:240]})"
        )
        return
    assert response.status_code in (401, 403), response.status_code
    assert marker not in response.text


def _evil_headers(extra=None):
    headers = {"Origin": EVIL_ORIGIN}
    if extra:
        headers.update(extra)
    return headers


def _preflight(client, path):
    return client.options(
        path,
        headers={
            "Origin": EVIL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


READS = [
    (
        f"/flights/{FLIGHT}/passengers?origin={AIRPORT}&date={DATE}",
        PASSENGER_LAST,
    ),
    (
        f"/flights/{FLIGHT}/passengers/summary?origin={AIRPORT}&date={DATE}",
        "totalPassengers",
    ),
    (
        f"/flights/{FLIGHT}/passengers/standby-list?origin={AIRPORT}&date={DATE}",
        PASSENGER_LAST,
    ),
    (
        f"/flights/{FLIGHT}/passengers/groups?origin={AIRPORT}&date={DATE}",
        PASSENGER_LAST,
    ),
    (
        f"/flights/{FLIGHT}/passengers/{PNR}?date={DATE}",
        PASSENGER_LAST,
    ),
    (
        f"/flights/{FLIGHT}/reservations?origin={AIRPORT}&date={DATE}",
        PASSENGER_LAST,
    ),
    (
        f"/flights/{FLIGHT}/reservations/{PNR}?date={DATE}",
        PNR,
    ),
    (
        f"/flights/{FLIGHT}/changes/passenger/{PNR}?date={DATE}",
        PASSENGER_LAST,
    ),
]


@pytest.mark.parametrize("path,marker", READS)
def test_untrusted_origin_cannot_read_stored_records(api, path, marker):
    client, _db = api
    response = client.get(path, headers=_evil_headers())
    assert_not_shared(response, EVIL_ORIGIN, marker)


def test_untrusted_origin_cannot_read_live_passenger_detail(api, monkeypatch):
    client, _db = api
    calls = []

    class FakeSabre:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_passenger_data(self, **kwargs):
            calls.append(kwargs)
            return (
                {
                    "PassengerDataResponseList": {
                        "PassengerDataResponse": {
                            "LastName": PASSENGER_LAST,
                            "FirstName": "JANE",
                            "PNRLocator": PNR,
                            "FreeTextInfoList": {
                                "FreeTextInfo": {
                                    "EditCode": "DOCS",
                                    "Text": (
                                        f"P/BH/{DOCUMENT_NUMBER}/BH/"
                                        "01JAN90/F/01JAN30/DOE/JANE"
                                    ),
                                }
                            },
                        }
                    }
                },
                "<GetPassengerDataRS/>",
                {},
            )

    monkeypatch.setattr(passengers_routes, "SabreClient", FakeSabre)
    response = client.get(
        f"/flights/{FLIGHT}/passengers/{PNR}/detail"
        f"?origin={AIRPORT}&date={DATE}&airline=GF",
        headers=_evil_headers(),
    )
    if response.status_code < 400:
        assert calls and calls[0]["pnr"] == PNR
        assert calls[0]["last_name"] == PASSENGER_LAST
    assert_not_shared(response, EVIL_ORIGIN, DOCUMENT_NUMBER)


def test_untrusted_origin_cannot_preflight_or_read_flight_ingest(api, monkeypatch):
    client, _db = api
    calls = []

    def fake_run_feeder(flights, progress_callback=None):
        calls.append(flights)
        flight = flights[0]
        return {
            "processedFlights": 1,
            "results": [{
                "flight": {
                    "airline": flight["airline"],
                    "flightNumber": flight["flightNumber"],
                    "origin": flight["origin"],
                    "departureDate": flight["departureDate"],
                    "departureDateTime": flight["departureDateTime"],
                },
                "success": True,
                "apis": {"flightStatus": {"status": "success"}},
            }],
        }

    monkeypatch.setattr(ingestion_routes, "run_feeder", fake_run_feeder)
    path = "/flights/ingest"
    response = client.post(
        path,
        headers=_evil_headers({"Content-Type": "application/json"}),
        json={
            "airline": "GF",
            "flightNumber": FLIGHT,
            "origin": AIRPORT,
            "departureDate": DATE,
            "departureDateTime": f"{DATE}T08:00:00",
        },
    )
    preflight = _preflight(client, path)
    if response.status_code < 400:
        assert calls and calls[0][0]["flightNumber"] == FLIGHT
    else:
        assert calls == []
    assert not preflight_authorizes(preflight, EVIL_ORIGIN, ["content-type"]), (
        f"preflight lets an untrusted page POST JSON; feeder_called={bool(calls)} "
        f"preflight_acao={preflight.headers.get('access-control-allow-origin')!r} "
        f"allow_headers={preflight.headers.get('access-control-allow-headers')!r}"
    )
    assert_not_shared(response, EVIL_ORIGIN, FLIGHT)


def test_untrusted_origin_cannot_preflight_or_read_batch_ingest(api, monkeypatch):
    client, _db = api
    queued = []

    monkeypatch.setattr(ingestion_routes, "create_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ingestion_tasks.run_batch_ingestion,
        "delay",
        lambda *args, **kwargs: queued.append(args),
    )
    path = "/flights/ingest/batch"
    response = client.post(
        path,
        headers=_evil_headers({"Content-Type": "application/json"}),
        json={
            "flights": [{
                "airline": "GF",
                "flightNumber": FLIGHT,
                "origin": AIRPORT,
                "departureDate": DATE,
                "departureDateTime": f"{DATE}T08:00:00",
            }]
        },
    )
    preflight = _preflight(client, path)
    if response.status_code < 400:
        assert queued and queued[0][1][0]["flightNumber"] == FLIGHT
    else:
        assert queued == []
    assert not preflight_authorizes(preflight, EVIL_ORIGIN, ["content-type"]), (
        f"preflight authorized batch ingest; queued={bool(queued)}"
    )
    assert_not_shared(response, EVIL_ORIGIN, "accepted")


def test_untrusted_origin_cannot_preflight_or_read_schedule_lookup(api, monkeypatch):
    client, _db = api
    calls = []
    stored = []

    class FakeSabre:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def verify_flight_details(self, **kwargs):
            calls.append(kwargs)
            raw = {
                "ApplicationResults": {"@status": "Complete"},
                "OriginDestinationOptions": {
                    "OriginDestinationOption": {
                        "@OriginTimeZone": "AST",
                        "FlightSegment": {
                            "@DepartureDateTime": f"{DATE}T08:00",
                            "@ArrivalDateTime": f"{DATE}T12:00",
                            "@AirMilesFlown": "3200",
                            "@ElapsedTime": "0400",
                            "OriginLocation": {"@LocationCode": AIRPORT, "@Terminal": "1"},
                            "DestinationLocation": {"@LocationCode": "LHR", "@Terminal": "4"},
                            "Equipment": {"@AirEquipType": "32N"},
                            "MarketingAirline": {"@Code": "GF", "@FlightNumber": FLIGHT},
                            "Meal": {"@Code": "M"},
                        },
                    }
                },
            }
            meta = {
                "requestXml": "<VerifyFlightDetailsRQ/>",
                "httpStatus": 200,
                "durationMs": 5,
                "sessionToken": "placeholder",
                "conversationId": "placeholder",
            }
            return raw, "<VerifyFlightDetailsRS/>", meta

    monkeypatch.setattr(schedule_routes, "SabreClient", FakeSabre)
    monkeypatch.setattr(
        schedule_routes.storage,
        "store_raw_request",
        lambda **kwargs: stored.append(("raw", kwargs)) or "request-id",
    )
    monkeypatch.setattr(
        schedule_routes.storage,
        "store_flight_schedule",
        lambda doc: stored.append(("schedule", doc)) or "schedule-id",
    )

    path = "/flights/schedule/lookup"
    response = client.post(
        path,
        headers=_evil_headers({"Content-Type": "application/json"}),
        json={"airline": "GF", "flightNumber": FLIGHT, "departureDate": DATE},
    )
    preflight = _preflight(client, path)
    if response.status_code < 400:
        assert calls and calls[0]["flight_number"] == FLIGHT
        assert any(kind == "schedule" for kind, _doc in stored)
    else:
        assert calls == []
        assert stored == []
    assert not preflight_authorizes(preflight, EVIL_ORIGIN, ["content-type"]), (
        f"preflight authorized schedule lookup; sabre_called={bool(calls)}"
    )
    assert_not_shared(response, EVIL_ORIGIN, AIRPORT)


def test_untrusted_origin_cannot_preflight_or_restore_snapshot(api):
    client, db = api
    path = (
        f"/flights/{FLIGHT}/snapshots/1/restore"
        f"?origin={AIRPORT}&date={DATE}"
    )
    before = len(db["passenger_list"].docs)
    response = client.post(
        path,
        headers=_evil_headers({"Content-Type": "application/json"}),
    )
    inserted = db["passenger_list"].docs[before:]
    preflight = _preflight(client, path)
    if response.status_code < 400:
        assert any(
            passenger.get("lastName") == RESTORED_LAST
            for doc in inserted
            for passenger in doc.get("passengers", [])
        )
        assert all("_raw" not in doc for doc in inserted)
    else:
        assert inserted == []
    assert not preflight_authorizes(preflight, EVIL_ORIGIN, ["content-type"]), (
        f"preflight authorized snapshot restore; inserted={len(inserted)}"
    )
    assert_not_shared(response, EVIL_ORIGIN, RESTORED_LAST)


@pytest.mark.parametrize(
    "path,task_name,body_marker",
    [
        ("/compare/flights/async?date=2026-04-01", "run_flight_comparison", "accepted"),
        (
            "/compare/pnr-mismatches/async?date=2026-04-01",
            "run_pnr_comparison",
            "accepted",
        ),
    ],
)
def test_untrusted_origin_cannot_preflight_or_queue_comparison(
    api, monkeypatch, path, task_name, body_marker
):
    client, _db = api
    queued = []
    monkeypatch.setattr(comparison_routes, "create_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        getattr(comparison_tasks, task_name),
        "delay",
        lambda *args, **kwargs: queued.append(args),
    )
    response = client.post(path, headers=_evil_headers({"Content-Type": "application/json"}))
    preflight = _preflight(client, path)
    if response.status_code < 400:
        assert queued and queued[0][1] == DATE
    else:
        assert queued == []
    assert not preflight_authorizes(preflight, EVIL_ORIGIN, ["content-type"]), (
        f"preflight authorized {path}; queued={bool(queued)}"
    )
    assert_not_shared(response, EVIL_ORIGIN, body_marker)
