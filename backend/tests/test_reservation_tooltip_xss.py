"""Reservation free text must not run as HTML in flight chart tooltips.

A booker-controlled form-of-payment string, corporate id, and the other
passenger strings copied from Sabre are charted by concatenating them with an
HTML line break. These tests follow that string from the SOAP body the feeder
parses through the insight and analytics labels into the chart builders, then
open the tooltip in the ECharts version the frontend ships.

They fail while the tooltip markup is still treated as HTML. Escaping the
label before it is inserted, rendering the tooltip as text, or removing the
markup before the label is charted makes them pass.
"""

import json
import os
import subprocess
import sys

import mongomock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.analytics import _run_network_analytics
from backend.api.routes.flights import _build_insights
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient
import backend.api.routes.analytics as analytics_routes

PAYLOAD = "<img src=x alt=FALCONXSS onerror=window.__tooltipHit=1>"


def _xml_text(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _parse_body(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _trip_search_xml(payload):
    text = _xml_text(payload)
    return f"""<?xml version="1.0"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="ABCDEF">
            <stl19:GetReservationRS>
              <stl19:Reservation>
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-01T00:00:00</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:POS>
                  <stl19:Source AgentDutyCode="7" AgentSine="{text}" ISOCountry="BH" PseudoCityCode="BAH"/>
                </stl19:POS>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger>
                      <stl19:LastName>TEST</stl19:LastName>
                      <stl19:FirstName>A</stl19:FirstName>
                      <stl19:FrequentFlyer>
                        <stl19:SupplierCode>GF</stl19:SupplierCode>
                        <stl19:Number>999000</stl19:Number>
                        <stl19:TierLevelNumber>{text}</stl19:TierLevelNumber>
                        <stl19:ShortText>BLU</stl19:ShortText>
                      </stl19:FrequentFlyer>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
                <stl19:OpenReservationElements>
                  <or114:OpenReservationElement type="FP">
                    <or114:FormOfPayment>
                      <or114:Other>
                        <or114:Text>{text}</or114:Text>
                      </or114:Other>
                    </or114:FormOfPayment>
                  </or114:OpenReservationElement>
                </stl19:OpenReservationElements>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _passenger_list_xml(payload):
    text = _xml_text(payload)
    return f"""<?xml version="1.0"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>1</Flight>
          <Origin>BAH</Origin>
          <Destination>DXB</Destination>
          <DepartureDate>2026-03-19</DepartureDate>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>TEST</LastName>
            <FirstName>A</FirstName>
          </Name_Details>
          <PassengerType>{text}</PassengerType>
          <CorpID>{text}</CorpID>
          <Cabin>Y</Cabin>
          <BaggageRouteList>
            <BaggageRoute>
              <Destination>{text}</Destination>
            </BaggageRoute>
          </BaggageRouteList>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details>
            <LastName>STANDBY</LastName>
            <FirstName>B</FirstName>
          </Name_Details>
          <PassengerType>S</PassengerType>
          <Cabin>{text}</Cabin>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _as_json_value(value):
    """Mirror the API JSON encode/decode the browser performs before charting."""
    return json.loads(json.dumps(value))


def _chart_cases(payload):
    reservation_doc = convert_reservations(
        _parse_body(_trip_search_xml(payload), "Trip_SearchRS"),
        "GF",
        "1",
        "BAH",
        "2026-03-19",
    )
    passenger_doc = convert_passenger_list(
        _parse_body(_passenger_list_xml(payload), "GetPassengerListRS"),
        "GF",
        "1",
        "2026-03-19",
        "BAH",
    )
    insights = _build_insights(
        passenger_doc["passengers"],
        reservation_doc,
        None,
        passenger_doc,
        {},
    )

    client = mongomock.MongoClient()
    db = client["falconeye"]
    db.reservations.insert_one(reservation_doc)
    db.flights.insert_one({
        "airline": "GF",
        "flightNumber": "1",
        "origin": "BAH",
        "departureDate": "2026-03-19",
    })
    db.passenger_list.insert_one({
        "flightNumber": "1",
        "origin": "BAH",
        "departureDate": "2026-03-19",
        "fetchedAt": reservation_doc["fetchedAt"],
        "totalPassengers": 1,
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": 1,
        "cabinSummary": [],
        "passengers": [{
            "cabin": "Y",
            "bookingClass": "Y",
            "isCheckedIn": False,
            "isBoarded": False,
            "isRevenue": True,
            "isStandby": False,
        }],
    })
    analytics_routes.get_db = lambda: db
    analytics = _run_network_analytics("2026-03-19", "2026-03-19")
    loyalty = analytics["loyaltyDistribution"]
    loyalty_label = loyalty[0]["tierName"] if loyalty else ""

    def first_key(mapping):
        keys = list(mapping)
        return keys[0] if keys else ""

    payment = first_key(insights["paymentMethods"])
    return [
        {"name": "paymentMethods", "kind": "horizontal", "label": _as_json_value(payment)},
        {"name": "corporateAccounts", "kind": "horizontal", "label": _as_json_value(first_key(insights["corporateTravel"]["companies"]))},
        {"name": "bookingChannels", "kind": "donut", "label": _as_json_value(first_key(insights["bookingChannels"]["channels"]))},
        {"name": "bagDestinations", "kind": "horizontal", "label": _as_json_value(first_key(insights["baggageRouting"]["destinations"]))},
        {"name": "passengerTypes", "kind": "pretty-horizontal", "label": _as_json_value(first_key(insights["passengerTypes"]))},
        {"name": "standbyCabins", "kind": "cabin-horizontal", "label": _as_json_value(first_key(insights["standbyUpgrade"]["standbyCabins"]))},
        {"name": "loyaltyTier", "kind": "analytics-donut", "label": _as_json_value(loyalty_label)},
        # Same horizontal-bar payload through the vertical-bar formatter. Its
        # current caller uses fixed booking-window labels; the formatter still
        # concatenates whatever label it is given into HTML.
        {"name": "verticalBar", "kind": "vertical", "label": _as_json_value(payment)},
    ]


def test_reservation_strings_are_not_executed_as_tooltip_html():
    cases = _chart_cases(PAYLOAD)
    expected = {
        "paymentMethods",
        "corporateAccounts",
        "bookingChannels",
        "bagDestinations",
        "passengerTypes",
        "standbyCabins",
        "loyaltyTier",
        "verticalBar",
    }
    assert {case["name"] for case in cases} == expected

    live = [case for case in cases if "<" in case["label"]]
    if not live:
        # Markup was removed before any of these labels were charted.
        return

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    script = os.path.join(repo_root, "frontend", "tests", "reservation-tooltip-xss.mjs")
    completed = subprocess.run(
        ["node", script],
        input=json.dumps(live),
        text=True,
        capture_output=True,
        cwd=repo_root,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
