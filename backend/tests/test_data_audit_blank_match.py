"""Flight data-audit must not reconcile fields it never compared.

The data-audit comparison is unauthenticated. It scores a field as a match
when both the OTP value and the Sabre value are missing, then adds that row
to ``summary.match``. The departure-gate row is one of those rows even when
ingestion stored a gate: the OTP side is hard-coded empty, and the Sabre
side reads ``departureGate`` while ``convert_flight_status`` stores ``gate``.

These tests exercise ``GET /data-audit/{flight_number}/compare`` on the
application that mounts that route. They fail while blank pairs are counted
as matches, and they pass once missing pairs are scored ``not_compared`` and
the Sabre gate is the value ingestion stored.

``GET /data-audit/{flight_number}/passengers`` is not covered. Its comparator
drops pairs that are empty on both sides before scoring, so those rows never
become matches and never enter the passenger matched total.
"""

from __future__ import annotations

import copy
from datetime import date, datetime

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes import data_audit
from backend.feeder.converter import convert_flight_status

FLIGHT_NUMBER = "2152"
FLIGHT_DATE = "2026-03-19"
STORED_GATE = "A12"
NOT_COMPARED = "not_compared"

# Fields whose OTP and Sabre values are both missing for the fixture below.
# Whitespace-only OTP values are missing too.
BLANK_FIELDS = (
    "Actual Block Off (UTC)",
    "Actual Block On (UTC)",
    "Estimated Block Off (UTC)",
    "Estimated Block On (UTC)",
    "Total Passengers",
    "Actual Origin",
    "Actual Destination",
    "Service Type",
)


def _blank(value: object) -> bool:
    return value is None or str(value).strip() == ""


def _otp_row() -> dict:
    """One ``otp.flight_xml_current`` row shaped like ``_PG_QUERY``.

    That query does not select a gate column, so the row has no gate value.
    """
    return {
        "flight_sequence_number": 42,
        "flight_number": FLIGHT_NUMBER,
        "scheduled_origin": "BAH",
        "scheduled_destination": "LHR",
        "actual_origin": "   ",
        "actual_destination": None,
        "flight_date": date(2026, 3, 19),
        "scheduled_departure_utc": datetime(2026, 3, 19, 10, 0),
        "estimated_block_off_utc": None,
        "scheduled_arrival_utc": datetime(2026, 3, 19, 14, 0),
        "estimated_block_on_utc": None,
        "actual_block_off_utc": None,
        "actual_block_on_utc": None,
        "actual_takeoff_utc": None,
        "actual_touchdown_utc": None,
        "scheduled_departure_local": None,
        "scheduled_arrival_local": None,
        "flight_status": "ON TIME",
        "aircraft_type": "32N",
        "aircraft_registration": "A9C-AB",
        "service_type_code": "",
        "cancel_reason_code": None,
        "passenger_counts": None,
        "delay_details": None,
        "source": "otp",
    }


def _stored_flight_status(departure_gate: str | None) -> dict:
    """Document ``convert_flight_status`` produces and ``store_flight_status`` inserts."""
    itin: dict = {
        "FlightStatus": "OPENCI",
        "AircraftType": "32N",
        "AircraftRegistration": "A9C-AB",
        "ScheduledDepartureDate": FLIGHT_DATE,
        "ScheduledDepartureTime": "10:00:00",
    }
    if departure_gate is not None:
        itin["DepartureGate"] = departure_gate
    raw = {"ItineraryResponseList": {"ItineraryInfoResponse": itin}}
    doc = convert_flight_status(raw, "GF", FLIGHT_NUMBER, "DXB", FLIGHT_DATE)
    doc["_id"] = "flight-status-1"
    return doc


class _FlightStatusDB:
    """Stand-in for the flight_status collection read by ``_fetch_mongo``.

    ``find_one`` applies the equality filters the route builds, then returns
    the stored document unchanged apart from the copy the route may pop ``_id``
    from.
    """

    def __init__(self, doc: dict):
        self.doc = doc
        self.queries: list[dict] = []

    def __getitem__(self, name: str):
        assert name == "flight_status"
        return self

    def find_one(self, query, sort=None):
        self.queries.append(copy.deepcopy(query))
        for key, value in query.items():
            if self.doc.get(key) != value:
                return None
        return copy.deepcopy(self.doc)


