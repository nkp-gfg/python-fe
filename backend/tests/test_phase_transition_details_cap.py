"""Phase-journey Transition Details must not drop a smaller status reversal.

An unauthenticated caller loads consecutive passenger snapshots through
GET /flights/{flight_number}/phase-journey. The handler returns every
booked / checked-in / boarded combination. The Transition Details card is
rendered from that payload.

The test fails while the card prints only six of those rows, ordered by
count, and leaves a boarded → booked reversal off the card with no remainder.
"""

import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.main import app
from backend.api.routes import flights


FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"

# Distinct counts so the largest six are unchanged passengers and forward
# moves. The boarded → booked reversal is the smallest real status change.
# Added and removed passengers are larger still, and are split out before
# the card's row cap.
MOVEMENTS = {
    ("booked", "booked"): 30,
    ("checkedIn", "checkedIn"): 28,
    ("boarded", "boarded"): 26,
    ("booked", "checkedIn"): 24,
    ("booked", "boarded"): 22,
    ("checkedIn", "boarded"): 20,
    ("checkedIn", "booked"): 18,
    ("boarded", "checkedIn"): 16,
    ("boarded", "booked"): 2,
}
ADDED = 40
REMOVED = 35


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=None):
        if isinstance(key, list):
            spec = key
        else:
            spec = [(key, 1 if direction is None else direction)]
        for field, direc in reversed(spec):
            self._docs.sort(key=lambda doc: doc.get(field, 0), reverse=direc < 0)
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query, *args, **kwargs):
        return _Cursor(doc for doc in self._docs if _matches(doc, query))

    def find_one(self, query, *args, **kwargs):
        docs = [doc for doc in self._docs if _matches(doc, query)]
        sort = kwargs.get("sort")
        if sort:
            for field, direc in reversed(sort):
                docs.sort(key=lambda doc: doc.get(field, ""), reverse=direc < 0)
        return docs[0] if docs else None


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.get(name, _Collection([]))


def _matches(doc, query):
    for key, expected in query.items():
        if doc.get(key) != expected:
            return False
    return True


def _flags(state):
    if state == "boarded":
        return {"isBoarded": True, "isCheckedIn": True}
    if state == "checkedIn":
        return {"isBoarded": False, "isCheckedIn": True}
    return {"isBoarded": False, "isCheckedIn": False}


def _passenger(index, state):
    # Fields the passenger-list converter stores and the transition helper reads.
    return {
        "pnr": f"P{index:04d}",
        "lastName": "DOE",
        "firstName": f"N{index}",
        "cabin": "Y",
        "isRevenue": True,
        "passengerType": "F",
        "editCodes": [],
        "isChild": False,
        "hasInfant": False,
        **_flags(state),
    }


def _snapshots():
    prev = []
    curr = []
    index = 0
    for (before, after), count in MOVEMENTS.items():
        for _ in range(count):
            index += 1
            prev.append(_passenger(index, before))
            curr.append(_passenger(index, after))
    for _ in range(REMOVED):
        index += 1
        prev.append(_passenger(index, "boarded"))
    for _ in range(ADDED):
        index += 1
        curr.append(_passenger(index, "booked"))

    def snapshot(snapshot_type, sequence, data):
        return {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "snapshotType": snapshot_type,
            "sequenceNumber": sequence,
            "capturedAt": f"2026-03-19T1{sequence}:00:00Z",
            "data": data,
        }

    return [
        snapshot("flight_status", 1, {"status": "OPENCI", "boarding": {"indicator": "BDG"}, "gate": "A1"}),
        snapshot("passenger_list", 1, {"passengers": prev}),
        snapshot("flight_status", 2, {"status": "FINAL", "boarding": {"indicator": ""}, "gate": "A1"}),
        snapshot("passenger_list", 2, {"passengers": curr}),
    ]


def _flow(body, from_state, to_state):
    flows = body["transitions"][0]["flows"]
    matches = [flow for flow in flows if flow["fromState"] == from_state and flow["toState"] == to_state]
    assert len(matches) == 1, flows
    return matches[0]


def test_phase_journey_card_keeps_status_reversal_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(flights, "get_db", lambda: _Database({"snapshots": _Collection(_snapshots())}))

    with TestClient(app) as client:
        response = client.get(
            f"/flights/{FLIGHT}/phase-journey",
            params={"origin": ORIGIN, "date": DATE},
        )

    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    body = response.json()

    assert len(body["phases"]) >= 2
    assert len(body["transitions"]) == 1
    transition = body["transitions"][0]
    assert transition["fromPhase"] == "BOARDING"
    assert transition["toPhase"] == "CLOSED"
    assert _flow(body, "boarded", "booked")["count"] == MOVEMENTS[("boarded", "booked")]
    assert _flow(body, "new", "booked")["count"] == ADDED
    assert _flow(body, "boarded", "removed")["count"] == REMOVED
    status_flows = [
        flow
        for flow in transition["flows"]
        if flow["fromState"] != "new" and flow["toState"] != "removed"
    ]
    assert len(status_flows) == len(MOVEMENTS)

    payload = tmp_path / "phase-journey.json"
    payload.write_text(json.dumps(body))
    repo = os.path.join(os.path.dirname(__file__), "..", "..")
    frontend = os.path.join(repo, "frontend")
    renderer = os.path.join(frontend, "tests", "phase-transition-details-card.test.tsx")
    completed = subprocess.run(
        ["npx", "--yes", "tsx", "--tsconfig", "tsconfig.json", renderer, str(payload)],
        cwd=frontend,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
