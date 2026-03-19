"""Tests for the change detection (differ) module."""

from backend.feeder.differ import diff_passenger_list, diff_flight_status, diff_reservations
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_passenger_added():
    flight_info = {"flightNumber": "2006",
                   "origin": "LHR", "departureDate": "2026-03-19"}
    before = {
        "snapshotId": "snap-1",
        "data": {
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "V", "seat": "21A", "bagCount": 1, "passengerType": "F"},
            ]
        }
    }
    after = {
        "snapshotId": "snap-2",
        "data": {
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "V", "seat": "21A", "bagCount": 1, "passengerType": "F"},
                {"pnr": "XYZ789", "lastName": "JONES", "firstName": "MARY",
                 "isCheckedIn": False, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "W", "seat": "", "bagCount": 0, "passengerType": "F"},
            ]
        }
    }
    changes = diff_passenger_list(before, after, flight_info)
    assert len(changes) == 1
    assert changes[0]["changeType"] == "PASSENGER_ADDED"
    assert changes[0]["passenger"]["pnr"] == "XYZ789"
    print("  PASS: test_passenger_added")


def test_passenger_boarded():
    flight_info = {"flightNumber": "2006",
                   "origin": "LHR", "departureDate": "2026-03-19"}
    before = {
        "snapshotId": "snap-1",
        "data": {
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "V", "seat": "21A", "bagCount": 1, "passengerType": "F"},
            ]
        }
    }
    after = {
        "snapshotId": "snap-2",
        "data": {
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
                 "isCheckedIn": True, "isBoarded": True, "cabin": "Y",
                 "bookingClass": "V", "seat": "21A", "bagCount": 1, "passengerType": "F"},
            ]
        }
    }
    changes = diff_passenger_list(before, after, flight_info)
    assert len(changes) == 1
    assert changes[0]["changeType"] == "BOARDED"
    print("  PASS: test_passenger_boarded")


def test_cabin_upgrade():
    flight_info = {"flightNumber": "2006",
                   "origin": "LHR", "departureDate": "2026-03-19"}
    before = {
        "snapshotId": "snap-1",
        "data": {
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "V", "seat": "21A", "bagCount": 1, "passengerType": "F"},
            ]
        }
    }
    after = {
        "snapshotId": "snap-2",
        "data": {
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "J",
                 "bookingClass": "D", "seat": "5C", "bagCount": 1, "passengerType": "F"},
            ]
        }
    }
    changes = diff_passenger_list(before, after, flight_info)
    types = {c["changeType"] for c in changes}
    assert "CABIN_CHANGE" in types
    assert "CLASS_CHANGE" in types
    assert "SEAT_CHANGE" in types
    assert len(changes) == 3
    cabin_change = [c for c in changes if c["changeType"] == "CABIN_CHANGE"][0]
    assert cabin_change["oldValue"] == "Y"
    assert cabin_change["newValue"] == "J"
    print("  PASS: test_cabin_upgrade")


def test_passenger_removed():
    flight_info = {"flightNumber": "2006",
                   "origin": "LHR", "departureDate": "2026-03-19"}
    before = {
        "snapshotId": "snap-1",
        "data": {
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "V", "seat": "21A", "bagCount": 1, "passengerType": "F"},
                {"pnr": "DEF456", "lastName": "LEE", "firstName": "STAFF",
                 "isCheckedIn": False, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "S", "seat": "", "bagCount": 0, "passengerType": "S"},
            ]
        }
    }
    after = {
        "snapshotId": "snap-2",
        "data": {
            "passengers": [
                {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "V", "seat": "21A", "bagCount": 1, "passengerType": "F"},
            ]
        }
    }
    changes = diff_passenger_list(before, after, flight_info)
    assert len(changes) == 1
    assert changes[0]["changeType"] == "PASSENGER_REMOVED"
    assert changes[0]["passenger"]["pnr"] == "DEF456"
    print("  PASS: test_passenger_removed")


