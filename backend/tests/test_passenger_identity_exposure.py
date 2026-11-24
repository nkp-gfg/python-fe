"""Spec for passenger-identity exposure.

These tests fail while a repository reader can take a record locator published
in the Bruno collection and, with no credential, read the stored traveler
name, seat, and check-in flag. They pass once those lookups require a
credential the caller does not have, and once every committed locator is an
obvious placeholder.

Run:
    mongod --dbpath /tmp/mongo-data --bind_ip 127.0.0.1 --port 27017
    PYTHONPATH=. MONGODB_URI=mongodb://127.0.0.1:27017 \\
        pytest backend/tests/test_passenger_identity_exposure.py
"""

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["MONGODB_URI"] = os.environ.get(
    "MONGODB_URI", "mongodb://127.0.0.1:27017"
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")

from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.feeder.differ import detect_changes
from backend.feeder import storage

COLLECTION = Path(__file__).resolve().parents[2] / "backend_api_collection"
PLACEHOLDER_LOCATORS = {"ABC123"}
FILENAME_LOCATOR = re.compile(r"Passenger ([A-Za-z0-9]{6})\.bru$")
FILENAME_IGNORE = {"DETAIL", "SUMMARY", "CHANGES"}
SURNAME = "QXTESTSURNAME"
GIVEN = "QXTESTGIVEN"
SEAT = "84Q"
DECOY_SURNAME = "QXDECOYLAST"
DECOY_GIVEN = "QXDECOYFIRST"
DECOY_SEAT = "73W"
DECOY_LOCATOR = "DECOY1"
MARKERS = (SURNAME, GIVEN, SEAT, DECOY_SURNAME, DECOY_GIVEN, DECOY_SEAT)
UNRECOGNIZED_AUTHORIZATION = "Bearer placeholder-token"


@dataclass
class PublishedLookup:
    flight_number: str
    locator: str
    date: str
    origin: str
    source: str


def _bru_urls():
    urls = []
    for path in sorted(COLLECTION.glob("*.bru")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"url:\s*\{\{baseUrl\}\}(\S+)", text)
        if match:
            urls.append((path.name, match.group(1)))
    return urls


def _query(url, key):
    values = parse_qs(urlsplit(url).query).get(key)
    return values[0] if values else None


def committed_locator_files():
    """Filenames that publish a record locator other than a placeholder."""
    found = []
    for path in sorted(COLLECTION.glob("*.bru")):
        name_match = FILENAME_LOCATOR.search(path.name)
        if name_match and name_match.group(1).upper() not in FILENAME_IGNORE:
            if name_match.group(1).upper() not in PLACEHOLDER_LOCATORS:
                found.append(path.name)
                continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"/passengers/([A-Za-z0-9]{5,6})(?=/|\?|$)"
            r"|/reservations/([A-Za-z0-9]{5,6})(?=\?|$)"
            r"|/changes/passenger/([A-Za-z0-9]{5,6})(?=\?|$)",
            text,
        ):
            token = next(group for group in match.groups() if group)
            if token.upper() not in PLACEHOLDER_LOCATORS:
                found.append(path.name)
                break
    return found


def published_lookup() -> PublishedLookup:
    """The passenger-by-locator request the collection tells a reader to send."""
    by_pnr = []
    list_origins = {}
    for filename, url in _bru_urls():
        path = urlsplit(url).path
        direct = re.fullmatch(r"/flights/([^/]+)/passengers/([A-Za-z0-9]{5,6})", path)
        if direct:
            by_pnr.append(
                PublishedLookup(
                    flight_number=direct.group(1),
                    locator=direct.group(2),
                    date=_query(url, "date") or "2026-01-01",
                    origin=_query(url, "origin") or "",
                    source=filename,
                )
            )
        listed = re.fullmatch(r"/flights/([^/]+)/passengers", path)
        if listed and _query(url, "origin"):
            list_origins[listed.group(1)] = _query(url, "origin")

    preferred = [
        item for item in by_pnr if item.locator.upper() not in PLACEHOLDER_LOCATORS
    ]
    chosen = (preferred or by_pnr or [None])[0]
    if chosen is None:
        return PublishedLookup("9999", "ABC123", "2026-01-01", "ZZZ", "")
    if not chosen.origin:
        chosen.origin = list_origins.get(chosen.flight_number, "ZZZ")
    return chosen


