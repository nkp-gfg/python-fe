"""Shared input validators for API route parameters."""

import re
from datetime import date

from fastapi import HTTPException

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IATA_AIRPORT_RE = re.compile(r"^[A-Z]{3}$")
_FLIGHT_NUMBER_RE = re.compile(r"^\d{1,5}$")
_AIRLINE_RE = re.compile(r"^[A-Z0-9]{2}$")


def validate_date(value: str | None, param_name: str = "date") -> None:
    """Raise 400 if date is present but not YYYY-MM-DD."""
    if value is not None and not _DATE_RE.fullmatch(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {param_name}: expected YYYY-MM-DD format",
        )


def validate_date_range(date_from: str, date_to: str, max_days: int) -> None:
    """Raise 400 unless both dates are real calendar dates, in order, and
    span at most max_days days (inclusive)."""
    try:
        if not (_DATE_RE.fullmatch(date_from) and _DATE_RE.fullmatch(date_to)):
            raise ValueError
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date range: expected YYYY-MM-DD dates",
        )
    if start > end:
        raise HTTPException(
            status_code=400, detail="date_from must be before date_to")
    if (end - start).days + 1 > max_days:
        raise HTTPException(
            status_code=400,
            detail=f"Date range cannot exceed {max_days} days",
        )


def validate_origin(value: str | None) -> None:
    """Raise 400 if origin is present but not a 3-letter IATA code."""
    if value is not None and not _IATA_AIRPORT_RE.match(value.upper()):
        raise HTTPException(
            status_code=400,
            detail="Invalid origin: expected 3-letter IATA airport code",
        )


def validate_flight_number(value: str) -> None:
    """Raise 400 if flight number is not 1-5 digits."""
    if not _FLIGHT_NUMBER_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="Invalid flight number: expected 1-5 digits",
        )


def validate_airline(value: str) -> None:
    """Raise 400 if airline code is not exactly 2 alphanumeric chars."""
    if not _AIRLINE_RE.match(value.upper()):
        raise HTTPException(
            status_code=400,
            detail="Invalid airline code: expected 2-character IATA code",
        )
