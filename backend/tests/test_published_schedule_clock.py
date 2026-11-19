"""Published schedule card must show a clock for Sabre VerifyFlightDetails times.

VerifyFlightDetailsRS FlightSegment DepartureDateTime and ArrivalDateTime use
the sws:dateTime type. That type allows a year and seconds to be omitted, and
the attribute documentation gives MM-DDTHH:MM. convert_schedule stores those
attribute strings, the dashboard payload returns them, and the Live Ops
published-schedule card prints them.

These tests fail while that card, and the Live Ops header ScheduleDelay
indicator, take a fixed slice of the stored time. They pass when Depart,
Arrive, and every segment render only the hour and minute for each form the
schema allows, and when the header shows the hour and minute from the ACS
date and time the feeder stores.

Run from the repository root, after installing backend and frontend dependencies:

    python3 -m pip install -r requirements.txt
    npm install --prefix frontend
    python3 backend/tests/test_published_schedule_clock.py
"""

import json
import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_flight_status, convert_schedule
from backend.sabre.client import SabreClient

RENDERER = os.path.join(REPO_ROOT, "frontend", "tests", "render-published-schedule.cjs")
ACS_SAMPLE = os.path.join(
    REPO_ROOT,
    "sample_data",
    "sample_xmlfiles",
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml",
)

# Hour and minute are the same in every case. Only the sws:dateTime spelling changes.
DEPARTURE = "14:30"
ARRIVAL = "18:45"
SECOND_DEPARTURE = "20:10"
SECOND_ARRIVAL = "02:05"


