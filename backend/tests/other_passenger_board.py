"""Build the live-operations board from the code that produces it.

The dashboard counts and the passenger manifest are the same objects the
API returns. Trip-report names go through ``convert_trip_report`` and
``merge_trip_reports`` so the identities are the ones the feeder stores.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import importlib.util

from backend.api.routes.flights import _build_dashboard_payload

# Load the converter module directly. Importing backend.feeder runs the
# feeder package init, which pulls the Sabre client and its HTTP stack.
# The functions below are the ones the feeder uses to store trip reports.
_converter_path = os.path.join(ROOT, "backend", "feeder", "converter.py")
_spec = importlib.util.spec_from_file_location("falconeye_feeder_converter", _converter_path)
_converter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_converter)
convert_trip_report = _converter.convert_trip_report
merge_trip_reports = _converter.merge_trip_reports

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-04-02"
AIRLINE = "GF"


def display_name(last_name: str, first_name: str) -> str:
    return f"{last_name}, {first_name}"


def manifest_passenger(
    last_name: str,
    first_name: str,
    pnr: str,
    *,
    checked_in: bool,
    boarded: bool,
    revenue: bool,
    seat: str,
    bags: int,
):
    """One GetPassengerList row, with the fields the passenger table reads."""
    return {
        "lastName": last_name,
        "firstName": first_name,
        "pnr": pnr,
        "passengerId": pnr,
        "lineNumber": 1,
        "priorityCode": "",
        "bookingClass": "Y",
        "desiredBookingClass": "",
        "cabin": "Y",
        "seat": seat,
        "destination": "LHR",
        "passengerType": "E" if not revenue else "F",
        "isStandby": False,
        "corpId": "",
        "seniorityDate": "",
        "bagCount": bags,
        "isCheckedIn": checked_in or boarded,
        "isBoarded": boarded,
        "boardingPassIssued": boarded,
        "checkInSequence": 1 if (checked_in or boarded) else 0,
        "isRevenue": revenue,
        "isThru": False,
        "isChild": False,
        "hasInfant": False,
        "vcrType": "",
        "ticketNumber": "",
        "editCodes": [],
        "nationality": "BH",
        "groupCode": "",
        "isGroup": False,
        "isUnnamedGroup": False,
        "specialMeal": "",
        "wheelchairCode": "",
        "hasEmergencyContact": False,
        "ffTierName": "",
    }


def manifest_roster():
    """Four travelers still on the current manifest, in distinct states.

    ALI is checked in and not boarded, so he is still expected to fly.
    BAKER is booked and has not checked in, so he is still on the manifest.
    CREW is non-revenue staff already boarded.
    DANI is a revenue passenger already boarded.
    """
    return [
        manifest_passenger(
            "ALI", "AMIN", "ALI001",
            checked_in=True, boarded=False, revenue=True, seat="14A", bags=2,
        ),
        manifest_passenger(
            "BAKER", "BEN", "BAK001",
            checked_in=False, boarded=False, revenue=True, seat="15C", bags=1,
        ),
        manifest_passenger(
            "CREW", "SAM", "CRW001",
            checked_in=True, boarded=True, revenue=False, seat="1A", bags=0,
        ),
        manifest_passenger(
            "DANI", "DANA", "DAN001",
            checked_in=True, boarded=True, revenue=True, seat="16A", bags=1,
        ),
    ]


def _report(passengers, report_type):
    raw = {
        "PassengerList": {
            "Passenger": [
                {
                    "Name": {"LastName": p["lastName"], "FirstName": p["firstName"]},
                    "PNRLocator": p["pnr"],
                    "PassengerType": p.get("passengerType", "F"),
                    "Cabin": "Y",
                    "Seat": "",
                    "BookingClass": "Y",
                }
                for p in passengers
            ]
        }
    }
    return convert_trip_report(raw, AIRLINE, FLIGHT, DATE, ORIGIN, report_type)


def trip_report(cancelled, ever_booked):
    mlx = _report(cancelled, "MLX") if cancelled is not None else None
    mlc = _report(ever_booked, "MLC") if ever_booked is not None else None
    if mlx is None and mlc is None:
        return None
    return merge_trip_reports(mlx, mlc, AIRLINE, FLIGHT, DATE, ORIGIN)


def absent_from_manifest(ever_booked, manifest):
    """Same key the dashboard uses: (pnr, lastName upper)."""
    current = {
        (p.get("pnr", ""), p.get("lastName", "").upper())
        for p in manifest
    }
    missing = []
    for person in ever_booked:
        key = (person.get("pnr", ""), person.get("lastName", "").upper())
        if key not in current:
            missing.append(person)
    return missing


def flight_status(status: str, cockpit: int, cabin: int):
    return {
        "airline": AIRLINE,
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": "2026-04-02T18:00:00Z",
        "status": status,
        "gate": "A12",
        "aircraft": {"type": "789", "registration": "A9C-FE"},
        "schedule": {
            "scheduledDeparture": "2026-04-02T19:00:00",
            "scheduledArrival": "2026-04-02T23:30:00",
        },
        "boarding": {"time": "", "indicator": ""},
        "jumpSeat": {
            "cockpit": cockpit,
            "cabin": cabin,
            "cockpitInUse": cockpit > 0,
            "cabinInUse": cabin > 0,
        },
        "legs": [
            {"city": ORIGIN, "controllingCity": True, "status": status},
            {"city": "LHR", "controllingCity": False, "status": status},
        ],
        "passengerCounts": {},
        "remarks": [],
        "codeshareInfo": [],
        "freeTextRemarks": [],
    }


def passenger_list(passengers):
    return {
        "airline": AIRLINE,
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": "2026-04-02T18:00:00Z",
        "destination": "LHR",
        "departureGate": "A12",
        "totalPassengers": len(passengers),
        "adultCount": len(passengers),
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": len(passengers),
        "cabinSummary": [
            {"cabin": "Y", "count": len(passengers), "authorized": 150, "destination": "LHR"}
        ],
        "passengers": passengers,
        "groupBookings": [],
    }


def flight_list_item(status_doc):
    return {
        "airline": AIRLINE,
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "destination": "LHR",
        "departureDate": DATE,
        "status": status_doc["status"],
        "gate": status_doc["gate"],
        "aircraft": status_doc["aircraft"],
        "schedule": status_doc["schedule"],
        "passengerCounts": {},
        "jumpSeat": status_doc["jumpSeat"],
        "fetchedAt": status_doc["fetchedAt"],
    }


def names(people):
    return [display_name(p["lastName"], p["firstName"]) for p in people]


def build_board(
    *,
    status: str,
    cockpit: int,
    cabin: int,
    cancelled,
    ever_booked,
):
    manifest = manifest_roster()
    report = None
    if cancelled is not None or ever_booked is not None:
        report = trip_report(cancelled or [], ever_booked or [])
    status_doc = flight_status(status, cockpit, cabin)
    plist = passenger_list(manifest)
    dashboard = _build_dashboard_payload(
        status_doc,
        plist,
        ORIGIN,
        DATE,
        {},
        reservation_doc=None,
        trip_report_doc=report,
        schedule_doc=None,
    )
    cancelled_people = list(cancelled or [])
    ever_booked_people = list(ever_booked or [])
    no_shows = absent_from_manifest(ever_booked_people, manifest) if report else []
    checked_in_not_boarded = [
        p for p in manifest if p["isCheckedIn"] and not p["isBoarded"]
    ]
    not_checked_in = [
        p for p in manifest if not p["isCheckedIn"] and not p["isBoarded"]
    ]
    non_revenue = [p for p in manifest if not p["isRevenue"]]
    others = dashboard["stateSummary"]["others"]
    return {
        "flight": flight_list_item(status_doc),
        "dashboard": dashboard,
        "passengers": plist,
        "expected": {
            "cancelled": names(cancelled_people),
            "cancelledPnrs": [p["pnr"] for p in cancelled_people],
            "noShows": names(no_shows),
            "noShowPnrs": [p["pnr"] for p in no_shows],
            "checkedInNotBoarded": names(checked_in_not_boarded),
            "notCheckedIn": names(not_checked_in),
            "nonRevenue": names(non_revenue),
            "manifest": names(manifest),
            "jumpSeat": others["jumpSeat"],
            "offloadedCount": others["offloaded"],
            "noShowCount": others["noShow"],
            "offloadedAvailable": others["offloadedAvailable"],
            "noShowAvailable": others["noShowAvailable"],
            "flightClosed": others["flightClosed"],
            "checkedInNotBoardedCount": others["checkedInNotBoarded"],
            "notCheckedInCount": others["notCheckedIn"],
        },
    }


REMOVED = {"lastName": "REMOVED", "firstName": "RIA", "pnr": "RMV001", "passengerType": "F"}
GHOST = {"lastName": "GHOST", "firstName": "GIL", "pnr": "GHO001", "passengerType": "F"}


def _ever_booked(extra):
    roster = [
        {"lastName": p["lastName"], "firstName": p["firstName"], "pnr": p["pnr"], "passengerType": p["passengerType"]}
        for p in manifest_roster()
    ]
    return roster + extra


SCENARIOS = {
    # Trip report is present and the flight is closed. Offloaded is the
    # cancelled list. No-show is every ever-booked name missing from the
    # manifest. Jump seat is zero while a staff passenger is on the manifest.
    "authoritative": lambda: build_board(
        status="FINAL",
        cockpit=0,
        cabin=0,
        cancelled=[REMOVED],
        ever_booked=_ever_booked([REMOVED, GHOST]),
    ),
    # Jump-seat seats are in use. They are a flight-status count, not the
    # non-revenue manifest.
    "jump-seats": lambda: build_board(
        status="FINAL",
        cockpit=1,
        cabin=2,
        cancelled=[REMOVED],
        ever_booked=_ever_booked([REMOVED, GHOST]),
    ),
    # Authoritative counts of zero must not fall through to manifest filters.
    "empty-exceptions": lambda: build_board(
        status="FINAL",
        cockpit=0,
        cabin=0,
        cancelled=[],
        ever_booked=_ever_booked([]),
    ),
    # No trip report. The figures are inferred manifest counts.
    "inferred": lambda: build_board(
        status="FINAL",
        cockpit=0,
        cabin=0,
        cancelled=None,
        ever_booked=None,
    ),
    # Check-in is still open. Offloaded still comes from the cancelled list.
    # No-show is not authoritative yet, so the row is the inferred manifest.
    "open-checkin": lambda: build_board(
        status="OPENCI",
        cockpit=0,
        cabin=0,
        cancelled=[REMOVED],
        ever_booked=_ever_booked([REMOVED, GHOST]),
    ),
}


def build_scenario(name: str):
    try:
        return SCENARIOS[name]()
    except KeyError as exc:
        known = ", ".join(sorted(SCENARIOS))
        raise SystemExit(f"unknown scenario {name!r}; expected one of: {known}") from exc


def _walk_strings(value, found: set):
    if isinstance(value, str):
        found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            _walk_strings(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_strings(item, found)


def dashboard_strings(dashboard) -> set:
    found = set()
    _walk_strings(dashboard, found)
    return found


if __name__ == "__main__":
    scenario = build_scenario(sys.argv[1] if len(sys.argv) > 1 else "authoritative")
    json.dump(scenario, sys.stdout)
