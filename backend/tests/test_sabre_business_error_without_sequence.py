"""Sabre business errors must fail ingest when no flight sequence is resolved.

These tests exercise the live feeder path: a caller-supplied flight with no
flightSequenceNumber, a PostgreSQL lookup that returns no row or cannot run,
and an HTTP 200 ACS_FlightDetailRS business error. They fail while that error
is stored as the latest flight status and the passenger, reservation, trip,
and schedule calls still run.
"""

import asyncio
import json
import os
from datetime import date, datetime

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "https://sabre.invalid")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "XXX")
os.environ.setdefault("SABRE_ORGANIZATION", "ORG")
os.environ.setdefault("SABRE_DOMAIN", "DOMAIN")

import mongomock
import pytest
from fastapi import HTTPException

from backend.api import database as api_database
from backend.api.routes.flights import (
    get_flight_dashboard,
    get_flight_status,
    list_flights,
)
from backend.api.routes.ingestion import SabreIngestRequest, ingest_flight
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.tasks.ingestion import run_batch_ingestion

AIRLINE = "GF"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-03-19"
DEPARTURE_DATETIME = "2026-03-19T08:00:00"
PRIOR_FETCHED_AT = "2020-01-01T00:00:00Z"
FOLLOW_UP_ACTIONS = (
    "GetPassengerListRQ",
    "Trip_SearchRQ",
    "Trip_ReportsRQ",
    "VerifyFlightDetailsLLSRQ",
    "GetPassengerDataRQ",
)
STORED_COLLECTIONS = (
    "sabre_requests",
    "snapshots",
    "changes",
    "flight_status",
    "passenger_list",
    "reservations",
    "trip_reports",
    "flight_schedules",
    "flights",
)

SESSION_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>
"""

EMPTY_SOAP = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <UnusedResponse>not-a-flight-detail</UnusedResponse>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _flight_detail_error(code, message):
    """ACS_FlightDetailRS business error shaped like the shipped STL ResultRecord."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <eb:MessageHeader xmlns:eb="http://www.ebxml.org/namespaces/messageHeader" eb:version="1.0" soap-env:mustUnderstand="1">
      <eb:Action>ACS_FlightDetailRS</eb:Action>
    </eb:MessageHeader>
  </soap-env:Header>
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      <Result messageId="business-error" timeStamp="2026-03-19T09:09:47.021Z">
        <ns2:Status>BusinessLogicError</ns2:Status>
        <ns2:SystemSpecificResults>
          <ns2:ErrorMessage code="{code}">{message}</ns2:ErrorMessage>
        </ns2:SystemSpecificResults>
      </Result>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class SabreHttp:
    """Stand-in for requests.post. Session create is local; flight detail is the fixture XML."""

    def __init__(self, detail_xml):
        self.detail_xml = detail_xml
        self.actions = []
        self.bodies = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        action = (headers or {}).get("SOAPAction", "")
        self.actions.append(action)
        self.bodies.append(data or "")
        if action == "SessionCreateRQ":
            return _Response(SESSION_RESPONSE)
        if action == "ACS_FlightDetailRQ":
            return _Response(self.detail_xml)
        if action == "SessionCloseRQ":
            return _Response(EMPTY_SOAP)
        return _Response(EMPTY_SOAP)


@pytest.fixture()
def mongo(monkeypatch):
    client = mongomock.MongoClient()
    db = client["falconeye"]
    storage._client = None
    storage._db = None
    storage._owns_connection = False
    storage.init_db(db)
    api_database._client = client
    api_database._db = db
    monkeypatch.setattr(
        "backend.sabre.client.requests.post",
        SabreHttp(_flight_detail_error("2566", "FLIGHT NOT INITIALIZED")),
    )
    yield db
    storage._client = None
    storage._db = None
    storage._owns_connection = False
    api_database._client = None
    api_database._db = None


def _install_http(monkeypatch, detail_xml):
    http = SabreHttp(detail_xml)
    monkeypatch.setattr("backend.sabre.client.requests.post", http)
    return http


def _flight(flight_number, sequence=None, include_sequence=True):
    payload = {
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": DEPARTURE_DATETIME,
    }
    if include_sequence:
        payload["flightSequenceNumber"] = sequence
    return payload


