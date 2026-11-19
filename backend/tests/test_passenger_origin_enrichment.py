"""Passenger manifest enrichment must use the requested departure airport.

GET /flights/{flightNumber}/passengers selects the manifest by origin, then
copies nationality, special meal, wheelchair code, emergency-contact flag, and
loyalty tier from reservations. Reservation documents store that airport as
departureAirport. These tests fail while the live lookup ignores it and
copies attributes from whichever reservation document was fetched latest.

They pass once that lookup is limited to the requested departureAirport and,
when the manifest query includes a date, to that departure date. Historical
snapshot reads already filter origin; the snapshot test keeps that behavior.

Run (MongoDB listening on 127.0.0.1:27017):

    PYTHONPATH=. python3 -m pytest backend/tests/test_passenger_origin_enrichment.py -q
"""

import os
import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo import MongoClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.passengers import router as passengers_router
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.feeder import storage

MONGO_URI = "mongodb://127.0.0.1:27017"
DB_NAME = "falconeye_pax_origin_enrichment_test"
FLIGHT = "2057"
DATE = "2026-03-19"
OTHER_DATE = "2026-03-20"


def _passenger_list(origin, departure_date, passengers, fetched_at):
    raw_passengers = []
    for pax in passengers:
        raw_passengers.append({
            "Name_Details": {
                "LastName": pax["lastName"],
                "FirstName": pax["firstName"],
            },
            "PNRLocator": {"#text": pax["pnr"]},
            "PassengerID": pax["passengerId"],
            "Seat": pax["seat"],
            "LineNumber": "1",
        })
    doc = convert_passenger_list(
        {"PassengerInfoList": {"PassengerInfo": raw_passengers}},
        "GF",
        FLIGHT,
        departure_date,
        origin,
    )
    doc["fetchedAt"] = fetched_at
    return doc


def _reservation_passenger(pax):
    special = {
        "APISRequest": {
            "DOCSEntry": {
                "Gender": "M",
                "DateOfBirth": "1990-01-01",
                "DocumentNationalityCountry": pax["nationality"],
            }
        },
        "SpecialMealRequest": {"MealType": pax["meal"]},
        "WheelchairRequest": {"WheelchairCode": pax["wheelchair"]},
    }
    if pax.get("emergency"):
        special["EmergencyContactRequest"] = {"PassengerRefusal": "false"}
    return {
        "LastName": pax["lastName"],
        "FirstName": pax["firstName"],
        "SpecialRequests": special,
        "FrequentFlyer": {
            "Number": "FF123",
            "TierLevelNumber": pax["tier"],
            "ShortText": pax["tierName"],
            "StatusCode": pax["ffStatus"],
        },
    }


def _reservations(origin, departure_date, reservations, fetched_at):
    raw_reservations = []
    for res in reservations:
        raw_reservations.append({
            "@Locator": res["pnr"],
            "GetReservationRS": {
                "Reservation": {
                    "PassengerReservation": {
                        "Passengers": {
                            "Passenger": [
                                _reservation_passenger(pax) for pax in res["passengers"]
                            ]
                        }
                    }
                }
            },
        })
    doc = convert_reservations(
        {
            "ReservationsList": {
                "@NumberResults": str(len(raw_reservations)),
                "Reservations": {"Reservation": raw_reservations},
            }
        },
        "GF",
        FLIGHT,
        origin,
        departure_date,
    )
    doc["fetchedAt"] = fetched_at
    return doc


def _shared(meal, wheelchair, nationality, tier, tier_name, ff_status, emergency=False):
    return {
        "lastName": "AlKhalifa",
        "firstName": "Sam",
        "pnr": "SHARE1",
        "meal": meal,
        "wheelchair": wheelchair,
        "nationality": nationality,
        "tier": tier,
        "tierName": tier_name,
        "ffStatus": ff_status,
        "emergency": emergency,
    }


def _only_bah():
    return {
        "lastName": "Naser",
        "firstName": "Noor",
        "pnr": "BAH999",
        "meal": "AVML",
        "wheelchair": "WCHS",
        "nationality": "QA",
        "tier": "2",
        "tierName": "FALCON",
        "ffStatus": "ACT",
        "emergency": False,
    }


def _manifest_row(pnr, last_name, first_name, passenger_id, seat):
    return {
        "pnr": pnr,
        "lastName": last_name,
        "firstName": first_name,
        "passengerId": passenger_id,
        "seat": seat,
    }


def _attributes(row):
    return {
        "seat": row.get("seat"),
        "passengerId": row.get("passengerId"),
        "nationality": row.get("nationality", ""),
        "specialMeal": row.get("specialMeal"),
        "wheelchairCode": row.get("wheelchairCode"),
        "ffTierLevel": row.get("ffTierLevel"),
        "ffTierName": row.get("ffTierName"),
        "ffStatus": row.get("ffStatus"),
        "hasEmergencyContact": row.get("hasEmergencyContact", False),
    }


def _by_pnr(body, pnr):
    matches = [p for p in body["passengers"] if p.get("pnr") == pnr]
    assert len(matches) == 1, body["passengers"]
    return matches[0]


