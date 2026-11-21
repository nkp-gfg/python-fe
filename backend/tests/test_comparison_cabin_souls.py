"""Comparison cabin figures must be boarded souls, not the full manifest.

The comparison column renders overview.soulsOnBoard beside overview.economySouls
and overview.businessSouls (frontend/app/compare/page.tsx). Souls on board are
the boarded state bucket. Economy and Business must be that same bucket's
cabin souls: a booked or checked-in passenger, and their lap infant, are not
already in the cabin.

These tests fail while those overview and operational-summary fields are filled
from the full manifest cabin totals.
"""

import copy

import pytest

from backend.api.routes import flights


def _pax(cabin, *, boarded, checked_in, infant=False):
    """Passenger record in the shape convert_passenger_list stores."""
    return {
        "lastName": "TEST",
        "firstName": "PAX",
        "pnr": "ABC123",
        "cabin": cabin,
        "isCheckedIn": checked_in,
        "isBoarded": boarded,
        "hasInfant": infant,
        "isChild": False,
        "isRevenue": True,
        "passengerType": "F",
        "editCodes": [],
    }


# Before departure: some passengers are boarded, some only checked in, some
# still booked. Lap infants ride on hasInfant and are not separate records.
MIXED_PASSENGERS = [
    _pax("Y", boarded=True, checked_in=True, infant=True),
    _pax("Y", boarded=False, checked_in=False),
    _pax("Y", boarded=False, checked_in=True),
    _pax("J", boarded=True, checked_in=True),
    _pax("J", boarded=False, checked_in=True, infant=True),
    _pax("J", boarded=False, checked_in=False),
    # Blank cabin is economy. Boarded wins even when check-in is still false.
    _pax("", boarded=True, checked_in=False, infant=True),
]

# Boarded economy: Y+infant (2) and blank cabin+infant (2). Boarded business: 1.
BOARDED_ECONOMY_SOULS = 4
BOARDED_BUSINESS_SOULS = 1
BOARDED_TOTAL_SOULS = BOARDED_ECONOMY_SOULS + BOARDED_BUSINESS_SOULS
# Manifest cabin souls count every passenger plus a lap infant, boarded or not.
MANIFEST_ECONOMY_SOULS = 6
MANIFEST_BUSINESS_SOULS = 4

ALL_BOARDED_PASSENGERS = [
    _pax("Y", boarded=True, checked_in=True, infant=True),
    _pax("J", boarded=True, checked_in=True),
]


def _soul_of(passenger):
    return 1 + (1 if passenger.get("hasInfant") else 0)


def _expected_boarded_cabin_souls(passengers):
    economy = 0
    business = 0
    for passenger in passengers:
        if not passenger.get("isBoarded"):
            continue
        if passenger.get("cabin") == "J":
            business += _soul_of(passenger)
        else:
            economy += _soul_of(passenger)
    return economy, business


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            field, direction = sort[0]
            matches.sort(key=lambda doc: doc.get(field, ""), reverse=direction < 0)
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query, projection=None):
        return [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)]


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections.get(name, _Collection([]))


def _matches(doc, query):
    return all(doc.get(key) == expected for key, expected in query.items())


def _passenger_list(flight_number, origin, date, passengers):
    infants = sum(1 for passenger in passengers if passenger.get("hasInfant"))
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": date,
        "destination": "LHR",
        "fetchedAt": "2026-04-01T06:00:00Z",
        "totalPassengers": len(passengers),
        "adultCount": len(passengers),
        "childCount": 0,
        "infantCount": infants,
        "totalSouls": len(passengers) + infants,
        "passengers": passengers,
    }


def _flight_status(flight_number, origin, date):
    return {
        "airline": "GF",
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": date,
        "fetchedAt": "2026-04-01T06:00:00Z",
        "status": "OPEN",
        "gate": "A1",
        "aircraft": {"type": "789", "registration": "A9C-XX"},
        "legs": [{"city": "LHR"}],
    }


@pytest.fixture(autouse=True)
def _clear_dashboard_cache():
    flights._dashboard_cache.clear()
    yield
    flights._dashboard_cache.clear()


def _load_dashboard(monkeypatch, flight_number, passengers):
    origin = "BAH"
    date = "2026-04-01"
    database = _Database({
        "flight_status": _Collection([_flight_status(flight_number, origin, date)]),
        "passenger_list": _Collection([
            _passenger_list(flight_number, origin, date, passengers),
        ]),
        "reservations": _Collection([]),
        "trip_reports": _Collection([]),
        "flight_schedules": _Collection([]),
        "changes": _Collection([]),
        "flights": _Collection([]),
    })
    monkeypatch.setattr(flights, "get_db", lambda: database)
    return flights.get_flight_dashboard(flight_number, origin, date, None)