def _seed_prior_status(db, flight_number):
    db["flight_status"].insert_one({
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "fetchedAt": PRIOR_FETCHED_AT,
        "status": "OPENCI",
        "gate": "B41",
        "schedule": {"scheduledDeparture": DEPARTURE_DATETIME},
        "aircraft": {"type": "789"},
    })
    db["flights"].insert_one({
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "summary": {"status": "OPENCI", "gate": "B41"},
        "snapshotCount": 3,
        "latestFlightStatusSnapshotId": "prior-snapshot",
    })


def _patch_pg(monkeypatch, rows=None, error=None):
    def query_all(sql, params=None):
        assert "otp.flight_xml_current" in sql
        assert "flight_sequence_number" in sql
        assert params[0]
        assert params[1] == ORIGIN
        assert params[2] == DEPARTURE_DATE
        if error is not None:
            raise error
        return rows if rows is not None else []

    monkeypatch.setattr("backend.api.postgres.query_all", query_all)


def _counts(db):
    return {name: db[name].count_documents({}) for name in STORED_COLLECTIONS}


def _assert_not_persisted(db, before, http, flight_number):
    assert http.actions.count("ACS_FlightDetailRQ") == 1
    detail_bodies = [
        body for action, body in zip(http.actions, http.bodies)
        if action == "ACS_FlightDetailRQ"
    ]
    assert len(detail_bodies) == 1
    assert f"<Airline>{AIRLINE}</Airline>" in detail_bodies[0]
    assert f"<Flight>{flight_number}</Flight>" in detail_bodies[0]
    assert f"<Origin>{ORIGIN}</Origin>" in detail_bodies[0]
    assert f"<DepartureDate>{DEPARTURE_DATE}</DepartureDate>" in detail_bodies[0]
    assert not any(action in FOLLOW_UP_ACTIONS for action in http.actions)
    assert _counts(db) == before

    listed = list_flights(date=DEPARTURE_DATE)
    match = [row for row in listed if row["flightNumber"] == flight_number]
    assert len(match) == 1
    assert match[0]["status"] == "OPENCI"
    assert match[0]["gate"] == "B41"

    latest = get_flight_status(
        flight_number, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None,
    )
    assert latest["status"] == "OPENCI"
    assert latest["fetchedAt"] == PRIOR_FETCHED_AT

    dashboard = get_flight_dashboard(
        flight_number, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None,
    )
    assert dashboard["flightStatus"]["status"] == "OPENCI"
    assert dashboard["flightStatus"]["fetchedAt"] == PRIOR_FETCHED_AT

    state = db["flights"].find_one({"flightNumber": flight_number})
    assert state["summary"] == {"status": "OPENCI", "gate": "B41"}
    assert state["snapshotCount"] == 3
    assert state["latestFlightStatusSnapshotId"] == "prior-snapshot"


def _assert_feeder_rejected(result, flight_number, marker):
    assert result["processedFlights"] == 1
    flight_result = result["results"][0]
    assert flight_result["success"] is False
    flight_status = flight_result["apis"]["flightStatus"]
    assert flight_status["status"] == "error"
    assert marker in flight_status["error"].upper()
    assert "snapshotId" not in flight_status
    for api_name in ("passengerList", "reservations", "tripReports", "schedule"):
        api_result = flight_result["apis"][api_name]
        assert api_result["status"] == "error"
        assert "snapshotId" not in api_result
    return flight_result


def test_missing_sequence_and_no_pg_row_stores_initialized_error_as_success(mongo, monkeypatch):
    flight_number = "9101"
    _seed_prior_status(mongo, flight_number)
    before = _counts(mongo)
    http = _install_http(monkeypatch, _flight_detail_error("2566", "FLIGHT NOT INITIALIZED"))
    _patch_pg(monkeypatch, rows=[])

    result = run_feeder([_flight(flight_number, include_sequence=False)])

    _assert_feeder_rejected(result, flight_number, "FLIGHT NOT INITIALIZED")
    _assert_not_persisted(mongo, before, http, flight_number)


def test_null_sequence_and_pg_unavailable_rejects_invalid_date_or_city(mongo, monkeypatch):
    flight_number = "9102"
    _seed_prior_status(mongo, flight_number)
    before = _counts(mongo)
    http = _install_http(
        monkeypatch,
        _flight_detail_error("5001", "INVALID DATE OR CITY"),
    )
    _patch_pg(
        monkeypatch,
        error=RuntimeError("POSTGRES_URI environment variable is not set"),
    )

    result = run_feeder([_flight(flight_number, sequence=None)])

    _assert_feeder_rejected(result, flight_number, "INVALID DATE OR CITY")
    _assert_not_persisted(mongo, before, http, flight_number)


