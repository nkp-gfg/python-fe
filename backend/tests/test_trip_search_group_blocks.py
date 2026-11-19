"""Trip Search group blocks must survive conversion and the reservation API.

Sabre Trip_SearchRS reservations carry Passengers.PNRB children named
Corporate, BlockedSpaceGroup, and ZGroup in addition to Passenger.
convert_reservations currently keeps only Passenger. These tests parse XML
the same way SabreClient.get_reservations does, store that converted document
the way the feeder stores it, and read it back through the reservation routes.

They fail until each kept group is returned as travelerGroups:

    {
        "groupType": "Corporate" | "BlockedSpaceGroup" | "ZGroup",
        "name": str,
        "id": str,
        "nameId": str,
        "numInGroup": int | None,
        "numberOfSeatsRemaining": int | None,
    }

numInGroup and numberOfSeatsRemaining are None when that element is not in
the group block. Zero is a real NumberOfSeatsRemaining and must stay 0.
Named Passenger rows must still be returned.

Not executed here: frontend/components/dashboard/reservation-view.tsx, which
renders only reservation.passengers, and backend/feeder/differ.py
diff_reservations, which compares numberInParty only. GetPassengerList
groupCode handling is a different response shape and is not this path.
"""

import copy
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import reservations as reservations_api
from backend.feeder.converter import convert_reservations
from backend.feeder.runner import _process_api_call
from backend.feeder import storage
from backend.sabre.client import SabreClient

SAMPLE_XML = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "Trip_SearchRS_GF2274_DMM_2026-03-19.xml",
)

# Gulf Air Trip Search capture for GF2274 / DMM / 2026-03-19.
# Group name, counts, and name ids are taken from that response.
GF2274_GROUPS = {
    "IQOWOH": {"numInGroup": 90, "id": "3", "nameId": "1.C", "party": 94},
    "LACREE": {"numInGroup": 90, "id": "3", "nameId": "1.C", "party": 92},
    "IODHDL": {"numInGroup": 12, "id": "4", "nameId": "1.C", "party": 12},
}


