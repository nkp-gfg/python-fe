"""Build the Other Passengers drill-down fixture from the product's own converters.

The dashboard counts and the passenger list are produced by the same functions
the API uses. Names are not invented after the fact: offloaded names are the
MLX passengers ``merge_trip_reports`` stored, and no-show names are the MLC
passengers ``_build_dashboard_payload`` excludes from the current manifest.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Load converter.py directly. backend.feeder.__init__ imports the feeder runner,
# which is not part of this path and pulls the Sabre client.
_converter_path = REPO_ROOT / "backend" / "feeder" / "converter.py"
_spec = importlib.util.spec_from_file_location("falconeye_converter", _converter_path)
_converter = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_converter)
convert_flight_status = _converter.convert_flight_status
convert_passenger_list = _converter.convert_passenger_list
convert_trip_report = _converter.convert_trip_report
merge_trip_reports = _converter.merge_trip_reports

from backend.api.routes.flights import _build_dashboard_payload

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-20"
AIRLINE = "GF"


def _passenger(
    last_name: str,
    first_name: str,
    pnr: str,
    passenger_type: str,
    indicator: str,
    *,
    checked_in: bool,
    boarded: bool = False,
    seat: str,
) -> dict:
    return {
        "Name_Details": {"LastName": last_name, "FirstName": first_name},
        "PNRLocator": {"#text": pnr, "@nameAssociationID": "1"},
        "PassengerID": pnr,
        "LineNumber": "1",
        "PassengerType": passenger_type,
        "Cabin": "Y",
        "Seat": seat,
        "Destination": "LHR",
        "Indicators": {"Indicator": indicator},
        "CheckIn_Info": {
            "CheckInStatus": "true" if checked_in else "false",
            "CheckInNumber": "10" if checked_in else "0",
        },
        "Boarding_Info": {"BoardStatus": "true" if boarded else "false"},
        "BagCount": "1" if checked_in else "0",
        "BoardingPassFlag": "true" if checked_in else "false",
    }


def _trip_passenger(last_name: str, first_name: str, pnr: str) -> dict:
    return {
        "Name": {"LastName": last_name, "FirstName": first_name},
        "PNRLocator": pnr,
        "PassengerType": "F",
        "Cabin": "Y",
        "Seat": "14C",
        "BookingClass": "Y",
    }


def _manifest_raw() -> dict:
    return {
        "ItineraryInfo": {
            "Itinerary": {"Destination": "LHR", "AircraftType": "32N"},
            "CabinInfoList": {
                "CabinInfo": {
                    "Cabin": "Y",
                    "Count": "3",
                    "Authorized": "150",
                    "Available": "147",
                    "Destination": "LHR",
                }
            },
        },
        "PassengerInfoList": {
            "PassengerInfo": [
                # Non-revenue employee still on the current manifest.
                _passenger(
                    "STAFF", "ALICE", "STF001", "E", "NonRevenue",
                    checked_in=False, seat="1A",
                ),
                # Revenue passenger checked in but not boarded.
                _passenger(
                    "PAYING", "BOB", "REV002", "F", "Revenue",
                    checked_in=True, seat="12A",
                ),
                # Revenue passenger booked and not checked in.
                _passenger(
                    "PAYING", "CAROL", "REV003", "F", "Revenue",
                    checked_in=False, seat="12B",
                ),
            ]
        },
    }


def _flight_status_raw() -> dict:
    return {
        "ItineraryResponseList": {
            "ItineraryInfoResponse": {
                "FlightStatus": "FINAL",
                "Destination": "LHR",
                "ScheduledDepartureDate": DATE,
                "DepartureGate": "A1",
                "AircraftType": "32N",
            }
        },
        "JumpSeat": {"Cockpit": "1", "Cabin": "1"},
    }


def _names_not_on_manifest(ever_booked: list[dict], manifest: list[dict]) -> list[dict]:
    """Same key the dashboard uses: (pnr, lastName upper) absent from the manifest."""
    current = {
        (pax.get("pnr", ""), pax.get("lastName", "").upper())
        for pax in manifest
    }
    missing = []
    for booked in ever_booked:
        key = (booked.get("pnr", ""), booked.get("lastName", "").upper())
        if key not in current:
            missing.append({
                "lastName": booked.get("lastName", ""),
                "firstName": booked.get("firstName", ""),
                "pnr": booked.get("pnr", ""),
            })
    return missing


def build_scenario(*, with_trip_reports: bool) -> dict:
    passenger_doc = convert_passenger_list(
        _manifest_raw(), AIRLINE, FLIGHT, DATE, ORIGIN,
    )
    flight_status = convert_flight_status(
        _flight_status_raw(), AIRLINE, FLIGHT, ORIGIN, DATE,
    )
    trip_doc = None
    offloaded_names: list[dict] = []
    no_show_names: list[dict] = []
    if with_trip_reports:
        mlx = convert_trip_report(
            {"PassengerList": {"Passenger": [
                _trip_passenger("REMOVED", "DANA", "OFF001"),
            ]}},
            AIRLINE, FLIGHT, DATE, ORIGIN, "MLX",
        )
        mlc = convert_trip_report(
            {"PassengerList": {"Passenger": [
                # Still on the manifest, so the dashboard must not count her.
                _trip_passenger("STAFF", "ALICE", "STF001"),
                _trip_passenger("ABSENT", "ERIK", "NOS004"),
            ]}},
            AIRLINE, FLIGHT, DATE, ORIGIN, "MLC",
        )
        trip_doc = merge_trip_reports(mlx, mlc, AIRLINE, FLIGHT, DATE, ORIGIN)
        offloaded_names = [
            {
                "lastName": pax.get("lastName", ""),
                "firstName": pax.get("firstName", ""),
                "pnr": pax.get("pnr", ""),
            }
            for pax in trip_doc.get("cancelledPassengers", [])
        ]
        no_show_names = _names_not_on_manifest(
            trip_doc.get("everBookedPassengers", []),
            passenger_doc.get("passengers", []),
        )

    dashboard = _build_dashboard_payload(
        flight_status,
        passenger_doc,
        ORIGIN,
        DATE,
        {},
        None,
        trip_doc,
        None,
    )
    others = dashboard["stateSummary"]["others"]
    if with_trip_reports:
        if others["offloaded"] != len(offloaded_names):
            raise SystemExit(
                f"offloaded count {others['offloaded']} != {len(offloaded_names)} MLX names"
            )
        if others["noShow"] != len(no_show_names):
            raise SystemExit(
                f"no-show count {others['noShow']} != {len(no_show_names)} names absent from the manifest"
            )
        if not others["offloadedAvailable"] or not others["noShowAvailable"]:
            raise SystemExit("trip-report counts were not marked available")
    else:
        if others["offloadedAvailable"] or others["noShowAvailable"]:
            raise SystemExit("counts were marked available without a trip report")

    def public_doc(doc: dict) -> dict:
        cleaned = dict(doc)
        cleaned.pop("_raw", None)
        cleaned.pop("_id", None)
        return cleaned

    passengers = public_doc(passenger_doc)
    manifest = passengers["passengers"]
    return {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "date": DATE,
        "withTripReports": with_trip_reports,
        "dashboard": dashboard,
        "passengers": passengers,
        "offloadedNames": offloaded_names,
        "noShowNames": no_show_names,
        "staffNames": [
            {"lastName": p["lastName"], "firstName": p["firstName"], "pnr": p["pnr"]}
            for p in manifest
            if (not p.get("isRevenue")) or p.get("passengerType") == "E"
        ],
        "checkedInNotBoardedNames": [
            {"lastName": p["lastName"], "firstName": p["firstName"], "pnr": p["pnr"]}
            for p in manifest
            if p.get("isCheckedIn") and not p.get("isBoarded")
        ],
        "bookedNames": [
            {"lastName": p["lastName"], "firstName": p["firstName"], "pnr": p["pnr"]}
            for p in manifest
            if not p.get("isCheckedIn") and not p.get("isBoarded")
        ],
        "jumpSeat": others["jumpSeat"],
        "nonRevenue": others["nonRevenue"],
        "offloaded": others["offloaded"],
        "noShow": others["noShow"],
    }


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "with-reports"
    scenario = build_scenario(with_trip_reports=which != "without-reports")
    json.dump(scenario, sys.stdout)


if __name__ == "__main__":
    main()