def test_comparison_column_cabin_souls_are_boarded_load(monkeypatch):
    """Two stored flights, the selection the comparison page loads per column.

    Economy and Business next to souls on board must exclude passengers who
    are still booked or only checked in, and must exclude their lap infants.
    A boarded passenger's lap infant stays in that cabin.
    """
    mixed = _load_dashboard(monkeypatch, "2057", MIXED_PASSENGERS)
    boarded_only = _load_dashboard(monkeypatch, "2058", ALL_BOARDED_PASSENGERS)

    mixed_boarded = mixed["stateSummary"]["boarded"]
    assert mixed_boarded["economySouls"] == BOARDED_ECONOMY_SOULS
    assert mixed_boarded["businessSouls"] == BOARDED_BUSINESS_SOULS
    assert mixed_boarded["totalSouls"] == BOARDED_TOTAL_SOULS
    assert mixed["overview"]["soulsOnBoard"] == BOARDED_TOTAL_SOULS

    # Full manifest totals stay on the analysis the other screens use.
    assert mixed["analysis"]["cabinTotals"]["economy"]["souls"] == MANIFEST_ECONOMY_SOULS
    assert mixed["analysis"]["cabinTotals"]["business"]["souls"] == MANIFEST_BUSINESS_SOULS

    overview = mixed["overview"]
    assert overview["economySouls"] == BOARDED_ECONOMY_SOULS
    assert overview["businessSouls"] == BOARDED_BUSINESS_SOULS
    assert overview["economySouls"] + overview["businessSouls"] == overview["soulsOnBoard"]

    economy, business = _expected_boarded_cabin_souls(ALL_BOARDED_PASSENGERS)
    boarded_overview = boarded_only["overview"]
    assert boarded_overview["soulsOnBoard"] == economy + business
    assert boarded_overview["economySouls"] == economy
    assert boarded_overview["businessSouls"] == business


def test_list_summaries_use_boarded_cabin_souls():
    """Flight-list operational summaries pair the same three figures.

    list_flights publishes them through _summarize_flight_for_list_fast.
    _summarize_flight_for_list computes the same pair and is covered here
    even though no route calls it.
    """
    origin = "BAH"
    date = "2026-04-01"
    status = _flight_status("2060", origin, date)
    passenger_doc = _passenger_list("2060", origin, date, MIXED_PASSENGERS)

    for summarize in (
        flights._summarize_flight_for_list,
        flights._summarize_flight_for_list_fast,
    ):
        summary = summarize(status, passenger_doc)
        operational = summary["operationalSummary"]
        assert operational["soulsOnBoard"] == BOARDED_TOTAL_SOULS
        assert operational["economySouls"] == BOARDED_ECONOMY_SOULS
        assert operational["businessSouls"] == BOARDED_BUSINESS_SOULS
        assert (
            operational["economySouls"] + operational["businessSouls"]
            == operational["soulsOnBoard"]
        )

    counts = flights._quick_operational_counts(passenger_doc)
    assert counts["soulsOnBoard"] == BOARDED_TOTAL_SOULS
    assert counts["economySouls"] == BOARDED_ECONOMY_SOULS
    assert counts["businessSouls"] == BOARDED_BUSINESS_SOULS


def test_list_flights_aggregation_cabin_souls_are_boarded(monkeypatch):
    """list_flights reduces cabin souls in Mongo, then returns them beside souls on board.

    _boardedSouls keeps only boarded passengers. _econSouls and _bizSouls must
    use that same boarded filter. The passengers array is removed before the
    response is built, so the numbers come from the aggregation itself.
    """
    from pymongo import MongoClient
    from pymongo.errors import ServerSelectionTimeoutError

    import backend.api.database as database

    uri = "mongodb://127.0.0.1:27017"
    probe = MongoClient(uri, serverSelectionTimeoutMS=2000)
    try:
        probe.admin.command("ping")
    except ServerSelectionTimeoutError:
        pytest.skip("MongoDB is not running on 127.0.0.1:27017")
    finally:
        probe.close()

    monkeypatch.setenv("MONGODB_URI", uri)
    if database._client is not None:
        database._client.close()
    database._client = None
    database._db = None

    flight_number = "2062"
    origin = "BAH"
    date = "2026-04-02"
    db = database.get_db()
    db["flight_status"].delete_many({"flightNumber": flight_number, "departureDate": date})
    db["passenger_list"].delete_many({"flightNumber": flight_number, "departureDate": date})
    db["flight_status"].insert_one(_flight_status(flight_number, origin, date))
    db["passenger_list"].insert_one(
        _passenger_list(flight_number, origin, date, MIXED_PASSENGERS)
    )
    try:
        listed = flights.list_flights(date)
    finally:
        db["flight_status"].delete_many({"flightNumber": flight_number, "departureDate": date})
        db["passenger_list"].delete_many({"flightNumber": flight_number, "departureDate": date})

    match = [row for row in listed if row["flightNumber"] == flight_number]
    assert len(match) == 1
    operational = match[0]["operationalSummary"]
    assert operational["soulsOnBoard"] == BOARDED_TOTAL_SOULS
    assert operational["economySouls"] == BOARDED_ECONOMY_SOULS
    assert operational["businessSouls"] == BOARDED_BUSINESS_SOULS
    assert (
        operational["economySouls"] + operational["businessSouls"]
        == operational["soulsOnBoard"]
    )
