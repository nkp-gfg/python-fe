"""Contract for the OTP flight list that Today's Flights renders.

The display bug is in the page, not this mapper: these tests pass while the
estimate is still missing from the table. They lock the wire field the page
must read, and that GET /otp/flights takes no credential.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.otp import list_otp_flights, router


def _row():
    return {
        "flight_sequence_number": 9001,
        "carrier_code": "GF",
        "flight_number": "2057",
        "scheduled_origin": "BAH",
        "scheduled_destination": "LHR",
        "actual_origin": None,
        "actual_destination": None,
        "flight_date": datetime(2026, 3, 30),
        "local_flight_date": datetime(2026, 3, 30),
        "leg_departure_date": datetime(2026, 3, 30),
        "scheduled_departure_utc": datetime(2026, 3, 30, 9, 0),
        "estimated_block_off_utc": datetime(2026, 3, 30, 9, 0),
        "scheduled_arrival_utc": datetime(2026, 3, 30, 14, 0),
        "estimated_block_on_utc": datetime(2026, 3, 30, 15, 25),
        "actual_block_off_utc": None,
        "actual_block_on_utc": None,
        "actual_takeoff_utc": None,
        "actual_touchdown_utc": None,
        "scheduled_departure_local": datetime(2026, 3, 30, 12, 0),
        "scheduled_arrival_local": datetime(2026, 3, 30, 14, 0),
        "published_departure_local": None,
        "published_arrival_local": None,
        "flight_status": "SCHEDULED",
        "aircraft_type": "32N",
        "aircraft_registration": "A9C-XX",
        "service_type_code": "J",
        "cancel_reason_code": None,
        "passenger_counts": [{"Count": 120}],
        "delay_details": None,
        "source": "otp",
    }


def test_list_otp_flights_maps_estimated_block_on(monkeypatch):
    captured = {}

    def fake_query_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [_row()]

    monkeypatch.setattr("backend.api.routes.otp.query_all", fake_query_all)

    flights = list_otp_flights(date="2026-03-30")

    assert captured["params"] == ("2026-03-30", "2026-03-30")
    assert "estimated_block_on_utc" in captured["sql"]
    body = flights[0].model_dump(mode="json")
    assert body["scheduledArrivalUtc"] == "2026-03-30T14:00:00"
    assert body["estimatedBlockOnUtc"] == "2026-03-30T15:25:00"
    assert body["estimatedBlockOffUtc"] == "2026-03-30T09:00:00"
    assert body["actualBlockOnUtc"] is None


def test_otp_flights_route_takes_no_credential():
    routes = [route for route in router.routes if getattr(route, "path", None) == "/otp/flights"]
    assert len(routes) == 1
    route = routes[0]
    assert "GET" in route.methods
    assert [param.name for param in route.dependant.query_params] == ["date"]
    assert route.dependencies == []
    assert route.dependant.dependencies == []
