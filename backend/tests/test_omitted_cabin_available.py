"""Open seats when Sabre omits CabinInfo/Available.

GetPassengerList CabinInfo.Available is optional. Gulf Air responses such as
the committed GF2057 passenger list send Count and Authorized and leave
Available out. Ingestion must not store that omission as zero open seats.

Contract these tests lock in:

* ``availableSource`` is ``"reported"`` when the Available element is present.
  ``available`` is that integer, including a real zero. It is not replaced by
  Authorized minus Count.
* ``availableSource`` is ``"derived"`` when Available is absent and both Count
  and Authorized are present. ``available`` is Authorized minus Count, which
  may be zero or negative. That derived zero stays distinct from a reported zero.
* ``availableSource`` is ``"unknown"`` and ``available`` is None when Available
  is absent and Count or Authorized is also absent. None must survive the
  standby, passenger, summary, and data-audit reads; it must not become 0.
* The standby panel shows the derived open-seat count, shows a reported zero
  as sold out, and shows an unknown state when availability cannot be
  determined. An unknown cabin must not render as a full bar with 0 seats left.

The same document is what store_passenger_list and store_snapshot persist
(insert of the converted dict / snapshot ``data``). These tests pass that dict
through the real route functions.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from backend.api.routes import passengers as passengers_routes
from backend.api.routes.data_audit import _compare_passengers
from backend.api.routes.passengers import (
    get_passenger_summary,
    get_passengers,
    get_standby_list,
)
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "sample_data" / "sample_xmlfiles"
RENDERER = ROOT / "frontend" / "tests" / "render-standby-panel.tsx"

# Committed Gulf Air responses whose CabinInfo elements omit Available.
GF2057 = "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
GF2056 = "GetPassengerListRS_GF2056_DMM_2026-03-16.xml"
GF2274 = "GetPassengerListRS_GF2274_DMM_2026-03-19.xml"


class _Collection:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query, sort=None):
        for doc in self._docs:
            if _matches(doc, query):
                return copy.deepcopy(doc)
        return None


class _Database:
    """In-memory stand-in for the collections the passenger routes read.

    store_passenger_list inserts the converted document unchanged, and
    store_snapshot stores it on ``data`` unchanged. find_one in the routes
    has no projection, so returning that same document is the read those
    routes perform.
    """

    def __init__(self, passenger_docs, snapshot_docs=None):
        self._collections = {
            "passenger_list": _Collection(passenger_docs),
            "reservations": _Collection([]),
            "snapshots": _Collection(snapshot_docs or []),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _matches(doc, query):
    for key, expected in query.items():
        if isinstance(expected, dict) and "$lte" in expected:
            actual = doc.get(key)
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


def _body_from_response_xml(xml_text):
    """Run the SOAP parse path get_passenger_list uses after the HTTP call."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _convert_response(xml_text, airline, flight_number, departure_date, origin):
    body = _body_from_response_xml(xml_text)
    return convert_passenger_list(
        body, airline, flight_number, departure_date, origin
    )


def _load_sample(name, airline, flight_number, departure_date, origin):
    xml_text = (SAMPLES / name).read_text(encoding="utf-8")
    return _convert_response(
        xml_text, airline, flight_number, departure_date, origin
    )


def _bind(monkeypatch, doc, snapshots=None):
    db = _Database([doc], snapshots)
    monkeypatch.setattr(passengers_routes, "get_db", lambda: db)
    return db


def _by_cabin(rows):
    return {row["cabin"]: row for row in rows}


def _assert_availability(row, *, available, source, authorized=None, count=None):
    assert row.get("availableSource") == source
    assert row.get("available") == available
    if authorized is not None:
        assert row.get("authorized") == authorized
    if count is not None:
        assert row.get("count") == count


def _availability_markup(html):
    """Visible cabin-availability section, without the standby passenger table."""
    text = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    start = text.find("Cabin Availability")
    end = text.find("Upgrade Queue")
    if start == -1 or end == -1:
        return text
    return text[start:end]


