"""Informational Trip Search air segments must not be stored as confirmed holdings.

Sabre marks a display-only air segment with Informational=true while still
sending action code HK. These tests follow that response through the product's
XML parser, reservation converter, reservation stores, list and PNR endpoints,
confirmed-itinerary counts, and the reservation card.

They fail while the converter drops Informational and the card prints HK.
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_insights
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.feeder import storage
from backend.sabre.client import SabreClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_XML = (
    REPO_ROOT
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)
CARD_RENDERER = REPO_ROOT / "frontend" / "tests" / "render-reservation-card.mjs"

# PNRs in the captured GF2057 Trip Search response whose Air India 2788
# leg is Informational=true and whose Gulf Air 2057 leg is Informational=false.
INFORMATIONAL_PNRS = ("QCWBUJ", "NPMSWW", "CWHLFE")


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class MemoryCollection:
    """Document store with the same insert/find shape the reservation path uses.

    PyMongo's insert_one stores the mapping it is given and adds _id
    (pymongo 4.20.1, synchronous/collection.py). This stand-in does that and
    nothing else, so a missing informational flag is the converter's doing.
    """

    def __init__(self):
        self.docs = []

    def insert_one(self, document):
        stored = copy.deepcopy(document)
        stored["_id"] = f"mem-{len(self.docs) + 1}"
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query=None, sort=None, projection=None):
        matches = [doc for doc in self.docs if _matches(doc, query or {})]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: _sort_value(doc, field),
                    reverse=int(direction) < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def index_information(self):
        return {}

    def create_index(self, keys, **kwargs):
        return kwargs.get("name")

    def drop_index(self, name):
        return None


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]

    def command(self, *args, **kwargs):
        return {"ok": 1}


class _RedisPing:
    def ping(self):
        return True


def _sort_value(doc, field):
    value = doc.get(field)
    return "" if value is None else value


def _matches(doc, query):
    for key, expected in query.items():
        if "." in key:
            head, tail = key.split(".", 1)
            items = doc.get(head) or []
            if not any(
                isinstance(item, dict) and item.get(tail) == expected for item in items
            ):
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


def _parse_trip_search(xml_text):
    """Same two calls get_reservations makes after the SOAP response arrives."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(object(), parsed, "Trip_SearchRS")


def _informational(value):
    return str(value).lower() == "true"


def _raw_air_segments(raw_body):
    """Air segments the converter walks, with Sabre's Informational flag."""
    data = _strip_ns(raw_body)
    wrapper = data.get("ReservationsList") or {}
    reservations = _ensure_list(
        (wrapper.get("Reservations") or {}).get("Reservation", [])
    )
    parsed = []
    for reservation in reservations:
        inner = (reservation.get("GetReservationRS") or {}).get("Reservation") or {}
        passenger_reservation = inner.get("PassengerReservation") or {}
        raw_segments = _ensure_list(
            (passenger_reservation.get("Segments") or {}).get("Segment", [])
        )
        air_segments = []
        for segment in raw_segments:
            if not isinstance(segment, dict):
                continue
            air = segment.get("Air") or {}
            if not isinstance(air, dict) or not air:
                continue
            air_segments.append(
                {
                    "pnr": reservation.get("@Locator", ""),
                    "marketingAirline": air.get("MarketingAirlineCode", ""),
                    "flightNumber": str(air.get("FlightNumber", "")),
                    "actionCode": air.get("ActionCode", ""),
                    "informational": _informational(air.get("Informational", "false")),
                    "informationalElementPresent": "Informational" in air,
                }
            )
        parsed.append(air_segments)
    return parsed


def _confirmed_distribution(raw_body):
    distribution = {}
    for segments in _raw_air_segments(raw_body):
        confirmed = sum(1 for segment in segments if not segment["informational"])
        distribution[confirmed] = distribution.get(confirmed, 0) + 1
    return dict(sorted(distribution.items()))


def _segment(reservation, airline, flight_number):
    for segment in reservation.get("segments") or []:
        if (
            segment.get("marketingAirline") == airline
            and str(segment.get("flightNumber")) == flight_number
        ):
            return segment
    raise AssertionError(f"{airline}{flight_number} segment missing")


def _reservation(payload, pnr):
    for reservation in payload.get("reservations") or []:
        if reservation.get("pnr") == pnr:
            return reservation
    raise AssertionError(f"PNR {pnr} missing")


