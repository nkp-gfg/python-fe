"""In-memory OTP and Sabre rows behind the real data-audit routes.

The flight stored here is numeric 2152. Gate, scheduled departure, and
passenger totals differ between PostgreSQL and MongoDB so a successful
comparison is observable. This module is test-only.
"""

from __future__ import annotations

import sys
import threading
from copy import deepcopy
from datetime import datetime

from fastapi import FastAPI

import backend.api.routes.data_audit as data_audit_routes

_LOCK = threading.Lock()
LOOKUPS: list[dict] = []
REQUESTS: list[dict] = []

_SEEDED_FLIGHT = "2152"
_SEEDED_ORIGIN = "BAH"
_SEEDED_DATE = "2026-03-19"

_PG_ROW = {
    "flight_number": _SEEDED_FLIGHT,
    "flight_sequence_number": 1,
    "scheduled_origin": _SEEDED_ORIGIN,
    "scheduled_destination": "LHR",
    "flight_date": datetime(2026, 3, 19).date(),
    "flight_status": "SCH",
    "aircraft_type": "32N",
    "aircraft_registration": "A9C-DA",
    "scheduled_departure_utc": datetime(2026, 3, 19, 8, 5),
    "scheduled_arrival_utc": datetime(2026, 3, 19, 14, 0),
    "actual_block_off_utc": None,
    "actual_block_on_utc": None,
    "estimated_block_off_utc": None,
    "estimated_block_on_utc": None,
    "actual_origin": _SEEDED_ORIGIN,
    "actual_destination": "LHR",
    "service_type_code": "J",
    "passenger_counts": [{"@classOfService": "Y", "Count": 137}],
}

_MONGO_FLIGHT = {
    "flightNumber": _SEEDED_FLIGHT,
    "origin": _SEEDED_ORIGIN,
    "destination": "LHR",
    "departureDate": _SEEDED_DATE,
    "flightStatus": "SCH",
    "aircraftType": "32N",
    "aircraftRegistration": "A9C-DA",
    "scheduledDepartureUtc": "2026-03-19T09:41:00",
    "scheduledArrivalUtc": "2026-03-19T14:00:00",
    "totalPassengers": 96,
    "departureGate": "G14",
    "actualOrigin": _SEEDED_ORIGIN,
    "actualDestination": "LHR",
    "serviceType": "J",
}

_MONGO_PAX = {
    "flightNumber": _SEEDED_FLIGHT,
    "origin": _SEEDED_ORIGIN,
    "departureDate": _SEEDED_DATE,
    "totalPassengers": 96,
    "passengers": [],
    "cabinSummary": [
        {"cabin": "Y", "count": 96, "authorized": 150, "available": 54}
    ],
}


def _seeded(flight_number, origin, flight_date) -> bool:
    if flight_number != _SEEDED_FLIGHT:
        return False
    if origin is not None and origin != _SEEDED_ORIGIN:
        return False
    if flight_date is not None and str(flight_date) != _SEEDED_DATE:
        return False
    return True


def _record(**entry) -> None:
    with _LOCK:
        LOOKUPS.append(entry)


def fake_query_all(sql, params=None):
    params = tuple(params or ())
    flight_number = params[0] if params else None
    extra = list(params[1:])
    seq = None
    flight_date = None
    origin = None
    if "flight_sequence_number = %s" in sql and extra:
        seq = extra.pop(0)
    if "flight_date = %s" in sql and extra:
        flight_date = extra.pop(0)
    if "scheduled_origin = %s" in sql and extra:
        origin = extra.pop(0)
    _record(
        store="postgres",
        flightNumber=flight_number,
        origin=origin,
        date=None if flight_date is None else str(flight_date),
        seq=seq,
    )
    if _seeded(flight_number, origin, flight_date):
        return [deepcopy(_PG_ROW)]
    return []


class _Collection:
    def __init__(self, name: str, document: dict):
        self.name = name
        self.document = document

    def find_one(self, query, sort=None):
        flight_number = query.get("flightNumber")
        origin = query.get("origin")
        flight_date = query.get("departureDate")
        _record(
            store=self.name,
            flightNumber=flight_number,
            origin=origin,
            date=flight_date,
        )
        if _seeded(flight_number, origin, flight_date):
            return deepcopy(self.document)
        return None


class _Database:
    def __getitem__(self, name: str):
        if name == "flight_status":
            return _Collection("flight_status", _MONGO_FLIGHT)
        if name == "passenger_list":
            return _Collection("passenger_list", _MONGO_PAX)
        raise KeyError(name)


def fake_get_db():
    return _Database()


data_audit_routes.query_all = fake_query_all
data_audit_routes.get_db = fake_get_db

app = FastAPI()
app.include_router(data_audit_routes.router)


@app.middleware("http")
async def record_data_audit_requests(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/data-audit/"):
        with _LOCK:
            REQUESTS.append(
                {
                    "path": request.url.path,
                    "query": request.url.query,
                    "status": response.status_code,
                }
            )
    return response


@app.get("/__test__/observations")
def observations():
    with _LOCK:
        return {"lookups": list(LOOKUPS), "requests": list(REQUESTS)}


@app.post("/__test__/reset")
def reset_observations():
    with _LOCK:
        LOOKUPS.clear()
        REQUESTS.clear()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1])
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
