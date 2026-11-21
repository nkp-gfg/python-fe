"""Partner record locator from Sabre teletype bookings must survive ingest.

Trip Search returns the selling-system record under the point-of-sale source
as TTYRecordLocator (CRS code, record locator, agency id, IATA number).
Reservation conversion must copy that element onto each reservation as
``teletypeRecordLocator``, and the reservation list response model must keep
it. These tests fail until that happens.

The flight dashboard is not covered. It aggregates the converted reservation
and does not return reservation records, so it has no place to expose this
locator. The raw Sabre archive collection is not covered either: no route
reads it. The reservation view component is not covered: it renders whatever
the list endpoint returns and has no separate read of the Sabre body.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Constructing SabreClient reads these names. The parse path under test does
# not use them; placeholders keep a real secret out of the test.
os.environ.setdefault("SABRE_BASE_URL", "https://placeholder.invalid")
os.environ.setdefault("SABRE_CPAID", "XX")
os.environ.setdefault("SABRE_USERNAME", "placeholder")
os.environ.setdefault("SABRE_PASSWORD", "placeholder")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "XXX")
os.environ.setdefault("SABRE_ORGANIZATION", "XX")
os.environ.setdefault("SABRE_DOMAIN", "XX")

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import reservations as reservations_route
from backend.feeder.converter import _strip_ns, convert_reservations
from backend.sabre.client import SabreClient

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

# Values taken from TTYRecordLocator on the committed GF2057 Trip Search
# response. The Sabre locator stays on ``pnr``; these are the other system's.
_GCPVKA = {
    "crsCode": "1A",
    "recordLocator": "CEKUDX",
    "agencyId": "HOUG42100",
    "iataNumber": "4554163",
}
_QCWBUJ = {
    "crsCode": "1G",
    "recordLocator": "G8391K",
    "agencyId": "NR1",
    "iataNumber": "07210114",
}
_EMPTY = {
    "crsCode": "",
    "recordLocator": "",
    "agencyId": "",
    "iataNumber": "",
}


def _trip_search_body():
    """Parse the captured Trip Search SOAP response the way get_reservations does."""
    client = SabreClient()
    parsed = client._parse_xml(_SAMPLE.read_text(encoding="utf-8"))
    return client._extract_body(parsed, "Trip_SearchRS")


def _converted():
    """Run the same converter arguments the feeder passes for this flight."""
    return convert_reservations(
        _trip_search_body(), "GF", "2057", "BOM", "2026-03-18"
    )


def _reservation(doc, pnr):
    matches = [r for r in doc["reservations"] if r.get("pnr") == pnr]
    assert len(matches) == 1, pnr
    return matches[0]


def _tty_source(parsed_body, pnr):
    data = _strip_ns(parsed_body)
    reservations = data["ReservationsList"]["Reservations"]["Reservation"]
    if isinstance(reservations, dict):
        reservations = [reservations]
    for reservation in reservations:
        if reservation.get("@Locator") != pnr:
            continue
        source = reservation["GetReservationRS"]["Reservation"]["POS"]["Source"]
        return source.get("TTYRecordLocator")
    raise AssertionError(pnr)


class _Collection:
    """Enough of a Mongo collection for the reservation read routes.

    find_one returns a copy. PyMongo does the same, and the routes mutate the
    document they return when they strip ``_raw``.
    """

    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc, field=field: doc.get(field) or "",
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])


class _Db:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _matches(doc, query):
    for key, expected in query.items():
        if not _value_matches(_lookup(doc, key), expected):
            return False
    return True


def _lookup(doc, key):
    if "." not in key:
        return doc.get(key)
    head, tail = key.split(".", 1)
    value = doc.get(head)
    if isinstance(value, list):
        return [
            item.get(tail) if isinstance(item, dict) else None
            for item in value
        ]
    if isinstance(value, dict):
        return value.get(tail)
    return None


def _value_matches(actual, expected):
    if isinstance(expected, dict) and "$lte" in expected:
        return actual is not None and actual <= expected["$lte"]
    if isinstance(actual, list):
        return expected in actual
    return actual == expected


def _client_for(doc):
    snapshot = {
        "flightNumber": "2057",
        "snapshotType": "reservations",
        "sequenceNumber": 4,
        "origin": "BOM",
        "departureDate": "2026-03-18",
        "data": copy.deepcopy(doc),
    }
    db = _Db({
        "reservations": _Collection([copy.deepcopy(doc)]),
        "snapshots": _Collection([snapshot]),
    })
    reservations_route.get_db = lambda: db
    app = FastAPI()
    app.include_router(reservations_route.router)
    return TestClient(app)


def test_parser_delivers_teletype_record_to_the_converter():
    """The product's SOAP parser keeps the nested teletype element."""
    body = _trip_search_body()
    tty = _tty_source(body, "GCPVKA")
    assert tty["CRSCode"] == _GCPVKA["crsCode"]
    assert tty["RecordLocator"] == _GCPVKA["recordLocator"]
    assert tty["AgencyId"] == _GCPVKA["agencyId"]
    assert tty["IataNumber"] == _GCPVKA["iataNumber"]
    absent = _tty_source(body, "ATAMUS")
    assert absent is None


