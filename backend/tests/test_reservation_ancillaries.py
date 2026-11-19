"""Paid Trip Search ancillaries must survive ingestion and the reservation API.

Sabre places AncillaryServices on each passenger. The charged amount is
TTLPrice Price/Currency, the count is NumberOfItems, and the EMD document
number is on the ancillary Segment. These tests fail while conversion reads
the passenger-reservation wrapper and the ancillary element's own EMDNumber
and Quantity, and while the reservation response model drops the amount.
"""

import copy
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import reservations as reservations_route
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.sabre.client import SabreClient

SAMPLE_XML = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)

ANCILLARY_FIELDS = (
    "code",
    "status",
    "quantity",
    "emdNumber",
    "groupCode",
    "subGroupCode",
    "price",
    "currency",
)

# Synthetic SOAP body. Passenger one's ancillary element carries a different
# EMDNumber and Quantity than the segment document number and NumberOfItems.
# Those element values must not be the ones stored.
SYNTHETIC_TRIP_SEARCH = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS Version="4.5.0">
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="ABC123">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="2" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-19T01:00:00</stl19:CreationTimestamp>
                  <stl19:UpdateTimestamp>2026-03-19T02:00:00</stl19:UpdateTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="01.01">
                      <stl19:LastName>ONE</stl19:LastName>
                      <stl19:FirstName>PAX</stl19:FirstName>
                      <stl19:AncillaryServices>
                        <stl19:AncillaryService>
                          <stl19:CommercialName>EXTRA BAG</stl19:CommercialName>
                          <stl19:EMDNumber>0000000000000</stl19:EMDNumber>
                          <stl19:Quantity>1</stl19:Quantity>
                          <stl19:TTLPrice>
                            <stl19:Price>15.500</stl19:Price>
                            <stl19:Currency>BHD</stl19:Currency>
                          </stl19:TTLPrice>
                          <stl19:ActionCode>HI</stl19:ActionCode>
                          <stl19:NumberOfItems>4</stl19:NumberOfItems>
                          <stl19:GroupCode>BG</stl19:GroupCode>
                          <stl19:Segment>
                            <stl19:EMDNumber>9990000000001</stl19:EMDNumber>
                          </stl19:Segment>
                        </stl19:AncillaryService>
                      </stl19:AncillaryServices>
                    </stl19:Passenger>
                    <stl19:Passenger nameId="02.01">
                      <stl19:LastName>TWO</stl19:LastName>
                      <stl19:FirstName>PAX</stl19:FirstName>
                      <stl19:AncillaryServices>
                        <stl19:AncillaryService>
                          <stl19:CommercialName>LEGROOM SEAT</stl19:CommercialName>
                          <stl19:TTLPrice>
                            <stl19:Price>21.000</stl19:Price>
                            <stl19:Currency>BHD</stl19:Currency>
                          </stl19:TTLPrice>
                          <stl19:ActionCode>HD</stl19:ActionCode>
                          <stl19:NumberOfItems>2</stl19:NumberOfItems>
                          <stl19:GroupCode>SA</stl19:GroupCode>
                          <stl19:Segment>
                            <stl19:EMDNumber>9990000000002</stl19:EMDNumber>
                          </stl19:Segment>
                        </stl19:AncillaryService>
                      </stl19:AncillaryServices>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""

SYNTHETIC_EXPECTED = [
    {
        "code": "EXTRA BAG",
        "status": "HI",
        "quantity": 4,
        "emdNumber": "9990000000001",
        "groupCode": "BG",
        "subGroupCode": "",
        "price": "15.500",
        "currency": "BHD",
    },
    {
        "code": "LEGROOM SEAT",
        "status": "HD",
        "quantity": 2,
        "emdNumber": "9990000000002",
        "groupCode": "SA",
        "subGroupCode": "",
        "price": "21.000",
        "currency": "BHD",
    },
]