def _passenger(locator, surname, given, seat, group_code, priority):
    return {
        "Name_Details": {"LastName": surname, "FirstName": given},
        "PNRLocator": {"#text": locator, "@nameAssociationID": "1"},
        "PassengerID": locator,
        "LineNumber": "1",
        "PriorityCode": priority,
        "BookingClass": "Y",
        "DesiredBookingClass": "J",
        "Cabin": "Y",
        "Seat": seat,
        "Destination": "BAH",
        "PassengerType": "F",
        "GroupCode": group_code,
        "BagCount": "1",
        "BoardingPassFlag": "false",
        "ThruIndicator": "false",
        "CheckIn_Info": {
            "CheckInStatus": "true",
            "CheckInNumber": "4",
            "CheckInDate": "2026-03-23",
            "CheckInTime": "09:15",
        },
        "Boarding_Info": {"BoardStatus": "false"},
        "Indicators": {"Indicator": ["Revenue"]},
    }


def _raw_passenger_list(passengers):
    return {
        "ItineraryInfo": {
            "Itinerary": {"Destination": "BAH", "AircraftType": "32N"},
            "DepartureGate": "A1",
            "DepartureArrival_Dates": {},
            "CabinInfoList": {"CabinInfo": []},
        },
        "PassengerInfoList": {"PassengerInfo": passengers},
    }


