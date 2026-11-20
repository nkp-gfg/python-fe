"""Network analytics Upgrades headline must follow stored cabin-change direction.

Passenger comparison writes every cabin move as CABIN_CHANGE. metadata.direction
is UPGRADE only for Y→J, Y→F, and J→F; every other cabin move is DOWNGRADE.
The network analytics page renders upgradeAnalytics.cabinChanges as the
Upgrades headline (frontend/app/analytics/page.tsx) and
upgradeAnalytics.upgradesConfirmed as the smaller confirmed subtitle.

Contract these tests lock, which is what the headline has to show:

- upgradeAnalytics.cabinChanges counts CABIN_CHANGE documents whose stored
  metadata.direction is exactly UPGRADE. It is the Upgrades headline.
- upgradeAnalytics.downgrades counts CABIN_CHANGE documents whose stored
  metadata.direction is exactly DOWNGRADE, as its own figure.
- upgradeAnalytics.upgradesConfirmed counts UPGRADE_CONFIRMED documents only
  and is not folded into either cabin figure.
- A CABIN_CHANGE with no direction counts as neither.
- The headline reads the stored direction. A document whose cabin letters
  look like an upgrade still counts as a downgrade when the stored direction
  is DOWNGRADE, and the reverse for UPGRADE.
- changeDistribution still counts every CABIN_CHANGE. Downgrades stay in the
  change log; they only leave the Upgrades headline.

These assertions fail while the headline counts every CABIN_CHANGE.
"""

import os

import pytest

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")

from backend.api import database
from backend.api.routes.analytics import _run_network_analytics
from backend.feeder import storage
from backend.feeder.differ import _make_change, detect_changes

DAY = "2026-06-01"
OTHER_DAY = "2026-06-03"
DOWNGRADE_ONLY_DAY = "2026-06-04"


def _passenger(pnr, cabin, booking="Y", desired=None):
    passenger = {
        "pnr": pnr,
        "lastName": "DOE",
        "firstName": pnr,
        "isCheckedIn": False,
        "isBoarded": False,
        "cabin": cabin,
        "bookingClass": booking,
        "seat": "10A",
        "bagCount": 0,
        "passengerType": "F",
        "priorityCode": "",
        "editCodes": [],
    }
    if desired is not None:
        passenger["desiredBookingClass"] = desired
    return passenger


def _compare(flight, before, after):
    before_snap = {"snapshotId": f"before-{before['pnr']}", "data": {"passengers": [before]}}
    after_snap = {"snapshotId": f"after-{after['pnr']}", "data": {"passengers": [after]}}
    return detect_changes(before_snap, after_snap, "passenger_list", flight)


def _flight(number, origin, date):
    return {
        "airline": "GF",
        "flightNumber": number,
        "origin": origin,
        "departureDate": date,
    }


def _cabin_changes():
    """Build the change documents ingestion would store for one network day."""
    bah = _flight("GF100", "BAH", DAY)
    changes = []
    changes += _compare(
        bah,
        _passenger("PAX1", "Y", booking="V"),
        _passenger("PAX1", "J", booking="D"),
    )
    changes += _compare(bah, _passenger("PAX2", "Y"), _passenger("PAX2", "F"))
    changes += _compare(bah, _passenger("PAX3", "J"), _passenger("PAX3", "F"))
    changes += _compare(bah, _passenger("PAX4", "J"), _passenger("PAX4", "Y"))
    changes += _compare(bah, _passenger("PAX5", "F"), _passenger("PAX5", "Y"))
    changes += _compare(bah, _passenger("PAX6", "F"), _passenger("PAX6", "J"))
    changes += _compare(bah, _passenger("PAX7", "S"), _passenger("PAX7", "Y"))
    # Confirmed upgrade without a cabin move. The subtitle must keep this,
    # and the headline must not add it.
    changes += _compare(
        bah,
        _passenger("PAX8", "Y", booking="V", desired="D"),
        _passenger("PAX8", "Y", booking="D"),
    )
    changes += _compare(
        _flight("GF200", "DOH", DAY),
        _passenger("PAX10", "Y"),
        _passenger("PAX10", "J"),
    )
    changes += _compare(
        _flight("GF100", "BAH", OTHER_DAY),
        _passenger("PAX11", "Y"),
        _passenger("PAX11", "J"),
    )
    changes += _compare(
        _flight("GF100", "BAH", OTHER_DAY),
        _passenger("PAX12", "J"),
        _passenger("PAX12", "Y"),
    )
    changes += _compare(
        _flight("GF300", "BAH", DOWNGRADE_ONLY_DAY),
        _passenger("PAX16", "J"),
        _passenger("PAX16", "Y"),
    )

    # Same shape store_changes persists. Direction is stored independently of
    # the cabin letters so the headline cannot re-derive the count from them.
    changes.append(_make_change(
        bah, "CABIN_CHANGE", "before-PAX13", "after-PAX13",
        passenger=_passenger("PAX13", "J"),
        field="cabin",
        old_value="Y",
        new_value="J",
        metadata={"direction": "DOWNGRADE"},
    ))
    changes.append(_make_change(
        bah, "CABIN_CHANGE", "before-PAX14", "after-PAX14",
        passenger=_passenger("PAX14", "Y"),
        field="cabin",
        old_value="F",
        new_value="Y",
        metadata={"direction": "UPGRADE"},
    ))
    changes.append(_make_change(
        bah, "CABIN_CHANGE", "before-PAX15", "after-PAX15",
        passenger=_passenger("PAX15", "Y"),
        field="cabin",
        old_value="J",
        new_value="Y",
    ))
    # A second letter-upgrade stored as a downgrade. Together with PAX14 this
    # makes the upgrade total differ from a recount that trusts cabin letters.
    changes.append(_make_change(
        bah, "CABIN_CHANGE", "before-PAX17", "after-PAX17",
        passenger=_passenger("PAX17", "J"),
        field="cabin",
        old_value="Y",
        new_value="J",
        metadata={"direction": "DOWNGRADE"},
    ))
    return changes