def _render_card(reservation):
    completed = subprocess.run(
        ["node", str(CARD_RENDERER)],
        input=json.dumps(reservation),
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT / "frontend"),
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "reservation card renderer failed\n"
            + completed.stderr
            + completed.stdout
        )
    return completed.stdout


def _segment_row(html, flight_number):
    start = html.find(flight_number)
    assert start >= 0, f"flight {flight_number} is not on the reservation card"
    row_start = html.rfind("<div", 0, start)
    assert row_start >= 0
    end = html.find("</div>", start)
    assert end >= 0
    return html[row_start:end]


@pytest.fixture(scope="module")
def ingested():
    sample_xml = SAMPLE_XML.read_text(encoding="utf-8")
    sample_raw = _parse_trip_search(sample_xml)
    sample_doc = convert_reservations(sample_raw, "GF", "2057", "BOM", "2026-03-18")

    synthetic_raw = _parse_trip_search(_synthetic_trip_search())
    synthetic_doc = convert_reservations(
        synthetic_raw, "GF", "9999", "BAH", "2026-04-01"
    )

    db = MemoryDB()
    import backend.api.database as database
    import backend.api.main as main
    import backend.api.routes.reservations as reservations_route

    previous_db = database.get_db
    previous_route_db = reservations_route.get_db
    previous_main_db = main.get_db
    previous_redis = main.get_redis
    previous_storage_db = storage._db
    database.get_db = lambda: db
    reservations_route.get_db = lambda: db
    main.get_db = lambda: db
    main.get_redis = lambda: _RedisPing()
    storage.init_db(db)
    storage.store_reservations(sample_doc)
    storage.store_snapshot(
        request_id="req-sample",
        airline="GF",
        flight_number="2057",
        origin="BOM",
        departure_date="2026-03-18",
        snapshot_type="reservations",
        data=sample_doc,
    )
    storage.store_reservations(synthetic_doc)

    from fastapi.testclient import TestClient

    try:
        with TestClient(main.app) as client:
            yield {
                "client": client,
                "sample_raw": sample_raw,
                "sample_doc": sample_doc,
                "synthetic_raw": synthetic_raw,
                "synthetic_doc": synthetic_doc,
            }
    finally:
        database.get_db = previous_db
        reservations_route.get_db = previous_route_db
        main.get_db = previous_main_db
        main.get_redis = previous_redis
        storage._db = previous_storage_db


