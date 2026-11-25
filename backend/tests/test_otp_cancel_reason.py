"""OTP list must hand the stored cancel reason to Today's Flights.

The board reads cancelReasonCode from GET /otp/flights. These tests lock the
copy from otp.flight_xml_current.cancel_reason_code onto that field, with no
credential on the request. They pass while the UI still hides the code.
"""

import unittest
from datetime import date
from unittest.mock import patch

from backend.api.routes.otp import _row_to_flight, list_otp_flights


def _row(**overrides):
    base = {
        "flight_sequence_number": 9001,
        "carrier_code": "GF",
        "flight_number": "2057",
        "scheduled_origin": "BAH",
        "scheduled_destination": "LHR",
        "flight_date": date(2026, 3, 30),
        "scheduled_departure_local": None,
        "flight_status": "CANCELLED",
        "cancel_reason_code": "WX-FOG",
    }
    base.update(overrides)
    return base


class OtpCancelReasonApiTests(unittest.TestCase):
    def test_row_copies_cancel_reason_and_marks_cancelled(self):
        flight = _row_to_flight(_row())
        self.assertEqual(flight.cancelReasonCode, "WX-FOG")
        self.assertEqual(flight.flightStatus, "CANCELLED")
        self.assertTrue(flight.isCancelled)

    def test_cnx_status_is_cancelled_and_keeps_its_own_reason(self):
        flight = _row_to_flight(
            _row(
                flight_sequence_number=9003,
                flight_number="3001",
                flight_status="CNX",
                cancel_reason_code="TECH-42",
            )
        )
        self.assertEqual(flight.flightStatus, "CNX")
        self.assertTrue(flight.isCancelled)
        self.assertEqual(flight.cancelReasonCode, "TECH-42")

    def test_missing_reason_stays_empty(self):
        flight = _row_to_flight(_row(cancel_reason_code=None))
        self.assertIsNone(flight.cancelReasonCode)
        self.assertTrue(flight.isCancelled)

    def test_list_returns_each_flights_reason_with_no_credential(self):
        rows = [
            _row(flight_sequence_number=9001, cancel_reason_code="WX-FOG"),
            _row(
                flight_sequence_number=9002,
                flight_number="188",
                scheduled_destination="DXB",
                cancel_reason_code="COM-LOAD",
            ),
        ]
        with patch("backend.api.routes.otp.query_all", return_value=rows) as query:
            flights = list_otp_flights(date="2026-03-30")

        self.assertEqual(
            [(item.flightNumber, item.cancelReasonCode, item.isCancelled) for item in flights],
            [("2057", "WX-FOG", True), ("188", "COM-LOAD", True)],
        )
        dumped = [item.model_dump() for item in flights]
        self.assertEqual(dumped[0]["cancelReasonCode"], "WX-FOG")
        self.assertEqual(dumped[1]["cancelReasonCode"], "COM-LOAD")
        query.assert_called_once()
        _sql, params = query.call_args.args
        self.assertEqual(params, ("2026-03-30", "2026-03-30"))


if __name__ == "__main__":
    unittest.main()