def _scoped(changes, date_from, date_to, origin=None, flight_number=None):
    selected = []
    for change in changes:
        if not (date_from <= change["departureDate"] <= date_to):
            continue
        if origin is not None and change["origin"] != origin:
            continue
        if flight_number is not None and change["flightNumber"] != flight_number:
            continue
        selected.append(change)
    return selected


def _expected(changes):
    upgrades = 0
    downgrades = 0
    confirmed = 0
    cabin_events = 0
    for change in changes:
        kind = change["changeType"]
        if kind == "UPGRADE_CONFIRMED":
            confirmed += 1
        elif kind == "CABIN_CHANGE":
            cabin_events += 1
            direction = (change.get("metadata") or {}).get("direction")
            if direction == "UPGRADE":
                upgrades += 1
            elif direction == "DOWNGRADE":
                downgrades += 1
    return {
        "cabinChanges": upgrades,
        "downgrades": downgrades,
        "upgradesConfirmed": confirmed,
        "cabinEvents": cabin_events,
    }


@pytest.fixture(scope="module")
def stored_changes():
    client_probe = __import__("pymongo").MongoClient(
        os.environ["MONGODB_URI"], serverSelectionTimeoutMS=3000)
    client_probe.admin.command("ping")
    client_probe.close()

    database.close_db()
    storage._db = None
    storage._client = None
    storage._owns_connection = False

    db = database.get_db()
    db.client.drop_database(database.DB_NAME)
    storage.init_db(db)

    changes = _cabin_changes()
    stored = storage.store_changes(changes)
    assert stored == len(changes)

    persisted = db["changes"].find_one(
        {"passenger.pnr": "PAX4", "changeType": "CABIN_CHANGE"})
    assert persisted is not None
    assert persisted["metadata"]["direction"] == "DOWNGRADE"
    assert persisted["oldValue"] == "J"
    assert persisted["newValue"] == "Y"
    return changes


def _assert_headline(result, changes):
    expected = _expected(changes)
    # The seeded range always contains at least one downgrade, so a headline
    # that counts every cabin change cannot satisfy this.
    assert expected["downgrades"] > 0
    assert expected["cabinEvents"] > expected["cabinChanges"]

    distribution = {
        item["changeType"]: item["count"] for item in result["changeDistribution"]
    }
    assert distribution.get("CABIN_CHANGE", 0) == expected["cabinEvents"]

    analytics = result["upgradeAnalytics"]
    assert analytics["cabinChanges"] == expected["cabinChanges"]
    assert analytics["downgrades"] == expected["downgrades"]
    assert analytics["upgradesConfirmed"] == expected["upgradesConfirmed"]


