"""Flight-status free text must not be published as codeshare partners.

ACS Flight Detail ``FreeTextInfoList`` is ordinary free text (seat counts,
station closeout remarks, and other lines). Those lines already belong in
``freeTextRemarks``. ``codeshareInfo`` may contain only a dedicated
operating-carrier or codeshare element on the itinerary.

These tests follow the product path: SOAP XML is parsed by
``SabreClient._parse_xml`` / ``_extract_body``, normalized by
``convert_flight_status``, then published by the dashboard and flight-status
routes. They fail while free-text lines are copied into ``codeshareInfo``.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights as flight_routes
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient


_GF2006_REMARKS = [
    "26/256",
    "LHR   MS1 2218 LCL CLOSE J7Y38/SECOK- 1",
    "NN",
]
_GF2274_REMARKS = ["12/180", "NN"]


def _envelope(inner):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3" xmlns:ns2="http://services.sabre.com/STL/v3">
      {inner}
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _free_text_blocks(lines):
    """Build one FreeTextInfo per line, matching ACS TextLine/Text."""
    blocks = []
    for line in lines:
        blocks.append(
            "<ns2:FreeTextInfo>"
            "<ns2:TextLine>"
            f"<ns2:Text>{line}</ns2:Text>"
            "</ns2:TextLine>"
            "</ns2:FreeTextInfo>"
        )
    return "<FreeTextInfoList>" + "".join(blocks) + "</FreeTextInfoList>"


def _itinerary(flight, origin, status, extra=""):
    return f"""
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>{flight}</Flight>
          <Origin>{origin}</Origin>
          <ScheduledDepartureDate>2026-03-18</ScheduledDepartureDate>
          <DepartureGate>408</DepartureGate>
          <FlightStatus>{status}</FlightStatus>
          {extra}
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    """


def _parse_flight_detail(xml_text):
    """Run the same SOAP parse the flight-status client uses, without a session."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, "ns3:ACS_FlightDetailRS")


def _convert(inner, flight, origin, departure_date="2026-03-18"):
    raw = _parse_flight_detail(_envelope(inner))
    return convert_flight_status(raw, "GF", flight, origin, departure_date)


def _assert_codeshare_is_only(doc, partners, remarks):
    assert doc["freeTextRemarks"] == remarks
    assert doc["codeshareInfo"] == partners
    for line in remarks:
        assert line not in doc["codeshareInfo"]


class _FlightStatusCollection:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, sort=None, **kwargs):
        return self.docs[0] if self.docs else None

    def find(self, query, **kwargs):
        return _Cursor(list(self.docs))


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *args, **kwargs):
        return self

    def limit(self, count):
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class _Database:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, name):
        assert name == "flight_status"
        return self.collection


def test_gf2006_free_text_is_not_a_codeshare_partner():
    """Seat count, local-close remark, and NN from the GF2006 shape."""
    extra = (
        "<SeatConfig>26/256</SeatConfig>"
        + _free_text_blocks(_GF2006_REMARKS)
    )
    doc = _convert(_itinerary("2006", "LHR", "PDC", extra), "2006", "LHR")

    _assert_codeshare_is_only(doc, [], _GF2006_REMARKS)
    assert doc["aircraft"]["seatConfig"] == "26/256"
    assert doc["gate"] == "408"


def test_gf2274_free_text_is_not_a_codeshare_partner():
    """Seat count and NN from the GF2274 shape."""
    extra = "<SeatConfig>12/180</SeatConfig>" + _free_text_blocks(_GF2274_REMARKS)
    doc = _convert(
        _itinerary("2274", "DMM", "FINAL", extra),
        "2274",
        "DMM",
        "2026-03-19",
    )

    _assert_codeshare_is_only(doc, [], _GF2274_REMARKS)
    assert doc["aircraft"]["seatConfig"] == "12/180"


def test_single_free_text_element_with_edit_code_is_not_a_codeshare_partner():
    """xmltodict leaves a single FreeTextInfo as a dict, not a list."""
    extra = """
      <FreeTextInfoList>
        <ns2:FreeTextInfo>
          <ns2:EditCode>RMK</ns2:EditCode>
          <ns2:TextLine>
            <ns2:Text>NN</ns2:Text>
          </ns2:TextLine>
        </ns2:FreeTextInfo>
      </FreeTextInfoList>
    """
    doc = _convert(_itinerary("9", "BAH", "OPEN", extra), "9", "BAH")
    _assert_codeshare_is_only(doc, [], ["NN"])


def test_second_text_line_is_not_a_codeshare_partner():
    """A TextLine with two Text values must not publish the second as a partner."""
    extra = """
      <FreeTextInfoList>
        <ns2:FreeTextInfo>
          <ns2:TextLine>
            <ns2:Text>SEAT MAP ONLY</ns2:Text>
            <ns2:Text>WY 999</ns2:Text>
          </ns2:TextLine>
        </ns2:FreeTextInfo>
      </FreeTextInfoList>
    """
    doc = _convert(_itinerary("10", "BAH", "OPEN", extra), "10", "BAH")
    _assert_codeshare_is_only(doc, [], ["SEAT MAP ONLY", "WY 999"])


