"""ACS Flight Detail special-meal totals must survive ingestion and be served.

Sabre sends Meals and SpecialMeals as separate cabin totals. The flight-status
document must store SpecialMeals on each cabin as ``specialMeals`` next to
``meals``. The flight list, flight status, and status history return that cabin
object. The dashboard meal summary keeps the reservation meal-code breakdown and
also reports the Sabre cabin totals, because those totals are not the same count.

These tests fail while SpecialMeals is dropped or the dashboard summary ignores it.
"""

import copy
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api.routes import flights
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml"
)

_SPECIAL_MEALS = re.compile(r"<SpecialMeals>\s*(\d+)\s*</SpecialMeals>")


def _parse_flight_detail(xml_text):
    """Run the product SOAP parse and body extract. No network call.

    ``_extract_body`` does not use instance state. ``__new__`` skips
    ``SabreClient.__init__``, which only reads session settings.
    """
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "ns3:ACS_FlightDetailRS")


def _replace_special_meals(xml_text, values):
    values = list(values)
    found = _SPECIAL_MEALS.findall(xml_text)
    if len(found) != len(values):
        raise AssertionError(
            f"sample has {len(found)} SpecialMeals elements, test expected {len(values)}"
        )
    index = 0

    def _sub(_match):
        nonlocal index
        replacement = f"<SpecialMeals>{values[index]}</SpecialMeals>"
        index += 1
        return replacement

    return _SPECIAL_MEALS.sub(_sub, xml_text)


def _convert(xml_text):
    body = _parse_flight_detail(xml_text)
    doc = convert_flight_status(body, "GF", "2057", "BOM", "2026-03-19")
    # The archived response is not part of the served cabin object.
    doc.pop("_raw", None)
    return doc


def _reservation_with_one_special_meal():
    return {
        "flightNumber": "2057",
        "departureAirport": "BOM",
        "departureDate": "2026-03-19",
        "reservations": [
            {
                "numberInParty": 2,
                "pointOfSale": {"agentSine": "WEB"},
                "passengers": [
                    {"specialMeal": "VGML", "lastName": "DOE"},
                    {"specialMeal": "", "lastName": "ROE"},
                ],
            }
        ],
    }


def _passenger_list():
    return {
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "destination": "DMM",
        "totalPassengers": 1,
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": 1,
        "passengers": [
            {
                "pnr": "ABC123",
                "lastName": "DOE",
                "firstName": "JANE",
                "cabin": "Y",
                "bookingClass": "Y",
                "isCheckedIn": False,
                "isBoarded": False,
                "isRevenue": True,
                "editCodes": [],
            }
        ],
    }


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    def limit(self, count):
        return _Cursor(self._docs[:count])

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    """In-memory stand-in for the collections these routes read.

    ``aggregate`` applies the route's ``$project`` exclusions and returns the
    stored document. It does not invent cabin fields.
    """

    def __init__(self, docs):
        self.docs = docs

    def aggregate(self, pipeline, allowDiskUse=False):
        exclusions = []
        for stage in pipeline:
            project = stage.get("$project")
            if not project:
                continue
            for key, flag in project.items():
                if flag == 0 and key.startswith("doc."):
                    exclusions.append(key.split(".", 1)[1])
        results = []
        for doc in self.docs:
            served = copy.deepcopy(doc)
            for field in exclusions:
                served.pop(field, None)
            results.append({
                "_id": {
                    "airline": served.get("airline", "GF"),
                    "flightNumber": served.get("flightNumber"),
                    "origin": served.get("origin", ""),
                    "departureDate": served.get("departureDate", ""),
                },
                "doc": served,
            })
        return results

    def find_one(self, query=None, projection=None, sort=None):
        if not self.docs:
            return None
        return copy.deepcopy(self.docs[0])

    def find(self, query=None, projection=None):
        return _Cursor([copy.deepcopy(doc) for doc in self.docs])


