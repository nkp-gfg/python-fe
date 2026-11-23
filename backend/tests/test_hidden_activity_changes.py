"""Live Ops activity card hides low-count operational changes.

These tests follow the product path: Sabre-shaped payloads are normalized by
the feeder converters, diffed by detect_changes (the function the ingest
runner stores), counted by the unauthenticated dashboard route, then rendered
by the Live Ops activity card and the change-velocity panel.

They fail while gate, status, and terminal changes are dropped once four or
more higher-volume passenger change types fill the card.
"""

import copy
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.feeder.differ import detect_changes

FLIGHT = "4317"
ORIGIN = "BAH"
DATE = "2026-11-23"
OPERATIONAL = ("GATE_CHANGE", "STATUS_CHANGE", "TERMINAL_CHANGE")
PASSENGER_VOLUME = (
    "CHECKED_IN",
    "BOARDED",
    "SEAT_CHANGE",
    "BAG_COUNT_CHANGE",
)


def _passenger(index, *, checked_in, boarded, cabin, booking_class, seat, bags, passenger_type, priority, edit_codes):
    return {
        "Name_Details": {"LastName": f"LAST{index}", "FirstName": "ANN"},
        "PNRLocator": {"#text": f"PN{index:04d}"},
        "PassengerID": str(index),
        "LineNumber": str(index),
        "PriorityCode": priority,
        "BookingClass": booking_class,
        "Cabin": cabin,
        "Seat": seat,
        "PassengerType": passenger_type,
        "BagCount": str(bags),
        "CheckIn_Info": {"CheckInStatus": "true" if checked_in else "false"},
        "Boarding_Info": {"BoardStatus": "true" if boarded else "false"},
        "EditCodeList": {"EditCode": list(edit_codes)},
        "Indicators": {"Indicator": ["Revenue"]},
    }


def _passenger_payload(phase):
    """Two passengers. The later snapshot checks them in, boards them, and
    changes seat, bag, cabin, class, type, priority, and edit codes."""
    if phase == "before":
        specs = [
            _passenger(i, checked_in=False, boarded=False, cabin="Y", booking_class="V",
                       seat=f"{10 + i}A", bags=0, passenger_type="F", priority="", edit_codes=[])
            for i in (1, 2)
        ]
    else:
        specs = [
            _passenger(i, checked_in=True, boarded=True, cabin="J", booking_class="C",
                       seat=f"{i}A", bags=1, passenger_type="E", priority="UPG",
                       edit_codes=["FF", "DOCS"])
            for i in (1, 2)
        ]
    return {"PassengerInfoList": {"PassengerInfo": specs}}


def _status_payload(status, gate, terminal):
    return {
        "ItineraryResponseList": {
            "ItineraryInfoResponse": {
                "FlightStatus": status,
                "DepartureGate": gate,
                "DepartureTerminal": terminal,
                "ScheduledDepartureDate": DATE,
            }
        }
    }


def _match(doc, filt):
    for key, expected in (filt or {}).items():
        if doc.get(key) != expected:
            return False
    return True


class _Collection:
    """Equality filter plus the cursor/index calls this request path makes.

    fetch_change_summary iterates find() results and counts changeType.
    find_one is used for the current flight documents. Startup calls
    index_information/create_index/drop_index and ignores their results.
    """

    def __init__(self, name, docs):
        self.name = name
        self.docs = docs

    def find(self, filt=None, projection=None, *args, **kwargs):
        return [copy.deepcopy(doc) for doc in self.docs if _match(doc, filt)]

    def find_one(self, filt=None, *args, **kwargs):
        matches = self.find(filt)
        sort = kwargs.get("sort")
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc, field=field: doc.get(field) or "",
                    reverse=direction < 0,
                )
        return matches[0] if matches else None

    def index_information(self):
        if self.name == "flight_status":
            return {"flight_lookup": {}}
        return {}

    def drop_index(self, name):
        return None

    def create_index(self, keys, **kwargs):
        return kwargs.get("name")