def test_sync_ingest_without_sequence_rejects_before_snapshot(mongo, monkeypatch):
    flight_number = "9103"
    _seed_prior_status(mongo, flight_number)
    before = _counts(mongo)
    http = _install_http(monkeypatch, _flight_detail_error("2566", "FLIGHT NOT INITIALIZED"))
    _patch_pg(monkeypatch, rows=[])
    payload = SabreIngestRequest(
        airline=AIRLINE,
        flightNumber=flight_number,
        origin=ORIGIN,
        departureDate=date(2026, 3, 19),
        departureDateTime=datetime(2026, 3, 19, 8, 0, 0),
    )
    assert payload.flightSequenceNumber is None

    with pytest.raises(HTTPException) as raised:
        asyncio.run(ingest_flight(payload))

    assert raised.value.status_code == 409
    assert "FLIGHT NOT INITIALIZED" in str(raised.value.detail).upper()
    _assert_not_persisted(mongo, before, http, flight_number)


def test_batch_task_without_sequence_rejects_before_snapshot(mongo, monkeypatch):
    flight_number = "9104"
    _seed_prior_status(mongo, flight_number)
    before = _counts(mongo)
    http = _install_http(
        monkeypatch,
        _flight_detail_error("5001", "Invalid date or city"),
    )
    _patch_pg(monkeypatch, rows=[])
    for name in (
        "set_job_running",
        "set_job_progress",
        "set_ingestion_job_finished",
        "set_job_failed",
        "cache_invalidate_pattern",
    ):
        monkeypatch.setattr(f"backend.tasks.ingestion.{name}", lambda *args, **kwargs: None)

    payload = SabreIngestRequest(
        airline=AIRLINE,
        flightNumber=flight_number,
        origin=ORIGIN,
        departureDate=date(2026, 3, 19),
        departureDateTime=datetime(2026, 3, 19, 8, 0, 0),
    ).model_dump(mode="json")
    assert payload["flightSequenceNumber"] is None

    run_batch_ingestion.run("job-placeholder", [payload])

    _assert_not_persisted(mongo, before, http, flight_number)


def test_cli_input_without_sequence_rejects_before_snapshot(mongo, monkeypatch, tmp_path):
    flight_number = "9105"
    _seed_prior_status(mongo, flight_number)
    before = _counts(mongo)
    http = _install_http(monkeypatch, _flight_detail_error("2566", "FLIGHT NOT INITIALIZED"))
    _patch_pg(monkeypatch, rows=[])
    input_path = tmp_path / "flights.json"
    input_path.write_text(json.dumps({"flights": [_flight(flight_number, include_sequence=False)]}))
    monkeypatch.setattr("sys.argv", ["backend.feeder.runner", str(input_path)])

    from backend.feeder.runner import main
    main()

    _assert_not_persisted(mongo, before, http, flight_number)


def test_supplied_sequence_rejects_business_error_before_snapshot(mongo, monkeypatch):
    flight_number = "9106"
    _seed_prior_status(mongo, flight_number)
    before = _counts(mongo)
    http = _install_http(monkeypatch, _flight_detail_error("2566", "FLIGHT NOT INITIALIZED"))
    _patch_pg(monkeypatch, rows=[])

    result = run_feeder([_flight(flight_number, sequence=4242)])

    _assert_feeder_rejected(result, flight_number, "FLIGHT NOT INITIALIZED")
    _assert_not_persisted(mongo, before, http, flight_number)
    assert result["results"][0]["flight"]["flightSequenceNumber"] == 4242


def test_pg_resolved_sequence_rejects_business_error_before_snapshot(mongo, monkeypatch):
    flight_number = "9107"
    _seed_prior_status(mongo, flight_number)
    before = _counts(mongo)
    http = _install_http(
        monkeypatch,
        _flight_detail_error("5001", "INVALID DATE OR CITY"),
    )
    _patch_pg(monkeypatch, rows=[{"flight_sequence_number": 7777}])

    result = run_feeder([_flight(flight_number, include_sequence=False)])

    _assert_feeder_rejected(result, flight_number, "INVALID DATE OR CITY")
    _assert_not_persisted(mongo, before, http, flight_number)
    assert result["results"][0]["flight"]["flightSequenceNumber"] == 7777
