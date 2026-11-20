"""Shared record locators must not spread one traveler's audit warning.

The GF2057 passenger list has several travelers on locator LSGNZL. They differ
by passenger id, name association id, and name. These tests feed that manifest
through the Sabre XML parse, the passenger-list converter, and the flight audit
route. One traveler is boarded without a boarding pass. Another is checked in
without a boarding pass. The other travelers on that locator fail neither rule.

passengerAlerts must attach each rule only to the traveler who failed it.
The passenger table must paint the warning icon only on those rows.

The tests fail while the index and the table key warnings by record locator
alone.
"""

import copy
import json
import os
import re
import subprocess
import sys

import pytest

from backend.api.routes import audit as audit_route
from backend.api.routes import passengers as passengers_route
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MANIFEST_XML = os.path.join(
    REPO_ROOT,
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2057_BOM_2026-03-19.xml",
)
RENDER_SCRIPT = os.path.join(
    REPO_ROOT, "frontend", "tests", "passenger-alert-attribution.render.tsx"
)

FLIGHT = "2057"
ORIGIN = "BOM"
DATE = "2026-03-19"
LOCATOR = "LSGNZL"

# Display text the passenger table renders as "{lastName}, {firstName}".
BOARDED_WITHOUT_BP = "BOTLA, VINAYAK MR"
CHECKED_IN_WITHOUT_BP = "SAFI, MD GUFRAN MR"
UNINVOLVED = (
    "GUPTA, SHIVAM MR",
    "PULIYANCHALI, RASHEEDALI MR",
    "VALIYA PARAMBATH, SHAHID MUNEER MR",
)


class _Collection:
    def __init__(self, document):
        self._document = document

    def find_one(self, query, sort=None, projection=None):
        return self._document