class _Database:
    def __init__(self, **collections):
        self._collections = {
            name: _Collection(docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return self._collections.get(name, _Collection([]))


class AcsSpecialMealTests(unittest.TestCase):
    def setUp(self):
        flights._dashboard_cache.clear()

    def test_committed_sample_stores_zero_special_meals_beside_regular_meals(self):
        xml_text = _SAMPLE.read_text(encoding="utf-8")
        parsed_counts = _parse_flight_detail(xml_text)["PassengerCounts"]
        self.assertEqual(
            [(c.get("@classOfService"), c.get("Meals"), c.get("SpecialMeals"))
             for c in parsed_counts],
            [("Y", "57", "0"), ("J", "2", "0")],
        )

        doc = _convert(xml_text)
        database = _Database(flight_status=[doc])
        with patch("backend.api.routes.flights.get_db", return_value=database):
            status = flights.get_flight_status(
                "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
            )
        counts = status["passengerCounts"]
        self.assertEqual(counts["Y"]["meals"], 57)
        self.assertEqual(counts["J"]["meals"], 2)
        self.assertIn("specialMeals", counts["Y"])
        self.assertIn("specialMeals", counts["J"])
        self.assertEqual(counts["Y"]["specialMeals"], 0)
        self.assertEqual(counts["J"]["specialMeals"], 0)

    def test_nonzero_special_meals_reach_list_status_and_history(self):
        xml_text = _replace_special_meals(
            _SAMPLE.read_text(encoding="utf-8"), ["7", "3"]
        )
        parsed_counts = _parse_flight_detail(xml_text)["PassengerCounts"]
        self.assertEqual(
            [(c.get("@classOfService"), c.get("Meals"), c.get("SpecialMeals"))
             for c in parsed_counts],
            [("Y", "57", "7"), ("J", "2", "3")],
        )

        doc = _convert(xml_text)
        database = _Database(flight_status=[doc])
        with patch("backend.api.routes.flights.get_db", return_value=database):
            listed = flights.list_flights(date="2026-03-19")
            status = flights.get_flight_status(
                "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
            )
            history = flights.get_flight_status_history(
                "2057", origin="BOM", date="2026-03-19", limit=20,
            )

        self.assertEqual(len(listed), 1)
        for served in (
            listed[0]["passengerCounts"],
            status["passengerCounts"],
            history[0]["passengerCounts"],
        ):
            self.assertEqual(served["Y"]["meals"], 57)
            self.assertEqual(served["J"]["meals"], 2)
            self.assertIn("specialMeals", served["Y"])
            self.assertIn("specialMeals", served["J"])
            self.assertEqual(served["Y"]["specialMeals"], 7)
            self.assertEqual(served["J"]["specialMeals"], 3)

    def test_dashboard_meal_summary_reports_sabre_totals_separately_from_reservation_codes(self):
        xml_text = _replace_special_meals(
            _SAMPLE.read_text(encoding="utf-8"), ["7", "3"]
        )
        doc = _convert(xml_text)
        database = _Database(
            flight_status=[doc],
            passenger_list=[_passenger_list()],
            reservations=[_reservation_with_one_special_meal()],
        )
        with patch("backend.api.routes.flights.get_db", return_value=database):
            dashboard = flights.get_flight_dashboard(
                "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
            )

        counts = dashboard["flightStatus"]["passengerCounts"]
        self.assertEqual(counts["Y"]["meals"], 57)
        self.assertIn("specialMeals", counts["Y"])
        self.assertIn("specialMeals", counts["J"])
        self.assertEqual(counts["Y"]["specialMeals"], 7)
        self.assertEqual(counts["J"]["specialMeals"], 3)

        summary = dashboard["specialRequestsSummary"]
        self.assertIsNotNone(summary)
        # Reservation meal codes stay a code breakdown. They are not the ACS total.
        self.assertEqual(summary["specialMeals"], {"VGML": 1})
        self.assertEqual(summary["totalSpecialMeals"], 1)
        self.assertIn("sabreSpecialMeals", summary)
        self.assertIn("sabreTotalSpecialMeals", summary)
        self.assertEqual(summary["sabreSpecialMeals"], {"Y": 7, "J": 3})
        self.assertEqual(summary["sabreTotalSpecialMeals"], 10)

        insights = dashboard["insights"]
        self.assertIsNotNone(insights)
        self.assertEqual(insights["mealCodes"], {"VGML": 1})
        self.assertIn("sabreSpecialMeals", insights)
        self.assertIn("sabreSpecialMealTotal", insights)
        self.assertEqual(insights["sabreSpecialMeals"], {"Y": 7, "J": 3})
        self.assertEqual(insights["sabreSpecialMealTotal"], 10)

    def test_dashboard_reports_explicit_zero_when_sabre_special_meals_are_zero(self):
        doc = _convert(_SAMPLE.read_text(encoding="utf-8"))
        database = _Database(
            flight_status=[doc],
            passenger_list=[_passenger_list()],
            reservations=[_reservation_with_one_special_meal()],
        )
        with patch("backend.api.routes.flights.get_db", return_value=database):
            dashboard = flights.get_flight_dashboard(
                "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
            )

        summary = dashboard["specialRequestsSummary"]
        self.assertEqual(summary["totalSpecialMeals"], 1)
        self.assertIn("sabreSpecialMeals", summary)
        self.assertIn("sabreTotalSpecialMeals", summary)
        self.assertEqual(summary["sabreSpecialMeals"], {"Y": 0, "J": 0})
        self.assertEqual(summary["sabreTotalSpecialMeals"], 0)
        self.assertIn("sabreSpecialMealTotal", dashboard["insights"])
        self.assertEqual(dashboard["insights"]["sabreSpecialMealTotal"], 0)
        self.assertIn(
            "specialMeals", dashboard["flightStatus"]["passengerCounts"]["Y"]
        )
        self.assertEqual(
            dashboard["flightStatus"]["passengerCounts"]["Y"]["specialMeals"], 0
        )

    def test_single_passenger_counts_element_keeps_special_meals(self):
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS>
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <Origin>BOM</Origin>
          <ScheduledDepartureDate>2026-03-19</ScheduledDepartureDate>
          <FlightStatus>OPEN</FlightStatus>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <PassengerCounts classOfService="Y">
        <Booked>10</Booked>
        <Meals>10</Meals>
        <SpecialMeals>4</SpecialMeals>
        <TotalOnBoard>8</TotalOnBoard>
      </PassengerCounts>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
        body = _parse_flight_detail(xml_text)
        self.assertIsInstance(body["PassengerCounts"], dict)
        self.assertEqual(body["PassengerCounts"]["SpecialMeals"], "4")

        doc = _convert(xml_text)
        database = _Database(
            flight_status=[doc],
            passenger_list=[_passenger_list()],
            reservations=[_reservation_with_one_special_meal()],
        )
        with patch("backend.api.routes.flights.get_db", return_value=database):
            dashboard = flights.get_flight_dashboard(
                "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None,
            )
        counts = dashboard["flightStatus"]["passengerCounts"]
        self.assertEqual(counts["Y"]["meals"], 10)
        self.assertIn("specialMeals", counts["Y"])
        self.assertEqual(counts["Y"]["specialMeals"], 4)
        summary = dashboard["specialRequestsSummary"]
        self.assertEqual(summary["totalSpecialMeals"], 1)
        self.assertIn("sabreSpecialMeals", summary)
        self.assertIn("sabreTotalSpecialMeals", summary)
        self.assertEqual(summary["sabreSpecialMeals"], {"Y": 4})
        self.assertEqual(summary["sabreTotalSpecialMeals"], 4)
        self.assertIn("sabreSpecialMealTotal", dashboard["insights"])
        self.assertEqual(dashboard["insights"]["sabreSpecialMealTotal"], 4)


if __name__ == "__main__":
    unittest.main()