def _compare(doc: dict) -> tuple[dict, _FlightStatusDB, list]:
    """Call the mounted compare route with no credentials."""
    database = _FlightStatusDB(doc)
    sql_calls: list[tuple] = []

    def query_all(sql, params=None):
        sql_calls.append((sql, params))
        assert "otp.flight_xml_current" in sql
        assert params is not None
        assert params[0] == FLIGHT_NUMBER
        return [_otp_row()]

    route = next(
        r
        for r in data_audit.router.routes
        if isinstance(r, APIRoute) and r.path == "/data-audit/{flight_number}/compare"
    )
    assert route.methods == {"GET"}
    assert route.dependencies == []
    mounted = False
    for parent in app.routes:
        if getattr(parent, "original_router", None) is data_audit.router:
            mounted = True
            assert parent.include_context.dependencies == []
        if getattr(parent, "path", None) == "/data-audit/{flight_number}/compare":
            mounted = True
            assert list(getattr(parent, "dependencies", [])) == []
    assert mounted, "compare route is not mounted on the application"

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(data_audit, "get_db", lambda: database)
        patch.setattr(data_audit, "query_all", query_all)
        client = TestClient(app)
        response = client.get(
            f"/data-audit/{FLIGHT_NUMBER}/compare",
            params={"date": FLIGHT_DATE},
        )

    assert response.status_code == 200, response.text
    assert "authorization" not in {k.lower() for k in response.request.headers}
    body = response.json()
    assert database.queries, "flight number never reached the flight_status read"
    assert database.queries[0]["flightNumber"] == FLIGHT_NUMBER
    assert database.queries[0]["departureDate"] == FLIGHT_DATE
    assert sql_calls, "flight number never reached the OTP read"
    return body, database, sql_calls


def _rows_by_field(body: dict) -> dict[str, dict]:
    return {row["field"]: row for row in body["rows"]}


def _assert_match_means_equal_values(body: dict) -> None:
    matched = [row for row in body["rows"] if row["match"] == "match"]
    assert body["summary"]["match"] == len(matched)
    for row in matched:
        assert not _blank(row["pgValue"])
        assert not _blank(row["mongoValue"])
        assert row["pgValue"] == row["mongoValue"]


def _assert_blank_pair(row: dict) -> None:
    assert _blank(row["pgValue"])
    assert _blank(row["mongoValue"])
    assert row["match"] == NOT_COMPARED


def test_stored_sabre_gate_is_not_scored_as_a_blank_match():
    """A gate ingestion stored under ``gate`` must not reconcile as two blanks."""
    doc = _stored_flight_status(STORED_GATE)
    assert doc["gate"] == STORED_GATE
    assert "departureGate" not in doc

    body, _, _ = _compare(doc)
    rows = _rows_by_field(body)

    gate = rows["Departure Gate"]
    assert gate == {
        "field": "Departure Gate",
        "pgValue": None,
        "mongoValue": STORED_GATE,
        "match": "mongo_only",
        "remark": None,
    }, gate

    for field in BLANK_FIELDS:
        _assert_blank_pair(rows[field])

    assert rows["Flight Number"]["match"] == "match"
    assert rows["Flight Number"]["pgValue"] == FLIGHT_NUMBER
    assert rows["Flight Number"]["mongoValue"] == FLIGHT_NUMBER
    assert rows["Flight Date"]["match"] == "match"
    assert rows["Origin"]["match"] == "mismatch"
    assert rows["Origin"]["pgValue"] == "BAH"
    assert rows["Origin"]["mongoValue"] == "DXB"

    _assert_match_means_equal_values(body)
    assert body["summary"]["match"] == 2
    assert all(rows[field]["match"] != "match" for field in (*BLANK_FIELDS, "Departure Gate"))


def test_unassigned_gate_and_other_blank_fields_are_not_compared():
    """Two missing values, including an empty stored gate, are not a match."""
    doc = _stored_flight_status(None)
    assert doc["gate"] == ""
    assert "departureGate" not in doc

    body, _, _ = _compare(doc)
    rows = _rows_by_field(body)

    for field in (*BLANK_FIELDS, "Departure Gate"):
        _assert_blank_pair(rows[field])

    assert rows["Flight Number"]["match"] == "match"
    assert rows["Flight Date"]["match"] == "match"
    assert rows["Origin"]["match"] == "mismatch"

    _assert_match_means_equal_values(body)
    assert body["summary"]["match"] == 2
    assert body["summary"]["mismatch"] >= 1
