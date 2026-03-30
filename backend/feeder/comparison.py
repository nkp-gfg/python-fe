"""Cross-database comparison engine using Polars DataFrames.

Compares Sabre passenger data (MongoDB) against OTP flight data (PostgreSQL)
using vectorized operations for performance at scale (100+ flights).
"""

import logging
from datetime import datetime, timezone
from typing import Callable

import polars as pl

from backend.feeder.storage import get_db
from backend.api.postgres import query_all

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Data Loaders ──────────────────────────────────────────────────────────

def load_sabre_flights(date: str, flight_numbers: list[str] | None = None) -> pl.DataFrame:
    """Load latest Sabre flight data from MongoDB into a Polars DataFrame."""
    db = get_db()
    query = {"departureDate": date}
    if flight_numbers:
        query["flightNumber"] = {"$in": flight_numbers}

    flights = list(db["flights"].find(query, {
        "_id": 0,
        "airline": 1,
        "flightNumber": 1,
        "origin": 1,
        "departureDate": 1,
        "lastUpdatedAt": 1,
        "summary": 1,
        "flightSequenceNumber": 1,
    }))

    if not flights:
        return pl.DataFrame(schema={
            "flightNumber": pl.Utf8, "origin": pl.Utf8,
            "sabre_status": pl.Utf8, "sabre_gate": pl.Utf8,
            "sabre_totalPax": pl.Int64, "sabre_checkedIn": pl.Int64,
            "sabre_boarded": pl.Int64, "sabre_lastUpdated": pl.Utf8,
        })

    rows = []
    for f in flights:
        s = f.get("summary") or {}
        rows.append({
            "flightNumber": f.get("flightNumber", ""),
            "origin": f.get("origin", ""),
            "sabre_status": s.get("status", ""),
            "sabre_gate": s.get("gate", ""),
            "sabre_totalPax": s.get("totalPax", 0) or 0,
            "sabre_checkedIn": s.get("checkedIn", 0) or 0,
            "sabre_boarded": s.get("boarded", 0) or 0,
            "sabre_lastUpdated": f.get("lastUpdatedAt", ""),
        })

    return pl.DataFrame(rows)


def load_otp_flights(date: str, flight_numbers: list[str] | None = None) -> pl.DataFrame:
    """Load OTP flight data from PostgreSQL into a Polars DataFrame."""
    sql = """
        SELECT
            flight_number,
            scheduled_origin,
            flight_status,
            scheduled_departure_utc,
            actual_block_off_utc,
            actual_block_on_utc,
            aircraft_type,
            passenger_counts
        FROM otp.flight_xml_current
        WHERE (scheduled_departure_local::date = %s
               OR scheduled_arrival_local::date = %s)
    """
    params: list = [date, date]
    if flight_numbers:
        placeholders = ",".join(["%s"] * len(flight_numbers))
        sql += f" AND flight_number IN ({placeholders})"
        params.extend(flight_numbers)

    sql += " ORDER BY scheduled_departure_utc ASC"

    try:
        rows = query_all(sql, tuple(params))
    except Exception as exc:
        logger.warning("Failed to load OTP flights: %s", exc)
        return pl.DataFrame(schema={
            "flightNumber": pl.Utf8, "origin": pl.Utf8,
            "otp_status": pl.Utf8, "otp_totalPax": pl.Int64,
            "otp_depUtc": pl.Utf8, "otp_aircraft": pl.Utf8,
        })

    import json

    result = []
    for r in rows:
        counts = r.get("passenger_counts")
        total = 0
        if counts:
            items = counts if isinstance(counts, list) else json.loads(counts)
            total = sum(int(p.get("Count", 0)) for p in items)

        result.append({
            "flightNumber": str(r.get("flight_number", "")).strip(),
            "origin": r.get("scheduled_origin", ""),
            "otp_status": r.get("flight_status", ""),
            "otp_totalPax": total,
            "otp_depUtc": str(r.get("scheduled_departure_utc", "") or ""),
            "otp_aircraft": r.get("aircraft_type", ""),
        })

    if not result:
        return pl.DataFrame(schema={
            "flightNumber": pl.Utf8, "origin": pl.Utf8,
            "otp_status": pl.Utf8, "otp_totalPax": pl.Int64,
            "otp_depUtc": pl.Utf8, "otp_aircraft": pl.Utf8,
        })

    return pl.DataFrame(result)


def load_sabre_passengers(date: str, flight_numbers: list[str] | None = None) -> pl.DataFrame:
    """Load latest Sabre passenger lists from MongoDB into Polars."""
    db = get_db()
    query = {"departureDate": date}
    if flight_numbers:
        query["flightNumber"] = {"$in": flight_numbers}

    # Get latest passenger_list per flight
    pipeline = [
        {"$match": {**query}},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "origin": "$origin"},
            "doc": {"$first": "$$ROOT"},
        }},
    ]
    cursor = db["passenger_list"].aggregate(pipeline)

    rows = []
    for item in cursor:
        doc = item["doc"]
        fn = doc.get("flightNumber", "")
        origin = doc.get("origin", "")
        for pax in doc.get("passengers", []):
            rows.append({
                "flightNumber": fn,
                "origin": origin,
                "pnr": pax.get("pnr", ""),
                "lastName": pax.get("lastName", ""),
                "firstName": pax.get("firstName", ""),
                "cabin": pax.get("cabin", ""),
                "bookingClass": pax.get("bookingClass", ""),
                "isCheckedIn": bool(pax.get("isCheckedIn")),
                "isBoarded": bool(pax.get("isBoarded")),
                "seat": pax.get("seat", ""),
            })

    if not rows:
        return pl.DataFrame(schema={
            "flightNumber": pl.Utf8, "origin": pl.Utf8,
            "pnr": pl.Utf8, "lastName": pl.Utf8, "firstName": pl.Utf8,
            "cabin": pl.Utf8, "bookingClass": pl.Utf8,
            "isCheckedIn": pl.Boolean, "isBoarded": pl.Boolean,
            "seat": pl.Utf8,
        })

    return pl.DataFrame(rows)


