"""Passenger manifest must not repeat reservation service codes.

GET /flights/{flight_number}/passengers has no response model. It loads the
stored manifest and the stored reservations, then copies wheelchairCode,
specialMeal, hasEmergencyContact, ffTierLevel, ffTierName, and ffStatus from
the reservation passenger with the same record locator and last name onto
every matching manifest row.

GET /flights/{flight_number}/reservations declares ReservationsResponse.
ReservationPassenger does not define those fields, and the installed Pydantic
default extra='ignore' drops them during response serialization. The same
caller therefore cannot read the codes from the reservation collection, but
can read them, joined to name and record locator, from the manifest.

These tests call the real FastAPI routes with no credentials. Stored documents
are produced by convert_passenger_list and convert_reservations. They fail
while the manifest copy is present and pass once those fields are left off
the manifest response.
"""

import copy

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.feeder.converter import convert_passenger_list, convert_reservations

FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-04-02"
PNR = "ABC123"

# Values that exist only on reservation passengers. They are not part of the
# manifest document convert_passenger_list stores.
WHEELCHAIR = "WCHC-NEED"
MEAL = "DBML-NEED"
TIER_LEVEL = "TIER-NEED"
TIER_NAME = "FALCON-NEED"
FF_STATUS = "FFSTATUS-NEED"
DECOY_WHEELCHAIR = "DECOY-WCHC"
STALE_WHEELCHAIR = "STALE-WCHC"
OTHER_FLIGHT_WHEELCHAIR = "OTHER-FLIGHT-WCHC"
HISTORICAL_WHEELCHAIR = "WCHC-HIST"
FUTURE_WHEELCHAIR = "FUTURE-WCHC"

SERVICE_CODE_FIELDS = (
    "wheelchairCode",
    "specialMeal",
    "hasEmergencyContact",
    "ffTierLevel",
    "ffTierName",
    "ffStatus",
)

MANIFEST_SENTINELS = (
    WHEELCHAIR,
    MEAL,
    TIER_LEVEL,
    TIER_NAME,
    FF_STATUS,
    DECOY_WHEELCHAIR,
    STALE_WHEELCHAIR,
    OTHER_FLIGHT_WHEELCHAIR,
    HISTORICAL_WHEELCHAIR,
    FUTURE_WHEELCHAIR,
)