@pytest.fixture
def mongo_db():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    db = client[DB_NAME]
    client.drop_database(DB_NAME)
    storage.init_db(db)
    yield db
    client.drop_database(DB_NAME)
    client.close()


@pytest.fixture
def api(mongo_db):
    app = FastAPI()
    app.include_router(passengers_router)
    with patch("backend.api.routes.passengers.get_db", return_value=mongo_db):
        with TestClient(app) as client:
            yield client


def _seed_two_sectors(mongo_db):
    """Same flight and record locator on BAH and LHR. LHR was ingested later."""
    storage.store_passenger_list(_passenger_list(
        "BAH",
        DATE,
        [
            _manifest_row("SHARE1", "AlKhalifa", "Sam", "BAH-SHARE", "12A"),
            _manifest_row("BAH999", "Naser", "Noor", "BAH-ONLY", "14C"),
        ],
        "2026-03-19T01:00:00Z",
    ))
    storage.store_passenger_list(_passenger_list(
        "LHR",
        DATE,
        [
            _manifest_row("SHARE1", "AlKhalifa", "Sam", "LHR-SHARE", "1A"),
            _manifest_row("LHR888", "Jones", "Mary", "LHR-ONLY", "2B"),
        ],
        "2026-03-19T03:30:00Z",
    ))
    # Older BAH reservation, then the current BAH reservation, then a newer
    # BAH reservation for a different date, then the later LHR reservation.
    storage.store_reservations(_reservations(
        "BAH",
        DATE,
        [{"pnr": "SHARE1", "passengers": [
            _shared("STALE", "WCHR", "BH", "1", "GOLD", "ACT")
        ]}],
        "2026-03-19T01:00:00Z",
    ))
    storage.store_reservations(_reservations(
        "BAH",
        DATE,
        [
            {"pnr": "SHARE1", "passengers": [
                _shared("VGML", "WCHR", "BH", "1", "GOLD", "ACT")
            ]},
            {"pnr": "BAH999", "passengers": [_only_bah()]},
        ],
        "2026-03-19T02:00:00Z",
    ))
    storage.store_reservations(_reservations(
        "BAH",
        OTHER_DATE,
        [{"pnr": "SHARE1", "passengers": [
            _shared("DBML", "WCHC", "US", "9", "OTHERDAY", "OLD")
        ]}],
        "2026-03-20T04:00:00Z",
    ))
    storage.store_reservations(_reservations(
        "LHR",
        DATE,
        [{"pnr": "SHARE1", "passengers": [
            _shared("KSML", "WCHC", "GB", "3", "SILVER", "EXP", emergency=True)
        ]}],
        "2026-03-19T03:00:00Z",
    ))


def test_live_manifest_keeps_requested_origin_reservation_attributes(api, mongo_db):
    _seed_two_sectors(mongo_db)

    bah = api.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": "BAH", "date": DATE},
    )
    assert bah.status_code == 200, bah.text
    bah_body = bah.json()
    assert bah_body["origin"] == "BAH"
    assert bah_body["departureDate"] == DATE
    pnrs = {p["pnr"] for p in bah_body["passengers"]}
    assert pnrs == {"SHARE1", "BAH999"}

    assert _attributes(_by_pnr(bah_body, "SHARE1")) == {
        "seat": "12A",
        "passengerId": "BAH-SHARE",
        "nationality": "BH",
        "specialMeal": "VGML",
        "wheelchairCode": "WCHR",
        "ffTierLevel": "1",
        "ffTierName": "GOLD",
        "ffStatus": "ACT",
        "hasEmergencyContact": False,
    }
    assert _attributes(_by_pnr(bah_body, "BAH999")) == {
        "seat": "14C",
        "passengerId": "BAH-ONLY",
        "nationality": "QA",
        "specialMeal": "AVML",
        "wheelchairCode": "WCHS",
        "ffTierLevel": "2",
        "ffTierName": "FALCON",
        "ffStatus": "ACT",
        "hasEmergencyContact": False,
    }

    lhr = api.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": "LHR", "date": DATE},
    )
    assert lhr.status_code == 200, lhr.text
    lhr_body = lhr.json()
    assert lhr_body["origin"] == "LHR"
    assert {p["pnr"] for p in lhr_body["passengers"]} == {"SHARE1", "LHR888"}
    assert _attributes(_by_pnr(lhr_body, "SHARE1")) == {
        "seat": "1A",
        "passengerId": "LHR-SHARE",
        "nationality": "GB",
        "specialMeal": "KSML",
        "wheelchairCode": "WCHC",
        "ffTierLevel": "3",
        "ffTierName": "SILVER",
        "ffStatus": "EXP",
        "hasEmergencyContact": True,
    }
    lhr_only = _by_pnr(lhr_body, "LHR888")
    assert lhr_only["seat"] == "2B"
    assert lhr_only.get("nationality", "") == ""
    assert "specialMeal" not in lhr_only
    assert "wheelchairCode" not in lhr_only
    assert "ffTierLevel" not in lhr_only


