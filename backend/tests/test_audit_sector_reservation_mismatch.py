"""Sector reservations must not be compared with another departure's manifest.

These tests fail while the vulnerability is present. They pass once each join
uses the reservation document for the same departure airport and the same
operating day as the manifest it is compared with.

The process-audit route filters passenger_list by origin, then loads
reservations with only the flight number and an optional date. Reservation
documents are written by convert_reservations with the airport in
departureAirport, so the newest document for that flight number can belong
to another sector or another day. The party-size rule then warns using that
document's record locator and numberInParty.

The same unconstrained reservation lookup is used when the passenger list is
enriched and when PNR mismatches are aggregated. Both are covered here.

GET /flights/{flightNumber}/reservations/{pnr} is not covered: it does not
take an origin and does not compare a manifest with reservations.
Historical snapshot reads are not covered: snapshot documents store origin
and get_snapshot_data_as_of filters on that field.
"""

import os
import sys
from pathlib import Path

# Force the local server. load_dotenv() does not override an existing value,
# so importing the app cannot point these tests at another database.
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.feeder import storage
from backend.feeder.converter import convert_passenger_list, convert_reservations


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(client):
    database = storage.get_db()
    for name in ("passenger_list", "reservations", "changes"):
        database[name].delete_many({})
    yield database
    for name in ("passenger_list", "reservations", "changes"):
        database[name].delete_many({})


def _passenger(last_name, first_name, pnr):
    return {
        "Name_Details": {"LastName": last_name, "FirstName": first_name},
        "PNRLocator": pnr,
    }


def _reservation(pnr, party_size, passengers=None):
    record = {
        "@Locator": pnr,
        "GetReservationRS": {
            "Reservation": {
                "@numberInParty": str(party_size),
                "BookingDetails": {},
                "PassengerReservation": {},
            }
        },
    }
    if passengers:
        record["GetReservationRS"]["Reservation"]["PassengerReservation"] = {
            "Passengers": {"Passenger": passengers},
        }
    return record


def _reservation_passenger(last_name, first_name, nationality, meal):
    return {
        "LastName": last_name,
        "FirstName": first_name,
        "SpecialRequests": {
            "APISRequest": {
                "DOCSEntry": {
                    "Gender": "F",
                    "DocumentNationalityCountry": nationality,
                }
            },
            "SpecialMealRequest": {"MealType": meal},
        },
    }


def _store_manifest(flight_number, origin, departure_date, fetched_at, passengers):
    doc = convert_passenger_list(
        {"PassengerInfoList": {"PassengerInfo": passengers}},
        "GF",
        flight_number,
        departure_date,
        origin,
    )
    doc["fetchedAt"] = fetched_at
    assert doc["origin"] == origin
    assert doc["departureDate"] == departure_date
    assert "departureAirport" not in doc
    storage.store_passenger_list(doc)
    return doc


def _store_reservations(flight_number, airport, departure_date, fetched_at, records):
    doc = convert_reservations(
        {
            "ReservationsList": {
                "@TotalResults": str(len(records)),
                "Reservations": {"Reservation": records},
            }
        },
        "GF",
        flight_number,
        airport,
        departure_date,
    )
    doc["fetchedAt"] = fetched_at
    assert doc["departureAirport"] == airport
    assert doc["departureDate"] == departure_date
    assert "origin" not in doc
    stored_parties = {item["pnr"]: item["numberInParty"] for item in doc["reservations"]}
    storage.store_reservations(doc)
    return doc, stored_parties


def _party_alerts(body):
    found = {}
    for alert in body["alerts"]:
        if alert["ruleId"] != "PARTY_SIZE_MISMATCH":
            continue
        found[alert["pnr"]] = {
            "message": alert["message"],
            "declaredPartySize": alert["details"]["declaredPartySize"],
            "manifestCount": alert["details"]["manifestCount"],
        }
    return found