class _Db:
    def __init__(self, collections):
        self._collections = {
            name: _Collection(name, docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection(name, [])
        return self._collections[name]

    def command(self, name, *args, **kwargs):
        return {"ok": 1}


class _Redis:
    def ping(self):
        return True


def _build_changes():
    flight_info = {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
    }
    before_pax = convert_passenger_list(
        _passenger_payload("before"), "GF", FLIGHT, DATE, ORIGIN)
    after_pax = convert_passenger_list(
        _passenger_payload("after"), "GF", FLIGHT, DATE, ORIGIN)
    before_status = convert_flight_status(
        _status_payload("OPENCI", "A1", "1"), "GF", FLIGHT, ORIGIN, DATE)
    after_status = convert_flight_status(
        _status_payload("FINAL", "B4", "2"), "GF", FLIGHT, ORIGIN, DATE)

    passenger_changes = detect_changes(
        {"snapshotId": "pax-before", "data": before_pax},
        {"snapshotId": "pax-after", "data": after_pax},
        "passenger_list",
        flight_info,
    )
    status_changes = detect_changes(
        {"snapshotId": "status-before", "data": before_status},
        {"snapshotId": "status-after", "data": after_status},
        "flight_status",
        flight_info,
    )
    return passenger_changes + status_changes, after_pax, after_status


def _dashboard_response():
    from fastapi.testclient import TestClient
    import backend.api.main as mainmod
    import backend.api.routes.flights as flightsmod

    changes, passenger_list, flight_status = _build_changes()
    db = _Db({
        "changes": changes,
        "passenger_list": [passenger_list],
        "flight_status": [flight_status],
    })
    flightsmod.get_db = lambda: db
    flightsmod._dashboard_cache.clear()
    mainmod.get_db = lambda: db
    mainmod.get_redis = lambda: _Redis()

    with TestClient(mainmod.app) as client:
        response = client.get(
            f"/flights/{FLIGHT}/dashboard",
            params={"origin": ORIGIN, "date": DATE},
        )
    return response


def test_unauthenticated_dashboard_counts_operational_changes():
    response = _dashboard_response()
    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    summary = response.json()["changeSummary"]

    for change_type in PASSENGER_VOLUME:
        assert summary[change_type] >= 2
    for change_type in OPERATIONAL:
        assert summary[change_type] == 1

    higher = [name for name, count in summary.items() if count > summary["GATE_CHANGE"]]
    assert len(higher) >= 4


def _render(dashboard):
    payload = {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "date": DATE,
        "dashboard": dashboard,
    }
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    payload_path = os.path.join(repo_root, "frontend", "tests", ".activity-payload.json")
    with open(payload_path, "w", encoding="utf8") as handle:
        json.dump(payload, handle)
    try:
        completed = subprocess.run(
            ["node", "frontend/tests/render-live-ops-activity.mjs", payload_path],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        if os.path.exists(payload_path):
            os.remove(payload_path)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_live_ops_activity_keeps_gate_status_and_terminal_visible():
    response = _dashboard_response()
    assert response.status_code == 200, response.text
    body = response.json()
    rendered = _render(body)

    assert rendered["workbenchHasActivityHeading"], rendered["activityText"]
    activity = rendered["activityText"]
    assert "No recent activity" not in activity
    visible = rendered["activity"]
    missing = [name for name in OPERATIONAL if not visible.get(name)]
    assert not missing, (
        "Live Ops activity card omitted operational changes "
        f"{missing}. Visible text: {activity}"
    )


def test_change_velocity_keeps_gate_status_and_terminal_visible():
    response = _dashboard_response()
    assert response.status_code == 200, response.text
    body = response.json()
    rendered = _render(body)

    assert rendered["insightsHasVelocityHeading"], rendered["velocityText"]
    visible = rendered["velocity"]
    missing = [name for name in OPERATIONAL if not visible.get(name)]
    assert not missing, (
        "Change velocity panel omitted operational changes "
        f"{missing}. Visible text: {rendered['velocityText']}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