class _Changes:
    def find(self, query, projection=None):
        return self

    def sort(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(())


class _Db:
    def __init__(self, passenger_doc):
        self._passenger_doc = passenger_doc

    def __getitem__(self, name):
        if name == "passenger_list":
            return _Collection(self._passenger_doc)
        if name == "reservations":
            return _Collection(None)
        if name == "changes":
            return _Changes()
        raise KeyError(name)


def _locator_text(passenger):
    raw = passenger.get("PNRLocator")
    if isinstance(raw, dict):
        return raw.get("#text", "")
    return str(raw or "")


def _load_manifest_with_two_distinct_failures():
    """Parse the committed GF2057 list the way get_passenger_list does.

    BOTLA stays boarded and loses the boarding pass. SAFI stays unboarded,
    without a boarding pass, and becomes checked in. Everyone else is unchanged.
    """
    with open(MANIFEST_XML, encoding="utf-8") as handle:
        xml_text = handle.read()
    client = SabreClient.__new__(SabreClient)
    parsed = SabreClient._parse_xml(xml_text)
    data = client._extract_body(parsed, "GetPassengerListRS")
    passengers = data.get("PassengerInfoList", {}).get("PassengerInfo", [])
    if isinstance(passengers, dict):
        passengers = [passengers]

    boarded_without_bp = 0
    checked_in_without_bp = 0
    for passenger in passengers:
        name = passenger.get("Name_Details") or {}
        if _locator_text(passenger) != LOCATOR:
            continue
        if name.get("LastName") == "BOTLA" and name.get("FirstName") == "VINAYAK MR":
            passenger["BoardingPassFlag"] = "false"
            boarded_without_bp += 1
        elif name.get("LastName") == "SAFI" and name.get("FirstName") == "MD GUFRAN MR":
            check_in = dict(passenger.get("CheckIn_Info") or {})
            check_in["CheckInStatus"] = "true"
            passenger["CheckIn_Info"] = check_in
            checked_in_without_bp += 1

    assert boarded_without_bp == 1
    assert checked_in_without_bp == 1

    document = convert_passenger_list(data, "GF", FLIGHT, DATE, ORIGIN)
    document.pop("_raw", None)
    return document


def _segments(key):
    return [part for part in re.split(r"[|:/#,]+", str(key)) if part]


def _key_selects(key, passenger):
    """Whether an index key refers to this traveler.

    A key that is only the record locator refers to every traveler on it.
    A fixed index must also carry passengerId, nameAssociationId, or both
    the last name and the first name.
    """
    locator = passenger.get("pnr") or ""
    if key == locator:
        return True

    parts = _segments(key)
    passenger_id = passenger.get("passengerId") or ""
    last_name = passenger.get("lastName") or ""
    first_name = passenger.get("firstName") or ""
    association = str(passenger.get("nameAssociationId") or "")
    name = f"{last_name}/{first_name}" if last_name or first_name else ""

    identity_in_key = False
    if passenger_id and (key == passenger_id or passenger_id in parts):
        identity_in_key = True
    if association and association in parts:
        identity_in_key = True
    if last_name and first_name and last_name in parts and first_name in parts:
        identity_in_key = True
    if name and name in parts:
        identity_in_key = True
    if not identity_in_key:
        return False
    # Composite keys include the locator. A passenger id or full name is
    # already unique on this manifest, so those may stand alone.
    if locator and locator in parts:
        return True
    if passenger_id and (key == passenger_id or passenger_id in parts):
        return True
    if last_name and first_name and last_name in parts and first_name in parts:
        return True
    return False


def _rules_for(passenger, passenger_alerts, path=""):
    """Rule ids the passengerAlerts index assigns to this traveler."""
    if isinstance(passenger_alerts, list):
        if path and _key_selects(path, passenger):
            return [str(rule_id) for rule_id in passenger_alerts]
        return []
    if not isinstance(passenger_alerts, dict):
        return []

    found = []
    for key, value in passenger_alerts.items():
        next_path = f"{path}|{key}" if path else str(key)
        if isinstance(value, list):
            if _key_selects(str(key), passenger) or _key_selects(next_path, passenger):
                found.extend(str(rule_id) for rule_id in value)
        elif isinstance(value, dict):
            found.extend(_rules_for(passenger, value, next_path))
    return found


def _traveler(passengers, last_name, first_name):
    matches = [
        passenger
        for passenger in passengers
        if passenger.get("pnr") == LOCATOR
        and passenger.get("lastName") == last_name
        and passenger.get("firstName") == first_name
    ]
    assert len(matches) == 1
    return matches[0]


def _audit_and_manifest(monkeypatch):
    document = _load_manifest_with_two_distinct_failures()
    same_locator = [
        passenger for passenger in document["passengers"] if passenger.get("pnr") == LOCATOR
    ]
    assert len(same_locator) >= 5
    ids = {passenger.get("passengerId") for passenger in same_locator}
    associations = {str(passenger.get("nameAssociationId")) for passenger in same_locator}
    assert len(ids) == len(same_locator)
    assert len(associations) == len(same_locator)

    db = _Db(copy.deepcopy(document))
    monkeypatch.setattr(audit_route, "get_db", lambda: db)
    monkeypatch.setattr(passengers_route, "get_db", lambda: db)
    audit = audit_route.get_flight_audit(FLIGHT, origin=ORIGIN, date=DATE)
    # FastAPI injects None for omitted query params. Calling the route function
    # directly must do the same, or the Query default object is truthy and the
    # handler reads the snapshot collection instead of the passenger list.
    manifest = passengers_route.get_passengers(
        FLIGHT, origin=ORIGIN, date=DATE, snapshot_sequence=None
    )
    return audit, manifest


def test_passenger_alerts_stay_with_the_traveler_who_failed(monkeypatch):
    audit, manifest = _audit_and_manifest(monkeypatch)
    passengers = manifest["passengers"]
    botla = _traveler(passengers, "BOTLA", "VINAYAK MR")
    safi = _traveler(passengers, "SAFI", "MD GUFRAN MR")
    gupta = _traveler(passengers, "GUPTA", "SHIVAM MR")
    puliyanchali = _traveler(passengers, "PULIYANCHALI", "RASHEEDALI MR")
    valiya = _traveler(passengers, "VALIYA PARAMBATH", "SHAHID MUNEER MR")

    boarded = [alert for alert in audit["alerts"] if alert["ruleId"] == "BOARDED_WITHOUT_BP"]
    checked_in = [alert for alert in audit["alerts"] if alert["ruleId"] == "CHECKEDIN_NO_BP"]
    assert [(alert.get("pnr"), alert.get("passengerName")) for alert in boarded] == [
        (LOCATOR, "BOTLA/VINAYAK MR")
    ]
    assert [(alert.get("pnr"), alert.get("passengerName")) for alert in checked_in] == [
        (LOCATOR, "SAFI/MD GUFRAN MR")
    ]

    index = audit["passengerAlerts"]
    assert "BOARDED_WITHOUT_BP" in _rules_for(botla, index)
    assert "CHECKEDIN_NO_BP" in _rules_for(safi, index)

    for innocent in (safi, gupta, puliyanchali, valiya):
        assert "BOARDED_WITHOUT_BP" not in _rules_for(innocent, index)
    for innocent in (botla, gupta, puliyanchali, valiya):
        assert "CHECKEDIN_NO_BP" not in _rules_for(innocent, index)


def test_passenger_table_icon_follows_the_failing_traveler(monkeypatch):
    audit, manifest = _audit_and_manifest(monkeypatch)
    payload = {
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "date": DATE,
        "manifest": manifest,
        "audit": audit,
        "watched": [BOARDED_WITHOUT_BP, CHECKED_IN_WITHOUT_BP, *UNINVOLVED],
    }
    completed = subprocess.run(
        ["npx", "--yes", "tsx", RENDER_SCRIPT],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=os.path.join(REPO_ROOT, "frontend"),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout)
    assert rendered["loading"] is False
    assert rendered["error"] is False

    icons = rendered["icons"]
    assert icons[BOARDED_WITHOUT_BP] is True
    assert icons[CHECKED_IN_WITHOUT_BP] is True
    for label in UNINVOLVED:
        assert icons[label] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