def test_dated_audit_compares_manifest_with_same_sector_reservations(client, db):
    """Origin and date are both supplied. Another sector was ingested later.

    BAH's own reservation agrees with the manifest for LHR441 (2) and
    disagrees for REAL01 (declared 4, one passenger on the manifest).
    A newer DXB document says LHR441 has 9 people. An older AAA document
    says REAL01 has 99. Only the BAH figures may be reported.
    """
    flight = "4101"
    day = "2026-06-10"
    bah_fetched = "2026-06-10T10:00:00Z"
    _store_manifest(
        flight,
        "BAH",
        day,
        bah_fetched,
        [
            _passenger("SMITH", "ANN", "LHR441"),
            _passenger("SMITH", "BOB", "LHR441"),
            _passenger("HASSAN", "ALI", "REAL01"),
        ],
    )
    _store_manifest(
        flight,
        "DXB",
        day,
        "2026-06-10T12:00:00Z",
        [_passenger("OTHER", "SUE", "DXONLY")],
    )
    _store_reservations(
        flight,
        "AAA",
        day,
        "2026-06-10T08:00:00Z",
        [
            _reservation("LHR441", 99),
            _reservation("REAL01", 99),
        ],
    )
    bah_doc, bah_parties = _store_reservations(
        flight,
        "BAH",
        day,
        "2026-06-10T09:00:00Z",
        [
            _reservation("LHR441", 2),
            _reservation("REAL01", 4),
        ],
    )
    _store_reservations(
        flight,
        "DXB",
        day,
        "2026-06-10T13:00:00Z",
        [
            _reservation("LHR441", 9),
            _reservation("DXONLY", 3),
        ],
    )
    assert bah_parties == {"LHR441": 2, "REAL01": 4}
    assert bah_doc["departureAirport"] == "BAH"

    response = client.get(
        f"/flights/{flight}/audit",
        params={"origin": "BAH", "date": day},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fetchedAt"] == bah_fetched
    assert body["origin"] == "BAH"
    assert body["departureDate"] == day

    alerts = _party_alerts(body)
    assert alerts == {
        "REAL01": {
            "message": "PNR REAL01: reservation says 4 in party but 1 on manifest",
            "declaredPartySize": 4,
            "manifestCount": 1,
        }
    }
    assert "LHR441" not in alerts
    assert "DXONLY" not in alerts


def test_undated_audit_does_not_use_another_departure_party_size(client, db):
    """The date is omitted and another departure was fetched more recently.

    A 400 is the required-date fix. A 200 is acceptable only when every
    party-size warning matches the reservation document for the same
    airport and operating day as the manifest that was actually read.
    """
    flight = "4102"
    day_one = "2026-06-11"
    day_two = "2026-06-12"
    # Fetch times are not the operating days. The day-one manifest is the
    # newest passenger document, and the DXB reservation is the newest
    # reservation document.
    day_one_pax = "2026-06-12T16:00:00Z"
    day_two_pax = "2026-06-12T10:00:00Z"
    _store_manifest(
        flight,
        "BAH",
        day_one,
        day_one_pax,
        [
            _passenger("SMITH", "ANN", "LHR441"),
            _passenger("SMITH", "BOB", "LHR441"),
            _passenger("HASSAN", "ALI", "REAL3"),
        ],
    )
    _store_manifest(
        flight,
        "BAH",
        day_two,
        day_two_pax,
        [_passenger("NAIR", "RIA", "REAL4")],
    )
    _store_reservations(
        flight,
        "BAH",
        day_one,
        "2026-06-12T11:00:00Z",
        [
            _reservation("LHR441", 2),
            _reservation("REAL3", 5),
        ],
    )
    _store_reservations(
        flight,
        "BAH",
        day_two,
        "2026-06-12T12:00:00Z",
        [
            _reservation("LHR441", 8),
            _reservation("REAL4", 6),
        ],
    )
    _store_reservations(
        flight,
        "DXB",
        day_one,
        "2026-06-12T18:00:00Z",
        [
            _reservation("LHR441", 9),
            _reservation("DXONLY", 3),
        ],
    )
    _store_reservations(
        flight,
        "AAA",
        day_one,
        "2026-06-10T08:00:00Z",
        [
            _reservation("LHR441", 1),
            _reservation("REAL3", 99),
        ],
    )

    response = client.get(f"/flights/{flight}/audit", params={"origin": "BAH"})
    if response.status_code == 400:
        return
    assert response.status_code == 200, response.text
    body = response.json()
    expected = {
        day_one_pax: {
            "REAL3": {
                "message": "PNR REAL3: reservation says 5 in party but 1 on manifest",
                "declaredPartySize": 5,
                "manifestCount": 1,
            }
        },
        day_two_pax: {
            "REAL4": {
                "message": "PNR REAL4: reservation says 6 in party but 1 on manifest",
                "declaredPartySize": 6,
                "manifestCount": 1,
            }
        },
    }
    assert body["fetchedAt"] in expected
    assert _party_alerts(body) == expected[body["fetchedAt"]]


def test_passenger_list_enrichment_uses_same_sector_reservations(client, db):
    """Passenger enrichment reads reservations without departureAirport.

    The BAH reservation, which is neither the oldest nor the newest
    document for the flight number, carries the nationality and meal that
    belong on this sector's manifest.
    """
    flight = "4103"
    day = "2026-06-13"
    _store_manifest(
        flight,
        "BAH",
        day,
        "2026-06-13T10:00:00Z",
        [
            _passenger("SMITH", "ANN", "SAME01"),
            _passenger("JONES", "BETH", "ONLYBH"),
        ],
    )
    _store_reservations(
        flight,
        "AAA",
        day,
        "2026-06-13T08:00:00Z",
        [
            _reservation(
                "SAME01",
                1,
                [_reservation_passenger("SMITH", "ANN", "US", "KSML")],
            ),
            _reservation(
                "ONLYBH",
                1,
                [_reservation_passenger("JONES", "BETH", "US", "KSML")],
            ),
        ],
    )
    bah_doc, _parties = _store_reservations(
        flight,
        "BAH",
        day,
        "2026-06-13T09:00:00Z",
        [
            _reservation(
                "SAME01",
                1,
                [_reservation_passenger("SMITH", "ANN", "BH", "AVML")],
            ),
            _reservation(
                "ONLYBH",
                1,
                [_reservation_passenger("JONES", "BETH", "BH", "AVML")],
            ),
        ],
    )
    _store_reservations(
        flight,
        "DXB",
        day,
        "2026-06-13T13:00:00Z",
        [
            _reservation(
                "SAME01",
                1,
                [_reservation_passenger("SMITH", "ANN", "GB", "VGML")],
            ),
        ],
    )
    smith = next(
        passenger
        for record in bah_doc["reservations"]
        if record["pnr"] == "SAME01"
        for passenger in record["passengers"]
    )
    assert smith["nationality"] == "BH"
    assert smith["specialMeal"] == "AVML"

    response = client.get(
        f"/flights/{flight}/passengers",
        params={"origin": "BAH", "date": day},
    )
    assert response.status_code == 200, response.text
    by_pnr = {passenger["pnr"]: passenger for passenger in response.json()["passengers"]}
    assert by_pnr["SAME01"]["nationality"] == "BH"
    assert by_pnr["SAME01"]["specialMeal"] == "AVML"
    assert by_pnr["ONLYBH"]["nationality"] == "BH"
    assert by_pnr["ONLYBH"]["specialMeal"] == "AVML"


def test_pnr_mismatch_compares_each_sector_with_its_own_reservations(client, db):
    """The PNR mismatch aggregation binds origin and then ignores it.

    BAH's manifest contains KEEPME, which is on the BAH reservation, and
    MISSPA, which is not. A newer DXB reservation and an older AAA
    reservation contain different locators. Only MISSPA is a mismatch,
    and only on BAH.
    """
    flight = "4104"
    day = "2026-06-14"
    _store_manifest(
        flight,
        "BAH",
        day,
        "2026-06-14T10:00:00Z",
        [
            _passenger("SMITH", "ANN", "KEEPME"),
            _passenger("HASSAN", "ALI", "MISSPA"),
        ],
    )
    _store_manifest(
        flight,
        "DXB",
        day,
        "2026-06-14T10:00:00Z",
        [_passenger("OTHER", "SUE", "NOTBAH")],
    )
    _store_manifest(
        flight,
        "AAA",
        day,
        "2026-06-14T10:00:00Z",
        [_passenger("OLD", "PAT", "OLDPNR")],
    )
    _store_reservations(
        flight,
        "AAA",
        day,
        "2026-06-14T08:00:00Z",
        [_reservation("OLDPNR", 1)],
    )
    _store_reservations(
        flight,
        "BAH",
        day,
        "2026-06-14T09:00:00Z",
        [_reservation("KEEPME", 1)],
    )
    _store_reservations(
        flight,
        "DXB",
        day,
        "2026-06-14T13:00:00Z",
        [_reservation("NOTBAH", 1)],
    )

    response = client.get("/compare/pnr-mismatches", params={"date": day})
    assert response.status_code == 200, response.text
    flights = [
        item
        for item in response.json()["flights"]
        if item["flightNumber"] == flight
    ]
    assert len(flights) == 1
    assert flights[0]["origin"] == "BAH"
    assert set(flights[0]["inPaxNotReservations"]) == {"MISSPA"}
    assert set(flights[0]["inReservationsNotPax"]) == set()