def test_flight_status_change():
    flight_info = {"flightNumber": "2006",
                   "origin": "LHR", "departureDate": "2026-03-19"}
    before = {
        "snapshotId": "snap-1",
        "data": {"status": "OPENCI", "gate": "B41", "passengerCounts": {}}
    }
    after = {
        "snapshotId": "snap-2",
        "data": {"status": "FINAL", "gate": "B43", "passengerCounts": {}}
    }
    changes = diff_flight_status(before, after, flight_info)
    types = {c["changeType"] for c in changes}
    assert "STATUS_CHANGE" in types
    assert "GATE_CHANGE" in types
    status_chg = [c for c in changes if c["changeType"] == "STATUS_CHANGE"][0]
    assert status_chg["oldValue"] == "OPENCI"
    assert status_chg["newValue"] == "FINAL"
    print("  PASS: test_flight_status_change")


def test_no_changes():
    flight_info = {"flightNumber": "2006",
                   "origin": "LHR", "departureDate": "2026-03-19"}
    pax = [
        {"pnr": "ABC123", "lastName": "SMITH", "firstName": "JOHN",
         "isCheckedIn": True, "isBoarded": True, "cabin": "Y",
         "bookingClass": "V", "seat": "21A", "bagCount": 2, "passengerType": "F"},
    ]
    before = {"snapshotId": "snap-1", "data": {"passengers": pax}}
    after = {"snapshotId": "snap-2", "data": {"passengers": list(pax)}}
    changes = diff_passenger_list(before, after, flight_info)
    assert len(changes) == 0
    print("  PASS: test_no_changes")


def test_multiple_changes():
    flight_info = {"flightNumber": "2274",
                   "origin": "DMM", "departureDate": "2026-03-19"}
    before = {
        "snapshotId": "snap-1",
        "data": {
            "passengers": [
                {"pnr": "PNR001", "lastName": "A", "firstName": "B",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "V", "seat": "10A", "bagCount": 0, "passengerType": "F"},
                {"pnr": "PNR002", "lastName": "C", "firstName": "D",
                 "isCheckedIn": True, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "V", "seat": "10B", "bagCount": 1, "passengerType": "F"},
            ]
        }
    }
    after = {
        "snapshotId": "snap-2",
        "data": {
            "passengers": [
                {"pnr": "PNR001", "lastName": "A", "firstName": "B",
                 "isCheckedIn": True, "isBoarded": True, "cabin": "Y",
                 "bookingClass": "V", "seat": "10A", "bagCount": 2, "passengerType": "F"},
                {"pnr": "PNR002", "lastName": "C", "firstName": "D",
                 "isCheckedIn": True, "isBoarded": True, "cabin": "J",
                 "bookingClass": "D", "seat": "3A", "bagCount": 1, "passengerType": "F"},
                {"pnr": "PNR003", "lastName": "E", "firstName": "F",
                 "isCheckedIn": False, "isBoarded": False, "cabin": "Y",
                 "bookingClass": "W", "seat": "", "bagCount": 0, "passengerType": "P"},
            ]
        }
    }
    changes = diff_passenger_list(before, after, flight_info)
    types = [c["changeType"] for c in changes]
    assert "PASSENGER_ADDED" in types  # PNR003
    assert "BOARDED" in types  # PNR001, PNR002
    assert "BAG_COUNT_CHANGE" in types  # PNR001: 0→2
    assert "CABIN_CHANGE" in types  # PNR002: Y→J
    assert "CLASS_CHANGE" in types  # PNR002: V→D
    assert "SEAT_CHANGE" in types  # PNR002: 10B→3A
    # 1 added + 2 boarded + 1 bag + 1 cabin + 1 class + 1 seat
    assert len(changes) == 7
    print("  PASS: test_multiple_changes")


if __name__ == "__main__":
    print("Running differ tests...")
    test_passenger_added()
    test_passenger_boarded()
    test_cabin_upgrade()
    test_passenger_removed()
    test_flight_status_change()
    test_no_changes()
    test_multiple_changes()
    print("ALL TESTS PASS")
