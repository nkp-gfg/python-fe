"""Flight-board header must show wheelchair requests already stored on the reservation.

The assertions render FlightWorkbench. They fail while the tab header uses
totalWheelchairs only as a visibility check and prints meals and frequent-flyer
tiers without the wheelchair total or the wheelchairs map.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload, _strip_id
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
FRONTEND = os.path.join(REPO_ROOT, "frontend")
SAMPLE_DIR = os.path.join(REPO_ROOT, "sample_data", "sample_xmlfiles")

WHEELCHAIR_ONLY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="WCH001">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="2" numberOfInfants="0">
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameType="S">
                      <stl19:LastName>RAMP</stl19:LastName>
                      <stl19:FirstName>PAX</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:WheelchairRequest>
                          <stl19:WheelchairCode>WCHR</stl19:WheelchairCode>
                        </stl19:WheelchairRequest>
                      </stl19:SpecialRequests>
                    </stl19:Passenger>
                    <stl19:Passenger nameType="S">
                      <stl19:LastName>CABIN</stl19:LastName>
                      <stl19:FirstName>PAX</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:WheelchairRequest>
                          <stl19:WheelchairCode>WCHC</stl19:WheelchairCode>
                        </stl19:WheelchairRequest>
                      </stl19:SpecialRequests>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_response(xml_text, response_key):
    """Same parse and body extract the Sabre client uses after a SOAP response."""
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not read instance fields.
    return SabreClient._extract_body(None, parsed, response_key)


def _load_sample(filename, response_key):
    path = os.path.join(SAMPLE_DIR, filename)
    with open(path, encoding="utf-8") as handle:
        return _parse_response(handle.read(), response_key)


def _flight_shell(flight_number, origin, date, destination):
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "destination": destination,
        "departureDate": date,
        "status": "OPEN",
        "gate": "",
        "codeshareInfo": [],
        "freeTextRemarks": [],
        "jumpSeat": {"cockpit": 0, "cabin": 0},
    }


def _empty_passenger_list(origin, destination, date):
    return {
        "passengers": [],
        "totalPassengers": 0,
        "adultCount": 0,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": 0,
        "cabinSummary": [],
        "origin": origin,
        "destination": destination,
        "departureDate": date,
    }


def _dashboard(reservation_doc, passenger_list, flight_number, origin, date):
    # Live dashboard reads the reservations collection after _strip_id drops _id and _raw.
    reservation_doc = _strip_id(reservation_doc)
    passenger_list = _strip_id(passenger_list)
    flight_status = _flight_shell(
        flight_number,
        origin,
        date,
        passenger_list.get("destination") or "DMM",
    )
    payload = _build_dashboard_payload(
        flight_status,
        passenger_list,
        origin,
        date,
        {},
        reservation_doc,
        None,
        None,
    )
    summary = payload["specialRequestsSummary"]
    assert summary is not None
    assert summary["totalWheelchairs"] == sum(summary["wheelchairs"].values())
    return payload


def _gf2057_dashboard():
    reservations = convert_reservations(
        _load_sample("Trip_SearchRS_GF2057_BOM_2026-03-19.xml", "Trip_SearchRS"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    passenger_list = convert_passenger_list(
        _load_sample("GetPassengerListRS_GF2057_BOM_2026-03-19.xml", "GetPassengerListRS"),
        "GF",
        "2057",
        "2026-03-19",
        "BOM",
    )
    payload = _dashboard(reservations, passenger_list, "2057", "BOM", "2026-03-19")
    summary = payload["specialRequestsSummary"]
    assert summary["wheelchairs"].get("WCHS") == 1
    assert summary["totalSpecialMeals"] > 0
    # Passenger-list edit codes also record WCHS, but the header reads the reservation summary.
    assert payload["insights"]["wheelchairTypes"].get("WCHS") == 1
    return payload


def _wheelchair_only_dashboard():
    reservations = convert_reservations(
        _parse_response(WHEELCHAIR_ONLY_XML, "Trip_SearchRS"),
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    )
    passenger_list = _empty_passenger_list("BOM", "DMM", "2026-03-19")
    payload = _dashboard(reservations, passenger_list, "2057", "BOM", "2026-03-19")
    summary = payload["specialRequestsSummary"]
    assert summary["wheelchairs"] == {"WCHR": 1, "WCHC": 1}
    assert summary["totalWheelchairs"] == 2
    assert summary["totalSpecialMeals"] == 0
    assert summary["frequentFlyers"] == 0
    assert not (payload.get("insights") or {}).get("wheelchairTypes")
    return payload


def _case(name, payload):
    return {
        "name": name,
        "flightNumber": "2057",
        "origin": "BOM",
        "date": "2026-03-19",
        "dashboard": payload,
        "flights": [
            {
                "airline": "GF",
                "flightNumber": "2057",
                "origin": "BOM",
                "destination": payload["route"]["destination"] or "DMM",
                "departureDate": "2026-03-19",
                "status": "OPEN",
                "gate": "",
                "aircraft": {"type": ""},
                "schedule": {},
                "passengerCounts": {},
                "jumpSeat": {"cockpit": 0, "cabin": 0},
            }
        ],
        "tree": {
            "title": "Aircraft Humans Breakdown Tree",
            "badge": "Sabre Live",
            "width": 940,
            "height": 600,
            "nodes": [],
            "edges": [],
            "statusCards": [],
        },
    }


def _ensure_frontend_test_runner():
    vitest = os.path.join(FRONTEND, "node_modules", "vitest")
    testing_library = os.path.join(FRONTEND, "node_modules", "@testing-library", "react")
    jsdom = os.path.join(FRONTEND, "node_modules", "jsdom")
    if os.path.isdir(vitest) and os.path.isdir(testing_library) and os.path.isdir(jsdom):
        return
    if not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=FRONTEND, check=True)
    subprocess.run(
        [
            "npm",
            "install",
            "--no-save",
            "--no-audit",
            "--no-fund",
            "vitest@3.2.4",
            "jsdom@26",
            "@testing-library/react@16",
            "@testing-library/dom@10",
        ],
        cwd=FRONTEND,
        check=True,
    )


def test_flight_board_header_prints_wheelchair_summary(tmp_path):
    cases = [
        _case(
            "GF2057 Trip Search keeps WCHS beside meals and frequent flyers",
            _gf2057_dashboard(),
        ),
        _case(
            "wheelchair-only reservations still open the strip with every code",
            _wheelchair_only_dashboard(),
        ),
    ]
    payload_path = tmp_path / "wheelchair-cases.json"
    payload_path.write_text(json.dumps(cases))

    _ensure_frontend_test_runner()
    if shutil.which("npx") is None:
        pytest.fail("npx is required to render FlightWorkbench")

    env = os.environ.copy()
    env["WHEELCHAIR_CASES"] = str(payload_path)
    completed = subprocess.run(
        ["npx", "vitest", "run", "tests/wheelchair-header-strip.test.tsx"],
        cwd=FRONTEND,
        env=env,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
