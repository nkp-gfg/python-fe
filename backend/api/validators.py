"""Shared input validators for API route parameters."""

import re
from fastapi import HTTPException

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IATA_AIRPORT_RE = re.compile(r"^[A-Z]{3}$")
_FLIGHT_NUMBER_RE = re.compile(r"^\d{1,5}$")
_AIRLINE_RE = re.compile(r"^[A-Z0-9]{2}$")


def validate_date(value: str | None, param_name: str = "date") -> None:
    """Raise 400 if date is present but not YYYY-MM-DD."""
    if value is not None and not _DATE_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {param_name}: expected YYYY-MM-DD format",
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
