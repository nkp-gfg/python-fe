"""OTP list keeps scheduled and actual airports as separate fields.

The Today's Flights board reads this payload. Actual airports must survive
the row mapping so the board can show a diversion, and `origin` must stay
the scheduled departure airport because Sabre ingest uses that field.
"""

import os
import sys
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.routes import otp


def _row(**overrides):
    row = {
        "flight_sequence_number": 205,
        "carrier_code": "GF",
        "flight_number": "205",
        "scheduled_origin": "BAH",
        "scheduled_destination": "LHR",
        "actual_origin": "AUH",
        "actual_destination": "DXB",
        "flight_date": date(2026, 3, 30),
        "local_flight_date": date(2026, 3, 30),
        "scheduled_departure_local": None,
        "flight_status": "SCHEDULED",
    }
    row.update(overrides)
    return row


def test_row_mapping_keeps_actual_airports_distinct_from_scheduled():
    flight = otp._row_to_flight(_row(actual_origin=" AUH ", actual_destination="DXB"))
    dumped = flight.model_dump()
    assert dumped["origin"] == "BAH"
    assert dumped["destination"] == "LHR"
    assert dumped["actualOrigin"] == " AUH "
    assert dumped["actualDestination"] == "DXB"
    assert dumped["origin"] != dumped["actualOrigin"].strip()
    print("  PASS: test_row_mapping_keeps_actual_airports_distinct_from_scheduled")


def test_blank_actual_airports_are_preserved_for_the_client():
    flight = otp._row_to_flight(_row(actual_origin="", actual_destination=None))
    dumped = flight.model_dump()
    assert dumped["origin"] == "BAH"
    assert dumped["destination"] == "LHR"
    assert dumped["actualOrigin"] == ""
    assert dumped["actualDestination"] is None
    print("  PASS: test_blank_actual_airports_are_preserved_for_the_client")


def test_list_endpoint_returns_query_actual_airports_without_replacing_origin():
    row = _row()
    with patch.object(otp, "query_all", return_value=[row]) as query:
        flights = otp.list_otp_flights(date="2026-03-30")

    sql, params = query.call_args.args
    assert "actual_origin" in sql
    assert "actual_destination" in sql
    assert "scheduled_origin" in sql
    assert "scheduled_destination" in sql
    assert params == ("2026-03-30", "2026-03-30")

    dumped = flights[0].model_dump()
    assert dumped["origin"] == "BAH"
    assert dumped["destination"] == "LHR"
    assert dumped["actualOrigin"] == "AUH"
    assert dumped["actualDestination"] == "DXB"
    assert dumped["flightNumber"] == "205"
    print("  PASS: test_list_endpoint_returns_query_actual_airports_without_replacing_origin")


if __name__ == "__main__":
    print("Running OTP actual-airport contract tests...")
    test_row_mapping_keeps_actual_airports_distinct_from_scheduled()
    test_blank_actual_airports_are_preserved_for_the_client()
    test_list_endpoint_returns_query_actual_airports_without_replacing_origin()
    print("ALL TESTS PASS")
