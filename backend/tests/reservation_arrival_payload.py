"""Build the reservations JSON the flight board actually receives.

The sample Trip Search response is parsed with the Sabre client's XML parser,
normalized by ``convert_reservations``, then filtered by the same FastAPI
response field ``GET /flights/{flight_number}/reservations`` uses. The result
is what ``fetchReservations`` decodes. Nothing in this module prints the
sample file.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

from fastapi.routing import serialize_response

from backend.api.routes import reservations as reservations_route
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_XML = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

FLIGHT_NUMBER = "2057"
ORIGIN = "BOM"
DEPARTURE_DATE = "2026-03-18"


class _StoredReservations:
    """Stand-in for the ``reservations`` collection. Returns one stored document."""

    def __init__(self, document: dict):
        self._document = document

    def __getitem__(self, name: str):
        if name != "reservations":
            raise KeyError(name)
        return self

    def find_one(self, query, sort=None):
        if query.get("flightNumber") not in (None, FLIGHT_NUMBER):
            return None
        if query.get("departureAirport") not in (None, ORIGIN):
            return None
        if query.get("departureDate") not in (None, DEPARTURE_DATE):
            return None
        pnr = query.get("reservations.pnr")
        if pnr is not None and not any(
            reservation.get("pnr") == pnr
            for reservation in self._document.get("reservations", [])
        ):
            return None
        return self._document


def converted_gf2057_document() -> dict:
    """Run the sample SOAP envelope through the product's parser and converter."""
    parsed = SabreClient._parse_xml(SAMPLE_XML.read_text(encoding="utf-8"))
    # _extract_body does not use the session. Calling it on the class avoids
    # SabreClient(), which reads credential environment variables.
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(
        body, "GF", FLIGHT_NUMBER, ORIGIN, DEPARTURE_DATE
    )


def _list_route():
    for route in reservations_route.router.routes:
        if getattr(route, "path", None) == "/flights/{flight_number}/reservations":
            return route
    raise RuntimeError("reservations list route is not registered")


def reservations_list_response(document: dict | None = None) -> dict:
    """Return the JSON object FastAPI would send for the reservations list.

    ``snapshot_sequence`` is passed as ``None`` because that is the value
    FastAPI injects when the query string omits it (the Reservations tab).
    """
    stored = copy.deepcopy(document if document is not None else converted_gf2057_document())
    original_get_db = reservations_route.get_db
    reservations_route.get_db = lambda: _StoredReservations(stored)
    try:
        raw = reservations_route.get_reservations(
            FLIGHT_NUMBER,
            origin=ORIGIN,
            date=DEPARTURE_DATE,
            snapshot_sequence=None,
        )
    finally:
        reservations_route.get_db = original_get_db

    route = _list_route()
    rendered = asyncio.run(
        serialize_response(
            field=route.response_field,
            response_content=raw,
            is_coroutine=False,
        )
    )
    if not isinstance(rendered, dict):
        raise TypeError(f"expected a JSON object, got {type(rendered).__name__}")
    return rendered


def reservation_by_pnr(pnr: str, document: dict | None = None) -> dict:
    """Return the body of ``GET /flights/{flight_number}/reservations/{pnr}``."""
    stored = copy.deepcopy(document if document is not None else converted_gf2057_document())
    original_get_db = reservations_route.get_db
    reservations_route.get_db = lambda: _StoredReservations(stored)
    try:
        return reservations_route.get_reservation_by_pnr(
            FLIGHT_NUMBER,
            pnr,
            date=DEPARTURE_DATE,
        )
    finally:
        reservations_route.get_db = original_get_db


def main() -> None:
    json.dump(reservations_list_response(), fp=sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
