"""Stored carrier must be the carrier printed on the operations screens.

An unauthenticated caller can ingest or look up a flight for a two-character
airline other than GF. The flight list and dashboard return that airline. The
operations header, comparison labels, and analytics flight chip must print it.

The UI assertions fail while those surfaces hardcode a GF prefix.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")

from backend.api import database
from backend.api.routes import flights, schedule
from backend.feeder import runner, storage
from backend.sabre import templates

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"
TEST_DB_NAME = "falconeye_carrier_label_test"
CARRIER = "BA"
FLIGHT_NUMBER = "2057"
ORIGIN = "LHR"
DEPARTURE_DATE = "2026-03-19"
OTHER_CARRIER = "QR"
OTHER_FLIGHT = "3001"


class SabreTransportStandIn:
    """Stand-in for the SOAP POST.

    Request bodies are built with the product templates and the same fields
    SabreClient passes. The parsed bodies are the shape the converters read.
    The airline stored later is the caller argument, not a field parsed back
    out of the response.
    """

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def _common_vars(self):
        return {
            "cpaid": "TEST",
            "conversation_id": "conv",
            "message_id": "mid",
            "timestamp": "2026-03-19T00:00:00",
            "token": "placeholder-token",
        }

    def _meta(self, body):
        return {
            "requestXml": body,
            "httpStatus": 200,
            "durationMs": 1,
            "sessionToken": "placeholder-token",
            "conversationId": "conv",
        }

    def get_flight_status(self, airline, flight_number, origin, departure_date=None):
        body = templates.FLIGHT_STATUS.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
        )
        assert f"<Airline>{airline}</Airline>" in body
        data = {
            "ItineraryResponseList": {
                "ItineraryInfoResponse": {
                    "FlightStatus": "OPEN",
                    "ScheduledDepartureDate": departure_date,
                    "DepartureGate": "A1",
                    "AircraftType": "32N",
                }
            }
        }
        return data, "<ACS_FlightDetailRS/>", self._meta(body)

    def get_passenger_list(self, airline, flight_number, departure_date, origin, display_codes=None):
        body = templates.PASSENGER_LIST.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
            display_codes=display_codes or "",
        )
        assert airline in body
        data = {
            "ItineraryInfo": {"Itinerary": {"Destination": "BAH"}},
            "PassengerInfoList": {"PassengerInfo": []},
        }
        return data, "<GetPassengerListRS/>", self._meta(body)

    def get_reservations(self, airline, flight_number, departure_airport, departure_datetime):
        body = templates.RESERVATION.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_airport=departure_airport,
            departure_datetime=departure_datetime,
        )
        data = {"ReservationsList": {"@TotalResults": "0"}}
        return data, "<Trip_SearchRS/>", self._meta(body)

    def get_trip_report(self, airline, flight_number, departure_date, origin, report_type):
        body = templates.TRIP_REPORT.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_date=departure_date,
            origin=origin,
            report_type=report_type,
        )
        data = {"PassengerList": {"Passenger": []}}
        return data, "<Trip_ReportsRS/>", self._meta(body)

    def verify_flight_details(self, airline, flight_number, departure_date, origin, destination=""):
        departure_datetime = f"{departure_date[5:]}T00:00"
        body = templates.VERIFY_FLIGHT_DETAILS.format(
            **self._common_vars(),
            airline=airline,
            flight_number=flight_number,
            departure_datetime=departure_datetime,
            origin=origin,
            destination=destination,
        )
        assert f'Code="{airline}"' in body
        data = {
            "ApplicationResults": {"@status": "Complete"},
            "OriginDestinationOptions": {
                "OriginDestinationOption": {
                    "FlightSegment": {
                        "@DepartureDateTime": f"{departure_date}T08:00:00",
                        "@ArrivalDateTime": f"{departure_date}T12:00:00",
                        "OriginLocation": {"@LocationCode": origin or "LHR"},
                        "DestinationLocation": {"@LocationCode": "BAH"},
                        "Equipment": {"@AirEquipType": "32N"},
                        "MarketingAirline": {
                            "@Code": airline,
                            "@FlightNumber": flight_number,
                        },
                    }
                }
            },
        }
        return data, "<VerifyFlightDetailsRS/>", self._meta(body)


@pytest.fixture()
def api(monkeypatch):
    database.close_db()
    storage.close()
    database.DB_NAME = TEST_DB_NAME
    monkeypatch.setattr(runner, "SabreClient", SabreTransportStandIn)
    monkeypatch.setattr(schedule, "SabreClient", SabreTransportStandIn)
    flights._dashboard_cache.clear()

    from backend.api.main import app

    with TestClient(app) as client:
        db = database.get_db()
        db.client.drop_database(TEST_DB_NAME)
        yield client, db
        flights._dashboard_cache.clear()
        db.client.drop_database(TEST_DB_NAME)
    database.close_db()
    storage.close()


def test_non_gf_carrier_is_stored_and_shown_on_operations_screens(api, tmp_path):
    client, db = api
    ingest = client.post(
        "/flights/ingest",
        json={
            "airline": CARRIER,
            "flightNumber": FLIGHT_NUMBER,
            "origin": ORIGIN,
            "departureDate": DEPARTURE_DATE,
            "departureDateTime": f"{DEPARTURE_DATE}T08:00:00",
        },
    )
    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["result"]["flight"]["airline"] == CARRIER

    stored_status = db["flight_status"].find_one({"flightNumber": FLIGHT_NUMBER})
    assert stored_status["airline"] == CARRIER
    raw_status = db["sabre_requests"].find_one({"apiType": "FlightStatus"})
    assert f"<Airline>{CARRIER}</Airline>" in raw_status["requestXml"]

    listed = client.get("/flights")
    assert listed.status_code == 200, listed.text
    rows = [row for row in listed.json() if row["flightNumber"] == FLIGHT_NUMBER]
    assert rows and rows[0]["airline"] == CARRIER

    dashboard = client.get(
        f"/flights/{FLIGHT_NUMBER}/dashboard",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["flightStatus"]["airline"] == CARRIER

    tree = client.get(
        f"/flights/{FLIGHT_NUMBER}/tree",
        params={"origin": ORIGIN, "date": DEPARTURE_DATE},
    )
    assert tree.status_code == 200, tree.text

    lookup = client.post(
        "/flights/schedule/lookup",
        json={
            "airline": OTHER_CARRIER,
            "flightNumber": OTHER_FLIGHT,
            "departureDate": DEPARTURE_DATE,
        },
    )
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["airline"] == OTHER_CARRIER
    stored_schedule = db["flight_schedules"].find_one({"flightNumber": OTHER_FLIGHT})
    assert stored_schedule["airline"] == OTHER_CARRIER
    listed_after_lookup = client.get("/flights")
    assert all(row["flightNumber"] != OTHER_FLIGHT for row in listed_after_lookup.json())

    fixture = {
        "airline": CARRIER,
        "flightNumber": FLIGHT_NUMBER,
        "origin": ORIGIN,
        "date": DEPARTURE_DATE,
        "destination": rows[0].get("destination") or "BAH",
        "flights": rows,
        "dashboard": dashboard.json(),
        "tree": tree.json(),
    }
    fixture_path = tmp_path / "carrier-fixture.json"
    fixture_path.write_text(json.dumps(fixture))

    env = os.environ.copy()
    env["CARRIER_FIXTURE"] = str(fixture_path)
    completed = subprocess.run(
        [
            "node",
            "--experimental-test-module-mocks",
            "--import",
            str(FRONTEND / "node_modules/tsx/dist/esm/index.mjs"),
            "--test",
            "--test-force-exit",
            "tests/carrier-label-board.test.tsx",
        ],
        cwd=FRONTEND,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