class MemoryCollection:
    """find_one with equality, $lte, and sort, matching the calls these routes make."""

    def __init__(self):
        self.docs = []

    def insert(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def find_one(self, query=None, sort=None, *args, **kwargs):
        query = query or {}
        matches = [doc for doc in self.docs if _query_matches(doc, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc, field=field: _sort_key(doc.get(field)),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]


def _sort_key(value):
    return (value is None, "" if value is None else value)


def _query_matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _reservation_raw(wheelchair, *, decoy_wheelchair=None, last_name="NASSER"):
    passengers = [
        {
            "LastName": last_name,
            "FirstName": "LAYLA",
            "@nameId": "01.01",
            "SpecialRequests": {
                "SpecialMealRequest": {"MealType": MEAL},
                "WheelchairRequest": {"WheelchairCode": wheelchair},
                "EmergencyContactRequest": {
                    "PassengerRefusal": "false",
                    "ContactName": "CONTACT-NOT-ON-MANIFEST",
                },
            },
            "FrequentFlyer": {
                "FrequentFlyerNumber": "GF998877",
                "AirlineCode": "GF",
                "TierLevelNumber": TIER_LEVEL,
                "ShortText": TIER_NAME,
                "StatusCode": FF_STATUS,
            },
        }
    ]
    if decoy_wheelchair is not None:
        passengers.append(
            {
                "LastName": "DECOY",
                "FirstName": "SAM",
                "SpecialRequests": {
                    "WheelchairRequest": {"WheelchairCode": decoy_wheelchair},
                },
            }
        )
    return {
        "ReservationsList": {
            "@NumberResults": "1",
            "Reservations": {
                "Reservation": [
                    {
                        "@Locator": PNR,
                        "GetReservationRS": {
                            "Reservation": {
                                "@numberInParty": "2",
                                "BookingDetails": {
                                    "CreationTimestamp": "2026-04-01T00:00:00Z",
                                },
                                "PassengerReservation": {
                                    "Passengers": {"Passenger": passengers},
                                },
                            }
                        },
                    }
                ]
            },
        }
    }


def _manifest_raw():
    return {
        "ItineraryInfo": {"Itinerary": {"Destination": "LHR"}},
        "PassengerInfoList": {
            "PassengerInfo": [
                {
                    "Name_Details": {"LastName": "NASSER", "FirstName": "LAYLA"},
                    "PNRLocator": {"#text": PNR},
                    "PassengerID": "1",
                    "LineNumber": "1",
                    "BookingClass": "Y",
                    "Cabin": "Y",
                    "Seat": "12A",
                    "PassengerType": "F",
                },
                {
                    "Name_Details": {"LastName": "DECOY", "FirstName": "SAM"},
                    "PNRLocator": {"#text": PNR},
                    "PassengerID": "2",
                    "LineNumber": "2",
                    "BookingClass": "Y",
                    "Cabin": "Y",
                    "Seat": "12B",
                    "PassengerType": "F",
                },
                {
                    "Name_Details": {"LastName": "WALKUP", "FirstName": "NOMATCH"},
                    "PNRLocator": {"#text": "ZZZZZZ"},
                    "PassengerID": "3",
                    "LineNumber": "3",
                    "BookingClass": "Y",
                    "Cabin": "Y",
                    "PassengerType": "F",
                },
            ]
        },
    }


def _store_reservation(db, wheelchair, fetched_at, *, decoy_wheelchair=None, flight=FLIGHT):
    doc = convert_reservations(
        _reservation_raw(wheelchair, decoy_wheelchair=decoy_wheelchair),
        "GF",
        flight,
        ORIGIN,
        DATE,
    )
    doc["fetchedAt"] = fetched_at
    db["reservations"].insert(doc)
    return doc


def _store_manifest(db, fetched_at, *, flight=FLIGHT):
    doc = convert_passenger_list(_manifest_raw(), "GF", flight, DATE, ORIGIN)
    doc["fetchedAt"] = fetched_at
    db["passenger_list"].insert(doc)
    return doc


def _store_snapshot(db, snapshot_type, sequence, data, *, origin=ORIGIN):
    db["snapshots"].insert(
        {
            "flightNumber": FLIGHT,
            "snapshotType": snapshot_type,
            "sequenceNumber": sequence,
            "origin": origin,
            "departureDate": DATE,
            "capturedAt": f"2026-04-02T0{sequence}:00:00Z",
            "data": data,
        }
    )


class _LifespanRedis:
    def ping(self):
        return True


@pytest.fixture()
def db():
    database = MemoryDB()
    latest = _store_reservation(
        database,
        WHEELCHAIR,
        "2026-04-02T03:00:00Z",
        decoy_wheelchair=DECOY_WHEELCHAIR,
    )
    _store_reservation(database, STALE_WHEELCHAIR, "2026-04-02T01:00:00Z")
    _store_reservation(
        database,
        OTHER_FLIGHT_WHEELCHAIR,
        "2026-04-02T04:00:00Z",
        flight="9999",
    )
    manifest = _store_manifest(database, "2026-04-02T03:00:00Z")
    _store_manifest(database, "2026-04-02T04:00:00Z", flight="9999")

    # Manifest rows produced by the passenger-list converter do not carry
    # the reservation service codes. The codes below are only on reservations.
    for passenger in manifest["passengers"]:
        for field in SERVICE_CODE_FIELDS:
            assert field not in passenger

    matched = next(
        pax
        for reservation in latest["reservations"]
        if reservation["pnr"] == PNR
        for pax in reservation["passengers"]
        if pax["lastName"] == "NASSER"
    )
    assert matched["wheelchairCode"] == WHEELCHAIR
    assert matched["specialMeal"] == MEAL
    assert matched["hasEmergencyContact"] is True
    assert matched["ffTierLevel"] == TIER_LEVEL
    assert matched["ffTierName"] == TIER_NAME
    assert matched["ffStatus"] == FF_STATUS

    historical_res = convert_reservations(
        _reservation_raw(HISTORICAL_WHEELCHAIR, decoy_wheelchair=DECOY_WHEELCHAIR),
        "GF",
        FLIGHT,
        ORIGIN,
        DATE,
    )
    future_res = convert_reservations(
        _reservation_raw(FUTURE_WHEELCHAIR),
        "GF",
        FLIGHT,
        ORIGIN,
        DATE,
    )
    historical_manifest = convert_passenger_list(
        _manifest_raw(), "GF", FLIGHT, DATE, ORIGIN
    )
    future_manifest = convert_passenger_list(
        _manifest_raw(), "GF", FLIGHT, DATE, ORIGIN
    )
    _store_snapshot(database, "reservations", 1, historical_res)
    _store_snapshot(database, "passenger_list", 1, historical_manifest)
    _store_snapshot(database, "reservations", 3, future_res)
    _store_snapshot(database, "passenger_list", 3, future_manifest)
    # A newer snapshot for a different origin must not satisfy the filtered query.
    _store_snapshot(
        database,
        "reservations",
        2,
        future_res,
        origin="DXB",
    )
    return database


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(
        "backend.api.main.get_db",
        lambda: (_ for _ in ()).throw(RuntimeError("lifespan has no database")),
    )
    monkeypatch.setattr("backend.api.main.get_redis", lambda: _LifespanRedis())
    monkeypatch.setattr("backend.api.routes.passengers.get_db", lambda: db)
    monkeypatch.setattr("backend.api.routes.reservations.get_db", lambda: db)
    with TestClient(app) as test_client:
        yield test_client


def _assert_manifest_hides_service_codes(response):
    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    body = response.json()
    passengers = body["passengers"]
    matched = next(pax for pax in passengers if pax.get("lastName") == "NASSER")
    assert matched["pnr"] == PNR
    assert matched["firstName"] == "LAYLA"
    for field in SERVICE_CODE_FIELDS:
        assert field not in matched, (
            f"{field}={matched.get(field)!r} was copied onto the manifest row "
            f"for {matched['lastName']} {matched['pnr']}"
        )
        for pax in passengers:
            assert field not in pax
    for sentinel in MANIFEST_SENTINELS:
        assert sentinel not in response.text, (
            f"{sentinel} was returned by the passenger manifest"
        )


def test_latest_passenger_list_omits_reservation_service_codes(client):
    response = client.get(f"/flights/{FLIGHT}/passengers")
    _assert_manifest_hides_service_codes(response)


def test_filtered_passenger_list_omits_reservation_service_codes(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": ORIGIN, "date": DATE},
    )
    _assert_manifest_hides_service_codes(response)
    body = response.json()
    assert body["flightNumber"] == FLIGHT
    assert body["origin"] == ORIGIN
    assert body["departureDate"] == DATE


def test_historical_passenger_list_omits_reservation_service_codes(client):
    response = client.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": ORIGIN, "date": DATE, "snapshot_sequence": 2},
    )
    _assert_manifest_hides_service_codes(response)
    # sequence 2 with this origin resolves to snapshot 1 ($lte), not the
    # later snapshot and not the live passenger_list document.
    assert response.json()["snapshotSequenceNumber"] == 1


def test_reservation_collection_response_omits_service_codes(client):
    response = client.get(
        f"/flights/{FLIGHT}/reservations",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    body = response.json()
    reservation = next(item for item in body["reservations"] if item["pnr"] == PNR)
    passenger = next(pax for pax in reservation["passengers"] if pax["lastName"] == "NASSER")
    assert passenger["firstName"] == "LAYLA"
    for field in SERVICE_CODE_FIELDS:
        assert field not in passenger
    for sentinel in (
        WHEELCHAIR,
        MEAL,
        TIER_LEVEL,
        TIER_NAME,
        FF_STATUS,
        DECOY_WHEELCHAIR,
        STALE_WHEELCHAIR,
    ):
        assert sentinel not in response.text