def _synthetic_trip_search():
    """Minimal Trip_SearchRS with the same element names the sample uses.

    One informational HK leg, one explicit non-informational HK leg, and one
    HK leg that omits Informational. No credentials.
    """
    return """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS Version="4.5.0">
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="SYNTH1">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="1">
                <stl19:PassengerReservation>
                  <stl19:Segments>
                    <stl19:Segment>
                      <stl19:Air>
                        <stl19:DepartureAirport>COK</stl19:DepartureAirport>
                        <stl19:ArrivalAirport>BOM</stl19:ArrivalAirport>
                        <stl19:MarketingAirlineCode>AI</stl19:MarketingAirlineCode>
                        <stl19:FlightNumber>2788</stl19:FlightNumber>
                        <stl19:ClassOfService>U</stl19:ClassOfService>
                        <stl19:ActionCode>HK</stl19:ActionCode>
                        <stl19:Informational>true</stl19:Informational>
                      </stl19:Air>
                    </stl19:Segment>
                    <stl19:Segment>
                      <stl19:Air>
                        <stl19:DepartureAirport>BOM</stl19:DepartureAirport>
                        <stl19:ArrivalAirport>BAH</stl19:ArrivalAirport>
                        <stl19:MarketingAirlineCode>GF</stl19:MarketingAirlineCode>
                        <stl19:FlightNumber>9999</stl19:FlightNumber>
                        <stl19:ClassOfService>Q</stl19:ClassOfService>
                        <stl19:ActionCode>HK</stl19:ActionCode>
                        <stl19:Informational>false</stl19:Informational>
                      </stl19:Air>
                    </stl19:Segment>
                    <stl19:Segment>
                      <stl19:Air>
                        <stl19:DepartureAirport>BAH</stl19:DepartureAirport>
                        <stl19:ArrivalAirport>LHR</stl19:ArrivalAirport>
                        <stl19:MarketingAirlineCode>GF</stl19:MarketingAirlineCode>
                        <stl19:FlightNumber>0001</stl19:FlightNumber>
                        <stl19:ClassOfService>Y</stl19:ClassOfService>
                        <stl19:ActionCode>HK</stl19:ActionCode>
                      </stl19:Air>
                    </stl19:Segment>
                  </stl19:Segments>
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


def test_parser_keeps_informational_and_converter_stores_it(ingested):
    raw_segments = [
        segment
        for reservation in _raw_air_segments(ingested["sample_raw"])
        for segment in reservation
        if segment["pnr"] in INFORMATIONAL_PNRS
    ]
    assert any(
        segment["flightNumber"] == "2788" and segment["informational"] is True
        for segment in raw_segments
    )
    assert any(
        segment["flightNumber"] == "2057" and segment["informational"] is False
        for segment in raw_segments
    )

    for pnr in INFORMATIONAL_PNRS:
        reservation = _reservation(ingested["sample_doc"], pnr)
        inbound = _segment(reservation, "AI", "2788")
        operated = _segment(reservation, "GF", "2057")
        assert inbound["informational"] is True
        assert inbound["status"] == "HK"
        assert operated["informational"] is False
        assert operated["status"] == "HK"


def test_list_and_pnr_endpoints_keep_informational_flag(ingested):
    client = ingested["client"]
    listed = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert listed.status_code == 200
    assert "authorization" not in {name.lower() for name in listed.request.headers}

    for pnr in INFORMATIONAL_PNRS:
        reservation = _reservation(listed.json(), pnr)
        inbound = _segment(reservation, "AI", "2788")
        operated = _segment(reservation, "GF", "2057")
        assert inbound["informational"] is True
        assert operated["informational"] is False
        assert operated["status"] == "HK"

        by_pnr = client.get(
            f"/flights/2057/reservations/{pnr}",
            params={"date": "2026-03-18"},
        )
        assert by_pnr.status_code == 200
        stored = _reservation(by_pnr.json(), pnr)
        assert _segment(stored, "AI", "2788")["informational"] is True
        assert _segment(stored, "GF", "2057")["informational"] is False


def test_snapshot_list_keeps_informational_flag(ingested):
    client = ingested["client"]
    listed = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18", "snapshot_sequence": 1},
    )
    assert listed.status_code == 200
    reservation = _reservation(listed.json(), "QCWBUJ")
    assert _segment(reservation, "AI", "2788")["informational"] is True
    assert _segment(reservation, "GF", "2057")["informational"] is False


def test_confirmed_itinerary_counts_exclude_informational_segments(ingested):
    expected = _confirmed_distribution(ingested["sample_raw"])
    insights = _build_insights([], ingested["sample_doc"], None, None, None)
    assert insights["multiSegment"]["distribution"] == expected
    total = sum(expected.values())
    multi = sum(count for segments, count in expected.items() if segments > 1)
    assert insights["multiSegment"]["multiSegmentPct"] == round(multi / total * 100, 1)

    synthetic_expected = _confirmed_distribution(ingested["synthetic_raw"])
    synthetic_insights = _build_insights(
        [], ingested["synthetic_doc"], None, None, None
    )
    assert synthetic_insights["multiSegment"]["distribution"] == synthetic_expected
    # The informational AI leg is still on the reservation, and the two live
    # HK legs are the confirmed itinerary.
    reservation = _reservation(ingested["synthetic_doc"], "SYNTH1")
    assert len(reservation["segments"]) == 3
    assert _segment(reservation, "AI", "2788")["informational"] is True
    assert _segment(reservation, "GF", "9999")["informational"] is False
    omitted = _segment(reservation, "GF", "0001")
    assert omitted.get("informational", False) is False
    assert omitted["status"] == "HK"
    assert synthetic_expected == {2: 1}


def test_reservation_card_shows_informational_instead_of_confirmed_hk(ingested):
    client = ingested["client"]
    listed = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert listed.status_code == 200
    reservation = _reservation(listed.json(), "QCWBUJ")
    html = _render_card(reservation)

    inbound = _segment_row(html, "2788")
    operated = _segment_row(html, "2057")
    assert "informational" in inbound.lower()
    assert "border-emerald-300" not in inbound
    assert "HK" in operated
    assert "border-emerald-300" in operated
    assert "informational" not in operated.lower()