def _parse_trip_search(xml_text):
    """Same parse and body extract as SabreClient.get_reservations."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _convert(xml_text, flight_number="2274", origin="DMM", departure_date="2026-03-19"):
    raw = _parse_trip_search(xml_text)
    return convert_reservations(raw, "GF", flight_number, origin, departure_date)


def _matches(doc, query):
    for key, expected in query.items():
        if key == "reservations.pnr":
            if not any(item.get("pnr") == expected for item in doc.get("reservations", [])):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, sort=None, projection=None):
        found = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                found.sort(key=lambda doc: doc.get(field) or "", reverse=direction < 0)
        if not found:
            return None
        return copy.deepcopy(found[0])


class _FakeDB:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _client_for(reservation_doc, snapshot_doc=None):
    db = _FakeDB({
        "reservations": _FakeCollection([reservation_doc]),
        "snapshots": _FakeCollection([snapshot_doc] if snapshot_doc else []),
    })
    app = FastAPI()
    app.include_router(reservations_api.router)
    app.dependency_overrides.clear()
    return app, db


@pytest.fixture
def api(monkeypatch):
    holder = {}

    def _mount(reservation_doc, snapshot_doc=None):
        app, db = _client_for(reservation_doc, snapshot_doc)
        holder["db"] = db
        monkeypatch.setattr(reservations_api, "get_db", lambda: db)
        return TestClient(app)

    return _mount


def _assert_corporate(reservation, num_in_group, group_id, name_id):
    groups = reservation.get("travelerGroups")
    assert groups, (
        f"PNR {reservation.get('pnr')} dropped its Corporate group block; "
        "travelerGroups is missing"
    )
    corporate = next((group for group in groups if group.get("groupType") == "Corporate"), None)
    assert corporate is not None, f"PNR {reservation.get('pnr')} has no Corporate travelerGroups entry"
    assert corporate["name"] == "PAX/HYD GRP"
    assert corporate["numInGroup"] == num_in_group
    assert corporate["numberOfSeatsRemaining"] == 0
    assert corporate["id"] == group_id
    assert corporate["nameId"] == name_id


def _assert_named_passengers_kept(reservation, party_size):
    assert reservation["numberInParty"] == party_size
    assert len(reservation["passengers"]) == party_size
    assert reservation["passengers"][0]["lastName"]
    assert reservation["passengers"][0]["firstName"]


def test_gf2274_list_endpoint_returns_corporate_groups(api):
    xml_text = open(SAMPLE_XML, encoding="utf-8").read()
    doc = _convert(xml_text)
    client = api(doc)

    response = client.get("/flights/2274/reservations", params={"origin": "DMM", "date": "2026-03-19"})
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    by_pnr = {item["pnr"]: item for item in body["reservations"]}
    assert set(GF2274_GROUPS) <= set(by_pnr)
    for pnr, expected in GF2274_GROUPS.items():
        _assert_corporate(by_pnr[pnr], expected["numInGroup"], expected["id"], expected["nameId"])
        _assert_named_passengers_kept(by_pnr[pnr], expected["party"])


def test_gf2274_pnr_endpoint_returns_corporate_group(api):
    xml_text = open(SAMPLE_XML, encoding="utf-8").read()
    doc = _convert(xml_text)
    client = api(doc)

    response = client.get("/flights/2274/reservations/IQOWOH", params={"date": "2026-03-19"})
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert len(body["reservations"]) == 1
    reservation = body["reservations"][0]
    _assert_corporate(reservation, 90, "3", "1.C")
    _assert_named_passengers_kept(reservation, 94)


def test_gf2274_snapshot_endpoint_returns_corporate_group(api):
    xml_text = open(SAMPLE_XML, encoding="utf-8").read()
    doc = _convert(xml_text)
    snapshot = {
        "flightNumber": "2274",
        "origin": "DMM",
        "departureDate": "2026-03-19",
        "snapshotType": "reservations",
        "sequenceNumber": 2,
        "capturedAt": doc["fetchedAt"],
        "data": doc,
    }
    client = api(doc, snapshot)

    response = client.get(
        "/flights/2274/reservations",
        params={"origin": "DMM", "date": "2026-03-19", "snapshot_sequence": 2},
    )
    assert response.status_code == 200, response.text[:500]
    by_pnr = {item["pnr"]: item for item in response.json()["reservations"]}
    _assert_corporate(by_pnr["IQOWOH"], 90, "3", "1.C")
    _assert_named_passengers_kept(by_pnr["IQOWOH"], 94)


GROUP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS Version="4.5.0">
      <ReservationsList NumberResults="2" TotalResults="2">
        <Reservations>
          <Reservation Locator="GRPALL">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-19T00:00:00</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Corporate id="11" nameId="1.C">
                      <stl19:Name>CORP GROUP</stl19:Name>
                      <stl19:NumInGroup>8</stl19:NumInGroup>
                      <stl19:NumberOfSeatsRemaining>2</stl19:NumberOfSeatsRemaining>
                    </stl19:Corporate>
                    <stl19:BlockedSpaceGroup id="12" nameId="2.B">
                      <stl19:Name>BLOCK GROUP</stl19:Name>
                    </stl19:BlockedSpaceGroup>
                    <stl19:ZGroup id="13" nameId="3.Z">
                      <stl19:Name>ZED GROUP</stl19:Name>
                      <stl19:NumInGroup>4</stl19:NumInGroup>
                      <stl19:NumberOfSeatsRemaining>1</stl19:NumberOfSeatsRemaining>
                    </stl19:ZGroup>
                    <stl19:Passenger nameId="02.01" nameType="S">
                      <stl19:LastName>DOE</stl19:LastName>
                      <stl19:FirstName>JANE</stl19:FirstName>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
          <Reservation Locator="GRPONLY">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="8" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-19T00:00:00</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Corporate id="21" nameId="9.C">
                      <stl19:Name>ONLY GROUP</stl19:Name>
                      <stl19:NumInGroup>8</stl19:NumInGroup>
                      <stl19:NumberOfSeatsRemaining>3</stl19:NumberOfSeatsRemaining>
                    </stl19:Corporate>
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


def test_all_three_group_types_survive_list_and_pnr_endpoints(api):
    doc = _convert(GROUP_XML, flight_number="2274", origin="DMM", departure_date="2026-03-19")
    client = api(doc)

    listed = client.get("/flights/2274/reservations", params={"origin": "DMM", "date": "2026-03-19"})
    assert listed.status_code == 200, listed.text[:500]
    by_pnr = {item["pnr"]: item for item in listed.json()["reservations"]}

    combined = by_pnr["GRPALL"]
    groups = {group["groupType"]: group for group in combined.get("travelerGroups") or []}
    assert set(groups) == {"Corporate", "BlockedSpaceGroup", "ZGroup"}
    assert groups["Corporate"] == {
        "groupType": "Corporate",
        "name": "CORP GROUP",
        "id": "11",
        "nameId": "1.C",
        "numInGroup": 8,
        "numberOfSeatsRemaining": 2,
    }
    assert groups["BlockedSpaceGroup"] == {
        "groupType": "BlockedSpaceGroup",
        "name": "BLOCK GROUP",
        "id": "12",
        "nameId": "2.B",
        "numInGroup": None,
        "numberOfSeatsRemaining": None,
    }
    assert groups["ZGroup"] == {
        "groupType": "ZGroup",
        "name": "ZED GROUP",
        "id": "13",
        "nameId": "3.Z",
        "numInGroup": 4,
        "numberOfSeatsRemaining": 1,
    }
    assert len(combined["passengers"]) == 1
    assert combined["passengers"][0]["lastName"] == "DOE"
    assert combined["passengers"][0]["firstName"] == "JANE"

    only = by_pnr["GRPONLY"]
    assert only.get("travelerGroups") == [{
        "groupType": "Corporate",
        "name": "ONLY GROUP",
        "id": "21",
        "nameId": "9.C",
        "numInGroup": 8,
        "numberOfSeatsRemaining": 3,
    }]
    assert only["passengers"] == []
    assert only["numberInParty"] == 8

    by_locator = client.get("/flights/2274/reservations/GRPALL")
    assert by_locator.status_code == 200, by_locator.text[:500]
    stored = by_locator.json()["reservations"][0]
    stored_groups = {group["groupType"]: group for group in stored.get("travelerGroups") or []}
    assert stored_groups["BlockedSpaceGroup"]["name"] == "BLOCK GROUP"
    assert stored_groups["ZGroup"]["numInGroup"] == 4
    assert stored_groups["Corporate"]["numberOfSeatsRemaining"] == 2


def test_ingestion_stores_group_blocks_from_trip_search(monkeypatch):
    raw = _parse_trip_search(GROUP_XML)
    stored = {}

    monkeypatch.setattr(storage, "store_raw_request", lambda **kwargs: "request-id")
    monkeypatch.setattr(storage, "update_flight_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        storage,
        "store_snapshot",
        lambda **kwargs: stored.__setitem__("snapshot", kwargs["data"]) or ("snap", "checksum", True),
    )

    def _store_reservations(doc):
        stored["reservations"] = doc
        return "inserted"

    monkeypatch.setattr(storage, "store_reservations", _store_reservations)

    flight_info = {
        "airline": "GF",
        "flightNumber": "2274",
        "origin": "DMM",
        "departureDate": "2026-03-19",
    }
    _process_api_call(
        "Reservations",
        "reservations",
        flight_info,
        raw,
        "<Trip_SearchRS/>",
        {"requestXml": "<Trip_SearchRQ/>", "httpStatus": 200, "durationMs": 1},
        convert_reservations,
        (raw, "GF", "2274", "DMM", "2026-03-19"),
    )

    reservation = next(item for item in stored["reservations"]["reservations"] if item["pnr"] == "GRPONLY")
    assert reservation.get("travelerGroups") == [{
        "groupType": "Corporate",
        "name": "ONLY GROUP",
        "id": "21",
        "nameId": "9.C",
        "numInGroup": 8,
        "numberOfSeatsRemaining": 3,
    }]
    snapshot_reservation = next(
        item for item in stored["snapshot"]["reservations"] if item["pnr"] == "GRPALL"
    )
    kinds = {group["groupType"] for group in snapshot_reservation.get("travelerGroups") or []}
    assert kinds == {"Corporate", "BlockedSpaceGroup", "ZGroup"}