def _raw_reservation(locator):
    return {
        "ReservationsList": {
            "@NumberResults": "1",
            "Reservations": {
                "Reservation": {
                    "@Locator": locator,
                    "GetReservationRS": {
                        "Reservation": {
                            "BookingDetails": {},
                            "PassengerReservation": {
                                "Passengers": {
                                    "Passenger": {
                                        "LastName": SURNAME,
                                        "FirstName": GIVEN,
                                        "Seats": {
                                            "PreReservedSeats": {
                                                "PreReservedSeat": {
                                                    "SeatNumber": SEAT,
                                                }
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
        }
    }


def _identity_leaked(response) -> bool:
    body = response.text
    return any(marker in body for marker in MARKERS)


def _assert_withheld(response, sabre_called=False):
    leaked = _identity_leaked(response)
    assert response.status_code in (401, 403) and not leaked and not sabre_called, (
        f"status={response.status_code} identity_leaked={leaked} "
        f"sabre_called={sabre_called}"
    )


@pytest.fixture(scope="module")
def target():
    return published_lookup()


@pytest.fixture(scope="module")
def client(target):
    from pymongo import MongoClient

    mongo = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=2000)
    mongo.admin.command("ping")
    mongo.drop_database("falconeye")
    mongo.close()

    import backend.api.database as api_database

    api_database.close_db()
    storage._db = None
    storage._client = None

    from fastapi.testclient import TestClient
    from backend.api.main import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        flight = target.flight_number
        origin = target.origin
        date = target.date
        info = {
            "airline": "GF",
            "flightNumber": flight,
            "origin": origin,
            "departureDate": date,
        }
        empty = convert_passenger_list(
            _raw_passenger_list([]), "GF", flight, date, origin
        )
        full = convert_passenger_list(
            _raw_passenger_list(
                [
                    _passenger(target.locator, SURNAME, GIVEN, SEAT, "TGRP", "UPG"),
                    _passenger(DECOY_LOCATOR, DECOY_SURNAME, DECOY_GIVEN, DECOY_SEAT, "", ""),
                ]
            ),
            "GF",
            flight,
            date,
            origin,
        )
        stored = next(pax for pax in full["passengers"] if pax["pnr"] == target.locator)
        assert stored["lastName"] == SURNAME
        assert stored["firstName"] == GIVEN
        assert stored["seat"] == SEAT
        assert stored["isCheckedIn"] is True

        storage.store_snapshot("req-empty", "GF", flight, origin, date, "passenger_list", empty)
        snapshot_id, _, _ = storage.store_snapshot(
            "req-full", "GF", flight, origin, date, "passenger_list", full
        )
        storage.store_passenger_list(full)
        previous = storage.get_previous_snapshot(flight, origin, date, "passenger_list")
        changes = detect_changes(
            previous,
            {"snapshotId": snapshot_id, "data": full},
            "passenger_list",
            info,
        )
        assert changes, "passenger diff produced no changes"
        storage.store_changes(changes)

        reservation = convert_reservations(
            _raw_reservation(target.locator), "GF", flight, origin, date
        )
        assert reservation["reservations"][0]["passengers"][0]["lastName"] == SURNAME
        assert reservation["reservations"][0]["passengers"][0]["seatNumber"] == SEAT
        storage.store_reservations(reservation)
        yield test_client

    api_database.close_db()
    storage._db = None
    storage._client = None


def test_committed_record_locators_are_placeholders():
    files = committed_locator_files()
    assert files == [], (
        "Bruno collection publishes a non-placeholder record locator in: "
        + ", ".join(files)
    )


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_passenger_by_locator_withholds_identity(client, target, authorization):
    response = client.get(
        f"/flights/{target.flight_number}/passengers/{target.locator}",
        params={"date": target.date},
        headers=_headers(authorization),
    )
    _assert_withheld(response)


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_passenger_manifest_withholds_identity(client, target, authorization):
    response = client.get(
        f"/flights/{target.flight_number}/passengers",
        params={"origin": target.origin, "date": target.date},
        headers=_headers(authorization),
    )
    _assert_withheld(response)


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_standby_list_withholds_identity(client, target, authorization):
    response = client.get(
        f"/flights/{target.flight_number}/passengers/standby-list",
        params={"origin": target.origin, "date": target.date},
        headers=_headers(authorization),
    )
    _assert_withheld(response)


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_group_bookings_withholds_identity(client, target, authorization):
    response = client.get(
        f"/flights/{target.flight_number}/passengers/groups",
        params={"origin": target.origin, "date": target.date},
        headers=_headers(authorization),
    )
    _assert_withheld(response)


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_dashboard_withholds_group_member_identity(client, target, authorization):
    response = client.get(
        f"/flights/{target.flight_number}/dashboard",
        params={"origin": target.origin, "date": target.date},
        headers=_headers(authorization),
    )
    _assert_withheld(response)


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_reservation_by_locator_withholds_identity(client, target, authorization):
    response = client.get(
        f"/flights/{target.flight_number}/reservations/{target.locator}",
        params={"date": target.date},
        headers=_headers(authorization),
    )
    _assert_withheld(response)


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_passenger_changes_withhold_identity(client, target, authorization):
    response = client.get(
        f"/flights/{target.flight_number}/changes/passenger/{target.locator}",
        params={"date": target.date},
        headers=_headers(authorization),
    )
    _assert_withheld(response)


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_passenger_timeline_withholds_identity(client, target, authorization):
    response = client.get(
        f"/flights/{target.flight_number}/passengers/{target.locator}/timeline",
        params={"date": target.date, "origin": target.origin},
        headers=_headers(authorization),
    )
    _assert_withheld(response)


@pytest.mark.parametrize("authorization", [None, UNRECOGNIZED_AUTHORIZATION])
def test_passenger_detail_rejects_before_sabre(client, target, authorization, monkeypatch):
    called = {}

    class SabreClient:
        def __init__(self):
            called["init"] = True
            raise RuntimeError("passenger detail reached Sabre without a credential")

        def __enter__(self):
            called["enter"] = True
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("backend.api.routes.passengers.SabreClient", SabreClient)
    response = client.get(
        f"/flights/{target.flight_number}/passengers/{target.locator}/detail",
        params={"origin": target.origin, "date": target.date, "airline": "GF"},
        headers=_headers(authorization),
    )
    _assert_withheld(response, sabre_called=bool(called))


def _headers(authorization):
    if authorization is None:
        return {}
    return {"Authorization": authorization}
