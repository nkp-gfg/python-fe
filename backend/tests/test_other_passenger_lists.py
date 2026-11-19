"""The Other Passengers figures must name the people they count.

Offloaded is the trip-report cancelled list. A no-show is an ever-booked
name that is not on the current manifest. Those identities have to travel
with the dashboard, because the passenger-list endpoint only returns people
who are still on the manifest.

These assertions fail until the dashboard payload carries those identities.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.tests.other_passenger_board import (
    build_scenario,
    dashboard_strings,
)


def test_authoritative_counts_match_the_trip_report_populations():
    board = build_scenario("authoritative")
    expected = board["expected"]
    assert expected["offloadedAvailable"] is True
    assert expected["noShowAvailable"] is True
    assert expected["flightClosed"] is True
    assert expected["jumpSeat"] == 0
    assert expected["offloadedCount"] == len(expected["cancelled"])
    assert expected["noShowCount"] == len(expected["noShows"])
    assert expected["cancelled"] == ["REMOVED, RIA"]
    assert "GHOST, GIL" in expected["noShows"]
    assert "REMOVED, RIA" in expected["noShows"]
    assert "ALI, AMIN" in expected["checkedInNotBoarded"]
    assert "BAKER, BEN" in expected["notCheckedIn"]
    assert expected["nonRevenue"] == ["CREW, SAM"]
    assert "ALI, AMIN" not in expected["cancelled"]
    assert "BAKER, BEN" not in expected["noShows"]
    assert "CREW, SAM" not in expected["noShows"]


def test_dashboard_payload_names_cancelled_and_no_show_passengers():
    board = build_scenario("authoritative")
    text = dashboard_strings(board["dashboard"])
    missing = []
    for token in ("REMOVED", "RIA", "RMV001", "GHOST", "GIL", "GHO001"):
        if token not in text:
            missing.append(token)
    assert missing == [], (
        "stateSummary.others counts cancelled and no-show passengers but the "
        f"dashboard payload drops their identities: {missing}"
    )


def test_empty_trip_report_counts_are_zero_and_still_authoritative():
    board = build_scenario("empty-exceptions")
    expected = board["expected"]
    assert expected["offloadedAvailable"] is True
    assert expected["noShowAvailable"] is True
    assert expected["offloadedCount"] == 0
    assert expected["noShowCount"] == 0
    assert expected["cancelled"] == []
    assert expected["noShows"] == []
    assert expected["checkedInNotBoardedCount"] > 0
    assert expected["notCheckedInCount"] > 0


def test_open_flight_does_not_publish_a_no_show_count():
    board = build_scenario("open-checkin")
    expected = board["expected"]
    assert expected["offloadedAvailable"] is True
    assert expected["offloadedCount"] == 1
    assert expected["noShowAvailable"] is False
    assert expected["flightClosed"] is False
    assert expected["noShowCount"] is None


def test_missing_trip_report_is_marked_inferred():
    board = build_scenario("inferred")
    expected = board["expected"]
    assert expected["offloadedAvailable"] is False
    assert expected["noShowAvailable"] is False
    assert expected["flightClosed"] is True
    assert expected["offloadedCount"] is None
    assert expected["noShowCount"] is None
    assert expected["checkedInNotBoarded"] == ["ALI, AMIN"]
    assert expected["notCheckedIn"] == ["BAKER, BEN"]


def test_jump_seat_count_is_flight_status_not_non_revenue():
    board = build_scenario("jump-seats")
    expected = board["expected"]
    assert expected["jumpSeat"] == 3
    assert expected["nonRevenue"] == ["CREW, SAM"]
    others = board["dashboard"]["stateSummary"]["others"]
    assert others["jumpSeat"] != others["nonRevenue"]


if __name__ == "__main__":
    test_authoritative_counts_match_the_trip_report_populations()
    test_dashboard_payload_names_cancelled_and_no_show_passengers()
    test_empty_trip_report_counts_are_zero_and_still_authoritative()
    test_open_flight_does_not_publish_a_no_show_count()
    test_missing_trip_report_is_marked_inferred()
    test_jump_seat_count_is_flight_status_not_non_revenue()
    print("ALL TESTS PASS")
