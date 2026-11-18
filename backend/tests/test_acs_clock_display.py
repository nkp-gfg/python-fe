"""ACS flight-detail clocks must render as a 24-hour time and a real delay.

Sabre ACS_FlightDetailRS times use Time_hhmma (hh:mmAM/PM). The feeder joins
that time onto the date with a T and the dashboard returns the string
unchanged. These tests feed that stored string into the workbench header
(ScheduleDelay), the History tab, and FlightHeader.

They fail while those views slice a fixed character range or ignore the
meridian. A fix has to parse hh:mmAM/PM, including a bare boarding time, and
use the date when the estimate crosses midnight.

Run from the repository root, after `npm ci` in `frontend/`:

    python -m pytest backend/tests/test_acs_clock_display.py -q
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLE = os.path.join(
    ROOT,
    "sample_data",
    "sample_xmlfiles",
    "ACS_FlightDetailRS_GF2006_LHR_2026-03-18.xml",
)
FRONTEND = os.path.join(ROOT, "frontend")

import sys

sys.path.insert(0, ROOT)

from backend.api.routes.flights import _build_dashboard_payload, _strip_id
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient

_RENDERED = None


def _load_body():
    with open(SAMPLE, encoding="utf-8") as handle:
        parsed = SabreClient._parse_xml(handle.read())
    return SabreClient._extract_body(object(), parsed, "ns3:ACS_FlightDetailRS")


def _with_times(body, **fields):
    cloned = copy.deepcopy(body)
    cloned["ItineraryResponseList"]["ItineraryInfoResponse"].update(fields)
    return cloned


def _convert(body):
    return convert_flight_status(body, "GF", "2006", "LHR", "2026-03-18")


def _as_shown_by_api(doc):
    """Dashboard and history responses keep the stored schedule strings."""
    payload = _build_dashboard_payload(doc, None, "LHR", "2026-03-18", {})
    assert payload["flightStatus"] is doc
    assert payload["flightStatus"]["schedule"] == doc["schedule"]
    shown = _strip_id(copy.deepcopy(doc))
    assert shown["schedule"] == doc["schedule"]
    assert "_raw" not in shown
    return shown


def _render():
    global _RENDERED
    if _RENDERED is not None:
        return _RENDERED

    body = _load_body()
    sample = _as_shown_by_api(_convert(body))
    assert sample["schedule"]["scheduledDeparture"] == "2026-03-18T09:50PM"
    assert sample["schedule"]["estimatedDeparture"] == "2026-03-18T09:50PM"
    assert sample["schedule"]["scheduledArrival"] == "2026-03-19T07:20AM"
    assert sample["boarding"]["time"] == "08:50PM"

    later_same_evening = _as_shown_by_api(
        _convert(_with_times(body, EstimatedDepartureTime="10:20PM"))
    )
    earlier_same_evening = _as_shown_by_api(
        _convert(_with_times(body, EstimatedDepartureTime="09:20PM"))
    )
    crosses_midnight = _as_shown_by_api(
        _convert(
            _with_times(
                body,
                ScheduledDepartureDate="2026-03-18",
                ScheduledDepartureTime="11:50PM",
                EstimatedDepartureDate="2026-03-19",
                EstimatedDepartureTime="12:20AM",
            )
        )
    )
    afternoon_into_next_morning = _as_shown_by_api(
        _convert(
            _with_times(
                body,
                ScheduledDepartureDate="2026-03-18",
                ScheduledDepartureTime="01:00PM",
                EstimatedDepartureDate="2026-03-19",
                EstimatedDepartureTime="01:30AM",
            )
        )
    )
    noon = _as_shown_by_api(
        _convert(
            _with_times(
                body,
                ScheduledDepartureTime="12:00PM",
                EstimatedDepartureTime="12:00PM",
            )
        )
    )
    just_after_midnight = _as_shown_by_api(
        _convert(
            _with_times(
                body,
                ScheduledDepartureTime="12:05AM",
                EstimatedDepartureTime="12:05AM",
            )
        )
    )
    sample_with_afternoon_arrival = _as_shown_by_api(
        _convert(_with_times(body, EstimatedArrivalTime="01:15PM"))
    )

    assert later_same_evening["schedule"]["estimatedDeparture"] == "2026-03-18T10:20PM"
    assert earlier_same_evening["schedule"]["estimatedDeparture"] == "2026-03-18T09:20PM"
    assert crosses_midnight["schedule"]["scheduledDeparture"] == "2026-03-18T11:50PM"
    assert crosses_midnight["schedule"]["estimatedDeparture"] == "2026-03-19T12:20AM"
    assert afternoon_into_next_morning["schedule"]["scheduledDeparture"] == "2026-03-18T01:00PM"
    assert afternoon_into_next_morning["schedule"]["estimatedDeparture"] == "2026-03-19T01:30AM"
    assert noon["schedule"]["scheduledDeparture"] == "2026-03-18T12:00PM"
    assert just_after_midnight["schedule"]["scheduledDeparture"] == "2026-03-18T12:05AM"
    assert sample_with_afternoon_arrival["schedule"]["estimatedArrival"] == "2026-03-19T01:15PM"
    assert sample_with_afternoon_arrival["schedule"]["scheduledDeparture"] == "2026-03-18T09:50PM"

    cases = {
        "std-ontime": {"kind": "schedule", "schedule": sample["schedule"]},
        "std-plus-30": {"kind": "schedule", "schedule": later_same_evening["schedule"]},
        "std-minus-30": {"kind": "schedule", "schedule": earlier_same_evening["schedule"]},
        "std-cross-midnight": {"kind": "schedule", "schedule": crosses_midnight["schedule"]},
        "std-twelve-hours": {
            "kind": "schedule",
            "schedule": afternoon_into_next_morning["schedule"],
        },
        "std-noon": {"kind": "schedule", "schedule": noon["schedule"]},
        "std-after-midnight": {
            "kind": "schedule",
            "schedule": just_after_midnight["schedule"],
        },
        "header-equal": {"kind": "header", "flight": sample},
        "header-plus-30": {"kind": "header", "flight": later_same_evening},
        "header-cross-midnight": {"kind": "header", "flight": crosses_midnight},
        "header-twelve-hours": {"kind": "header", "flight": afternoon_into_next_morning},
        "history-sample": {"kind": "history", "record": sample_with_afternoon_arrival},
        "history-afternoon-to-morning": {
            "kind": "history",
            "record": afternoon_into_next_morning,
        },
        "history-cross-midnight": {"kind": "history", "record": crosses_midnight},
    }
    completed = subprocess.run(
        ["npx", "--yes", "tsx", "tests/render-acs-clock.tsx"],
        cwd=FRONTEND,
        input=json.dumps({"cases": cases}),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "clock renderer failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    _RENDERED = json.loads(completed.stdout)
    return _RENDERED


class AcsClockDisplayTests(unittest.TestCase):
    def test_gf2006_header_shows_2150_not_a_sliced_clock(self):
        text = _render()["std-ontime"]
        self.assertIn("STD 21:50", text)
        self.assertNotIn("T09:5", text)
        self.assertNotIn("09:50", text)

    def test_same_evening_delay_is_thirty_minutes_on_a_24_hour_clock(self):
        text = _render()["std-plus-30"]
        self.assertIn("STD 21:50", text)
        self.assertIn("ETD 22:20 (+30m)", text)
        self.assertNotIn("NaN", text)
        self.assertNotIn("T09", text)
        self.assertNotIn("T10", text)

    def test_early_estimate_is_a_negative_delay_on_a_24_hour_clock(self):
        text = _render()["std-minus-30"]
        self.assertIn("STD 21:50", text)
        self.assertIn("ETD 21:20 (-30m)", text)
        self.assertNotIn("NaN", text)

    def test_delay_across_midnight_uses_the_meridian_and_the_date(self):
        text = _render()["std-cross-midnight"]
        self.assertIn("STD 23:50", text)
        self.assertIn("ETD 00:20 (+30m)", text)
        self.assertNotIn("11:50", text)
        self.assertNotIn("12:20", text)
        self.assertNotIn("NaN", text)

    def test_afternoon_to_next_morning_is_not_a_thirty_minute_delay(self):
        text = _render()["std-twelve-hours"]
        self.assertIn("STD 13:00", text)
        self.assertIn("ETD 01:30 (+750m)", text)
        self.assertNotIn("(+30m)", text)
        self.assertNotIn("STD 01:00", text)
        self.assertNotIn("NaN", text)

    def test_noon_and_midnight_keep_the_twelve_hour_meridian(self):
        noon = _render()["std-noon"]
        after_midnight = _render()["std-after-midnight"]
        self.assertIn("STD 12:00", noon)
        self.assertNotIn("24:00", noon)
        self.assertNotIn("00:00", noon)
        self.assertNotIn("T12", noon)
        self.assertIn("STD 00:05", after_midnight)
        self.assertNotIn("12:05", after_midnight)

    def test_flight_header_omits_a_delay_when_the_estimate_matches(self):
        text = _render()["header-equal"]
        self.assertNotIn("+", text)
        self.assertNotIn("NaN", text)

    def test_flight_header_delay_for_a_later_same_evening_estimate(self):
        text = _render()["header-plus-30"]
        self.assertIn("+30m", text)
        self.assertNotIn("NaN", text)

    def test_flight_header_delay_across_midnight_is_thirty_minutes(self):
        text = _render()["header-cross-midnight"]
        self.assertIn("+30m", text)
        self.assertNotIn("NaN", text)

    def test_flight_header_delay_across_twelve_hours_is_not_thirty_minutes(self):
        text = _render()["header-twelve-hours"]
        self.assertIn("+750m", text)
        self.assertNotIn("+30m", text)
        self.assertNotIn("NaN", text)

    def test_history_tab_shows_the_evening_departure_and_afternoon_arrival(self):
        text = _render()["history-sample"]
        self.assertIn("Flight Status Evolution", text)
        self.assertNotIn("Loading flight history", text)
        self.assertIn("STD 21:50", text)
        self.assertIn("ETD 21:50", text)
        self.assertIn("STA 07:20", text)
        self.assertIn("ETA 13:15", text)
        self.assertIn("Boarding 20:50", text)
        self.assertNotIn("STD 09:50", text)
        self.assertNotIn("ETD 09:50", text)
        self.assertNotIn("ETA 01:15", text)
        self.assertNotIn("Boarding 08:50", text)

    def test_history_tab_converts_an_afternoon_departure_and_a_next_morning_estimate(self):
        text = _render()["history-afternoon-to-morning"]
        self.assertIn("STD 13:00", text)
        self.assertIn("ETD 01:30", text)
        self.assertNotIn("STD 01:00", text)

    def test_history_tab_converts_a_departure_that_crosses_midnight(self):
        text = _render()["history-cross-midnight"]
        self.assertIn("STD 23:50", text)
        self.assertIn("ETD 00:20", text)
        self.assertNotIn("STD 11:50", text)
        self.assertNotIn("ETD 12:20", text)


if __name__ == "__main__":
    unittest.main()
