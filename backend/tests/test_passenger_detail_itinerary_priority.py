"""The live passenger-detail itinerary must show Sabre's priority-listing code.

The sheet test fails while that code is stored on the detail response and left
off the itinerary card. Run:

    python3 -m pytest backend/tests/test_passenger_detail_itinerary_priority.py -q
"""

import ast
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.api.routes import passengers  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402

SAMPLE = (
    REPO
    / "sabre_docs/GetPassengerDataRQ_v4-0-4_Samples/GetPassengerDataRQ_v4-0-3_SamplePayload_4_2_7.xml"
)
VENDOR_PNR = "VWLGVM"
VENDOR_SEAT = "17D"
VENDOR_PRIORITY = "EOS"
SECOND_SEAT = "4A"
SECOND_PRIORITY = "PL9"
SECOND_PASSENGER_TYPE = "C"
BLANK_SEAT = "9Z"


def _vendor_response_xml() -> str:
    text = SAMPLE.read_text()
    marker = "<!--Response-->"
    start = text.index(marker) + len(marker)
    return text[start:].strip()


def _passenger_data_body():
    """Parse the vendor SOAP response the same way SabreClient.get_passenger_data does."""
    parsed = SabreClient._parse_xml(_vendor_response_xml())
    body = SabreClient._extract_body(None, parsed, "GetPassengerDataRS")
    itinerary = body["PassengerDataResponseList"]["PassengerDataResponse"][
        "PassengerItineraryList"
    ]["PassengerItinerary"]
    assert itinerary["Priority"] == VENDOR_PRIORITY
    assert itinerary["Seat"] == VENDOR_SEAT
    assert itinerary["PassengerType"] == "F"

    listed = copy.deepcopy(itinerary)
    second = copy.deepcopy(itinerary)
    second["SegmentID"] = "3"
    second["Seat"] = SECOND_SEAT
    second["Priority"] = SECOND_PRIORITY
    second["PassengerType"] = SECOND_PASSENGER_TYPE
    blank = copy.deepcopy(itinerary)
    blank["SegmentID"] = "4"
    blank["Seat"] = BLANK_SEAT
    blank.pop("Priority", None)
    body["PassengerDataResponseList"]["PassengerDataResponse"][
        "PassengerItineraryList"
    ]["PassengerItinerary"] = [listed, second, blank]
    return body


class _PassengerList:
    def find_one(self, query, sort=None, **kwargs):
        assert query["flightNumber"] == "1"
        assert query["passengers.pnr"] == VENDOR_PNR
        assert query["departureDate"] == "2018-05-05"
        return {
            "flightNumber": "1",
            "origin": "AUH",
            "departureDate": "2018-05-05",
            "fetchedAt": "2018-05-04T20:41:07Z",
            "passengers": [
                {"pnr": "OTHER1", "lastName": "OTHER", "firstName": "NAME"},
                {"pnr": VENDOR_PNR, "lastName": "TEST", "firstName": "PAX"},
            ],
        }


class _DB:
    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


class _Sabre:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_passenger_data(self, **kwargs):
        self.calls.append(kwargs)
        return _passenger_data_body(), "", {}


def _client(monkeypatch):
    monkeypatch.setattr(passengers, "get_db", lambda: _DB())
    monkeypatch.setattr(passengers, "SabreClient", _Sabre)
    app = FastAPI()
    app.include_router(passengers.router)
    return TestClient(app)


def _detail(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        f"/flights/1/passengers/{VENDOR_PNR}/detail",
        params={"origin": "AUH", "date": "2018-05-05"},
    )
    assert response.status_code == 200, response.text
    assert "authorization" not in {name.lower() for name in response.request.headers}
    return response.json()


def test_production_app_mounts_detail_without_auth():
    source = (REPO / "backend/api/main.py").read_text()
    tree = ast.parse(source)
    mounted = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "include_router":
            if isinstance(func.value, ast.Name) and func.value.id == "app":
                if node.args and isinstance(node.args[0], ast.Attribute):
                    target = node.args[0]
                    if (
                        isinstance(target.value, ast.Name)
                        and target.value.id == "passengers"
                        and target.attr == "router"
                    ):
                        mounted = True
        if isinstance(func, ast.Name) and func.id == "FastAPI":
            assert not any(keyword.arg == "dependencies" for keyword in node.keywords)
    assert mounted
    assert "HTTPBearer" not in source
    assert "APIKey" not in source


def test_detail_route_returns_each_segment_priority(monkeypatch):
    _Sabre.calls = []
    payload = _detail(monkeypatch)
    assert _Sabre.calls == [
        {
            "airline": "GF",
            "flight_number": "1",
            "departure_date": "2018-05-05",
            "origin": "AUH",
            "last_name": "TEST",
            "first_name": "PAX",
            "pnr": VENDOR_PNR,
        }
    ]
    by_seat = {segment["seat"]: segment for segment in payload["passengers"][0]["itinerary"]}
    assert by_seat[VENDOR_SEAT]["priority"] == VENDOR_PRIORITY
    assert by_seat[VENDOR_SEAT]["passengerType"] == "F"
    assert by_seat[VENDOR_SEAT]["cabin"] == "Y"
    assert by_seat[VENDOR_SEAT]["bookingClass"] == "Y"
    assert by_seat[SECOND_SEAT]["priority"] == SECOND_PRIORITY
    assert by_seat[SECOND_SEAT]["passengerType"] == SECOND_PASSENGER_TYPE
    assert by_seat[BLANK_SEAT]["priority"] == ""


def test_detail_sheet_shows_priority_listing_code(monkeypatch, tmp_path):
    payload = _detail(monkeypatch)
    fixture = tmp_path / "passenger-detail.json"
    fixture.write_text(json.dumps(payload))
    env = os.environ.copy()
    env["PASSENGER_DETAIL_FIXTURE"] = str(fixture)
    result = subprocess.run(
        ["npm", "run", "test:passenger-detail-priority"],
        cwd=REPO / "frontend",
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    assert result.returncode == 0, "passenger detail sheet hid the priority listing code"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
