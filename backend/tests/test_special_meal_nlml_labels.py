"""Special-meal codes from Trip Search must stay intact through network analytics.

NLML is the Sabre/IATA non-lactose meal code. LSML is the low-sodium code.
This drives a Trip_SearchRS body through the SOAP parser and reservation
converter, stores two snapshots, and reads the network-analytics meal distribution.
The chart label for those codes is covered by the frontend test.
"""

import copy
import os
import unittest
from urllib.parse import urlparse

# Repository root (backend/tests/ -> ../..)
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

FLIGHT = "9191"
ORIGIN = "BAH"
DEPARTURE_DATE = "2099-11-21"

# Counts on the latest snapshot. An older snapshot carries a different NLML
# total and must not be included.
EXPECTED_MEAL_COUNTS = {"NLML": 4, "LSML": 2, "ZZML": 1}


def _passenger(last_name, first_name, meal_codes):
    if meal_codes is None:
        requests = ""
    else:
        blocks = "".join(
            "<stl19:SpecialMealRequest>"
            f"<stl19:MealType>{code}</stl19:MealType>"
            "<stl19:FlightNumber>9191</stl19:FlightNumber>"
            "<stl19:VendorCode>GF</stl19:VendorCode>"
            "</stl19:SpecialMealRequest>"
            for code in meal_codes
        )
        requests = f"<stl19:SpecialRequests>{blocks}</stl19:SpecialRequests>"
    return (
        "<stl19:Passenger>"
        f"<stl19:LastName>{last_name}</stl19:LastName>"
        f"<stl19:FirstName>{first_name}</stl19:FirstName>"
        f"{requests}"
        "</stl19:Passenger>"
    )


def _reservation(locator, passengers_xml):
    return f"""
          <Reservation Locator="{locator}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2099-11-01T00:00:00</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    {passengers_xml}
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
    """


def trip_search_xml():
    """SOAP Trip_SearchRS shaped like the stored Sabre sample responses."""
    reservations = [
        # A list of SpecialMealRequest elements: only the first MealType is stored.
        _reservation(
            "NL0001",
            _passenger("DAIRY", "FREE", ["NLML", "LSML"]),
        ),
        _reservation(
            "NL0002",
            _passenger("ONE", "NL", ["NLML"])
            + _passenger("TWO", "NL", ["NLML"])
            + _passenger("THREE", "NL", ["NLML"]),
        ),
        _reservation(
            "LS0001",
            _passenger("LOW", "SODIUM", ["LSML"])
            + _passenger("LOW", "SALT", ["LSML"]),
        ),
        _reservation(
            "ZZ0001",
            _passenger("UNKNOWN", "CODE", ["ZZML"])
            + _passenger("NO", "MEAL", None),
        ),
    ]
    body = "\n".join(reservations)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="4">
        <Reservations>
          {body}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _require_local_mongo():
    uri = os.environ.get("MONGODB_URI", "")
    if not uri:
        raise unittest.SkipTest("MONGODB_URI is not set")
    host = urlparse(uri).hostname
    if host not in {"127.0.0.1", "localhost"}:
        raise unittest.SkipTest("MONGODB_URI must point at localhost for this test")
    return uri


class SpecialMealNlmlAnalyticsTest(unittest.TestCase):
    def test_trip_search_nlml_reaches_network_analytics_as_its_own_code(self):
        _require_local_mongo()

        from backend.sabre.client import SabreClient
        from backend.feeder.converter import convert_reservations
        from backend.feeder import storage
        from backend.api.routes.analytics import _run_network_analytics

        parsed = SabreClient._parse_xml(trip_search_xml())
        # _extract_body does not use credentials; avoid constructing a client.
        client = object.__new__(SabreClient)
        raw = SabreClient._extract_body(client, parsed, "Trip_SearchRS")
        latest = convert_reservations(raw, "GF", FLIGHT, ORIGIN, DEPARTURE_DATE)

        stored = []
        for reservation in latest["reservations"]:
            for passenger in reservation["passengers"]:
                stored.append(
                    (
                        passenger["lastName"],
                        passenger["firstName"],
                        passenger["specialMeal"],
                    )
                )
        self.assertIn(("DAIRY", "FREE", "NLML"), stored)
        self.assertIn(("LOW", "SODIUM", "LSML"), stored)
        self.assertIn(("UNKNOWN", "CODE", "ZZML"), stored)
        self.assertIn(("NO", "MEAL", ""), stored)
        self.assertEqual(
            sorted(code for _, _, code in stored if code),
            ["LSML", "LSML", "NLML", "NLML", "NLML", "NLML", "ZZML"],
        )

        older = copy.deepcopy(latest)
        older["fetchedAt"] = "2099-11-20T00:00:00Z"
        for reservation in older["reservations"]:
            for passenger in reservation["passengers"]:
                passenger["specialMeal"] = "NLML"
        latest["fetchedAt"] = "2099-11-21T12:00:00Z"

        db = storage.get_db()
        db["reservations"].delete_many(
            {"flightNumber": FLIGHT, "departureDate": DEPARTURE_DATE}
        )
        try:
            storage.store_reservations(older)
            storage.store_reservations(latest)
            result = _run_network_analytics(DEPARTURE_DATE, DEPARTURE_DATE)
        finally:
            db["reservations"].delete_many(
                {"flightNumber": FLIGHT, "departureDate": DEPARTURE_DATE}
            )

        distribution = result["mealDistribution"]
        by_code = {item["mealCode"]: item["count"] for item in distribution}
        self.assertEqual(by_code, EXPECTED_MEAL_COUNTS)
        # The API returns Sabre codes. It must not rename NLML to a salt label.
        self.assertNotIn("No Salt", by_code)
        self.assertNotIn("Non-lactose", by_code)
        self.assertEqual(
            [item["mealCode"] for item in distribution],
            ["NLML", "LSML", "ZZML"],
        )


if __name__ == "__main__":
    unittest.main()