# ── Comparison Engine ─────────────────────────────────────────────────────

def compare_flights_for_date(
    date: str,
    flight_numbers: list[str] | None = None,
    progress_callback: Callable | None = None,
) -> dict:
    """Compare Sabre vs OTP data for all flights on a given date.

    Returns a structured comparison report.
    """
    sabre_df = load_sabre_flights(date, flight_numbers)
    otp_df = load_otp_flights(date, flight_numbers)

    if progress_callback:
        progress_callback(1, 3, "Loaded data from both sources")

    # Join on flightNumber + origin
    joined = sabre_df.join(
        otp_df, on=["flightNumber", "origin"], how="full", coalesce=True)

    if progress_callback:
        progress_callback(2, 3, "Joined datasets")

    # Compute anomalies
    anomalies = []

    # 1. Flights in Sabre but not OTP
    sabre_only = joined.filter(pl.col("otp_status").is_null())
    for row in sabre_only.iter_rows(named=True):
        anomalies.append({
            "type": "SABRE_ONLY",
            "flightNumber": row["flightNumber"],
            "origin": row["origin"],
            "message": f"Flight exists in Sabre but not in OTP",
            "sabre_status": row.get("sabre_status"),
        })

    # 2. Flights in OTP but not Sabre
    otp_only = joined.filter(pl.col("sabre_status").is_null())
    for row in otp_only.iter_rows(named=True):
        anomalies.append({
            "type": "OTP_ONLY",
            "flightNumber": row["flightNumber"],
            "origin": row["origin"],
            "message": f"Flight exists in OTP but not in Sabre",
            "otp_status": row.get("otp_status"),
        })

    # 3. Passenger count mismatches (both sources have data)
    both = joined.filter(
        pl.col("sabre_status").is_not_null() & pl.col(
            "otp_status").is_not_null()
    )
    pax_mismatch = both.filter(
        (pl.col("sabre_totalPax") != pl.col("otp_totalPax"))
        & (pl.col("otp_totalPax") > 0)
    )
    for row in pax_mismatch.iter_rows(named=True):
        diff = (row.get("sabre_totalPax") or 0) - \
            (row.get("otp_totalPax") or 0)
        anomalies.append({
            "type": "PAX_COUNT_MISMATCH",
            "flightNumber": row["flightNumber"],
            "origin": row["origin"],
            "message": f"Sabre has {row.get('sabre_totalPax')} pax, OTP has {row.get('otp_totalPax')} (diff: {diff:+d})",
            "sabre_totalPax": row.get("sabre_totalPax"),
            "otp_totalPax": row.get("otp_totalPax"),
            "difference": diff,
        })

    # Summary stats
    summary = {
        "totalSabreFlights": len(sabre_df),
        "totalOtpFlights": len(otp_df),
        "matchedFlights": len(both),
        "sabreOnlyFlights": len(sabre_only),
        "otpOnlyFlights": len(otp_only),
        "paxCountMismatches": len(pax_mismatch),
    }

    if progress_callback:
        progress_callback(3, 3, "Comparison complete")

    return {
        "date": date,
        "comparedAt": _now_iso(),
        "flightsCompared": len(joined),
        "summary": summary,
        "anomalies": anomalies,
    }


def compare_passengers_across_flights(
    date: str,
    flight_numbers: list[str] | None = None,
) -> dict:
    """Detailed passenger-level comparison across multiple flights.

    Uses Polars for vectorized aggregation of cabin distribution,
    check-in rates, boarding rates, etc.
    """
    pax_df = load_sabre_passengers(date, flight_numbers)

    if pax_df.is_empty():
        return {"date": date, "flights": [], "summary": {}}

    # Per-flight aggregation
    flight_stats = pax_df.group_by("flightNumber", "origin").agg([
        pl.count().alias("totalPax"),
        pl.col("isCheckedIn").sum().alias("checkedIn"),
        pl.col("isBoarded").sum().alias("boarded"),
        pl.col("cabin").value_counts().alias("cabinDist"),
        pl.col("pnr").n_unique().alias("uniquePnrs"),
    ])

    flights = []
    for row in flight_stats.iter_rows(named=True):
        cabin_dist = {}
        for item in (row.get("cabinDist") or []):
            if isinstance(item, dict):
                cabin_dist[item.get("cabin", "?")] = item.get("count", 0)

        flights.append({
            "flightNumber": row["flightNumber"],
            "origin": row["origin"],
            "totalPax": row["totalPax"],
            "checkedIn": row["checkedIn"],
            "boarded": row["boarded"],
            "uniquePnrs": row["uniquePnrs"],
            "checkInRate": round(row["checkedIn"] / max(row["totalPax"], 1) * 100, 1),
            "boardingRate": round(row["boarded"] / max(row["totalPax"], 1) * 100, 1),
            "cabinDistribution": cabin_dist,
        })

    summary = {
        "totalFlights": len(flights),
        "totalPax": pax_df.height,
        "avgCheckInRate": round(
            sum(f["checkInRate"] for f in flights) / max(len(flights), 1), 1
        ),
        "avgBoardingRate": round(
            sum(f["boardingRate"] for f in flights) / max(len(flights), 1), 1
        ),
    }

    return {
        "date": date,
        "comparedAt": _now_iso(),
        "flights": flights,
        "summary": summary,
    }