def _soap(departure, arrival, second_departure, second_arrival):
    """SOAP body shaped like the VerifyFlightDetails response the client extracts."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header/>
  <soap-env:Body>
    <ns6:VerifyFlightDetailsRS xmlns:ns6="http://webservices.sabre.com/sabreXML/2011/10" Version="2.0.1">
      <stl:ApplicationResults status="Complete" xmlns:stl="http://services.sabre.com/STL/v01"/>
      <ns6:OriginDestinationOptions>
        <ns6:OriginDestinationOption OriginTimeZone="GMT+3" DestinationTimeZone="GMT+4">
          <ns6:FlightSegment DepartureDateTime="{departure}" ArrivalDateTime="{arrival}" AirMilesFlown="800" ElapsedTime="04.15">
            <ns6:DestinationLocation LocationCode="DXB" Terminal="1"/>
            <ns6:Equipment AirEquipType="789"/>
            <ns6:MarketingAirline Code="GF" FlightNumber="2057"/>
            <ns6:Meal Code="M"/>
            <ns6:OriginLocation LocationCode="BAH" Terminal="1"/>
          </ns6:FlightSegment>
          <ns6:FlightSegment DepartureDateTime="{second_departure}" ArrivalDateTime="{second_arrival}" AirMilesFlown="1200" ElapsedTime="05.55">
            <ns6:DestinationLocation LocationCode="LHR" Terminal="4"/>
            <ns6:Equipment AirEquipType="32N"/>
            <ns6:MarketingAirline Code="GF" FlightNumber="2058"/>
            <ns6:OriginLocation LocationCode="DXB" Terminal="1"/>
          </ns6:FlightSegment>
        </ns6:OriginDestinationOption>
      </ns6:OriginDestinationOptions>
    </ns6:VerifyFlightDetailsRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _dashboard_from_sabre(xml):
    parsed = SabreClient._parse_xml(xml)
    body = SabreClient._extract_body(None, parsed, "VerifyFlightDetailsRS")
    schedule = convert_schedule(body, "GF", "2057", "2026-03-25")
    # ACS flight detail is present but has no usable clock. The published card
    # is the timetable for that flight.
    flight_status = {
        "status": "SCHEDULED",
        "origin": "BAH",
        "departureDate": "2026-03-25",
        "aircraft": {},
        "schedule": {
            "scheduledDeparture": "",
            "estimatedDeparture": "",
            "scheduledArrival": "",
            "estimatedArrival": "",
        },
    }
    dashboard = _build_dashboard_payload(
        flight_status,
        None,
        "BAH",
        "2026-03-25",
        {},
        None,
        None,
        schedule,
    )
    return schedule, dashboard


def _render(dashboard):
    payload = {
        "selection": {"flightNumber": "2057", "origin": "BAH", "date": "2026-03-25"},
        "dashboard": dashboard,
    }
    completed = subprocess.run(
        ["node", RENDERER],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "published schedule render failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"renderer produced no JSON\nstderr:\n{completed.stderr}")
    return json.loads(lines[-1])


class PublishedScheduleClockTest(unittest.TestCase):
    def _assert_clocks(self, spelling, departure, arrival, second_departure, second_arrival):
        schedule, dashboard = _dashboard_from_sabre(
            _soap(departure, arrival, second_departure, second_arrival)
        )
        self.assertTrue(schedule["success"], schedule.get("error"))
        self.assertEqual(schedule["scheduledDeparture"], departure)
        self.assertEqual(schedule["scheduledArrival"], arrival)
        self.assertEqual(
            [segment["departureDateTime"] for segment in schedule["segments"]],
            [departure, second_departure],
        )
        self.assertEqual(
            [segment["arrivalDateTime"] for segment in schedule["segments"]],
            [arrival, second_arrival],
        )
        # The dashboard hands the stored schedule to the card unchanged.
        self.assertEqual(dashboard["schedule"]["scheduledDeparture"], departure)
        self.assertEqual(dashboard["schedule"]["scheduledArrival"], arrival)
        self.assertEqual(
            dashboard["schedule"]["segments"][1]["departureDateTime"],
            second_departure,
        )

        rendered = _render(dashboard)
        self.assertTrue(
            rendered["hasPublishedSchedule"],
            f"{spelling}: published schedule card was not rendered",
        )
        self.assertEqual(
            rendered["summary"],
            [
                {"label": "Depart", "text": DEPARTURE},
                {"label": "Arrive", "text": ARRIVAL},
            ],
            f"{spelling}: summary clocks from {departure} and {arrival}",
        )
        self.assertEqual(
            rendered["segments"],
            [
                f"{DEPARTURE} → {ARRIVAL}",
                f"{SECOND_DEPARTURE} → {SECOND_ARRIVAL}",
            ],
            f"{spelling}: segment clocks",
        )

    def test_month_day_hour_minute(self):
        """Attribute documentation: MM-DDTHH:MM. 03-25T14:30 must paint 14:30."""
        self._assert_clocks(
            "MM-DDTHH:MM",
            "03-25T14:30",
            "03-25T18:45",
            "03-25T20:10",
            "03-26T02:05",
        )

    def test_month_day_hour_minute_second(self):
        """sws:dateTime allows seconds. 03-25T14:30:00 must still paint 14:30."""
        self._assert_clocks(
            "MM-DDTHH:MM:SS",
            "03-25T14:30:00",
            "03-25T18:45:00",
            "03-25T20:10:00",
            "03-26T02:05:00",
        )

    def test_year_month_day_hour_minute(self):
        """sws:dateTime example yyyy-mm-ddThh:mm. The card must paint the clock."""
        self._assert_clocks(
            "YYYY-MM-DDTHH:MM",
            "2026-03-25T14:30",
            "2026-03-25T18:45",
            "2026-03-25T20:10",
            "2026-03-26T02:05",
        )

    def _dashboard_from_acs(self, xml):
        parsed = SabreClient._parse_xml(xml)
        # Same response element get_flight_status extracts.
        body = SabreClient._extract_body(None, parsed, "ns3:ACS_FlightDetailRS")
        status = convert_flight_status(body, "GF", "2006", "LHR", "2026-03-19")
        dashboard = _build_dashboard_payload(
            status, None, "LHR", "2026-03-19", {}, None, None, None,
        )
        return status, dashboard

    def test_acs_header_clock_uses_the_same_slice(self):
        """Live Ops header ScheduleDelay slices the ACS date+time the feeder stores.

        The captured ACS sample combines to 2026-03-19T09:50PM. The header must
        show the hour and minute, 09:50, not a slice of that string.
        """
        with open(ACS_SAMPLE, encoding="utf-8") as sample:
            xml = sample.read()
        status, dashboard = self._dashboard_from_acs(xml)
        self.assertEqual(status["schedule"]["scheduledDeparture"], "2026-03-19T09:50PM")
        self.assertEqual(status["schedule"]["estimatedDeparture"], "2026-03-19T09:50PM")
        rendered = _render(dashboard)
        self.assertEqual(rendered["scheduleDelay"]["std"], "09:50", rendered["scheduleDelay"])
        self.assertIsNone(rendered["scheduleDelay"]["etd"])

    def test_acs_header_delay_uses_parsed_hours(self):
        """A later estimated departure must show both clocks and the minute gap."""
        with open(ACS_SAMPLE, encoding="utf-8") as sample:
            xml = sample.read()
        xml = xml.replace(
            "<EstimatedDepartureTime>09:50PM</EstimatedDepartureTime>",
            "<EstimatedDepartureTime>10:20PM</EstimatedDepartureTime>",
            1,
        )
        status, dashboard = self._dashboard_from_acs(xml)
        self.assertEqual(status["schedule"]["scheduledDeparture"], "2026-03-19T09:50PM")
        self.assertEqual(status["schedule"]["estimatedDeparture"], "2026-03-19T10:20PM")
        rendered = _render(dashboard)
        self.assertEqual(rendered["scheduleDelay"]["std"], "09:50", rendered["scheduleDelay"])
        self.assertEqual(rendered["scheduleDelay"]["etd"], "10:20", rendered["scheduleDelay"])
        self.assertEqual(rendered["scheduleDelay"]["delayMinutes"], "30", rendered["scheduleDelay"])

    def test_year_month_day_hour_minute_second(self):
        """sws:dateTime example yyyy-mm-ddThh:mm:ss. The card must paint the clock."""
        self._assert_clocks(
            "YYYY-MM-DDTHH:MM:SS",
            "2026-03-25T14:30:00",
            "2026-03-25T18:45:00",
            "2026-03-25T20:10:00",
            "2026-03-26T02:05:00",
        )


if __name__ == "__main__":
    unittest.main()
