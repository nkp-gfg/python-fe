"""Manifest Codes column must show every Sabre edit code.

The flight board builds that column from a GetPassengerList response. These
tests follow a captured response through the SOAP parser, the passenger-list
converter, and the passengers API, then render PassengerTable. They fail while
the column keeps only the first four codes behind a plus-count badge that
does not reveal the rest.

Run from the repository root, after installing backend requirements and the
frontend packages (including jsdom and tsx):

    python3 -m pytest backend/tests/test_manifest_edit_code_cap.py -q

or:

    python3 backend/tests/test_manifest_edit_code_cap.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.api.routes.passengers import get_passengers
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import diff_passenger_list
from backend.sabre.client import SabreClient

SAMPLE = REPO / "sample_data" / "sample_xmlfiles" / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
RENDERER = REPO / "frontend" / "tests" / "render-manifest-codes.tsx"
TSX = REPO / "frontend" / "node_modules" / ".bin" / "tsx"

# Codes the change tracker turns into document events. APP and AVML are edit
# codes on the same passengers; the tracker does not emit events for them.
DOCUMENT_CODES = {"DOCS", "DOCA", "DOCV", "DCVI"}
HIDDEN_ON_FIRST_PASSENGER = ["DOCV", "DOCS", "DOCA", "APP", "AVML"]


class _Collection:
    def __init__(self, document):
        self._document = document

    def find_one(self, query, *args, **kwargs):
        if self._document is None:
            return None
        for key, value in query.items():
            if self._document.get(key) != value:
                return None
        return self._document


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _passenger_list_from_sample():
    """Parse the captured GetPassengerListRS the way the Sabre client does."""
    xml_text = SAMPLE.read_text(encoding="utf-8")
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not use instance state; avoid building a live client.
    body = SabreClient._extract_body(object(), parsed, "GetPassengerListRS")
    document = convert_passenger_list(body, "GF", "2057", "2026-03-19", "BOM")
    # The converter keeps the SOAP body. Drop it before any assertion or file.
    document.pop("_raw", None)
    return document


def _api_passenger_list(document):
    """Run the passengers route against the document storage would insert."""
    database = _Database({
        "passenger_list": _Collection(document),
        "reservations": _Collection(None),
        "snapshots": _Collection(None),
    })
    original = sys.modules["backend.api.routes.passengers"].get_db
    sys.modules["backend.api.routes.passengers"].get_db = lambda: database
    try:
        # Calling the route function directly otherwise keeps FastAPI's Query
        # default, which is truthy and selects the historical snapshot branch.
        return get_passengers(
            "2057",
            origin="BOM",
            date="2026-03-19",
            snapshot_sequence=None,
        )
    finally:
        sys.modules["backend.api.routes.passengers"].get_db = original


def _render_manifest(api_document):
    encoded = json.dumps(api_document)
    if "BinarySecurityToken" in encoded or "SecurityToken" in encoded:
        raise AssertionError("passenger list response still contains a session token")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        handle.write(encoded)
        payload_path = handle.name
    try:
        completed = subprocess.run(
            [str(TSX), str(RENDERER), payload_path],
            cwd=REPO / "frontend",
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        os.unlink(payload_path)
    if completed.returncode != 0:
        raise AssertionError(
            "manifest render failed\n"
            f"stdout={completed.stdout[-2000:]}\n"
            f"stderr={completed.stderr[-4000:]}"
        )
    return json.loads(completed.stdout)


def test_manifest_codes_column_shows_every_edit_code():
    converted = _passenger_list_from_sample()
    passengers = converted["passengers"]
    assert passengers, "sample passenger list produced no passengers"

    first = passengers[0]
    assert (first["lastName"], first["firstName"], first["pnr"]) == ("BABOO", "MOHIT MR", "ESMMQK")
    assert first["editCodes"][:4] == ["M", "YS", "FF", "BLU"]
    assert first["editCodes"][4:][:12] == [
        "WB", "ET", "CM", "PRCH", "DOCV", "DOCS", "DOCA", "SM", "ETI", "AE", "APP", "AVML",
    ]
    for code in HIDDEN_ON_FIRST_PASSENGER:
        assert first["editCodes"].index(code) >= 4

    # The tracker reads the full edit-code list, including codes past the fourth.
    before_codes = [code for code in first["editCodes"] if code not in DOCUMENT_CODES]
    flight_info = {"flightNumber": "2057", "origin": "BOM", "departureDate": "2026-03-19"}
    changes = diff_passenger_list(
        {"snapshotId": "before", "data": {"passengers": [{**first, "editCodes": before_codes}]}},
        {"snapshotId": "after", "data": {"passengers": [first]}},
        flight_info,
    )
    document_events = sorted(
        change["newValue"]
        for change in changes
        if change["changeType"] == "DOCUMENT_ADDED"
    )
    assert document_events == ["DOCA", "DOCS", "DOCV"]
    assert not any(
        change["changeType"] == "DOCUMENT_ADDED" and change["newValue"] in {"APP", "AVML"}
        for change in changes
    )

    served = _api_passenger_list(converted)
    served.pop("_raw", None)
    by_key = {
        (passenger["pnr"], passenger["lastName"], passenger["firstName"]): passenger["editCodes"]
        for passenger in served["passengers"]
    }
    for passenger in passengers:
        key = (passenger["pnr"], passenger["lastName"], passenger["firstName"])
        assert by_key[key] == passenger["editCodes"]

    report = _render_manifest(served)
    if report["failureCount"]:
        baboo = report.get("baboo") or {}
        raise AssertionError(
            "Codes column does not show every edit code. "
            f"{report['failureCount']} of {report['renderedPassengers']} rows hide codes "
            "that are only reachable past the four-code cap. "
            f"BABOO visible={baboo.get('visible')} missing={baboo.get('missing')}. "
            f"Other rows: {report.get('sampleFailures')}"
        )


if __name__ == "__main__":
    try:
        test_manifest_codes_column_shows_every_edit_code()
    except AssertionError as exc:
        print(exc)
        sys.exit(1)
    print("PASS")
