"""Flown Trip Search legs must stay marked past and stay out of the connection count.

These tests fail while reservation conversion ignores the Sabre isPast attribute.
A one-way booking that still carries an already-flown leg is then stored, returned,
and counted as a current confirmed connection.
"""

import copy
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import flights as flights_routes
from backend.api.routes import reservations as reservation_routes
from backend.api.routes.flights import _build_dashboard_payload, _build_insights
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.sabre.client import SabreClient

SAMPLE_XML = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-18.xml"
)

# Departure dates are deliberately inverted against the flag. A fix that infers
# "already flown" from the date, instead of from isPast, classifies these backwards.
INVERTED_DATE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="PASTDT">
            <GetReservationRS>
              <Reservation numberInParty="1">
                <PassengerReservation>
                  <Segments>
                    <Segment>
                      <Air isPast="true">
                        <DepartureAirport>BAH</DepartureAirport>
                        <ArrivalAirport>BLR</ArrivalAirport>
                        <DepartureDateTime>2026-04-01T10:00:00</DepartureDateTime>
                        <ArrivalDateTime>2026-04-01T16:00:00</ArrivalDateTime>
                        <MarketingAirlineCode>GF</MarketingAirlineCode>
                        <FlightNumber>0280</FlightNumber>
                        <ClassOfService>W</ClassOfService>
                        <ActionCode>HK</ActionCode>
                      </Air>
                    </Segment>
                    <Segment>
                      <Air isPast="false">
                        <DepartureAirport>BOM</DepartureAirport>
                        <ArrivalAirport>DMM</ArrivalAirport>
                        <DepartureDateTime>2026-01-01T10:00:00</DepartureDateTime>
                        <ArrivalDateTime>2026-01-01T12:00:00</ArrivalDateTime>
                        <MarketingAirlineCode>GF</MarketingAirlineCode>
                        <FlightNumber>2057</FlightNumber>
                        <ClassOfService>W</ClassOfService>
                        <ActionCode>HK</ActionCode>
                      </Air>
                    </Segment>
                  </Segments>
                </PassengerReservation>
              </Reservation>
            </GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _trip_search_body(xml_text):
    """Parse SOAP the same way SabreClient.get_reservations does, without calling Sabre."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _air_segments_by_pnr(body):
    """Air segments the converter walks, in order, with the raw isPast attribute."""
    data = _strip_ns(body)
    reservations = _ensure_list(
        (data.get("ReservationsList", {}).get("Reservations") or {}).get(
            "Reservation", []
        )
    )
    by_pnr = {}
    for reservation in reservations:
        locator = reservation.get("@Locator", "")
        inner = reservation.get("GetReservationRS", {}).get("Reservation", {})
        passenger_reservation = inner.get("PassengerReservation", {})
        raw_segments = _ensure_list(
            (passenger_reservation.get("Segments") or {}).get("Segment", [])
        )
        airs = []
        for segment in raw_segments:
            air = segment.get("Air")
            if isinstance(air, dict) and air:
                airs.append(air)
        by_pnr[locator] = airs
    return by_pnr


def _flag_is_past(air):
    return str(air.get("@isPast", "false")).lower() == "true"


def _segment_view(segment):
    return {
        "flight": f"{segment.get('marketingAirline', '')}{segment.get('flightNumber', '')}",
        "from": segment.get("departureAirport"),
        "to": segment.get("arrivalAirport"),
        "status": segment.get("status"),
        "isPast": segment.get("isPast"),
    }


def _expected_distribution(airs_by_pnr):
    counts = {}
    for airs in airs_by_pnr.values():
        current = sum(1 for air in airs if not _flag_is_past(air))
        counts[current] = counts.get(current, 0) + 1
    total = sum(counts.values())
    multi = sum(count for segments, count in counts.items() if segments > 1)
    pct = round(multi / total * 100, 1) if total else 0
    return dict(sorted(counts.items())), pct


def _convert_sample():
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    body = _trip_search_body(xml_text)
    document = convert_reservations(body, "GF", "2057", "BOM", "2026-03-18")
    # The raw archive holds the captured response, including session material.
    # The reservation card and the connection count read the normalized document.
    document.pop("_raw", None)
    return body, document


def _reservation(document, pnr):
    matches = [item for item in document["reservations"] if item.get("pnr") == pnr]
    assert len(matches) == 1, pnr
    return matches[0]


def _assert_flags_match_source(document, airs_by_pnr):
    assert document["reservations"], "converter returned no reservations"
    for reservation in document["reservations"]:
        pnr = reservation["pnr"]
        airs = airs_by_pnr[pnr]
        stored = reservation["segments"]
        assert len(stored) == len(airs), (pnr, len(stored), len(airs))
        for air, segment in zip(airs, stored):
            assert segment["departureAirport"] == air.get("DepartureAirport", "")
            assert segment["arrivalAirport"] == air.get("ArrivalAirport", "")
            assert segment["flightNumber"] == air.get("FlightNumber", "")
            assert segment["status"] == air.get("ActionCode", "")
            assert segment["isPast"] is _flag_is_past(air), _segment_view(segment)


class _Reservations:
    def __init__(self, document):
        self.document = document

    def find_one(self, query, projection=None, sort=None):
        document = self.document
        if document is None:
            return None
        if query.get("flightNumber") and query["flightNumber"] != document.get(
            "flightNumber"
        ):
            return None
        if query.get("departureAirport") and query["departureAirport"] != document.get(
            "departureAirport"
        ):
            return None
        if query.get("departureDate") and query["departureDate"] != document.get(
            "departureDate"
        ):
            return None
        wanted = query.get("reservations.pnr")
        if wanted is not None and wanted not in {
            item.get("pnr") for item in document.get("reservations", [])
        }:
            return None
        return copy.deepcopy(document)


class _Database(dict):
    def __init__(self, document):
        super().__init__()
        self._reservations = _Reservations(document)

    def __getitem__(self, name):
        if name == "reservations":
            return self._reservations
        return _Reservations(None)


def _client_with_document(monkeypatch, document):
    monkeypatch.setattr(reservation_routes, "get_db", lambda: _Database(document))
    app = FastAPI()
    app.include_router(reservation_routes.router)
    return TestClient(app)


def _segment_from_response(payload, pnr, flight_number):
    reservation = next(item for item in payload["reservations"] if item["pnr"] == pnr)
    return next(
        segment
        for segment in reservation["segments"]
        if segment["flightNumber"] == flight_number
    )


def _insights_for(reservations):
    """Run the dashboard payload the flight board returns when a manifest exists."""
    payload = _build_dashboard_payload(
        {"status": "OPEN", "origin": "BOM"},
        {
            "passengers": [{"pnr": "X", "lastName": "TEST"}],
            "origin": "BOM",
            "departureDate": "2026-03-18",
            "totalPassengers": 1,
        },
        "BOM",
        "2026-03-18",
        {},
        {"reservations": reservations},
        None,
        None,
    )
    assert payload["insights"] is not None
    return payload["insights"]["multiSegment"]


def test_sample_response_parses_past_attribute_before_conversion():
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    body = _trip_search_body(xml_text)
    airs_by_pnr = _air_segments_by_pnr(body)
    atamus = airs_by_pnr["ATAMUS"]
    assert [(air.get("FlightNumber"), air.get("@isPast"), air.get("ActionCode")) for air in atamus] == [
        ("0280", "true", "HK"),
        ("2057", "false", "HK"),
    ]
    netogy_flags = [air.get("@isPast") for air in airs_by_pnr["NETOGY"]]
    assert netogy_flags == ["true", "true", "false"]


def test_conversion_stores_is_past_for_every_sample_air_segment():
    body, document = _convert_sample()
    _assert_flags_match_source(document, _air_segments_by_pnr(body))
    flown = _segment_from_response(document, "ATAMUS", "0280")
    current = _segment_from_response(document, "ATAMUS", "2057")
    assert flown["status"] == "HK"
    assert flown["departureAirport"] == "BAH"
    assert flown["arrivalAirport"] == "BLR"
    assert flown["isPast"] is True
    assert current["status"] == "HK"
    assert current["isPast"] is False


def test_conversion_uses_is_past_attribute_not_departure_date():
    body = _trip_search_body(INVERTED_DATE_XML)
    document = convert_reservations(body, "GF", "2057", "BOM", "2026-03-18")
    document.pop("_raw", None)
    _assert_flags_match_source(document, _air_segments_by_pnr(body))
    insights = _insights_for(document["reservations"])
    assert insights["distribution"] == {1: 1}
    assert insights["multiSegmentPct"] == 0


def test_reservation_list_and_pnr_endpoints_return_the_flag(monkeypatch):
    _body, document = _convert_sample()
    client = _client_with_document(monkeypatch, document)

    listed = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert listed.status_code == 200
    flown = _segment_from_response(listed.json(), "ATAMUS", "0280")
    current = _segment_from_response(listed.json(), "ATAMUS", "2057")
    assert flown["status"] == "HK"
    assert flown["isPast"] is True
    assert current["isPast"] is False
    history = _segment_from_response(listed.json(), "NETOGY", "0274")
    assert history["isPast"] is True
    assert history["status"] == "HK"

    by_pnr = client.get(
        "/flights/2057/reservations/ATAMUS",
        params={"date": "2026-03-18"},
    )
    assert by_pnr.status_code == 200
    flown = _segment_from_response(by_pnr.json(), "ATAMUS", "0280")
    assert flown["isPast"] is True
    assert flown["status"] == "HK"


def test_snapshot_reservation_list_returns_the_flag(monkeypatch):
    _body, document = _convert_sample()

    def _as_of(*_args, **_kwargs):
        return copy.deepcopy(document)

    monkeypatch.setattr(reservation_routes, "get_db", lambda: _Database(None))
    monkeypatch.setattr(reservation_routes, "get_snapshot_data_as_of", _as_of)
    app = FastAPI()
    app.include_router(reservation_routes.router)
    client = TestClient(app)
    response = client.get(
        "/flights/2057/reservations",
        params={"origin": "BOM", "date": "2026-03-18", "snapshot_sequence": 2},
    )
    assert response.status_code == 200
    flown = _segment_from_response(response.json(), "ATAMUS", "0280")
    assert flown["isPast"] is True


def test_multi_segment_count_excludes_flown_legs_and_keeps_real_connections():
    body, document = _convert_sample()
    expected, expected_pct = _expected_distribution(_air_segments_by_pnr(body))
    insights = _insights_for(document["reservations"])
    assert insights["distribution"] == expected
    assert insights["multiSegmentPct"] == expected_pct

    one_way_with_history = _insights_for([_reservation(document, "ATAMUS")])
    assert one_way_with_history["distribution"] == {1: 1}
    assert one_way_with_history["multiSegmentPct"] == 0

    flown_round_trip_plus_current = _insights_for([_reservation(document, "NETOGY")])
    assert flown_round_trip_plus_current["distribution"] == {1: 1}
    assert flown_round_trip_plus_current["multiSegmentPct"] == 0


def test_real_connection_stays_multi_segment():
    _body, document = _convert_sample()
    connection = _insights_for([_reservation(document, "QCWBUJ")])
    assert connection["distribution"] == {2: 1}
    assert connection["multiSegmentPct"] == 100.0


def test_dashboard_endpoint_publishes_the_same_count(monkeypatch):
    body, document = _convert_sample()
    expected, expected_pct = _expected_distribution(_air_segments_by_pnr(body))

    def _fetch(*_args, **_kwargs):
        return {
            "flight_status": {"status": "OPEN", "origin": "BOM"},
            "passenger_list": {
                "passengers": [{"pnr": "X", "lastName": "TEST"}],
                "origin": "BOM",
                "departureDate": "2026-03-18",
                "totalPassengers": 1,
            },
            "reservations": document,
            "trip_reports": None,
            "schedule": None,
            "change_summary": {},
            "flight_meta": None,
        }

    flights_routes._dashboard_cache.clear()
    monkeypatch.setattr(flights_routes, "get_db", lambda: object())
    monkeypatch.setattr(flights_routes, "_fetch_dashboard_data_parallel", _fetch)
    app = FastAPI()
    app.include_router(flights_routes.router)
    client = TestClient(app)
    response = client.get(
        "/flights/2057/dashboard",
        params={"origin": "BOM", "date": "2026-03-18"},
    )
    assert response.status_code == 200
    multi = response.json()["insights"]["multiSegment"]
    assert {int(key): value for key, value in multi["distribution"].items()} == expected
    assert multi["multiSegmentPct"] == expected_pct


def test_reservation_card_labels_flown_leg_and_keeps_current_confirmed():
    """Render the reservations screen with a past HK leg beside the current HK leg."""
    repo = Path(__file__).resolve().parents[2]
    frontend = repo / "frontend"
    source = frontend / "tests" / "past-segment-card.tsx"
    runner = Path(__file__).resolve().parent / "run_reservation_card.cjs"
    esbuild_dir = Path(tempfile.mkdtemp(prefix="past-segment-card-"))
    install = subprocess.run(
        ["npm", "install", "--ignore-scripts", "esbuild", "jsdom"],
        cwd=esbuild_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if install.returncode != 0:
        raise AssertionError(install.stderr[-2000:])
    bundle = esbuild_dir / "bundle.cjs"
    built = subprocess.run(
        [
            str(esbuild_dir / "node_modules" / ".bin" / "esbuild"),
            str(source),
            "--bundle",
            "--platform=node",
            "--format=cjs",
            f"--outfile={bundle}",
            f"--alias:@={frontend}",
        ],
        cwd=frontend,
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        raise AssertionError(built.stderr[-4000:] or built.stdout[-4000:])
    env = os.environ.copy()
    env["NODE_PATH"] = str(esbuild_dir / "node_modules")
    rendered = subprocess.run(
        ["node", str(runner), str(bundle)],
        cwd=frontend,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert rendered.returncode == 0, (rendered.stdout + rendered.stderr)[-4000:]


def test_direct_insight_helper_matches_dashboard_payload():
    """The helper and the payload must agree, so a fix cannot update only one of them."""
    _body, document = _convert_sample()
    via_payload = _insights_for([_reservation(document, "ATAMUS")])
    via_helper = _build_insights(
        [{"pnr": "X"}],
        {"reservations": [_reservation(document, "ATAMUS")]},
        None,
        {"passengers": [{"pnr": "X"}]},
        {},
    )["multiSegment"]
    assert via_helper == via_payload
    assert via_helper["multiSegmentPct"] == 0
