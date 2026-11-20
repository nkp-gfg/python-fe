"""Party-size audit must not treat a lap infant as a missing passenger.

Sabre's reservation ``numberInParty`` counts the lap infant. The passenger
list does not add a row for that infant; it sets ``hasInfant`` and the ``INF``
edit code on the accompanying adult. ``numberOfInfants`` is stored on the
reservation. The sector headcount is ``numberInParty`` minus the lap infants
accounted for by that stored count or by those manifest flags.

These tests fail while the audit compares ``numberInParty`` with the manifest
row count directly.
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.audit_rules import run_audit
from backend.api.routes.audit import router
from backend.feeder.converter import _strip_ns, convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient

_ROOT = Path(__file__).resolve().parents[2]
_PAX_XML = _ROOT / "sample_data" / "sample_xmlfiles" / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
_TRIP_XML = _ROOT / "sample_data" / "sample_xmlfiles" / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"

_FLIGHT = "2057"
_ORIGIN = "BOM"
_DATE = "2026-03-19"
_PNR = "FXXTVG"


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def find_one(self, query, sort=None, projection=None):
        return self._one

    def find(self, query, projection=None):
        return _Cursor(self._many)


class _DB:
    def __init__(self, passenger_doc, reservations_doc, changes=None):
        self._collections = {
            "passenger_list": _Collection(one=passenger_doc),
            "reservations": _Collection(one=reservations_doc),
            "changes": _Collection(many=changes or []),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _parse_sabre_body(xml_text, response_key):
    """Run the same SOAP parse the feeder uses, without opening a session."""
    parsed = SabreClient._parse_xml(xml_text)
    client = object.__new__(SabreClient)
    return client._extract_body(parsed, response_key)


def _adult(pnr, *, infant=False, edit_codes=None):
    if edit_codes is None:
        edit_codes = ["INF"] if infant else []
    return {
        "pnr": pnr,
        "lastName": "KHAN",
        "firstName": "FATIMA",
        "hasInfant": infant,
        "editCodes": list(edit_codes),
        "isBoarded": False,
        "isCheckedIn": False,
        "boardingPassIssued": False,
    }


def _reservation(pnr, party, infants=0, include_infants=True):
    reservation = {"pnr": pnr, "numberInParty": party}
    if include_infants:
        reservation["numberOfInfants"] = infants
    return reservation


def _audit(passengers, reservations):
    return run_audit(
        {"passengers": passengers, "cabinSummary": []},
        {"reservations": reservations},
        [],
    )


def _party_alerts(result, pnr=None):
    alerts = [alert for alert in result["alerts"] if alert["ruleId"] == "PARTY_SIZE_MISMATCH"]
    if pnr is not None:
        alerts = [alert for alert in alerts if alert.get("pnr") == pnr]
    return alerts


def _assert_no_party_alert(result, pnr):
    alerts = _party_alerts(result, pnr)
    assert alerts == [], f"{pnr} was reported missing because a lap infant was counted: {alerts}"


def _assert_gap(result, pnr, declared, manifest):
    alerts = _party_alerts(result, pnr)
    assert len(alerts) == 1, alerts
    alert = alerts[0]
    assert alert["severity"] == "warning"
    details = alert["details"]
    assert details["declaredPartySize"] == declared
    assert details["manifestCount"] == manifest
    assert str(declared) in alert["message"]
    assert str(manifest) in alert["message"]


def _get_audit(monkeypatch, passenger_doc, reservations_doc):
    monkeypatch.setattr(
        "backend.api.routes.audit.get_db",
        lambda: _DB(passenger_doc, reservations_doc),
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.get(
        f"/flights/{_FLIGHT}/audit",
        params={"origin": _ORIGIN, "date": _DATE},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def gf2057_ingested():
    """Convert the captured GF2057 Sabre replies the way ingestion does."""
    passenger_body = _parse_sabre_body(_PAX_XML.read_text(encoding="utf-8"), "GetPassengerListRS")
    reservation_body = _parse_sabre_body(_TRIP_XML.read_text(encoding="utf-8"), "Trip_SearchRS")
    passenger_doc = convert_passenger_list(passenger_body, "GF", _FLIGHT, _DATE, _ORIGIN)
    reservations_doc = convert_reservations(reservation_body, "GF", _FLIGHT, _ORIGIN, _DATE)
    return passenger_body, reservation_body, passenger_doc, reservations_doc


def _fxx_source_counts(reservation_body):
    """Read the party attributes the converter sees after namespace prefixes are removed."""
    stripped = _strip_ns(reservation_body)
    data_reservations = stripped["ReservationsList"]["Reservations"]["Reservation"]
    if isinstance(data_reservations, dict):
        data_reservations = [data_reservations]
    source = next(item for item in data_reservations if item.get("@Locator") == _PNR)
    inner = source["GetReservationRS"]["Reservation"]
    return {
        "numberInSegment": inner.get("@NumberInSegment"),
        "numberInParty": inner.get("@numberInParty"),
        "numberOfInfants": inner.get("@numberOfInfants"),
    }


def test_ingested_fxxxtvg_counts_match_the_sabre_sample(gf2057_ingested):
    """The captured booking is an adult plus a lap infant, stored that way."""
    _passenger_body, reservation_body, passenger_doc, reservations_doc = gf2057_ingested
    source = _fxx_source_counts(reservation_body)
    assert source == {
        "numberInSegment": "1",
        "numberInParty": "2",
        "numberOfInfants": "1",
    }

    stored = next(item for item in reservations_doc["reservations"] if item["pnr"] == _PNR)
    assert stored["numberInParty"] == 2
    assert stored["numberOfInfants"] == 1

    rows = [pax for pax in passenger_doc["passengers"] if pax.get("pnr") == _PNR]
    assert len(rows) == 1
    assert rows[0]["hasInfant"] is True
    assert "INF" in rows[0]["editCodes"]


def test_audit_endpoint_does_not_warn_that_fxxxtvg_is_missing(gf2057_ingested, monkeypatch):
    """GET /flights/2057/audit must not warn when the extra party member is the lap infant."""
    _passenger_body, _reservation_body, passenger_doc, reservations_doc = gf2057_ingested
    body = _get_audit(monkeypatch, passenger_doc, reservations_doc)
    _assert_no_party_alert(body, _PNR)


def test_audit_endpoint_still_warns_for_a_real_shortfall(monkeypatch):
    """A party larger than the manifest plus its lap infants is still a warning."""
    passenger_doc = {
        "flightNumber": _FLIGHT,
        "origin": _ORIGIN,
        "departureDate": _DATE,
        "passengers": [_adult("REAL1", infant=True), _adult("REAL1")],
        "cabinSummary": [],
    }
    reservations_doc = {
        "flightNumber": _FLIGHT,
        "departureDate": _DATE,
        "reservations": [_reservation("REAL1", party=4, infants=1)],
    }
    body = _get_audit(monkeypatch, passenger_doc, reservations_doc)
    # Sector headcount is 4 - 1 lap infant = 3, and two manifest rows are present.
    _assert_gap(body, "REAL1", declared=3, manifest=2)


@pytest.mark.parametrize(
    ("passengers", "reservation", "expected"),
    [
        pytest.param(
            [_adult("LAP1", infant=True)],
            _reservation("LAP1", party=2, infants=1),
            None,
            id="stored-infant-count-and-inf-edit-code",
        ),
        pytest.param(
            [_adult("LAP2", infant=False, edit_codes=["INF"])],
            _reservation("LAP2", party=2, infants=0),
            None,
            id="inf-edit-code-without-stored-infant-count",
        ),
        pytest.param(
            [_adult("LAP3", infant=True, edit_codes=[])],
            _reservation("LAP3", party=2, infants=0),
            None,
            id="has-infant-flag-without-stored-infant-count",
        ),
        pytest.param(
            [_adult("LAP4")],
            _reservation("LAP4", party=2, infants=1),
            None,
            id="stored-infant-count-without-manifest-flag",
        ),
        pytest.param(
            [_adult("LAP5")],
            _reservation("LAP5", party=2, include_infants=False),
            {"declared": 2, "manifest": 1},
            id="missing-infant-field-does-not-hide-a-real-gap",
        ),
        pytest.param(
            [_adult("LAP6", infant=True)],
            _reservation("LAP6", party=3, infants=2),
            None,
            id="two-stored-infants-on-one-adult",
        ),
        pytest.param(
            [_adult("BOTH", infant=True), _adult("BOTH")],
            _reservation("BOTH", party=3, infants=1),
            None,
            id="adult-and-lap-infant-both-accounted-for",
        ),
        pytest.param(
            [_adult("BOTH2", infant=True), _adult("BOTH2", infant=True)],
            _reservation("BOTH2", party=4, infants=2),
            None,
            id="two-adults-each-with-a-lap-infant",
        ),
        pytest.param(
            [_adult("MISS", infant=True)],
            _reservation("MISS", party=3, infants=1),
            {"declared": 2, "manifest": 1},
            id="infant-does-not-cancel-an-additional-missing-adult",
        ),
        pytest.param(
            [_adult("MISS2", infant=True), _adult("MISS2")],
            _reservation("MISS2", party=4, infants=1),
            {"declared": 3, "manifest": 2},
            id="do-not-add-the-inf-flag-on-top-of-the-stored-infant-count",
        ),
        pytest.param(
            [_adult("EXACT")],
            _reservation("EXACT", party=1, infants=0),
            None,
            id="party-matches-manifest",
        ),
        pytest.param(
            [_adult("NONE")],
            _reservation("NONE", party=2, infants=0),
            {"declared": 2, "manifest": 1},
            id="real-mismatch-without-infants",
        ),
        pytest.param(
            [_adult("MORE"), _adult("MORE")],
            _reservation("MORE", party=1, infants=0),
            {"declared": 1, "manifest": 2},
            id="manifest-larger-than-party-still-warns",
        ),
        pytest.param(
            [_adult("OTHER")],
            _reservation("ABSENT", party=2, infants=1),
            None,
            id="reservation-absent-from-the-manifest-is-not-this-warning",
        ),
    ],
)
def test_party_size_rule_excludes_lap_infants(passengers, reservation, expected):
    result = _audit(passengers, [reservation])
    pnr = reservation["pnr"]
    if expected is None:
        _assert_no_party_alert(result, pnr)
    else:
        _assert_gap(result, pnr, expected["declared"], expected["manifest"])


def test_run_audit_and_the_route_share_the_party_size_rule(monkeypatch):
    """The only flight-audit entry point must apply the same lap-infant count."""
    passengers = [_adult("ROUTE", infant=True)]
    reservation = _reservation("ROUTE", party=2, infants=1)
    direct = _audit(passengers, [reservation])
    via_route = _get_audit(
        monkeypatch,
        {
            "flightNumber": _FLIGHT,
            "origin": _ORIGIN,
            "departureDate": _DATE,
            "passengers": passengers,
            "cabinSummary": [],
        },
        {
            "flightNumber": _FLIGHT,
            "departureDate": _DATE,
            "reservations": [reservation],
        },
    )
    assert _party_alerts(direct, "ROUTE") == []
    assert _party_alerts(via_route, "ROUTE") == []
