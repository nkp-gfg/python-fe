"""Spec for the comparison-page fan-out.

The compare page turns one `flights` query string into one dashboard request
and one passenger-tree request per entry. Those handlers assemble a response
from the stored passenger list and do not check a credential.

These tests fail while that behavior is present. They pass when:

* `GET /flights/{flight_number}/dashboard` and `GET /flights/{flight_number}/tree`
  reject a request that carries no credential with 401 or 403, and
* that rejection happens before either handler reads `passenger_list`.

A read of the manifest that happens and is then discarded still fails.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest
from fastapi.testclient import TestClient

PASSENGER_LAST_NAME = "PAXMARKER"
PASSENGER_PNR = "ZZZ999"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-01-15"
FLIGHT_NUMBER = "2057"


class _Collection:
    def __init__(self, name, documents, reads):
        self.name = name
        self._documents = documents
        self._reads = reads

    def find_one(self, query, projection=None, sort=None):
        self._reads.append(("find_one", self.name, dict(query)))
        document = self._documents.get(self.name)
        return copy.deepcopy(document) if document is not None else None

    def find(self, query, projection=None):
        self._reads.append(("find", self.name, dict(query)))
        return []

    def aggregate(self, pipeline, allowDiskUse=False):
        self._reads.append(("aggregate", self.name, {}))
        return []


class _Database:
    def __init__(self, documents, reads):
        self._documents = documents
        self._reads = reads

    def __getitem__(self, name):
        return _Collection(name, self._documents, self._reads)

    def command(self, *args, **kwargs):
        return {"ok": 1}


def _manifest():
    passenger = {
        "pnr": PASSENGER_PNR,
        "lastName": PASSENGER_LAST_NAME,
        "firstName": "NOOR",
        "passengerId": "PAX1",
        "lineNumber": 1,
        "cabin": "Y",
        "bookingClass": "Y",
        "isRevenue": True,
        "passengerType": "F",
        "isCheckedIn": True,
        "isBoarded": True,
        "isChild": False,
        "hasInfant": True,
        "editCodes": [],
        "groupCode": "G1",
        "seat": "12A",
        "isUnnamedGroup": False,
    }
    return {
        "flightNumber": FLIGHT_NUMBER,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "destination": "LHR",
        "totalPassengers": 1,
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 1,
        "totalSouls": 2,
        "cabinSummary": [],
        "fetchedAt": "2026-01-15T00:00:00Z",
        "passengers": [passenger],
    }


def _flight_status():
    return {
        "flightNumber": FLIGHT_NUMBER,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "status": "OPEN",
        "destination": "LHR",
        "aircraft": {"type": "32N", "registration": "A9C-XX"},
        "jumpSeat": {"cockpit": 0, "cabin": 0},
        "gate": "A1",
    }


class _Redis:
    def ping(self):
        return True


@pytest.fixture
def api(monkeypatch):
    """Serve the real FastAPI app against an in-memory manifest.

    MongoClient and Redis are replaced so the test cannot reach a real store.
    """
    reads = []
    documents = {
        "passenger_list": _manifest(),
        "flight_status": _flight_status(),
    }
    database = _Database(documents, reads)

    def refuse_mongo(*args, **kwargs):
        raise AssertionError("test must not open a MongoClient")

    def refuse_redis(*args, **kwargs):
        raise AssertionError("test must not open a Redis client")

    monkeypatch.setattr("backend.api.database.MongoClient", refuse_mongo)
    monkeypatch.setattr("backend.api.redis_client.redis.Redis", refuse_redis)
    monkeypatch.setattr("backend.api.database.get_db", lambda: database)
    monkeypatch.setattr("backend.api.main.get_db", lambda: database)
    monkeypatch.setattr("backend.api.routes.flights.get_db", lambda: database)
    monkeypatch.setattr("backend.api.main.get_redis", lambda: _Redis())
    monkeypatch.setattr("backend.api.main.feeder_storage.init_db", lambda db: None)
    monkeypatch.setattr("backend.api.main.feeder_storage.ensure_indexes", lambda: None)

    from backend.api.main import app
    from backend.api.routes import flights as flights_routes

    flights_routes._dashboard_cache.clear()
    with TestClient(app) as client:
        yield client, reads, flights_routes


def _passenger_list_reads(reads):
    return [entry for entry in reads if entry[1] == "passenger_list"]


@pytest.mark.parametrize(
    "path",
    [
        f"/flights/{FLIGHT_NUMBER}/dashboard?origin={ORIGIN}&date={DEPARTURE_DATE}",
        f"/flights/{FLIGHT_NUMBER}/tree?origin={ORIGIN}&date={DEPARTURE_DATE}",
    ],
)
def test_dashboard_and_tree_reject_anonymous_requests_before_reading_manifest(api, path):
    client, reads, flights_routes = api
    flights_routes._dashboard_cache.clear()
    reads.clear()

    response = client.get(path)

    problems = []
    if response.status_code not in (401, 403):
        problems.append(f"status {response.status_code}")
    manifest_reads = _passenger_list_reads(reads)
    if manifest_reads:
        problems.append(f"passenger_list reads={len(manifest_reads)} before refusal")
    body = response.text
    if PASSENGER_PNR in body or PASSENGER_LAST_NAME in body:
        problems.append("response included a stored passenger identifier")
    assert not problems, (
        f"{path} served the comparison payload without a credential: "
        + "; ".join(problems)
    )


def test_each_anonymous_dashboard_request_is_refused_on_its_own(api):
    """A burst of distinct flight numbers must not each assemble a manifest."""
    client, reads, flights_routes = api
    flights_routes._dashboard_cache.clear()
    reads.clear()

    statuses = []
    for offset in range(12):
        flight_number = str(1000 + offset)
        response = client.get(
            f"/flights/{flight_number}/dashboard",
            params={"origin": ORIGIN, "date": DEPARTURE_DATE},
        )
        statuses.append(response.status_code)

    manifest_reads = _passenger_list_reads(reads)
    assert statuses == [401] * 12 or statuses == [403] * 12, (
        f"statuses={statuses} passenger_list reads={len(manifest_reads)}"
    )
    assert manifest_reads == [], manifest_reads
