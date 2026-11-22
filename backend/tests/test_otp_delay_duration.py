"""OTP delay durations are returned unchanged by GET /otp/flights.

Today's Flights then parses those strings. The display assertions are in
frontend/tests/today-delay-duration.test.ts. They fail until that parser
accepts an ISO 8601 duration that omits a zero component.

Run:
    python3 backend/tests/test_otp_delay_duration.py
    python3 backend/tests/test_otp_delay_duration.py --http-body
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import otp

FLIGHT_DATE = "2026-03-19"


def _delay(duration: str) -> dict:
    return {
        "DelayComment": "recorded by operations",
        "DelayDuration": duration,
        "DelayReasonCode": "81",
    }


# (flight number, delay_details JSON value as stored in otp.flight_xml_current)
# Strings are the text psycopg2 returns when no jsonb typecaster is registered.
# Lists cover the branch _parse_delays takes when the value is already decoded.
SCENARIOS: list[tuple[str, object]] = [
    ("9001", json.dumps([_delay("PT24M")])),
    ("9002", json.dumps([_delay("PT2H")])),
    ("9003", json.dumps([_delay("PT0H24M")])),
    ("9004", json.dumps([_delay("PT2H30M")])),
    ("9005", json.dumps([_delay("PT1H30M30S")])),
    ("9006", json.dumps([_delay("P1D")])),
    ("9007", json.dumps([_delay("PT45S")])),
    ("9008", json.dumps([_delay("PT15M"), _delay("PT2H")])),
    ("9009", json.dumps([_delay("P1DT2H30M")])),
    ("9010", json.dumps([_delay("")])),
    ("9011", json.dumps([_delay("not-a-duration")])),
    ("9012", None),
    ("9013", json.dumps([_delay("PT0H10M"), _delay("PT20M")])),
    ("9014", json.dumps([_delay("PT24M"), _delay("")])),
    ("9015", json.dumps([_delay("XXPT1H2MYY")])),
    ("9016", [_delay("PT1H30M30S extra")]),
]


def _row(flight_number: str, delay_details) -> dict:
    scheduled = datetime(2026, 3, 19, 9, 55)
    return {
        "flight_sequence_number": int(flight_number),
        "carrier_code": "GF",
        "flight_number": flight_number,
        "scheduled_origin": "BAH",
        "scheduled_destination": "DXB",
        "actual_origin": None,
        "actual_destination": None,
        "flight_date": date(2026, 3, 19),
        "local_flight_date": None,
        "leg_departure_date": None,
        "scheduled_departure_utc": scheduled,
        "estimated_block_off_utc": scheduled,
        "scheduled_arrival_utc": datetime(2026, 3, 19, 11, 15),
        "estimated_block_on_utc": None,
        "actual_block_off_utc": None,
        "actual_block_on_utc": None,
        "actual_takeoff_utc": None,
        "actual_touchdown_utc": None,
        "scheduled_departure_local": datetime(2026, 3, 19, 12, 55),
        "scheduled_arrival_local": datetime(2026, 3, 19, 15, 15),
        "published_departure_local": None,
        "published_arrival_local": None,
        "flight_status": "DEPARTED",
        "aircraft_type": "32N",
        "aircraft_registration": "A9C-FE",
        "service_type_code": "J",
        "cancel_reason_code": None,
        "passenger_counts": json.dumps([{"Count": 120}]),
        "delay_details": delay_details,
        "source": "otp",
    }


def _install_query(rows: list[dict]):
    def query_all(sql, params):
        assert "delay_details" in sql
        assert params == (FLIGHT_DATE, FLIGHT_DATE)
        return rows

    otp.query_all = query_all


def _client(rows: list[dict]) -> TestClient:
    _install_query(rows)
    app = FastAPI()
    app.include_router(otp.router)
    return TestClient(app)


def http_body() -> str:
    rows = [_row(number, details) for number, details in SCENARIOS]
    response = _client(rows).get("/otp/flights", params={"date": FLIGHT_DATE})
    assert response.status_code == 200, response.text
    return response.text


def test_json_text_and_decoded_list_reach_the_response_unchanged():
    body = json.loads(http_body())
    by_number = {flight["flightNumber"]: flight for flight in body}
    assert set(by_number) == {number for number, _ in SCENARIOS}

    for number, stored in SCENARIOS:
        flight = by_number[number]
        if stored is None:
            assert flight["delayDetails"] is None
            continue
        expected = json.loads(stored) if isinstance(stored, str) else stored
        assert flight["delayDetails"] == expected
        for item, source in zip(flight["delayDetails"], expected):
            assert item["DelayDuration"] == source["DelayDuration"]
            assert item["DelayDuration"] is not None


def test_decoded_list_branch_preserves_minutes_only_duration():
    stored = [_delay("PT24M"), _delay("PT2H")]
    response = _client([_row("9001", stored)]).get(
        "/otp/flights", params={"date": FLIGHT_DATE}
    )
    assert response.status_code == 200
    details = response.json()[0]["delayDetails"]
    assert [item["DelayDuration"] for item in details] == ["PT24M", "PT2H"]


if __name__ == "__main__":
    if "--http-body" in sys.argv:
        sys.stdout.write(http_body())
        raise SystemExit(0)
    test_json_text_and_decoded_list_reach_the_response_unchanged()
    test_decoded_list_branch_preserves_minutes_only_duration()
    print("PASS: otp delay durations are returned unchanged")
