"""Flight-list API used by the analytics sidebar tests.

Invalid dates are refused by the product's validate_date. A missing date, or a
YYYY-MM-DD date, returns one stored departure so the sidebar can render it.
The Mongo aggregation behind the real list is not what these tests are about.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.api.validators import validate_date

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

seen: list[dict] = []


def _flight() -> dict:
    return {
        "airline": "GF",
        "flightNumber": "2057",
        "origin": "BAH",
        "destination": "LHR",
        "departureDate": os.environ["FIXTURE_DEPARTURE_DATE"],
        "status": "Scheduled",
        "gate": "",
        "aircraft": {"type": "", "registration": ""},
        "schedule": {},
        "passengerCounts": {},
        "jumpSeat": {},
        "fetchedAt": "2026-11-24T00:00:00Z",
    }


@app.get("/flights")
def list_flights(request: Request, date: str = Query(None)):
    raw_query = request.scope.get("query_string", b"").decode("latin-1")
    entry = {"path": "/flights", "date": date, "rawQuery": raw_query, "status": 200}
    seen.append(entry)
    try:
        validate_date(date)
    except HTTPException as exc:
        entry["status"] = exc.status_code
        raise
    return [_flight()]


@app.get("/__requests")
def requests_log():
    return seen


@app.post("/__reset")
def reset_requests():
    seen.clear()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["FLIGHT_LIST_PORT"]), log_level="warning")
