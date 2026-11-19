"""Home board default flight.

GET /flights (no date) is the list the operations home page loads. The home
board must not open a departure other than the one at the top of the flight
rail. The rail orders newest departure date first, then flight number
ascending. Opening nothing is also correct.

These tests fail while the home page copies the API's first row into the
selection. Run:

    pip install 'fastapi>=0.110,<1.0' 'pymongo>=4.6,<5.0' 'structlog>=24.1,<26.0' 'pydantic>=2.0,<3.0' httpx2 pytest
    cd frontend && npm install && npm install --no-save vitest@3.2.4 jsdom@26.1.0 @testing-library/react@16.3.0 @testing-library/dom@10.4.0 @testing-library/user-event@14.6.1
    PYTHONPATH=. pytest backend/tests/test_home_board_default_flight.py -q

Analytics, compare, and today do not auto-select a flight from this list.
They are not covered here.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.routes import flights as flights_module  # noqa: E402

FRONTEND = REPO_ROOT / "frontend"
VITEST = FRONTEND / "node_modules" / ".bin" / "vitest"


def _field(doc: dict, name: str):
    if name == "$":
        raise AssertionError(f"unsupported field path in {doc!r}")
    current = doc
    for part in name.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _eval_expr(doc: dict, expr):
    """Evaluate the aggregation expressions list_flights builds."""
    if isinstance(expr, str) and expr.startswith("$"):
        if expr == "$$ROOT":
            return doc
        return _field(doc, expr[1:])
    if isinstance(expr, dict) and len(expr) == 1:
        op, arg = next(iter(expr.items()))
        if op == "$ifNull":
            value_expr, default = arg
            value = _eval_expr(doc, value_expr)
            return default if value is None else value
        if op == "$literal":
            return arg
    if isinstance(expr, (str, int, float, bool)) or expr is None:
        return expr
    raise AssertionError(f"aggregation expression not implemented: {expr!r}")


def _delete_path(doc: dict, path: str) -> None:
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _apply_pipeline(docs: list[dict], pipeline: list[dict]) -> list[dict]:
    """Run the stages _latest_per_flight_agg and _latest_schedule_agg build.

    MongoDB is not running here. Each stage is the one those helpers append:
    optional $match, $sort fetchedAt descending, $group $first $$ROOT, $project
    exclusions, and the passenger pipeline's $addFields (a no-op on no rows).
    """
    rows = [copy.deepcopy(doc) for doc in docs]
    for stage in pipeline:
        if len(stage) != 1:
            raise AssertionError(f"unexpected aggregation stage: {stage!r}")
        op, spec = next(iter(stage.items()))
        if op == "$match":
            rows = [
                row
                for row in rows
                if all(row.get(key) == value for key, value in spec.items())
            ]
        elif op == "$sort":
            for field, direction in reversed(list(spec.items())):
                rows.sort(
                    key=lambda row, field=field: (
                        row.get(field) is None,
                        "" if row.get(field) is None else row.get(field),
                    ),
                    reverse=direction < 0,
                )
        elif op == "$group":
            if set(spec) != {"_id", "doc"} or spec["doc"] != {"$first": "$$ROOT"}:
                raise AssertionError(f"unexpected $group: {spec!r}")
            grouped: dict[tuple, dict] = {}
            order: list[tuple] = []
            for row in rows:
                identity = {
                    key: _eval_expr(row, expr) for key, expr in spec["_id"].items()
                }
                group_key = tuple(identity.items())
                if group_key in grouped:
                    continue
                grouped[group_key] = {"_id": identity, "doc": copy.deepcopy(row)}
                order.append(group_key)
            rows = [grouped[key] for key in order]
        elif op == "$project":
            projected = []
            for row in rows:
                cloned = copy.deepcopy(row)
                for path, flag in spec.items():
                    if flag != 0:
                        raise AssertionError(f"unexpected $project: {spec!r}")
                    _delete_path(cloned, path)
                projected.append(cloned)
            rows = projected
        elif op == "$addFields":
            if rows:
                raise AssertionError(
                    "passenger $addFields had rows; this stand-in only covers an empty passenger list"
                )
        else:
            raise AssertionError(f"aggregation stage not implemented: {op}")
    return rows


class _Collection:
    def __init__(self, docs: list[dict]):
        self.docs = docs

    def aggregate(self, pipeline, allowDiskUse=False):
        return _apply_pipeline(self.docs, pipeline)

    def find(self, query, projection=None):
        return [
            copy.deepcopy(doc)
            for doc in self.docs
            if all(doc.get(key) == value for key, value in query.items())
        ]


class _Database(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _Collection([])
        return dict.__getitem__(self, name)


def _status(
    flight_number: str,
    departure_date: str,
    gate: str,
    fetched_at: str,
    status: str,
    *,
    destination: str,
    boarding: bool,
) -> dict:
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": "BAH",
        "departureDate": departure_date,
        "status": status,
        "gate": gate,
        "fetchedAt": fetched_at,
        "boarding": {"indicator": "BDG" if boarding else ""},
        "aircraft": {"registration": f"A9C-{gate}"},
        "schedule": {"scheduledDeparture": f"{departure_date}T08:00AM"},
        "passengerCounts": {},
        "jumpSeat": {},
        "legs": [{"city": "BAH"}, {"city": destination}],
        "_raw": {"mustNotLeak": True},
    }


def _stored_flights() -> list[dict]:
    """Stored newest-departure first, with an older snapshot ahead of the latest one.

    list_flights must keep the latest fetchedAt snapshot, then the home page
    decides which of those departures to open.
    """
    return [
        _status("10", "2026-11-19", "G-STALE", "2026-11-01T00:00:00Z", "OPENCI", destination="DXB", boarding=True),
        _status("10", "2026-11-19", "G-NEW", "2026-11-20T12:00:00Z", "OPENCI", destination="DXB", boarding=True),
        _status("9", "2026-11-19", "G-NINE", "2026-11-20T11:00:00Z", "OPENCI", destination="CAI", boarding=True),
        _status("2", "2026-01-01", "G-OLD-STALE", "2026-01-01T01:00:00Z", "FINAL", destination="LHR", boarding=False),
        _status("2", "2026-01-01", "G-OLD", "2026-01-02T08:00:00Z", "FINAL", destination="LHR", boarding=False),
    ]


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(flights_module.router)
    return app


def _flight_list(monkeypatch) -> list[dict]:
    database = _Database()
    database["flight_status"] = _Collection(_stored_flights())
    database["passenger_list"] = _Collection([])
    database["flight_schedules"] = _Collection([])
    database["flights"] = _Collection([])
    monkeypatch.setattr(flights_module, "get_db", lambda: database)
    response = TestClient(_app()).get("/flights")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    return body


def test_list_keeps_latest_snapshot_for_each_departure(monkeypatch):
    flights = _flight_list(monkeypatch)
    by_number = {flight["flightNumber"]: flight for flight in flights}
    assert set(by_number) == {"10", "9", "2"}
    assert by_number["10"]["gate"] == "G-NEW"
    assert by_number["10"]["departureDate"] == "2026-11-19"
    assert by_number["10"]["destination"] == "DXB"
    assert by_number["2"]["gate"] == "G-OLD"
    assert by_number["2"]["departureDate"] == "2026-01-01"
    assert by_number["9"]["gate"] == "G-NINE"
    assert all(flight["gate"] not in {"G-STALE", "G-OLD-STALE"} for flight in flights)
    dates = {flight["departureDate"] for flight in flights}
    assert min(dates) != max(dates)


def test_home_board_opens_the_rail_head_or_nothing(monkeypatch, tmp_path):
    flights = _flight_list(monkeypatch)
    payload = tmp_path / "flights.json"
    payload.write_text(json.dumps(flights))
    if not VITEST.exists():
        raise AssertionError(
            "vitest is not installed. From frontend/: "
            "npm install && npm install --no-save vitest@3.2.4 jsdom@26.1.0 "
            "@testing-library/react@16.3.0 @testing-library/dom@10.4.0 "
            "@testing-library/user-event@14.6.1"
        )
    env = os.environ.copy()
    env["HOME_BOARD_FLIGHTS_FILE"] = str(payload)
    completed = subprocess.run(
        [
            str(VITEST),
            "run",
            "--config",
            "tests/vitest.config.ts",
            "tests/home-board-default-flight.test.tsx",
        ],
        cwd=FRONTEND,
        env=env,
        check=False,
    )
    assert completed.returncode == 0
