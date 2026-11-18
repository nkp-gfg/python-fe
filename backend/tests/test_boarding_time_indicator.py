"""BoardingTime with a boardingIndicator must stay a clock string.

ACS Flight Detail allows BoardingTime to carry a boardingIndicator attribute.
xmltodict then returns an object (attribute plus text). These tests follow the
ingestion path that stores that value, diffs it, and describes it, plus the
History tab formatter that reads it.

They fail while boarding.time is that object. They pass once the element text
is stored as boarding.time, the attribute is stored as boarding.indicator, a
plain string is left unchanged, and the activity text is that clock string.
"""

import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.encoders import jsonable_encoder

from backend.api.routes.changes import _describe_event
from backend.api.routes.flights import _strip_id
from backend.feeder.converter import convert_flight_status
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient

INDICATOR_SAMPLES = (
    ROOT / "sample_data/sample_xmlfiles/ACS_FlightDetailRS_GF2057_BOM_2026-03-19.xml",
    ROOT / "sample_data/sample_xmlfiles/ACS_FlightDetailRS_GF2274_DMM_2026-03-19.xml",
)
PLAIN_SAMPLE = (
    ROOT / "sample_data/sample_xmlfiles/ACS_FlightDetailRS_GF2006_LHR_2026-03-19.xml"
)

_TOKEN_RE = re.compile(
    r"(<wsse:BinarySecurityToken[^>]*>)(.*?)(</wsse:BinarySecurityToken>)",
    re.IGNORECASE | re.DOTALL,
)
_BOARDING_RE = re.compile(
    r"<BoardingTime\b([^>]*)>(.*?)</BoardingTime>|<BoardingTime\b([^>]*)/>",
    re.DOTALL,
)
_ATTR_RE = re.compile(r"""boardingIndicator\s*=\s*["']([^"']*)["']""")


def _redact_session_token(xml: str) -> str:
    return _TOKEN_RE.sub(r"\1REDACTED\3", xml)


def _boarding_from_xml(xml: str) -> tuple[str, str]:
    """Return (clock text, boardingIndicator) from the Flight Detail element."""
    match = _BOARDING_RE.search(xml)
    assert match, "Flight Detail response has no BoardingTime element"
    if match.group(0).endswith("/>"):
        attrs = match.group(3) or ""
        text = ""
    else:
        attrs = match.group(1) or ""
        text = (match.group(2) or "").strip()
    indicator = ""
    attr = _ATTR_RE.search(attrs)
    if attr:
        indicator = attr.group(1)
    return text, indicator


def _flight_detail_body(xml: str) -> dict:
    """Parse a SOAP response the way SabreClient.get_flight_status does."""
    parsed = SabreClient._parse_xml(xml)
    client = object.__new__(SabreClient)
    return SabreClient._extract_body(client, parsed, "ns3:ACS_FlightDetailRS")


def _convert_response(xml: str) -> dict:
    body = _flight_detail_body(xml)
    return convert_flight_status(body, "GF", "2057", "BOM", "2026-03-19")


def _history_document(doc: dict) -> dict:
    """JSON document returned by GET /flights/{flight}/status/history."""
    return jsonable_encoder(_strip_id(deepcopy(doc)))


def _fmt_time(value) -> str:
    """Call status-history.tsx fmtTime on a JSON-encoded history value."""
    script = Path(__file__).with_name("fmt_boarding_time.mjs")
    completed = subprocess.run(
        ["node", str(script), json.dumps(value)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _envelope(boarding_element: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>2057</Flight>
          <Origin>BOM</Origin>
          <DepartureDate>2026-03-19</DepartureDate>
          <FlightStatus>OPENCI</FlightStatus>
          {boarding_element}
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _boarding_changes(before_time: str, after_doc: dict) -> list[dict]:
    before_doc = deepcopy(after_doc)
    before_doc["boarding"] = {
        "time": before_time,
        "indicator": after_doc["boarding"]["indicator"],
    }
    flight_info = {
        "flightNumber": after_doc["flightNumber"],
        "origin": after_doc["origin"],
        "departureDate": after_doc["departureDate"],
    }
    changes = detect_changes(
        {"snapshotId": "before", "data": before_doc},
        {"snapshotId": "after", "data": after_doc},
        "flight_status",
        flight_info,
    )
    return [change for change in changes if change["changeType"] == "BOARDING_TIME_CHANGE"]


def test_indicator_samples_store_clock_text_and_indicator():
    for path in INDICATOR_SAMPLES:
        xml = _redact_session_token(path.read_text())
        clock, indicator = _boarding_from_xml(xml)
        assert clock
        assert indicator == "BDG"

        boarding = _convert_response(xml)["boarding"]
        assert boarding["time"] == clock
        assert boarding["indicator"] == indicator
        assert isinstance(boarding["time"], str)


def test_plain_boarding_time_stays_a_string():
    xml = _redact_session_token(PLAIN_SAMPLE.read_text())
    clock, indicator = _boarding_from_xml(xml)
    assert clock == "08:50PM"
    assert indicator == ""

    boarding = _convert_response(xml)["boarding"]
    assert boarding["time"] == "08:50PM"
    assert boarding["indicator"] == ""

    history = _history_document(_convert_response(xml))
    assert _fmt_time(history["boarding"]["time"]) == _fmt_time("08:50PM")


def test_attribute_only_boarding_time_stores_empty_clock():
    xml = _envelope('<BoardingTime boardingIndicator="BDG"/>')
    boarding = _convert_response(xml)["boarding"]
    assert boarding["time"] == ""
    assert boarding["indicator"] == "BDG"

    history = _history_document(_convert_response(xml))
    assert _fmt_time(history["boarding"]["time"]) == _fmt_time("")


def test_same_clock_with_new_indicator_is_not_a_time_change():
    xml = _redact_session_token(INDICATOR_SAMPLES[0].read_text())
    clock, _indicator = _boarding_from_xml(xml)
    after_doc = _convert_response(xml)

    assert _boarding_changes(clock, after_doc) == []


def test_changed_clock_records_clock_text_not_parser_object():
    xml = _redact_session_token(INDICATOR_SAMPLES[0].read_text())
    clock, _indicator = _boarding_from_xml(xml)
    after_doc = _convert_response(xml)

    changes = _boarding_changes("04:10PM", after_doc)
    assert len(changes) == 1
    change = changes[0]
    assert change["field"] == "boarding.time"
    assert change["oldValue"] == "04:10PM"
    assert change["newValue"] == clock
    assert "#text" not in change["newValue"]
    assert "boardingIndicator" not in change["newValue"]


def test_activity_and_timeline_description_uses_clock_text():
    xml = _redact_session_token(INDICATOR_SAMPLES[1].read_text())
    clock, _indicator = _boarding_from_xml(xml)
    changes = _boarding_changes("02:00PM", _convert_response(xml))
    assert len(changes) == 1

    # GET /activity/feed and GET /flights/{flight}/timeline both use this text.
    assert _describe_event(changes[0]) == f"Boarding time updated: {clock}"


def test_history_formatter_renders_ingested_boarding_clock():
    for path in INDICATOR_SAMPLES:
        xml = _redact_session_token(path.read_text())
        clock, _indicator = _boarding_from_xml(xml)
        history = _history_document(_convert_response(xml))
        stored = history["boarding"]["time"]

        # Status evolution calls fmtTime whenever boarding.time is truthy.
        assert stored
        assert _fmt_time(stored) == _fmt_time(clock)
        assert stored == clock