def test_convert_reservations_stores_partner_locator():
    doc = _converted()
    gcp = _reservation(doc, "GCPVKA")
    qc = _reservation(doc, "QCWBUJ")
    host = _reservation(doc, "ATAMUS")

    # Sabre's own locator and the point-of-sale attributes stay in place.
    assert gcp["pnr"] == "GCPVKA"
    assert gcp["pointOfSale"]["pseudoCityCode"] == "TTY"
    assert gcp["pointOfSale"]["isoCountry"] == "US"
    assert host["pnr"] == "ATAMUS"
    assert host["pointOfSale"]["agentSine"] == "ND1"
    assert host["pointOfSale"]["pseudoCityCode"] == "BAH"

    assert gcp["teletypeRecordLocator"] == _GCPVKA
    assert qc["teletypeRecordLocator"] == _QCWBUJ
    # A source with no teletype child still exposes the same object.
    assert host["teletypeRecordLocator"] == _EMPTY


def _assert_partner_locators(payload):
    by_pnr = {r["pnr"]: r for r in payload["reservations"]}
    assert by_pnr["GCPVKA"]["pnr"] == "GCPVKA"
    assert by_pnr["GCPVKA"]["teletypeRecordLocator"] == _GCPVKA
    assert by_pnr["QCWBUJ"]["teletypeRecordLocator"] == _QCWBUJ
    assert by_pnr["ATAMUS"]["teletypeRecordLocator"] == _EMPTY
    assert "_raw" not in payload
    for reservation in payload["reservations"]:
        assert "_raw" not in reservation


def test_reservation_list_returns_partner_locator():
    client = _client_for(_converted())
    response = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert response.status_code == 200, response.text[:500]
    _assert_partner_locators(response.json())


def test_reservation_list_snapshot_returns_partner_locator():
    client = _client_for(_converted())
    response = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18", "snapshot_sequence": 4},
    )
    assert response.status_code == 200, response.text[:500]
    _assert_partner_locators(response.json())


def test_reservation_by_pnr_returns_partner_locator_without_raw_body():
    client = _client_for(_converted())
    response = client.get(
        "/flights/2057/reservations/GCPVKA",
        params={"date": "2026-03-18"},
    )
    assert response.status_code == 200, response.text[:500]
    payload = response.json()
    assert payload["flightNumber"] == "2057"
    assert "_raw" not in payload
    assert len(payload["reservations"]) == 1
    reservation = payload["reservations"][0]
    assert reservation["pnr"] == "GCPVKA"
    assert reservation["teletypeRecordLocator"] == _GCPVKA
    assert "CEKUDX" not in str(payload.get("_raw", ""))
