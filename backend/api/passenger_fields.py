"""Passenger fields that stay internal to the API.

Sabre manifests carry staff seniority dates and corporate account identifiers.
They are stored for queue ordering and audit rules, but are never returned to
API callers.
"""

WORKFORCE_FIELDS = frozenset({"corpId", "seniorityDate"})


def public_passenger(passenger: dict) -> dict:
    """Return a copy of a stored passenger record without workforce fields."""
    return {k: v for k, v in passenger.items() if k not in WORKFORCE_FIELDS}
