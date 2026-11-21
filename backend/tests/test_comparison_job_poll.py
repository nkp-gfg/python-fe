"""Comparison jobs must be readable at the poll address the API returns.

POST /compare/flights/async and POST /compare/pnr-mismatches/async return a
pollUrl. The worker then stores the comparison report on the job. A caller
who follows that pollUrl must receive the report, including mismatched
flights or PNRs.

These tests fail while the poll route validates that stored report as a list
of ingestion flight records. They pass once the returned poll address delivers
the report.

The ingestion poll route is still required to return a completed ingestion
result list. That case is covered separately and already succeeds.

Not covered: GET /events/jobs/{job_id}. That stream does not use the ingestion
job schema. A client that connects after completion receives the raw job
document, and the live "completed" event only carries flightsProcessed.
"""

import json
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.redis_client import set_ingestion_job_finished
from backend.tasks.comparison import run_flight_comparison, run_pnr_comparison


class _MemoryRedis:
    """String store matching redis-py set/get/publish with decode_responses.

    redis_client json-encodes the job before set and decodes it after get.
    SET/GET themselves do not reshape the payload (redis 5.3.1 commands/core.py
    set and get pass the value through execute_command).
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, value: str, ex: int | None = None, **kwargs: Any) -> bool:
        self.values[name] = value
        return True

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def publish(self, channel: str, message: str, **kwargs: Any) -> int:
        return 0

    def ping(self) -> bool:
        return True


class _PnrCollection:
    """Returns the documents find_missing_pnrs reads after its aggregation.

    The pipeline's final projection emits flightNumber, origin, inPaxNotRes,
    inResNotPax, totalPaxPnrs, and totalResPnrs. MongoDB is not running here;
    the Python loop that turns those documents into the stored report still runs.
    """

    def aggregate(self, pipeline: list) -> list[dict]:
        assert pipeline, "comparison pipeline must be supplied"
        return [
            {
                "flightNumber": "044",
                "origin": "BAH",
                "inPaxNotRes": ["PNRONLY1"],
                "inResNotPax": ["RESONLY9"],
                "totalPaxPnrs": 2,
                "totalResPnrs": 2,
            }
        ]


class _PnrDatabase:
    def __getitem__(self, name: str) -> _PnrCollection:
        assert name == "passenger_list"
        return _PnrCollection()


def _sabre_flights(date: str, flight_numbers: list[str] | None = None) -> pl.DataFrame:
    assert date
    return pl.DataFrame(
        [
            {
                "flightNumber": "2057",
                "origin": "BAH",
                "sabre_status": "OPEN",
                "sabre_gate": "A1",
                "sabre_totalPax": 10,
                "sabre_checkedIn": 4,
                "sabre_boarded": 0,
                "sabre_lastUpdated": "2026-03-19T00:00:00Z",
            }
        ]
    )


def _otp_flights(date: str, flight_numbers: list[str] | None = None) -> pl.DataFrame:
    assert date
    return pl.DataFrame(
        schema={
            "flightNumber": pl.Utf8,
            "origin": pl.Utf8,
            "otp_status": pl.Utf8,
            "otp_totalPax": pl.Int64,
            "otp_depUtc": pl.Utf8,
            "otp_aircraft": pl.Utf8,
        }
    )


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    store = _MemoryRedis()
    monkeypatch.setattr("backend.api.redis_client.get_redis", lambda: store)
    # raise_server_exceptions is off so a response-validation failure is the
    # HTTP response the caller receives, not an exception inside the test.
    return TestClient(app, raise_server_exceptions=False)


def _accept_comparison(monkeypatch: pytest.MonkeyPatch, task: Any) -> None:
    monkeypatch.setattr(task, "delay", lambda *args, **kwargs: None)


def test_flight_comparison_poll_returns_mismatched_flights(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("backend.feeder.storage.get_db", lambda: object())
    monkeypatch.setattr("backend.feeder.comparison.load_sabre_flights", _sabre_flights)
    monkeypatch.setattr("backend.feeder.comparison.load_otp_flights", _otp_flights)
    _accept_comparison(monkeypatch, run_flight_comparison)

    with _client(monkeypatch) as client:
        accepted = client.post(
            "/compare/flights/async",
            params={"date": "2026-03-19", "flight_numbers": "2057"},
        )
        assert accepted.status_code == 202, accepted.text
        job = accepted.json()
        poll_url = job["pollUrl"]
        job_id = job["jobId"]

        run_flight_comparison.run(job_id, "2026-03-19", ["2057"])

        stored = client.get(f"/compare/jobs/{job_id}")
        assert stored.status_code == 200, stored.text
        stored_body = json.dumps(stored.json())
        assert "2057" in stored_body
        assert "SABRE_ONLY" in stored_body

        polled = client.get(poll_url)
        assert polled.status_code == 200, polled.text
        polled_body = json.dumps(polled.json())
        assert "2057" in polled_body
        assert "SABRE_ONLY" in polled_body


def test_pnr_comparison_poll_returns_mismatched_pnrs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("backend.feeder.storage.get_db", lambda: object())
    monkeypatch.setattr("backend.feeder.aggregations.get_db", lambda: _PnrDatabase())
    _accept_comparison(monkeypatch, run_pnr_comparison)

    with _client(monkeypatch) as client:
        accepted = client.post(
            "/compare/pnr-mismatches/async",
            params={"date": "2026-03-19"},
        )
        assert accepted.status_code == 202, accepted.text
        job = accepted.json()
        poll_url = job["pollUrl"]
        job_id = job["jobId"]

        run_pnr_comparison.run(job_id, "2026-03-19")

        stored = client.get(f"/compare/jobs/{job_id}")
        assert stored.status_code == 200, stored.text
        stored_body = json.dumps(stored.json())
        assert "PNRONLY1" in stored_body
        assert "044" in stored_body

        polled = client.get(poll_url)
        assert polled.status_code == 200, polled.text
        polled_body = json.dumps(polled.json())
        assert "PNRONLY1" in polled_body
        assert "RESONLY9" in polled_body
        assert "044" in polled_body


def test_ingestion_job_poll_returns_flight_results(monkeypatch: pytest.MonkeyPatch):
    with _client(monkeypatch) as client:
        from backend.api.redis_client import create_job

        job_id = "ingest-job-1"
        create_job(job_id, 1, job_type="ingestion")
        set_ingestion_job_finished(
            job_id,
            1,
            [
                {
                    "flight": {
                        "airline": "GF",
                        "flightNumber": "100",
                        "origin": "BAH",
                        "departureDate": "2026-03-19",
                        "departureDateTime": "2026-03-19T10:00:00",
                    },
                    "success": True,
                    "apis": {"flightStatus": {"status": "success"}},
                }
            ],
        )

        polled = client.get(f"/flights/ingest/jobs/{job_id}")
        assert polled.status_code == 200, polled.text
        body = polled.json()
        assert body["results"][0]["flight"]["flightNumber"] == "100"
        assert body["results"][0]["success"] is True