def test_comparison_stores_downgrades_as_cabin_changes(stored_changes):
    """The comparison the feeder runs classifies direction and keeps one type."""
    by_pnr = {}
    for change in stored_changes:
        if change["changeType"] != "CABIN_CHANGE":
            continue
        pnr = change["passenger"]["pnr"]
        by_pnr[pnr] = change

    assert by_pnr["PAX1"]["metadata"]["direction"] == "UPGRADE"
    assert by_pnr["PAX1"]["oldValue"] == "Y" and by_pnr["PAX1"]["newValue"] == "J"
    assert by_pnr["PAX2"]["metadata"]["direction"] == "UPGRADE"
    assert by_pnr["PAX2"]["newValue"] == "F"
    assert by_pnr["PAX3"]["metadata"]["direction"] == "UPGRADE"
    assert by_pnr["PAX3"]["oldValue"] == "J" and by_pnr["PAX3"]["newValue"] == "F"
    for pnr in ("PAX4", "PAX5", "PAX6", "PAX7", "PAX12", "PAX16"):
        assert by_pnr[pnr]["metadata"]["direction"] == "DOWNGRADE", pnr
    assert by_pnr["PAX7"]["oldValue"] == "S"

    confirmed = [
        change for change in stored_changes
        if change["changeType"] == "UPGRADE_CONFIRMED"
    ]
    assert len(confirmed) == 1
    assert confirmed[0]["passenger"]["pnr"] == "PAX8"
    assert not any(
        change["changeType"] == "CABIN_CHANGE" and change["passenger"]["pnr"] == "PAX8"
        for change in stored_changes
    )


def test_network_day_headline_counts_only_stored_upgrades(stored_changes):
    scoped = _scoped(stored_changes, DAY, DAY)
    result = _run_network_analytics(DAY, DAY)
    _assert_headline(result, scoped)

    analytics = result["upgradeAnalytics"]
    # Stored upgrades: PAX1, PAX2, PAX3, PAX10, and PAX14.
    # Stored downgrades: PAX4, PAX5, PAX6, PAX7, PAX13, and PAX17.
    # PAX15 has no direction and stays out of both figures.
    assert analytics["cabinChanges"] == 5
    assert analytics["downgrades"] == 6
    assert analytics["upgradesConfirmed"] == 1


def test_downgrade_only_day_does_not_increase_upgrades(stored_changes):
    scoped = _scoped(stored_changes, DOWNGRADE_ONLY_DAY, DOWNGRADE_ONLY_DAY)
    assert _expected(scoped) == {
        "cabinChanges": 0,
        "downgrades": 1,
        "upgradesConfirmed": 0,
        "cabinEvents": 1,
    }
    result = _run_network_analytics(DOWNGRADE_ONLY_DAY, DOWNGRADE_ONLY_DAY)
    _assert_headline(result, scoped)
    assert result["upgradeAnalytics"]["cabinChanges"] == 0
    assert result["upgradeAnalytics"]["downgrades"] == 1


def test_origin_and_flight_filters_still_drop_downgrades(stored_changes):
    scoped = _scoped(stored_changes, DAY, DAY, origin="BAH", flight_number="GF100")
    result = _run_network_analytics(DAY, DAY, origin="BAH", flight_number="GF100")
    _assert_headline(result, scoped)
    # PAX10 flies from DOH. GF100 at BAH keeps PAX1–PAX3 and PAX14 as
    # upgrades, and PAX4–PAX7 plus PAX13 and PAX17 as downgrades.
    assert result["upgradeAnalytics"]["cabinChanges"] == 4
    assert result["upgradeAnalytics"]["downgrades"] == 6
    assert result["upgradeAnalytics"]["upgradesConfirmed"] == 1


def test_date_window_does_not_let_downgrades_into_the_headline(stored_changes):
    scoped = _scoped(stored_changes, OTHER_DAY, OTHER_DAY)
    result = _run_network_analytics(OTHER_DAY, OTHER_DAY)
    _assert_headline(result, scoped)
    assert result["upgradeAnalytics"]["cabinChanges"] == 1
    assert result["upgradeAnalytics"]["downgrades"] == 1
    assert result["upgradeAnalytics"]["upgradesConfirmed"] == 0


def test_headline_follows_stored_direction_when_letters_disagree(stored_changes):
    """A later recount that looks at cabin letters instead of metadata.direction fails."""
    result = _run_network_analytics(DAY, DAY, origin="BAH", flight_number="GF100")
    analytics = result["upgradeAnalytics"]
    # PAX13 and PAX17 are stored Y→J with direction DOWNGRADE. PAX14 is
    # stored F→Y with direction UPGRADE. Counting the letters instead would
    # report 5 upgrades (PAX1, PAX2, PAX3, PAX13, PAX17).
    assert analytics["cabinChanges"] == 4
    assert analytics["downgrades"] == 6
    assert analytics["cabinChanges"] != 5