def _trip_search_body(xml_text):
    """Parse SOAP the same way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _text(value):
    if isinstance(value, dict):
        value = value.get("#text", "")
    if value is None:
        return ""
    return str(value)


def _segment_emd(ancillary):
    segment = ancillary.get("Segment")
    if isinstance(segment, dict):
        segments = [segment]
    elif isinstance(segment, list):
        segments = segment
    else:
        segments = []
    for seg in segments:
        if isinstance(seg, dict):
            emd = _text(seg.get("EMDNumber")).strip()
            if emd:
                return emd
    return ""


def _expected_from_passengers(ancillary):
    """Map one passenger AncillaryService the way the stored row must look."""
    ttl = ancillary.get("TTLPrice")
    if not isinstance(ttl, dict):
        ttl = {}
    status = _text(ancillary.get("StatusCode")).strip()
    if not status:
        status = _text(ancillary.get("ActionCode")).strip()
    number_of_items = ancillary.get("NumberOfItems")
    try:
        quantity = int(number_of_items) if number_of_items not in (None, "") else 1
    except (TypeError, ValueError):
        quantity = 1
    return {
        "code": _text(ancillary.get("CommercialName") or ancillary.get("Code")),
        "status": status,
        "quantity": quantity,
        "emdNumber": _segment_emd(ancillary),
        "groupCode": _text(ancillary.get("GroupCode")),
        "subGroupCode": _text(ancillary.get("SubGroupCode")),
        "price": _text(ttl.get("Price")),
        "currency": _text(ttl.get("Currency")),
    }


def _expected_by_pnr(raw_body):
    data = _strip_ns(raw_body)
    reservations = _ensure_list(
        (data.get("ReservationsList", {}).get("Reservations") or {}).get("Reservation", [])
    )
    expected = {}
    for reservation in reservations:
        pnr = reservation.get("@Locator", "")
        inner = reservation.get("GetReservationRS", {}).get("Reservation", {})
        passengers = _ensure_list(
            (inner.get("PassengerReservation", {}).get("Passengers") or {}).get("Passenger", [])
        )
        rows = []
        for passenger in passengers:
            services = _ensure_list(
                (passenger.get("AncillaryServices") or {}).get("AncillaryService", [])
            )
            for service in services:
                if not isinstance(service, dict) or not service:
                    continue
                rows.append(_expected_from_passengers(service))
        expected[pnr] = rows
    return expected


def _stored_rows(reservation):
    rows = []
    for ancillary in reservation.get("ancillaryServices") or []:
        missing = [field for field in ANCILLARY_FIELDS if field not in ancillary]
        assert not missing, f"ancillary row missing {missing}: {ancillary!r}"
        rows.append({field: ancillary[field] for field in ANCILLARY_FIELDS})
    return rows


def _convert_sample():
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    body = _trip_search_body(xml_text)
    document = convert_reservations(body, "GF", "2057", "BOM", "2026-03-19")
    return document, _expected_by_pnr(body)


def _convert_synthetic():
    body = _trip_search_body(SYNTHETIC_TRIP_SEARCH)
    document = convert_reservations(body, "GF", "2057", "BOM", "2026-03-19")
    return document


class _Collection:
    def __init__(self, document):
        self.document = document

    def find_one(self, query, sort=None):
        assert query.get("flightNumber") == "2057"
        return copy.deepcopy(self.document)


def _client_for(document, snapshot_sequence=None):
    snapshot = {
        "flightNumber": "2057",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "snapshotType": "reservations",
        "sequenceNumber": 3,
        "capturedAt": "2026-03-19T08:55:50Z",
        "data": copy.deepcopy(document),
    }
    database = {
        "reservations": _Collection(document),
        "snapshots": _Collection(snapshot),
    }

    def get_db():
        return database

    reservations_route.get_db = get_db
    app = FastAPI()
    app.include_router(reservations_route.router)
    return TestClient(app)


def test_trip_search_capture_keeps_paid_ancillary_emd_and_price():
    """GF2057 Trip Search capture: legroom seat 21.000 BHD and its segment EMD."""
    document, expected = _convert_sample()
    paid = [
        row
        for rows in expected.values()
        for row in rows
        if row["price"] == "21.000" and row["currency"] == "BHD"
    ]
    assert len(paid) == 1
    assert paid[0]["code"] == "LEGROOM  SEAT"
    assert paid[0]["emdNumber"] == "0721938046219"
    assert paid[0]["quantity"] == 1
    assert paid[0]["status"] == "HI"
    assert paid[0]["groupCode"] == "SA"
    assert sum(len(rows) for rows in expected.values()) == 7

    stored = {
        reservation["pnr"]: _stored_rows(reservation)
        for reservation in document["reservations"]
    }
    assert stored == expected


def test_segment_emd_and_item_count_override_element_fields():
    """NumberOfItems and Segment/EMDNumber win over Quantity and element EMDNumber."""
    document = _convert_synthetic()
    assert len(document["reservations"]) == 1
    assert _stored_rows(document["reservations"][0]) == SYNTHETIC_EXPECTED


def test_get_reservations_returns_emd_and_amount():
    document = _convert_synthetic()
    client = _client_for(document)
    response = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-19"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["flightNumber"] == "2057"
    assert _stored_rows(body["reservations"][0]) == SYNTHETIC_EXPECTED


def test_get_reservations_snapshot_returns_emd_and_amount():
    document = _convert_synthetic()
    client = _client_for(document)
    response = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-19", "snapshot_sequence": 3},
    )
    assert response.status_code == 200
    body = response.json()
    assert _stored_rows(body["reservations"][0]) == SYNTHETIC_EXPECTED


def test_get_reservation_by_pnr_returns_emd_and_amount():
    document = _convert_synthetic()
    client = _client_for(document)
    response = client.get("/flights/2057/reservations/ABC123")
    assert response.status_code == 200
    body = response.json()
    assert len(body["reservations"]) == 1
    assert _stored_rows(body["reservations"][0]) == SYNTHETIC_EXPECTED


def test_captured_flight_reservations_endpoint_returns_paid_seat():
    document, expected = _convert_sample()
    client = _client_for(document)
    response = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-19"},
    )
    assert response.status_code == 200
    body = response.json()
    stored = {
        reservation["pnr"]: _stored_rows(reservation)
        for reservation in body["reservations"]
    }
    assert stored == expected
    paid = [
        row
        for rows in stored.values()
        for row in rows
        if row["emdNumber"] == "0721938046219"
    ]
    assert len(paid) == 1
    assert paid[0]["price"] == "21.000"
    assert paid[0]["currency"] == "BHD"
