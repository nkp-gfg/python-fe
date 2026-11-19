"""Network analytics must label Sabre loyalty tiers from stored short text.

Ingestion keeps TierLevelNumber and ShortText as separate fields. The network
analytics route currently groups by the number and replaces it with a fixed
commercial name (1=Gold, 2=Silver, 4=Bronze, 8=Blue). These tests exercise
the product path:

  Trip_SearchRS XML
    -> SabreClient XML parse and body extract
    -> convert_reservations
    -> reservations collection
    -> GET /analytics/network

They fail while that route still invents Gold/Silver/Bronze/Blue/Unknown.
The numeric level stays the tier key. The displayed name is the stored short
text, or the number itself when the short text is missing.
"""

import os
import sys
from copy import deepcopy

# Point at a local server before any backend import reads MONGODB_URI.
os.environ["MONGODB_URI"] = os.environ.get(
    "FALCONEYE_TEST_MONGODB_URI", "mongodb://127.0.0.1:27017"
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest
from fastapi.testclient import TestClient

from backend.api.database import close_db, get_db
from backend.api.main import app
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

SAMPLE_XML = os.path.join(
    REPO_ROOT,
    "sample_data",
    "sample_xmlfiles",
    "Trip_SearchRS_GF2057_BOM_2026-03-19.xml",
)
DEPARTURE_DATE = "2099-11-19"
GUESSED_NAMES = {"Gold", "Silver", "Bronze", "Blue", "Unknown"}


def _trip_search_body(xml_text):
    """Parse a SOAP Trip_SearchRS the same way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not use instance state; skip SabreClient.__init__,
    # which reads live credential names from the environment.
    client = object.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, "Trip_SearchRS")


def _convert(xml_text, flight_number):
    body = _trip_search_body(xml_text)
    document = convert_reservations(
        body, "GF", flight_number, "BOM", DEPARTURE_DATE
    )
    # The analytics route never reads the archived payload.
    document.pop("_raw", None)
    return document


def _passenger(last_name, number=None, level=None, short=None):
    frequent_flyer = ""
    if number is not None:
        level_xml = (
            f"<stl19:TierLevelNumber>{level}</stl19:TierLevelNumber>"
            if level is not None
            else ""
        )
        short_xml = (
            f"<stl19:ShortText>{short}</stl19:ShortText>" if short is not None else ""
        )
        frequent_flyer = (
            "<stl19:FrequentFlyer>"
            f"<stl19:Number>{number}</stl19:Number>"
            f"{level_xml}{short_xml}"
            "</stl19:FrequentFlyer>"
        )
    return (
        "<stl19:Passenger>"
        f"<stl19:LastName>{last_name}</stl19:LastName>"
        "<stl19:FirstName>TEST</stl19:FirstName>"
        f"{frequent_flyer}"
        "</stl19:Passenger>"
    )


def _reservation(locator, passengers):
    return (
        f'<Reservation Locator="{locator}">'
        "<stl19:GetReservationRS>"
        '<stl19:Reservation numberInParty="1" numberOfInfants="0">'
        "<stl19:BookingDetails>"
        "<stl19:CreationTimestamp>2099-11-01T00:00:00</stl19:CreationTimestamp>"
        "</stl19:BookingDetails>"
        "<stl19:PassengerReservation><stl19:Passengers>"
        f"{''.join(passengers)}"
        "</stl19:Passengers></stl19:PassengerReservation>"
        "</stl19:Reservation>"
        "</stl19:GetReservationRS>"
        "</Reservation>"
    )


def _envelope(reservations):
    joined = "".join(reservations)
    count = len(reservations)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body><Trip_SearchRS>"
        f'<ReservationsList NumberResults="{count}">'
        f"<Reservations>{joined}</Reservations>"
        "</ReservationsList></Trip_SearchRS></soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _stored_pairs(document):
    pairs = []
    for reservation in document["reservations"]:
        for passenger in reservation["passengers"]:
            if passenger.get("frequentFlyerNumber", "") == "":
                continue
            pairs.append(
                (
                    reservation.get("pnr", ""),
                    passenger.get("lastName", ""),
                    "" if not passenger.get("ffTierLevel") else str(passenger["ffTierLevel"]),
                    "" if not passenger.get("ffTierName") else str(passenger["ffTierName"]),
                )
            )
    return pairs


def _expected_loyalty(documents):
    """Latest reservations snapshot per flight, labeled from short text.

    Mirrors the date and latest-snapshot selection in the analytics pipeline,
    then groups by numeric level AND short text. The displayed name is the
    short text, or the number when that text is missing.
    """
    latest = {}
    for document in documents:
        key = (
            document["flightNumber"],
            document["departureAirport"],
            document["departureDate"],
        )
        current = latest.get(key)
        if current is None or document.get("fetchedAt", "") >= current.get("fetchedAt", ""):
            latest[key] = document

    counts = {}
    for document in latest.values():
        for reservation in document.get("reservations", []):
            for passenger in reservation.get("passengers", []):
                if passenger.get("frequentFlyerNumber", "") == "":
                    continue
                level = passenger.get("ffTierLevel") or ""
                level = str(level) if level else ""
                short = passenger.get("ffTierName") or ""
                short = str(short) if short else ""
                label = short if short else level
                counts[(level, label)] = counts.get((level, label), 0) + 1
    return sorted((level, label, count) for (level, label), count in counts.items())


def _actual_loyalty(payload):
    return sorted(
        (item["tier"], item["tierName"], item["count"])
        for item in payload["loyaltyDistribution"]
    )


@pytest.fixture(scope="module")
def api():
    close_db()
    with TestClient(app) as client:
        yield client
    close_db()


@pytest.fixture
def reservations(api):
    db = get_db()
    db.reservations.delete_many({"flightNumber": {"$regex": r"^LOYALTYTEST"}})
    inserted = []

    def _insert(document):
        stored = deepcopy(document)
        db.reservations.insert_one(stored)
        inserted.append(stored)
        return stored

    yield _insert
    db.reservations.delete_many({"flightNumber": {"$regex": r"^LOYALTYTEST"}})


def _analytics(api, flight_number):
    response = api.get(
        "/analytics/network",
        params={
            "date_from": DEPARTURE_DATE,
            "date_to": DEPARTURE_DATE,
            "flight_number": flight_number,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_network_analytics_labels_each_short_text_not_a_fixed_commercial_name(
    api, reservations
):
    """One numeric level can carry different Sabre short texts.

    A map from the number alone cannot name these passengers, and an older
    snapshot for the same flight must not be counted.
    """
    latest_xml = _envelope(
        [
            _reservation(
                "LATEST",
                [
                    _passenger("SILVER", number="1001", level="1", short="SLV"),
                    _passenger("GOLD", number="1002", level="1", short="GLD"),
                    _passenger("SILVER_AT_TWO", number="1003", level="2", short="SLV"),
                    _passenger("NUMBER_ONLY", number="1004", level="2"),
                    _passenger("GOLD_AT_FOUR", number="1005", level="4", short="GLD"),
                    _passenger("SILVER_AT_EIGHT", number="1006", level="8", short="SLV"),
                    _passenger("SHORT_ONLY", number="1007", short="BLU"),
                    _passenger("BLANK_TIER", number="1008"),
                    _passenger("NOT_A_MEMBER", level="1", short="GLD"),
                ],
            )
        ]
    )
    latest = _convert(latest_xml, "LOYALTYTESTMIX")
    latest["fetchedAt"] = "2099-11-19T01:00:00Z"
    stored_pairs = _stored_pairs(latest)
    assert ("LATEST", "SILVER", "1", "SLV") in stored_pairs
    assert ("LATEST", "GOLD", "1", "GLD") in stored_pairs
    assert ("LATEST", "NUMBER_ONLY", "2", "") in stored_pairs
    assert ("LATEST", "SHORT_ONLY", "", "BLU") in stored_pairs
    assert ("LATEST", "BLANK_TIER", "", "") in stored_pairs
    assert all(pair[1] != "NOT_A_MEMBER" for pair in stored_pairs)

    older = deepcopy(latest)
    older["fetchedAt"] = "2099-11-19T00:00:00Z"
    older["reservations"] = [
        {
            "pnr": "OLDER",
            "passengers": [
                {
                    "lastName": "STALE",
                    "frequentFlyerNumber": "1999",
                    "ffTierLevel": "4",
                    "ffTierName": "BLK",
                }
            ],
        }
    ]

    reservations(older)
    reservations(latest)

    actual = _actual_loyalty(_analytics(api, "LOYALTYTESTMIX"))
    expected = _expected_loyalty([older, latest])
    assert actual == expected
    assert ("4", "BLK", 1) not in actual
    assert not any(name in GUESSED_NAMES for _, name, _ in actual)
    assert ("1", "SLV", 1) in actual
    assert ("1", "GLD", 1) in actual
    assert ("2", "SLV", 1) in actual
    assert ("2", "2", 1) in actual
    assert ("4", "GLD", 1) in actual
    assert ("8", "SLV", 1) in actual
    assert ("", "BLU", 1) in actual
    assert ("", "", 1) in actual


def test_committed_gf2057_trip_search_does_not_label_tier_1_slv_as_gold(
    api, reservations
):
    """The committed GF2057 Trip Search response pairs tier 1 with short text SLV."""
    with open(SAMPLE_XML, encoding="utf-8") as handle:
        document = _convert(handle.read(), "LOYALTYTEST2057")
    document["fetchedAt"] = "2099-11-19T01:00:00Z"

    patel = [
        pair
        for pair in _stored_pairs(document)
        if pair[0] == "ONHAFU" and pair[1] == "PATEL"
    ]
    assert patel == [("ONHAFU", "PATEL", "1", "SLV")]

    reservations(document)
    actual = _actual_loyalty(_analytics(api, "LOYALTYTEST2057"))
    assert actual == _expected_loyalty([document])
    assert ("1", "SLV", 1) in actual
    assert ("8", "BLU", 5) in actual
    assert not any(name in GUESSED_NAMES for _, name, _ in actual)