def test_other_origin_is_not_used_when_requested_origin_has_no_reservations(api, mongo_db):
    storage.store_passenger_list(_passenger_list(
        "BAH",
        DATE,
        [_manifest_row("SHARE1", "AlKhalifa", "Sam", "BAH-SHARE", "12A")],
        "2026-03-19T01:00:00Z",
    ))
    storage.store_reservations(_reservations(
        "LHR",
        DATE,
        [{"pnr": "SHARE1", "passengers": [
            _shared("KSML", "WCHC", "GB", "3", "SILVER", "EXP", emergency=True)
        ]}],
        "2026-03-19T03:00:00Z",
    ))

    response = api.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": "BAH", "date": DATE},
    )
    assert response.status_code == 200, response.text
    row = _by_pnr(response.json(), "SHARE1")
    assert row["seat"] == "12A"
    assert row.get("nationality", "") == ""
    assert "specialMeal" not in row
    assert "wheelchairCode" not in row
    assert "ffTierLevel" not in row
    assert "ffTierName" not in row
    assert "ffStatus" not in row
    assert row.get("hasEmergencyContact", False) is False


def test_omitted_date_still_does_not_copy_a_newer_other_origin(api, mongo_db):
    storage.store_passenger_list(_passenger_list(
        "BAH",
        DATE,
        [_manifest_row("SHARE1", "AlKhalifa", "Sam", "BAH-SHARE", "12A")],
        "2026-03-19T01:00:00Z",
    ))
    storage.store_reservations(_reservations(
        "BAH",
        DATE,
        [{"pnr": "SHARE1", "passengers": [
            _shared("VGML", "WCHR", "BH", "1", "GOLD", "ACT")
        ]}],
        "2026-03-19T02:00:00Z",
    ))
    storage.store_reservations(_reservations(
        "LHR",
        OTHER_DATE,
        [{"pnr": "SHARE1", "passengers": [
            _shared("KSML", "WCHC", "GB", "3", "SILVER", "EXP", emergency=True)
        ]}],
        "2026-03-20T05:00:00Z",
    ))

    response = api.get(f"/flights/{FLIGHT}/passengers", params={"origin": "BAH"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["origin"] == "BAH"
    assert _attributes(_by_pnr(body, "SHARE1")) == {
        "seat": "12A",
        "passengerId": "BAH-SHARE",
        "nationality": "BH",
        "specialMeal": "VGML",
        "wheelchairCode": "WCHR",
        "ffTierLevel": "1",
        "ffTierName": "GOLD",
        "ffStatus": "ACT",
        "hasEmergencyContact": False,
    }


def test_snapshot_enrichment_stays_on_requested_origin(api, mongo_db):
    bah_manifest = _passenger_list(
        "BAH",
        DATE,
        [_manifest_row("SHARE1", "AlKhalifa", "Sam", "BAH-SHARE", "12A")],
        "2026-03-19T01:00:00Z",
    )
    lhr_manifest = _passenger_list(
        "LHR",
        DATE,
        [_manifest_row("SHARE1", "AlKhalifa", "Sam", "LHR-SHARE", "1A")],
        "2026-03-19T03:30:00Z",
    )
    bah_reservations = _reservations(
        "BAH",
        DATE,
        [{"pnr": "SHARE1", "passengers": [
            _shared("VGML", "WCHR", "BH", "1", "GOLD", "ACT")
        ]}],
        "2026-03-19T02:00:00Z",
    )
    lhr_reservations = _reservations(
        "LHR",
        DATE,
        [{"pnr": "SHARE1", "passengers": [
            _shared("KSML", "WCHC", "GB", "3", "SILVER", "EXP", emergency=True)
        ]}],
        "2026-03-19T03:00:00Z",
    )
    storage.store_passenger_list(bah_manifest)
    storage.store_passenger_list(lhr_manifest)
    storage.store_reservations(bah_reservations)
    storage.store_reservations(lhr_reservations)
    storage.store_snapshot(
        "req-bah", "GF", FLIGHT, "BAH", DATE, "passenger_list", bah_manifest)
    storage.store_snapshot(
        "req-lhr", "GF", FLIGHT, "LHR", DATE, "passenger_list", lhr_manifest)
    storage.store_snapshot(
        "req-lhr-2", "GF", FLIGHT, "LHR", DATE, "passenger_list", lhr_manifest)
    storage.store_snapshot(
        "req-bah-res", "GF", FLIGHT, "BAH", DATE, "reservations", bah_reservations)
    storage.store_snapshot(
        "req-lhr-res", "GF", FLIGHT, "LHR", DATE, "reservations", lhr_reservations)
    storage.store_snapshot(
        "req-lhr-res-2", "GF", FLIGHT, "LHR", DATE, "reservations", lhr_reservations)

    response = api.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": "BAH", "date": DATE, "snapshot_sequence": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["origin"] == "BAH"
    assert _attributes(_by_pnr(body, "SHARE1")) == {
        "seat": "12A",
        "passengerId": "BAH-SHARE",
        "nationality": "BH",
        "specialMeal": "VGML",
        "wheelchairCode": "WCHR",
        "ffTierLevel": "1",
        "ffTierName": "GOLD",
        "ffStatus": "ACT",
        "hasEmergencyContact": False,
    }
