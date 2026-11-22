"""Network analytics must rank real nationalities and special meals only.

Sabre reservations with no document nationality are stored with an empty
string. The network analytics response is what the Top Nationalities and
Special Meal charts plot. An empty value must not take one of the fifteen
ranked slots, and a null nationality must not take one either. These tests
call GET /analytics/network with no credentials.

They fail while an empty string still consumes a slot inside the aggregation
limit. They pass once empty and null values are excluded before that limit.
"""

import os
import unittest
from xml.sax.saxutils import escape

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["REDIS_URL"] = (
    "redis://127.0.0.1:6399/0?socket_connect_timeout=0.2&socket_timeout=0.2"
)

from fastapi.testclient import TestClient
from pymongo import MongoClient

from backend.api.database import DB_NAME
from backend.api.main import app
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

DATE = "2026-04-15"
OTHER_DATE = "2026-01-01"
BLANK_COUNT = 40
NULL_COUNT = 30
RANK_LIMIT = 15


def _ranked_codes(prefix, count):
    """prefix01..prefix{count} with strictly decreasing counts, highest first.

    prefix01 has `count` passengers and the last code has one. The top chart
    keeps the first RANK_LIMIT of these pairs, so the fifteenth real code is
    the one an empty bucket can push out.
    """
    return [(f"{prefix}{index:02d}", count - index + 1) for index in range(1, count + 1)]


def _top_ranked(prefix):
    return _ranked_codes(prefix, 16)[:RANK_LIMIT]


def _passenger_xml(index, nationality_mode, nationality, meal, wheelchair):
    special = []
    if nationality_mode == "value":
        special.append(
            "<stl19:APISRequest><stl19:DOCSEntry>"
            f"<stl19:DocumentNationalityCountry>{escape(nationality)}</stl19:DocumentNationalityCountry>"
            "<stl19:Gender>F</stl19:Gender>"
            "</stl19:DOCSEntry></stl19:APISRequest>"
        )
    elif nationality_mode == "empty":
        # Present but empty. xmltodict yields None; the converter stores that None.
        special.append(
            "<stl19:APISRequest><stl19:DOCSEntry>"
            "<stl19:DocumentNationalityCountry></stl19:DocumentNationalityCountry>"
            "<stl19:Gender>F</stl19:Gender>"
            "</stl19:DOCSEntry></stl19:APISRequest>"
        )
    if meal:
        special.append(
            "<stl19:SpecialMealRequest>"
            f"<stl19:MealType>{escape(meal)}</stl19:MealType>"
            "</stl19:SpecialMealRequest>"
        )
    if wheelchair:
        special.append(
            "<stl19:WheelchairRequest>"
            f"<stl19:WheelchairCode>{escape(wheelchair)}</stl19:WheelchairCode>"
            "</stl19:WheelchairRequest>"
        )
    special_xml = ""
    if special:
        special_xml = "<stl19:SpecialRequests>" + "".join(special) + "</stl19:SpecialRequests>"
    return (
        f'<Reservation Locator="P{index:05d}">'
        "<stl19:GetReservationRS><stl19:Reservation numberInParty=\"1\">"
        "<stl19:PassengerReservation><stl19:Passengers><stl19:Passenger>"
        f"<stl19:LastName>PAX{index:05d}</stl19:LastName>"
        "<stl19:FirstName>TEST</stl19:FirstName>"
        f"{special_xml}"
        "</stl19:Passenger></stl19:Passengers></stl19:PassengerReservation>"
        "</stl19:Reservation></stl19:GetReservationRS></Reservation>"
    )


def _trip_xml(passengers):
    body = "".join(
        _passenger_xml(index, mode, nationality, meal, wheelchair)
        for index, (mode, nationality, meal, wheelchair) in enumerate(passengers)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body><Trip_SearchRS>"
        f'<ReservationsList NumberResults="{len(passengers)}"><Reservations>'
        f"{body}"
        "</Reservations></ReservationsList>"
        "</Trip_SearchRS></soap-env:Body></soap-env:Envelope>"
    )


def _stored_reservation(passengers, flight_number, origin, departure_date, fetched_at):
    """Parse SOAP the way SabreClient does, then normalize it for MongoDB."""
    parsed = SabreClient._parse_xml(_trip_xml(passengers))
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    document = convert_reservations(body, "GF", flight_number, origin, departure_date)
    document["fetchedAt"] = fetched_at
    return document


def _nationalities(document):
    values = []
    for reservation in document["reservations"]:
        for passenger in reservation["passengers"]:
            values.append(passenger["nationality"])
    return values


class BlankNationalityAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.mongo = MongoClient(
            os.environ["MONGODB_URI"], serverSelectionTimeoutMS=2000
        )
        self.mongo.admin.command("ping")
        host, port = self.mongo.address
        if host not in ("127.0.0.1", "localhost") or port != 27017:
            self.fail("refusing to run against a non-local MongoDB")
        self.db = self.mongo[DB_NAME]
        for name in ("reservations", "flights", "passenger_list", "changes"):
            self.db[name].delete_many({})
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.mongo.close()

    def _insert(self, document):
        self.db["reservations"].insert_one(document)

    def _analytics(self, **params):
        query = {"date_from": DATE, "date_to": DATE, **params}
        response = self.client.get("/analytics/network", params=query)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("authorization", {key.lower() for key in response.request.headers})
        return response.json()

    def _seed_nationality_flight(self):
        current = []
        for code, count in _ranked_codes("C", 16):
            current.extend([("value", code, None, None)] * count)
        # No APIS document: converter leaves nationality as "".
        current.extend([("omit", None, None, None)] * BLANK_COUNT)
        # Empty nationality element with a gender: converter stores None.
        current.extend([("empty", None, None, None)] * NULL_COUNT)

        latest = _stored_reservation(
            current, "2057", "BAH", DATE, "2026-04-15T12:00:00Z"
        )
        stored = _nationalities(latest)
        self.assertEqual(stored.count(""), BLANK_COUNT)
        self.assertEqual(stored.count(None), NULL_COUNT)
        self.assertEqual(stored.count("C15"), 2)
        self.assertEqual(stored.count("C16"), 1)

        # Older snapshot of the same flight would hide the missing country
        # if the aggregation summed every fetch instead of the latest one.
        older_passengers = [("value", "C15", None, None)] * 100
        older = _stored_reservation(
            older_passengers, "2057", "BAH", DATE, "2026-04-15T00:00:00Z"
        )
        outside = _stored_reservation(
            [("value", "QQ", None, None)] * 80,
            "2057",
            "BAH",
            OTHER_DATE,
            "2026-01-01T12:00:00Z",
        )
        self._insert(older)
        self._insert(latest)
        self._insert(outside)
        return latest

    def test_unauthenticated_network_analytics_keeps_fifteenth_nationality(self):
        self._seed_nationality_flight()
        payload = self._analytics()
        ranked = [
            (row["nationality"], row["count"])
            for row in payload["nationalityDistribution"]
        ]
        expected = _top_ranked("C")
        self.assertEqual(ranked, expected)
        self.assertNotIn("", [code for code, _count in ranked])
        self.assertNotIn(None, [code for code, _count in ranked])
        self.assertNotIn("QQ", [code for code, _count in ranked])
        self.assertNotIn("C16", [code for code, _count in ranked])

    def test_origin_filter_keeps_fifteenth_nationality_for_that_origin(self):
        self._seed_nationality_flight()
        other_origin = _stored_reservation(
            [("value", "ZZ", None, None)] * 80,
            "3001",
            "LHR",
            DATE,
            "2026-04-15T12:00:00Z",
        )
        self._insert(other_origin)
        payload = self._analytics(origin="BAH")
        ranked = [
            (row["nationality"], row["count"])
            for row in payload["nationalityDistribution"]
        ]
        self.assertEqual(ranked, _top_ranked("C"))
        self.assertNotIn("ZZ", [code for code, _count in ranked])

    def test_blank_special_meal_does_not_displace_fifteenth_meal(self):
        passengers = []
        for code, count in _ranked_codes("M", 16):
            passengers.extend([("value", "BH", code, None)] * count)
        passengers.extend([("value", "BH", None, None)] * BLANK_COUNT)
        document = _stored_reservation(
            passengers, "2058", "BAH", DATE, "2026-04-15T12:00:00Z"
        )
        meals = [
            passenger["specialMeal"]
            for reservation in document["reservations"]
            for passenger in reservation["passengers"]
        ]
        self.assertEqual(meals.count(""), BLANK_COUNT)
        self.assertEqual(meals.count("M15"), 2)
        self._insert(document)

        payload = self._analytics()
        ranked = [
            (row["mealCode"], row["count"]) for row in payload["mealDistribution"]
        ]
        self.assertEqual(ranked, _top_ranked("M"))
        self.assertNotIn("", [code for code, _count in ranked])

    def test_blank_wheelchair_request_does_not_drop_real_codes(self):
        # Wheelchair aggregation has the same repeated match key and no limit.
        # Every non-empty code must still be returned with its own count.
        codes = ["WCHR", "WCHS", "WCHC", "WCMP", "WCOB"]
        passengers = []
        for code in codes:
            passengers.extend([("value", "BH", None, code)] * 3)
        passengers.extend([("value", "BH", None, None)] * 12)
        document = _stored_reservation(
            passengers, "2059", "BAH", DATE, "2026-04-15T12:00:00Z"
        )
        chairs = [
            passenger["wheelchairCode"]
            for reservation in document["reservations"]
            for passenger in reservation["passengers"]
        ]
        self.assertEqual(chairs.count(""), 12)
        self._insert(document)

        payload = self._analytics()
        ranked = {
            row["code"]: row["count"] for row in payload["wheelchairDistribution"]
        }
        self.assertEqual(ranked, {code: 3 for code in codes})
        self.assertNotIn("", ranked)


if __name__ == "__main__":
    unittest.main()
