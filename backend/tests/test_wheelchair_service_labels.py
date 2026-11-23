"""Wheelchair codes from Trip Search must be shown as wheelchair services.

The analytics page currently prints a walking-ability phrase (or nothing) under
each code. This test fails until every code is described as the wheelchair
service it requests, with the raw code still visible.

Run from the repository root:

    python3 -m pytest backend/tests/test_wheelchair_service_labels.py -q

The UI half needs the frontend test runner installed (`npm ci` in `frontend`).
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import mongomock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.api.routes import analytics
from backend.sabre.client import SabreClient

EXPECTED_COUNTS = {
    "WCHR": 2,
    "WCHS": 1,
    "WCHC": 1,
    "WCBD": 1,
    "WCBW": 1,
    "WCMP": 1,
    "WCOB": 1,
}

TRIP_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="ABC123">
            <GetReservationRS>
              <Reservation numberInParty="9">
                <PassengerReservation>
                  <Passengers>
                    <Passenger nameId="1">
                      <LastName>RAMP</LastName>
                      <FirstName>ONE</FirstName>
                      <SpecialRequests>
                        <WheelchairRequest>
                          <stl19:WheelchairCode>WCHR</stl19:WheelchairCode>
                        </WheelchairRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger nameId="2">
                      <LastName>RAMP</LastName>
                      <FirstName>TWO</FirstName>
                      <SpecialRequests>
                        <WheelchairRequest>
                          <WheelchairCode>WCHR</WheelchairCode>
                        </WheelchairRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger nameId="3">
                      <LastName>STAIRS</LastName>
                      <FirstName>ONE</FirstName>
                      <SpecialRequests>
                        <WheelchairRequest>
                          <WheelchairCode>WCHS</WheelchairCode>
                        </WheelchairRequest>
                        <WheelchairRequest>
                          <WheelchairCode>WCHR</WheelchairCode>
                        </WheelchairRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger nameId="4">
                      <LastName>CABIN</LastName>
                      <FirstName>ONE</FirstName>
                      <SpecialRequests>
                        <WheelchairRequest>
                          <WheelchairCode>WCHC</WheelchairCode>
                        </WheelchairRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger nameId="5">
                      <LastName>DRY</LastName>
                      <FirstName>CELL</FirstName>
                      <SpecialRequests>
                        <WheelchairRequest>
                          <WheelchairCode>WCBD</WheelchairCode>
                        </WheelchairRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger nameId="6">
                      <LastName>WET</LastName>
                      <FirstName>CELL</FirstName>
                      <SpecialRequests>
                        <WheelchairRequest>
                          <WheelchairCode>WCBW</WheelchairCode>
                        </WheelchairRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger nameId="7">
                      <LastName>MANUAL</LastName>
                      <FirstName>OWN</FirstName>
                      <SpecialRequests>
                        <WheelchairRequest>
                          <WheelchairCode>WCMP</WheelchairCode>
                        </WheelchairRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger nameId="8">
                      <LastName>BOARD</LastName>
                      <FirstName>CHAIR</FirstName>
                      <SpecialRequests>
                        <WheelchairRequest>
                          <WheelchairCode>WCOB</WheelchairCode>
                        </WheelchairRequest>
                      </SpecialRequests>
                    </Passenger>
                    <Passenger nameId="9">
                      <LastName>NONE</LastName>
                      <FirstName>REQUEST</FirstName>
                      <SpecialRequests>
                        <SpecialMealRequest>
                          <MealType>VGML</MealType>
                        </SpecialMealRequest>
                      </SpecialRequests>
                    </Passenger>
                  </Passengers>
                </PassengerReservation>
              </Reservation>
            </GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _load_converter():
    """Load converter.py without importing the feeder package initializer."""
    path = REPO_ROOT / "backend" / "feeder" / "converter.py"
    spec = importlib.util.spec_from_file_location("falconeye_converter_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _latest_reservation_document():
    parsed = SabreClient._parse_xml(TRIP_SEARCH_XML)
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    converter = _load_converter()
    return converter.convert_reservations(body, "GF", "2057", "BAH", "2026-03-19")


def _analytics_for(latest_doc):
    """Run the real network-analytics function over the converted reservations.

    mongomock 4.3.0 executes the pipeline the route builds. An empty
    passenger_list makes mongomock emit a $group document whose $avg is None,
    which this route cannot round, so a single numeric passenger list is seeded.
    That document has no wheelchair field; wheelchair counts come only from
    reservations.
    """
    stored = {key: value for key, value in latest_doc.items() if key != "_raw"}
    older = {
        **stored,
        "fetchedAt": "2020-01-01T00:00:00Z",
        "reservations": [
            {
                "pnr": "OLD999",
                "numberInParty": 1,
                "pointOfSale": {"isoCountry": ""},
                "segments": [{"segmentBookedDate": None}],
                "passengers": [
                    {
                        "lastName": "STALE",
                        "firstName": "SNAP",
                        "wheelchairCode": "GONE",
                        "frequentFlyerNumber": "",
                        "specialMeal": "",
                        "nationality": "",
                    }
                ],
            }
        ],
    }
    client = mongomock.MongoClient()
    db = client["falconeye"]
    db.reservations.insert_many([older, stored])
    db.flights.insert_one(
        {
            "airline": "GF",
            "flightNumber": "2057",
            "origin": "BAH",
            "departureDate": "2026-03-19",
        }
    )
    db.passenger_list.insert_one(
        {
            "flightNumber": "2057",
            "origin": "BAH",
            "departureDate": "2026-03-19",
            "destination": "LHR",
            "fetchedAt": "2026-03-19T01:00:00Z",
            "totalPassengers": 1,
            "adultCount": 1,
            "childCount": 0,
            "infantCount": 0,
            "totalSouls": 1,
            "cabinSummary": [{"cabin": "Y", "count": 1, "authorized": 100}],
            "passengers": [
                {
                    "cabin": "Y",
                    "bookingClass": "Y",
                    "isCheckedIn": False,
                    "isBoarded": False,
                    "isRevenue": True,
                    "isStandby": False,
                }
            ],
        }
    )
    analytics.get_db = lambda: db
    return analytics._run_network_analytics("2026-03-01", "2026-03-31")


def test_trip_search_wheelchair_codes_are_labeled_as_services(tmp_path):
    latest = _latest_reservation_document()
    stored_codes = [
        passenger["wheelchairCode"]
        for passenger in latest["reservations"][0]["passengers"]
    ]
    assert stored_codes == [
        "WCHR",
        "WCHR",
        "WCHS",
        "WCHC",
        "WCBD",
        "WCBW",
        "WCMP",
        "WCOB",
        "",
    ]

    result = _analytics_for(latest)
    distribution = {
        item["code"]: item["count"] for item in result["wheelchairDistribution"]
    }
    assert distribution == EXPECTED_COUNTS
    assert "GONE" not in distribution
    assert "" not in distribution

    fixture = tmp_path / "network-analytics.json"
    fixture.write_text(json.dumps(result))

    frontend = REPO_ROOT / "frontend"
    env = os.environ.copy()
    env["WHEELCHAIR_ANALYTICS_FIXTURE"] = str(fixture)
    completed = subprocess.run(
        [
            str(frontend / "node_modules" / ".bin" / "vitest"),
            "run",
            "--config",
            "vitest.config.ts",
            "tests/wheelchair-service-labels.test.tsx",
        ],
        cwd=frontend,
        env=env,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, (
        "The analytics wheelchair panel did not name each code as a wheelchair "
        "service.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