def test_dedicated_carrier_elements_are_partners_and_free_text_is_not():
    """OperatingAirline and Codeshare are partners; free text never is."""
    extra = """
      <OperatingAirline>WY</OperatingAirline>
      <Codeshare>BA</Codeshare>
      <FreeTextInfoList>
        <ns2:FreeTextInfo>
          <ns2:TextLine>
            <ns2:Text>BA</ns2:Text>
            <ns2:Text>WY 999</ns2:Text>
          </ns2:TextLine>
        </ns2:FreeTextInfo>
      </FreeTextInfoList>
    """
    doc = _convert(_itinerary("12", "BAH", "OPEN", extra), "12", "BAH")

    assert sorted(doc["codeshareInfo"]) == ["BA", "WY"]
    assert len(doc["codeshareInfo"]) == 2
    assert doc["freeTextRemarks"] == ["BA", "WY 999"]
    assert "WY 999" not in doc["codeshareInfo"]


def test_missing_free_text_and_carrier_elements_leave_codeshare_empty():
    doc = _convert(_itinerary("13", "BAH", "OPEN"), "13", "BAH")
    assert doc["codeshareInfo"] == []
    assert doc["freeTextRemarks"] == []


def test_dashboard_and_status_routes_do_not_publish_free_text_as_partners(monkeypatch):
    """Gate-row badges and status history both read the stored codeshareInfo."""
    extra = (
        "<SeatConfig>26/256</SeatConfig>"
        + _free_text_blocks(_GF2006_REMARKS)
    )
    stored = _convert(_itinerary("2006", "LHR", "PDC", extra), "2006", "LHR")
    stored["_id"] = "flight-status-1"
    history_doc = {
        **stored,
        "codeshareInfo": list(stored["codeshareInfo"]),
        "freeTextRemarks": list(stored["freeTextRemarks"]),
    }

    monkeypatch.setattr(flight_routes, "get_db", lambda: _Database(
        _FlightStatusCollection([history_doc])
    ))
    monkeypatch.setattr(flight_routes, "_get_cached", lambda key: None)

    def _fetch(*args, **kwargs):
        return {
            "flight_status": stored,
            "passenger_list": {
                "departureGate": "408",
                "passengers": [],
                "origin": "LHR",
                "departureDate": "2026-03-18",
                "totalPassengers": 0,
            },
            "reservations": None,
            "trip_reports": None,
            "schedule": None,
            "change_summary": {},
            "flight_meta": None,
        }

    monkeypatch.setattr(flight_routes, "_fetch_dashboard_data_parallel", _fetch)

    dashboard = flight_routes.get_flight_dashboard(
        "2006", origin="LHR", date="2026-03-18", snapshot_sequence=None,
    )
    status = flight_routes.get_flight_status(
        "2006", origin="LHR", date="2026-03-18", snapshot_sequence=None,
    )
    history = flight_routes.get_flight_status_history(
        "2006", origin="LHR", date="2026-03-18", limit=20,
    )

    assert dashboard["departureGate"] == "408"
    assert dashboard["codeshareInfo"] == []
    assert dashboard["freeTextRemarks"] == _GF2006_REMARKS
    assert status["codeshareInfo"] == []
    assert status["freeTextRemarks"] == _GF2006_REMARKS
    assert status.get("_id") is None
    assert status.get("_raw") is None
    assert len(history) == 1
    assert history[0]["codeshareInfo"] == []
    assert history[0]["freeTextRemarks"] == _GF2006_REMARKS


def test_dashboard_keeps_dedicated_partners_only(monkeypatch):
    extra = """
      <OperatingAirline>WY</OperatingAirline>
      <Codeshare>BA</Codeshare>
    """ + _free_text_blocks(["26/256", "NN"])
    stored = _convert(_itinerary("14", "BAH", "OPEN", extra), "14", "BAH")

    monkeypatch.setattr(flight_routes, "get_db", lambda: object())
    monkeypatch.setattr(flight_routes, "_get_cached", lambda key: None)
    monkeypatch.setattr(
        flight_routes,
        "_fetch_dashboard_data_parallel",
        lambda *args, **kwargs: {
            "flight_status": stored,
            "passenger_list": {
                "departureGate": "20",
                "passengers": [],
                "origin": "BAH",
                "departureDate": "2026-03-18",
                "totalPassengers": 0,
            },
            "reservations": None,
            "trip_reports": None,
            "schedule": None,
            "change_summary": {},
            "flight_meta": None,
        },
    )

    dashboard = flight_routes.get_flight_dashboard(
        "14", origin="BAH", date="2026-03-18", snapshot_sequence=None,
    )

    assert dashboard["departureGate"] == "20"
    assert sorted(dashboard["codeshareInfo"]) == ["BA", "WY"]
    assert len(dashboard["codeshareInfo"]) == 2
    assert dashboard["freeTextRemarks"] == ["26/256", "NN"]
    assert "26/256" not in dashboard["codeshareInfo"]
    assert "NN" not in dashboard["codeshareInfo"]
