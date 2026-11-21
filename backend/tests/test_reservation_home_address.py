"""Home addresses from Trip Search must survive conversion, storage, and lookup.

Sabre places ``Addresses`` on the reservation element (``GetReservationRS`` /
``Reservation``), not on ``PassengerReservation``. Each ``AddressLine`` is
typed: N name, A street, C city, Z postal code, B business, O other.
Repeated lines of one type stay in document order, joined by a newline.

These tests fail until conversion copies that block onto each stored
reservation and ``ReservationsResponse`` keeps the field on the flight
reservations route.
"""

import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import reservations as reservations_route
from backend.feeder import storage
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

SAMPLE_XML = (
    "sample_data/sample_xmlfiles/Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

_LINE_FIELDS = {
    "N": "name",
    "A": "street",
    "C": "city",
    "Z": "postalCode",
    "B": "business",
    "O": "other",
}


def _ensure_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _line_text(line):
    if not isinstance(line, dict):
        return ""
    text = line.get("Text", "")
    if isinstance(text, dict):
        text = text.get("#text", "")
    return "" if text is None else str(text)


def expected_addresses(reservation_element):
    """Map Addresses on the reservation element. Ignore any other parent."""
    block = reservation_element.get("Addresses") or {}
    if not isinstance(block, dict):
        return []
    mapped = []
    for address in _ensure_list(block.get("Address")):
        if not isinstance(address, dict):
            continue
        buckets = {field: [] for field in _LINE_FIELDS.values()}
        lines = (address.get("AddressLines") or {}).get("AddressLine")
        for line in _ensure_list(lines):
            if not isinstance(line, dict):
                continue
            field = _LINE_FIELDS.get(line.get("@type", ""))
            text = _line_text(line)
            if field and text:
                buckets[field].append(text)
        mapped.append({
            "addressType": address.get("@type", "") or "",
            "name": "\n".join(buckets["name"]),
            "street": "\n".join(buckets["street"]),
            "city": "\n".join(buckets["city"]),
            "postalCode": "\n".join(buckets["postalCode"]),
            "business": "\n".join(buckets["business"]),
            "other": "\n".join(buckets["other"]),
        })
    return mapped


def parse_trip_search(xml_text):
    """Use the Sabre client's SOAP parse and body extract, then convert."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def reservation_elements(raw):
    """Yield (locator, inner Reservation element) from a Trip_SearchRS body."""
    from backend.feeder.converter import _strip_ns

    data = _strip_ns(raw)
    wrapper = (data.get("ReservationsList") or {}).get("Reservations") or {}
    for record in _ensure_list(wrapper.get("Reservation")):
        if not isinstance(record, dict):
            continue
        inner = (record.get("GetReservationRS") or {}).get("Reservation") or {}
        yield record.get("@Locator", ""), inner if isinstance(inner, dict) else {}


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class MemoryCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored["_id"] = len(self.docs) + 1
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for key, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc, field=key: _sort_value(doc, field),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        found = copy.deepcopy(matches[0])
        if projection:
            projected = {}
            for key, include in projection.items():
                if include and key in found:
                    projected[key] = found[key]
            return projected
        return found


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]


def _values(doc, path):
    current = [doc]
    for part in path.split("."):
        nxt = []
        for item in current:
            if isinstance(item, list):
                for element in item:
                    if isinstance(element, dict) and part in element:
                        nxt.append(element[part])
            elif isinstance(item, dict) and part in item:
                nxt.append(item[part])
        current = nxt
    return current


def _matches(doc, query):
    for key, expected in query.items():
        vals = _values(doc, key)
        if isinstance(expected, dict) and "$lte" in expected:
            if not any(val is not None and val <= expected["$lte"] for val in vals):
                return False
        elif not any(val == expected for val in vals):
            return False
    return True


def _sort_value(doc, field):
    vals = _values(doc, field)
    return vals[0] if vals else ""


@pytest.fixture
def stored_db(monkeypatch):
    db = MemoryDB()
    previous_db = storage._db
    previous_owns = storage._owns_connection
    storage.init_db(db)
    monkeypatch.setattr(reservations_route, "get_db", lambda: db)
    yield db
    storage._db = previous_db
    storage._owns_connection = previous_owns


def _client():
    app = FastAPI()
    app.include_router(reservations_route.router)
    return TestClient(app)


def _store_converted(raw, airline, flight_number, origin, departure_date):
    converted = convert_reservations(
        raw, airline, flight_number, origin, departure_date
    )
    storage.store_snapshot(
        request_id="req-address",
        airline=airline,
        flight_number=flight_number,
        origin=origin,
        departure_date=departure_date,
        snapshot_type="reservations",
        data=converted,
    )
    storage.store_reservations(converted)
    return converted


def _assert_reservation_addresses(reservations, expected_by_pnr):
    found = {record.get("pnr"): record for record in reservations}
    missing = sorted(set(expected_by_pnr) - set(found))
    assert not missing, missing
    for pnr, expected in expected_by_pnr.items():
        assert found[pnr].get("addresses") == expected, pnr


def test_sample_trip_search_home_address_survives_storage_and_lookups(stored_db):
    """GF2057 Trip Search sample: Salmiya address is stored and returned."""
    xml_text = open(SAMPLE_XML, encoding="utf-8").read()
    raw = parse_trip_search(xml_text)
    expected = {
        pnr: expected_addresses(inner)
        for pnr, inner in reservation_elements(raw)
    }
    assert expected["WUFOLD"] == [{
        "addressType": "",
        "name": "AOUN ASLAM MEMON MEMON",
        "street": "SALMIYA",
        "city": "SALMIYA  KW",
        "postalCode": "00000",
        "business": "",
        "other": "",
    }]
    assert expected["ATAMUS"] == [{
        "addressType": "",
        "name": "",
        "street": "",
        "city": "",
        "postalCode": "",
        "business": "",
        "other": "KARTRAVELANDTOURSWLL\nPOBOX28812\nPOBOX28812, BH BHR",
    }]
    assert any(address["street"] for addresses in expected.values() for address in addresses)

    _store_converted(raw, "GF", "2057", "BOM", "2026-03-18")

    stored = stored_db["reservations"].docs[0]
    _assert_reservation_addresses(stored["reservations"], expected)
    snapshot = stored_db["snapshots"].docs[0]["data"]
    _assert_reservation_addresses(snapshot["reservations"], expected)

    client = _client()
    latest = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert latest.status_code == 200, latest.text
    _assert_reservation_addresses(latest.json()["reservations"], expected)
    assert "_raw" not in latest.json()

    historical = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18", "snapshot_sequence": 1},
    )
    assert historical.status_code == 200, historical.text
    _assert_reservation_addresses(historical.json()["reservations"], expected)

    by_pnr = client.get(
        "/flights/2057/reservations/WUFOLD",
        params={"date": "2026-03-18"},
    )
    assert by_pnr.status_code == 200, by_pnr.text
    _assert_reservation_addresses(
        by_pnr.json()["reservations"],
        {"WUFOLD": expected["WUFOLD"]},
    )

    other_lines = client.get(
        "/flights/2057/reservations/ATAMUS",
        params={"date": "2026-03-18"},
    )
    assert other_lines.status_code == 200, other_lines.text
    _assert_reservation_addresses(
        other_lines.json()["reservations"],
        {"ATAMUS": expected["ATAMUS"]},
    )


def test_address_lines_come_from_reservation_not_passenger_wrapper(stored_db):
    """Line types N/A/C/Z/B/O are read from the reservation element only."""
    xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="3">
        <Reservations>
          <Reservation Locator="SYNTH1">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="1">
                <stl19:PassengerReservation>
                  <stl19:Addresses>
                    <stl19:Address>
                      <stl19:AddressLines>
                        <stl19:AddressLine type="A">
                          <stl19:Text>DECOY STREET</stl19:Text>
                        </stl19:AddressLine>
                      </stl19:AddressLines>
                    </stl19:Address>
                  </stl19:Addresses>
                </stl19:PassengerReservation>
                <stl19:Addresses>
                  <stl19:Address type="C">
                    <stl19:AddressLines>
                      <stl19:AddressLine type="N"><stl19:Text>HOME NAME</stl19:Text></stl19:AddressLine>
                      <stl19:AddressLine type="A"><stl19:Text>STREET ONE</stl19:Text></stl19:AddressLine>
                      <stl19:AddressLine type="A"><stl19:Text>STREET TWO</stl19:Text></stl19:AddressLine>
                      <stl19:AddressLine type="C"><stl19:Text>CITY NAME</stl19:Text></stl19:AddressLine>
                      <stl19:AddressLine type="Z"><stl19:Text>12345</stl19:Text></stl19:AddressLine>
                      <stl19:AddressLine type="B"><stl19:Text>BIZ LINE</stl19:Text></stl19:AddressLine>
                      <stl19:AddressLine type="O"><stl19:Text>OTHER ONE</stl19:Text></stl19:AddressLine>
                      <stl19:AddressLine type="O"><stl19:Text>OTHER TWO</stl19:Text></stl19:AddressLine>
                    </stl19:AddressLines>
                  </stl19:Address>
                  <stl19:Address type="A">
                    <stl19:AddressLines>
                      <stl19:AddressLine type="A"><stl19:Text>AGENCY STREET</stl19:Text></stl19:AddressLine>
                    </stl19:AddressLines>
                  </stl19:Address>
                </stl19:Addresses>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
          <Reservation Locator="SYNTH2">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="1" />
            </stl19:GetReservationRS>
          </Reservation>
          <Reservation Locator="SYNTH3">
            <stl19:GetReservationRS>
              <stl19:Reservation>
                <stl19:Addresses>
                  <stl19:Address>
                    <stl19:AddressLines>
                      <stl19:AddressLine type="A"><stl19:Text>ONLY STREET</stl19:Text></stl19:AddressLine>
                    </stl19:AddressLines>
                  </stl19:Address>
                </stl19:Addresses>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    raw = parse_trip_search(xml_text)
    expected = {
        pnr: expected_addresses(inner)
        for pnr, inner in reservation_elements(raw)
    }
    assert expected["SYNTH1"][0]["street"] == "STREET ONE\nSTREET TWO"
    assert expected["SYNTH1"][0]["addressType"] == "C"
    assert "DECOY STREET" not in str(expected)
    assert expected["SYNTH2"] == []
    assert expected["SYNTH3"] == [{
        "addressType": "",
        "name": "",
        "street": "ONLY STREET",
        "city": "",
        "postalCode": "",
        "business": "",
        "other": "",
    }]

    _store_converted(raw, "GF", "2057", "BOM", "2026-03-18")
    client = _client()
    response = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_reservation_addresses(body["reservations"], expected)
    rendered = str(body)
    assert "DECOY STREET" not in rendered
    assert "STREET ONE" in rendered and "STREET TWO" in rendered
    assert "BIZ LINE" in rendered and "OTHER ONE" in rendered

    single = client.get("/flights/2057/reservations/SYNTH3")
    assert single.status_code == 200, single.text
    _assert_reservation_addresses(
        single.json()["reservations"],
        {"SYNTH3": expected["SYNTH3"]},
    )
