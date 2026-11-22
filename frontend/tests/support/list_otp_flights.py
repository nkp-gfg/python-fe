"""Run the real OTP list handler against fixture rows.

PostgreSQL is not started here. ``timestamp without time zone :: date`` keeps
the stored calendar fields and does not shift them into the session time zone
(PostgreSQL 16, datatype-datetime: a timestamp without time zone is derived
from the date/time fields in the input and is not adjusted for time zone).
``list_otp_flights`` still builds ``_QUERY``, validates the date, and maps
rows through ``_row_to_flight`` / ``_resolve_sabre_departure_date``.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime

from backend.api.routes import otp as otp_route


def _parse_temporal(value):
    if value is None or not isinstance(value, str):
        return value
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", ""))
    if len(value) == 10:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return value


def _calendar_date(value) -> str | None:
    """Date fields of a timestamp/date, with no time-zone conversion."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text[:10] if text else None


def list_flights(requested_date: str, rows: list[dict]) -> list[dict]:
    parsed = []
    for row in rows:
        parsed.append({key: _parse_temporal(val) if key != "passenger_counts" and key != "delay_details" else val for key, val in row.items()})

    def query_all(sql, params):
        if sql != otp_route._QUERY:
            raise RuntimeError(
                "list_otp_flights issued SQL that these tests do not execute:\n" + sql
            )
        if params != (requested_date, requested_date):
            raise RuntimeError(f"unexpected OTP query params: {params!r}")
        matched = []
        for row in parsed:
            departure = _calendar_date(row.get("scheduled_departure_local"))
            arrival = _calendar_date(row.get("scheduled_arrival_local"))
            # WHERE scheduled_departure_local::date = %s OR scheduled_arrival_local::date = %s
            # NULL::date comparisons are not true.
            if departure == requested_date or arrival == requested_date:
                matched.append(row)
        matched.sort(key=lambda row: (
            row.get("scheduled_departure_local") is None,
            row.get("scheduled_departure_local") or datetime.min,
        ))
        return matched

    original = otp_route.query_all
    otp_route.query_all = query_all
    try:
        flights = otp_route.list_otp_flights(requested_date)
    finally:
        otp_route.query_all = original
    return [flight.model_dump(mode="json") for flight in flights]


def main() -> None:
    payload = json.load(sys.stdin)
    result = list_flights(payload["date"], payload["rows"])
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
