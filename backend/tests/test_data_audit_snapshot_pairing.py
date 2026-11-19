"""Data Audit must pair the newest OTP row with the Sabre snapshot for that day.

GET /data-audit/{flight}/compare and GET /data-audit/{flight}/passengers are
what the Data Audit page calls when Flight Date is left blank. These tests
fail while that request can report a date or field disagreement from a Sabre
document that is not the latest ingest for the OTP row's departure date.
"""

import os
import unittest

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["POSTGRES_URI"] = "dbname=falcon_audit_test host=/var/run/postgresql"

import psycopg2
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from backend.api.main import app
from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.feeder.storage import store_flight_status, store_passenger_list

# Distinct from any operational flight number. Validators allow 1-5 digits.
_FALSE_DATE = "99041"
_STALE_SAME_DAY = "99042"
_PASSENGERS = "99043"
_OTP_DATE = "2026-03-20"
_OLDER_DATE = "2026-03-01"
_OTHER_DATE = "2026-03-19"


def _flight_status(flight_number, departure_date, origin, fetched_at):
    """Build a flight_status document the same way ingestion does."""
    raw = {
        "ItineraryResponseList": {
            "ItineraryInfoResponse": {
                "ScheduledDepartureDate": departure_date,
                "FlightStatus": "SCH",
                "AircraftType": "A320",
                "AircraftRegistration": "A9C-XX",
                "DepartureGate": "G1",
            }
        },
        "LegInfoList": {"LegInfo": [{"City": origin}, {"City": "LHR"}]},
    }
    doc = convert_flight_status(raw, "GF", flight_number, origin, departure_date)
    doc["fetchedAt"] = fetched_at
    return doc


def _passenger_list(flight_number, departure_date, origin, fetched_at, count):
    """Build a passenger_list document the same way ingestion does."""
    passengers = [
        {
            "Name_Details": {"LastName": "PAX", "FirstName": str(index)},
            "Cabin": "Y",
            "PassengerType": "A",
        }
        for index in range(count)
    ]
    raw = {
        "ItineraryInfo": {
            "Itinerary": {"Destination": "LHR", "AircraftType": "A320"},
            "DepartureGate": "G1",
            "CabinInfoList": {
                "CabinInfo": {
                    "Cabin": "Y",
                    "Count": str(count),
                    "Authorized": "180",
                    "Available": "10",
                }
            },
        },
        "PassengerInfoList": {"PassengerInfo": passengers},
    }
    doc = convert_passenger_list(raw, "GF", flight_number, departure_date, origin)
    doc["fetchedAt"] = fetched_at
    return doc


def _ensure_otp_table(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS otp")
        cur.execute("DROP TABLE IF EXISTS otp.flight_xml_current")
        cur.execute(
            """
            CREATE TABLE otp.flight_xml_current (
                flight_sequence_number integer,
                flight_number text,
                scheduled_origin text,
                scheduled_destination text,
                actual_origin text,
                actual_destination text,
                flight_date date,
                scheduled_departure_utc timestamptz,
                estimated_block_off_utc timestamptz,
                scheduled_arrival_utc timestamptz,
                estimated_block_on_utc timestamptz,
                actual_block_off_utc timestamptz,
                actual_block_on_utc timestamptz,
                actual_takeoff_utc timestamptz,
                actual_touchdown_utc timestamptz,
                scheduled_departure_local timestamp,
                scheduled_arrival_local timestamp,
                flight_status text,
                aircraft_type text,
                aircraft_registration text,
                service_type_code text,
                cancel_reason_code text,
                passenger_counts jsonb,
                delay_details jsonb,
                source text
            )
            """
        )


def _insert_otp(conn, flight_number, flight_date, origin, passenger_count):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO otp.flight_xml_current (
                flight_sequence_number, flight_number, scheduled_origin,
                scheduled_destination, flight_date, flight_status,
                aircraft_registration, passenger_counts
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                1,
                flight_number,
                origin,
                "LHR",
                flight_date,
                "SCH",
                "A9C-OTP",
                Json([{"@classOfService": "Y", "Count": passenger_count}]),
            ),
        )


def _rows(body):
    return {row["field"]: row for row in body["rows"]}


def _refused_without_date(response):
    """A fix may reject a comparison that has no departure date."""
    if response.status_code not in (400, 422):
        return False
    payload = response.text.lower()
    if "date" not in payload:
        raise AssertionError(
            f"omitted date was refused without naming the date: {response.text}"
        )
    return True


class DataAuditSnapshotPairingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        admin = psycopg2.connect("dbname=postgres host=/var/run/postgresql")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = 'falcon_audit_test'"
            )
            if cur.fetchone() is None:
                cur.execute("CREATE DATABASE falcon_audit_test")
        admin.close()

        cls.pg = psycopg2.connect(os.environ["POSTGRES_URI"])
        cls.pg.autocommit = True
        _ensure_otp_table(cls.pg)

        # Newest OTP day is 2026-03-20. An older OTP row exists for the same number.
        _insert_otp(cls.pg, _FALSE_DATE, _OLDER_DATE, "BAH", 4)
        _insert_otp(cls.pg, _FALSE_DATE, _OTP_DATE, "BAH", 4)
        _insert_otp(cls.pg, _STALE_SAME_DAY, _OTP_DATE, "BAH", 4)
        _insert_otp(cls.pg, _PASSENGERS, _OTHER_DATE, "BAH", 1)
        _insert_otp(cls.pg, _PASSENGERS, _OTP_DATE, "BAH", 4)

        # Insert the snapshot the buggy sort returns first. None of these
        # documents carry _ingestTimestamp; ingestion stores fetchedAt.
        store_flight_status(
            _flight_status(_FALSE_DATE, _OLDER_DATE, "BAH", "2026-03-01T00:00:00Z")
        )
        store_flight_status(
            _flight_status(_FALSE_DATE, _OTHER_DATE, "BAH", "2026-03-22T00:00:00Z")
        )
        store_flight_status(
            _flight_status(_FALSE_DATE, _OTP_DATE, "BAH", "2026-03-20T12:00:00Z")
        )
        store_flight_status(
            _flight_status(_STALE_SAME_DAY, _OTP_DATE, "BAH", "2026-03-20T01:00:00Z")
        )
        store_flight_status(
            _flight_status(_STALE_SAME_DAY, _OTP_DATE, "DXB", "2026-03-20T23:00:00Z")
        )
        store_passenger_list(
            _passenger_list(_PASSENGERS, _OTP_DATE, "BAH", "2026-03-20T01:00:00Z", 4)
        )
        store_passenger_list(
            _passenger_list(_PASSENGERS, _OTHER_DATE, "BAH", "2026-03-22T00:00:00Z", 1)
        )

        from backend.api.database import get_db

        flight_docs = list(
            get_db()["flight_status"].find(
                {"flightNumber": {"$in": [_FALSE_DATE, _STALE_SAME_DAY]}}
            )
        )
        passenger_docs = list(
            get_db()["passenger_list"].find({"flightNumber": _PASSENGERS})
        )
        assert flight_docs and passenger_docs
        for doc in flight_docs + passenger_docs:
            assert "_ingestTimestamp" not in doc
            assert doc.get("fetchedAt")

        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)
        cls.pg.close()

    def test_omitted_date_does_not_report_another_days_departure(self):
        """Blank date must not compare the newest OTP day with an older Sabre day.

        Stored flight_status documents, inserted in this order:
          2026-03-01 fetchedAt 2026-03-01 (no _ingestTimestamp)
          2026-03-19 fetchedAt 2026-03-22 (newest ingest, different day)
          2026-03-20 fetchedAt 2026-03-20 (same day as the newest OTP row)
        The newest otp.flight_xml_current row is 2026-03-20.
        """
        response = self.client.get(f"/data-audit/{_FALSE_DATE}/compare")
        if _refused_without_date(response):
            return

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["pgFound"])
        self.assertTrue(body["mongoFound"])
        flight_date = _rows(body)["Flight Date"]
        self.assertEqual(flight_date["pgValue"], _OTP_DATE)
        self.assertEqual(flight_date["mongoValue"], _OTP_DATE)
        self.assertEqual(flight_date["match"], "match")

    def test_omitted_date_reports_disagreement_on_latest_same_day_snapshot(self):
        """Blank date must surface a disagreement that exists only on the latest ingest.

        Both Sabre documents are 2026-03-20. The earlier fetchedAt snapshot
        agrees with OTP on origin BAH. The later fetchedAt snapshot is DXB.
        """
        response = self.client.get(f"/data-audit/{_STALE_SAME_DAY}/compare")
        if _refused_without_date(response):
            return

        self.assertEqual(response.status_code, 200, response.text)
        rows = _rows(response.json())
        self.assertEqual(rows["Flight Date"]["mongoValue"], _OTP_DATE)
        self.assertEqual(rows["Flight Date"]["match"], "match")
        self.assertEqual(rows["Origin"]["pgValue"], "BAH")
        self.assertEqual(rows["Origin"]["mongoValue"], "DXB")
        self.assertEqual(rows["Origin"]["match"], "mismatch")

    def test_provided_date_uses_latest_fetched_at_snapshot(self):
        """A supplied departure date still has to select the newest fetchedAt snapshot."""
        response = self.client.get(
            f"/data-audit/{_STALE_SAME_DAY}/compare",
            params={"date": _OTP_DATE},
        )
        self.assertEqual(response.status_code, 200, response.text)
        rows = _rows(response.json())
        self.assertEqual(rows["Flight Date"]["pgValue"], _OTP_DATE)
        self.assertEqual(rows["Flight Date"]["mongoValue"], _OTP_DATE)
        self.assertEqual(rows["Origin"]["pgValue"], "BAH")
        self.assertEqual(rows["Origin"]["mongoValue"], "DXB")
        self.assertEqual(rows["Origin"]["match"], "mismatch")

    def test_omitted_date_passenger_compare_uses_otp_departure_date(self):
        """Passenger audit shares the blank-date pairing with the flight audit.

        The manifest for the newest OTP day has 4 passengers and an older
        fetchedAt. A different day's manifest was ingested later and has 1.
        """
        response = self.client.get(f"/data-audit/{_PASSENGERS}/passengers")
        if _refused_without_date(response):
            return

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["pgFound"])
        self.assertTrue(body["mongoFound"])
        rows = _rows(body)
        self.assertEqual(rows["Total Passengers"]["pgValue"], "4")
        self.assertEqual(rows["Total Passengers"]["mongoValue"], "4")
        self.assertEqual(rows["Total Passengers"]["match"], "match")
        self.assertEqual(rows["Economy Count"]["mongoValue"], "4")
        self.assertEqual(rows["Economy Count"]["match"], "match")


if __name__ == "__main__":
    unittest.main()
