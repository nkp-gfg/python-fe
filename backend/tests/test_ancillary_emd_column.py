"""Staff-facing ancillary columns on the live passenger detail sheet.

Sabre defines AEDetails/UsedEMD as the in-use flag (xsd:boolean), not the
electronic miscellaneous document number. EMDTicketNumber, when present, is
that document number. These tests drive the vendor baggage sample through
SabreClient XML parsing and convert_passenger_data, then render
PassengerDetailSheet and require:

- a column labeled "EMD in use" whose cell is "no" or "yes"
- a separate document column that shows EMDTicketNumber when the response
  includes it, and a placeholder when it does not

They fail while the sheet prints the raw UsedEMD text under a column
labeled EMD.
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SAMPLE = os.path.join(
    _REPO,
    "sabre_docs",
    "GetPassengerDataRQ_v4-0-4_Samples",
    "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_3.xml",
)
_RENDERER = os.path.join(os.path.dirname(__file__), "render_ancillary_emd_sheet.mjs")
_TICKET = "9990000000001"


def _sample_response_xml():
    text = open(_SAMPLE, encoding="utf-8").read()
    marker = "<!--Response-->"
    if marker not in text:
        raise AssertionError("vendor sample has no response marker")
    response = text.split(marker, 1)[1]
    end = response.rfind("</soap-env:Envelope>")
    if end < 0:
        raise AssertionError("vendor sample response envelope was not found")
    response = response[: end + len("</soap-env:Envelope>")]
    response = re.sub(
        r"<wsse:Security\b[^>]*>[\s\S]*?</wsse:Security>",
        "",
        response,
    )
    if "BinarySecurityToken" in response:
        raise AssertionError("sample security header was not removed before use")
    if "<ns2:UsedEMD>false</ns2:UsedEMD>" not in response:
        raise AssertionError("vendor baggage sample no longer contains UsedEMD false")
    return response


def _detail_from_response(xml):
    """Same parse and convert steps as get_passenger_detail after Sabre responds."""
    parsed = SabreClient._parse_xml(xml)
    # _extract_body does not use instance state; SabreClient() reads credentials.
    body = SabreClient._extract_body(None, parsed, "GetPassengerDataRS")
    detail = convert_passenger_data(body, "AZ", "317", "2016-03-17", "CDG")
    # FastAPI returns this dict as JSON. Round-trip so booleans stay booleans.
    return json.loads(json.dumps(detail))


def _render(detail, in_use, document):
    passenger = detail["passengers"][0]
    payload = {
        "detail": detail,
        "flightNumber": str(detail["flightNumber"]),
        "origin": detail["origin"],
        "date": detail["departureDate"],
        "pnr": passenger["pnr"],
        "inUse": in_use,
        "document": document,
    }
    proc = subprocess.run(
        ["node", _RENDERER],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=60,
        cwd=_REPO,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "passenger detail sheet did not show the ancillary columns correctly\n"
            + proc.stdout
            + proc.stderr
        )


def test_vendor_sample_unused_emd_shows_no_not_the_flag_text():
    detail = _detail_from_response(_sample_response_xml())
    _render(detail, in_use="no", document=None)


def test_used_emd_and_ticket_number_are_separate_columns():
    xml = _sample_response_xml()
    xml = xml.replace(
        "<ns2:UsedEMD>false</ns2:UsedEMD>",
        "<ns2:UsedEMD>true</ns2:UsedEMD>",
        1,
    )
    xml = xml.replace(
        "<ns2:PriceDetails>",
        f'<ns2:EMDTicketNumber couponNumber="1">{_TICKET}</ns2:EMDTicketNumber>'
        "<ns2:PriceDetails>",
        1,
    )
    if _TICKET not in xml or "<ns2:UsedEMD>true</ns2:UsedEMD>" not in xml:
        raise AssertionError("failed to build the in-use ticket response")
    detail = _detail_from_response(xml)
    _render(detail, in_use="yes", document=_TICKET)


if __name__ == "__main__":
    failures = 0
    for test in (
        test_vendor_sample_unused_emd_shows_no_not_the_flag_text,
        test_used_emd_and_ticket_number_are_separate_columns,
    ):
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}\n{exc}\n")
        else:
            print(f"PASS {test.__name__}")
    if failures:
        sys.exit(1)
    print("ALL TESTS PASS")