def _render_panel(payload, flight_number, origin, date):
    spec = {
        "flightNumber": flight_number,
        "origin": origin,
        "date": date,
        "payload": payload,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(spec, handle)
        spec_path = handle.name
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_URL"] = "http://127.0.0.1:9"
    completed = subprocess.run(
        ["npx", "--yes", "tsx", str(RENDERER), spec_path],
        cwd=str(ROOT / "frontend"),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    os.unlink(spec_path)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _sold_out_markup(html):
    """True when a cabin bar is drawn full and labeled with zero seats left.

    Match the parenthesized label. A bare ``0 avail`` is also a substring of
    ``10 avail`` and ``120 avail``.
    """
    section = _availability_markup(html)
    compact = section.replace(" ", "").replace("\n", "")
    return "(0 avail)" in section or "width:100%" in compact


REPORTED_ZERO_AND_UNKNOWN = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>1001</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BAH</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
        <CabinInfoList>
          <ns2:CabinInfo>
            <ns2:Destination>DXB</ns2:Destination>
            <ns2:Cabin>J</ns2:Cabin>
            <ns2:Count>2</ns2:Count>
            <ns2:Authorized>12</ns2:Authorized>
            <ns2:Available>0</ns2:Available>
          </ns2:CabinInfo>
          <ns2:CabinInfo>
            <ns2:Destination>DXB</ns2:Destination>
            <ns2:Cabin>Y</ns2:Cabin>
            <ns2:Count>4</ns2:Count>
            <ns2:Authorized>180</ns2:Authorized>
            <ns2:Available>027</ns2:Available>
          </ns2:CabinInfo>
          <ns2:CabinInfo>
            <ns2:Destination>DXB</ns2:Destination>
            <ns2:Cabin>W</ns2:Cabin>
          </ns2:CabinInfo>
          <ns2:CabinInfo>
            <ns2:Destination>DXB</ns2:Destination>
            <ns2:Cabin>Z</ns2:Cabin>
            <ns2:Count>4</ns2:Count>
          </ns2:CabinInfo>
        </CabinInfoList>
      </ItineraryInfo>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def test_gf2057_omitted_available_derives_open_seats_and_standby_panel():
    """Cabin J has 12 authorized and 2 counted, and no Available element.

    The standby list must return 10 open seats marked derived, and the panel
    must not draw that cabin as sold out.
    """
    doc = _load_sample(GF2057, "GF", "2057", "2026-03-19", "BOM")
    stored = _by_cabin(doc["cabinSummary"])
    _assert_availability(
        stored["J"], available=10, source="derived", authorized=12, count=2
    )
    _assert_availability(
        stored["Y"], available=123, source="derived", authorized=180, count=57
    )

    monkeypatch = pytest.MonkeyPatch()
    try:
        _bind(monkeypatch, doc)
        standby = get_standby_list(
            "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
        )
        passengers = get_passengers(
            "2057", origin="BOM", date="2026-03-19", snapshot_sequence=None
        )
        summary = get_passenger_summary(
            "2057", origin="BOM", date="2026-03-19"
        )
    finally:
        monkeypatch.undo()

    for payload in (
        standby["cabinAvailability"],
        passengers["cabinSummary"],
        summary["cabinSummary"],
    ):
        cabins = _by_cabin(payload)
        _assert_availability(
            cabins["J"], available=10, source="derived", authorized=12
        )
        _assert_availability(
            cabins["Y"], available=123, source="derived", authorized=180
        )

    rows = {
        row.field: row.mongoValue
        for row in _compare_passengers(None, doc)
    }
    assert rows["Business Available"] == "10"
    assert rows["Economy Available"] == "123"

    html = _availability_markup(_render_panel(standby, "2057", "BOM", "2026-03-19"))
    assert "(10 avail)" in html
    assert "(123 avail)" in html
    assert not _sold_out_markup(html)


def test_gf2057_historical_standby_view_keeps_derived_open_seats():
    doc = _load_sample(GF2057, "GF", "2057", "2026-03-19", "BOM")
    snapshot = {
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "snapshotType": "passenger_list",
        "sequenceNumber": 4,
        "capturedAt": "2026-03-19T13:56:57Z",
        "data": doc,
    }
    monkeypatch = pytest.MonkeyPatch()
    try:
        _bind(monkeypatch, doc, [snapshot])
        standby = get_standby_list(
            "2057",
            origin="BOM",
            date="2026-03-19",
            snapshot_sequence=4,
        )
    finally:
        monkeypatch.undo()

    cabins = _by_cabin(standby["cabinAvailability"])
    _assert_availability(cabins["J"], available=10, source="derived", authorized=12)
    _assert_availability(
        cabins["Y"], available=123, source="derived", authorized=180
    )


def test_overbooked_gf2056_derives_negative_open_seats():
    """Count 193 and Authorized 192 with no Available element is -1 open seat."""
    doc = _load_sample(GF2056, "GF", "2056", "2026-03-16", "DMM")
    stored = _by_cabin(doc["cabinSummary"])
    _assert_availability(
        stored["Y"], available=-1, source="derived", authorized=192, count=193
    )

    monkeypatch = pytest.MonkeyPatch()
    try:
        _bind(monkeypatch, doc)
        standby = get_standby_list(
            "2056", origin="DMM", date="2026-03-16", snapshot_sequence=None
        )
    finally:
        monkeypatch.undo()

    cabins = _by_cabin(standby["cabinAvailability"])
    _assert_availability(
        cabins["Y"], available=-1, source="derived", authorized=192
    )


def test_full_cabin_omission_is_derived_zero_not_a_reported_zero():
    """GF2274 cabin J is 12 counted and 12 authorized, with Available omitted.

    Open seats are a derived zero. Cabin Y is 136 of 180, so 44 derived.
    """
    doc = _load_sample(GF2274, "GF", "2274", "2026-03-19", "DMM")
    stored = _by_cabin(doc["cabinSummary"])
    _assert_availability(
        stored["J"], available=0, source="derived", authorized=12, count=12
    )
    _assert_availability(
        stored["Y"], available=44, source="derived", authorized=180, count=136
    )


def test_reported_zero_is_kept_and_unknown_cabins_are_not_zero():
    doc = _convert_response(REPORTED_ZERO_AND_UNKNOWN, "GF", "1001", "2026-03-19", "BAH")
    stored = _by_cabin(doc["cabinSummary"])
    _assert_availability(
        stored["J"], available=0, source="reported", authorized=12, count=2
    )
    _assert_availability(
        stored["Y"], available=27, source="reported", authorized=180, count=4
    )
    _assert_availability(stored["W"], available=None, source="unknown")
    _assert_availability(stored["Z"], available=None, source="unknown", count=4)
    assert stored["W"]["available"] is not 0
    assert stored["Z"]["available"] is not 0

    monkeypatch = pytest.MonkeyPatch()
    try:
        _bind(monkeypatch, doc)
        standby = get_standby_list(
            "1001", origin="BAH", date="2026-03-19", snapshot_sequence=None
        )
        summary = get_passenger_summary(
            "1001", origin="BAH", date="2026-03-19"
        )
    finally:
        monkeypatch.undo()

    standby_cabins = _by_cabin(standby["cabinAvailability"])
    summary_cabins = _by_cabin(summary["cabinSummary"])
    for cabins in (standby_cabins, summary_cabins):
        _assert_availability(
            cabins["J"], available=0, source="reported", authorized=12
        )
        _assert_availability(
            cabins["Y"], available=27, source="reported", authorized=180
        )
        _assert_availability(cabins["W"], available=None, source="unknown")
        _assert_availability(cabins["Z"], available=None, source="unknown")

    audit_values = {
        row.field: row.mongoValue for row in _compare_passengers(None, doc)
    }
    assert audit_values["Business Available"] == "0"
    assert audit_values["Economy Available"] == "27"
    assert audit_values.get("Cabin W Available") != "0"
    assert audit_values.get("Cabin Z Available") != "0"


def test_standby_panel_shows_reported_zero_as_sold_out():
    doc = _convert_response(REPORTED_ZERO_AND_UNKNOWN, "GF", "1001", "2026-03-19", "BAH")
    # Panel fixture is the reported-zero cabin only, so the other cabins in the
    # same response cannot satisfy the sold-out assertion by accident.
    reported = dict(_by_cabin(doc["cabinSummary"])["J"])
    doc = dict(doc)
    doc["cabinSummary"] = [reported]
    monkeypatch = pytest.MonkeyPatch()
    try:
        _bind(monkeypatch, doc)
        standby = get_standby_list(
            "1001", origin="BAH", date="2026-03-19", snapshot_sequence=None
        )
    finally:
        monkeypatch.undo()

    cabins = standby["cabinAvailability"]
    assert len(cabins) == 1
    _assert_availability(cabins[0], available=0, source="reported", authorized=12)
    html = _availability_markup(_render_panel(standby, "1001", "BAH", "2026-03-19"))
    compact = html.replace(" ", "").replace("\n", "")
    assert "(0 avail)" in html
    assert "width:100%" in compact


def test_standby_panel_shows_unknown_when_available_cannot_be_derived():
    doc = _convert_response(REPORTED_ZERO_AND_UNKNOWN, "GF", "1001", "2026-03-19", "BAH")
    unknown = dict(_by_cabin(doc["cabinSummary"])["W"])
    doc = dict(doc)
    doc["cabinSummary"] = [unknown]
    monkeypatch = pytest.MonkeyPatch()
    try:
        _bind(monkeypatch, doc)
        standby = get_standby_list(
            "1001", origin="BAH", date="2026-03-19", snapshot_sequence=None
        )
    finally:
        monkeypatch.undo()

    cabins = standby["cabinAvailability"]
    assert len(cabins) == 1
    _assert_availability(cabins[0], available=None, source="unknown")
    html = _availability_markup(_render_panel(standby, "1001", "BAH", "2026-03-19"))
    compact = html.replace(" ", "").replace("\n", "")
    assert "unknown" in html.lower()
    assert "nan" not in html.lower()
    assert "(0 avail)" not in html
    assert "width:100%" not in compact
