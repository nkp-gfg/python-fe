"""Type column on the flight-board reservations list.

Trip Search nameType is I for an infant and S for a standard passenger.
Check-in PassengerType S is a standby passenger. The reservations table
must not print those Trip Search letters as the traveler type, and it must
not invent ADT when nameType was omitted.

These assertions fail until that mapping is in place.
"""

import asyncio
import copy
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.routing import serialize_response

from backend.api.routes import reservations as reservations_route
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient

REPO = os.path.join(os.path.dirname(__file__), "..", "..")
RENDERER = os.path.join(os.path.dirname(__file__), "render_reservation_type_column.cjs")
TRIP_SEARCH = os.path.join(
    REPO,
    "sample_data/sample_xmlfiles/Trip_SearchRS_GF2057_BOM_2026-03-19.xml",
)
PASSENGER_LIST = os.path.join(
    REPO,
    "sample_data/sample_xmlfiles/GetPassengerListRS_GF2056_DMM_2026-03-16.xml",
)

# Same SOAP shape as Trip_SearchRS, with the nameType attribute left off.
OMITTED_NAME_TYPE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="1">
        <Reservations>
          <Reservation Locator="BLANK1">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:CreationTimestamp>2026-03-19T01:00:00</stl19:CreationTimestamp>
                  <stl19:UpdateTimestamp>2026-03-19T01:00:00</stl19:UpdateTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="01.01">
                      <stl19:LastName>NOCODE</stl19:LastName>
                      <stl19:FirstName>PAX</stl19:FirstName>
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


def _strip_security_header(xml_text):
    """Drop the SOAP security header before the parser sees the file.

    The sample responses carry a session token. The name type lives in the
    body, which this leaves intact. The returned text must not contain the token.
    """
    cleaned, removed = re.subn(
        r"<wsse:Security\b[^>]*>.*?</wsse:Security>",
        "",
        xml_text,
        count=1,
        flags=re.DOTALL,
    )
    if removed != 1 or "BinarySecurityToken" in cleaned or "IceSess" in cleaned:
        raise AssertionError("sample SOAP security header was not removed")
    return cleaned


def _parse_response_body(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body does not use instance state; avoid SabreClient(), which
    # reads credential environment variables.
    return SabreClient._extract_body(object(), parsed, response_key)


def _list_route():
    for route in reservations_route.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.endswith("/reservations") and "GET" in methods:
            return route
    raise AssertionError("GET reservations route is not registered")


def _api_json(stored_doc, snapshot_sequence):
    """Run get_reservations and FastAPI's response_model serialization.

    snapshot_sequence None is the current reservations document.
    A positive sequence loads snapshot.data through get_snapshot_data_as_of.
    Callers pass None explicitly: the FastAPI Query default is not None when
    the route function is called directly.
    """

    class _Reservations:
        def find_one(self, query, sort=None):
            doc = stored_doc
            if query.get("flightNumber") not in (None, doc.get("flightNumber")):
                return None
            if "departureAirport" in query and query["departureAirport"] != doc.get(
                "departureAirport"
            ):
                return None
            if "departureDate" in query and query["departureDate"] != doc.get(
                "departureDate"
            ):
                return None
            return copy.copy(doc)

    class _Snapshots:
        def find_one(self, query, sort=None):
            if query.get("snapshotType") != "reservations":
                return None
            if query.get("flightNumber") != stored_doc.get("flightNumber"):
                return None
            if query.get("origin") not in (None, stored_doc.get("departureAirport")):
                return None
            if query.get("departureDate") not in (None, stored_doc.get("departureDate")):
                return None
            return {
                "data": copy.copy(stored_doc),
                "sequenceNumber": 1,
                "capturedAt": stored_doc.get("fetchedAt", ""),
            }

    class _Database:
        def __getitem__(self, name):
            if name == "snapshots":
                return _Snapshots()
            return _Reservations()

    reservations_route.get_db = lambda: _Database()
    raw = reservations_route.get_reservations(
        "2057",
        origin="BOM",
        date="2026-03-19",
        snapshot_sequence=snapshot_sequence,
    )
    route = _list_route()
    serialized = asyncio.run(
        serialize_response(
            field=route.response_field,
            response_content=raw,
            is_coroutine=True,
        )
    )
    if hasattr(serialized, "model_dump"):
        serialized = serialized.model_dump()
    return serialized


def _reservation_rows(response, snapshot_sequence=None):
    proc = subprocess.run(
        ["node", RENDERER],
        input=json.dumps(
            {
                "flightNumber": "2057",
                "origin": "BOM",
                "date": "2026-03-19",
                "snapshotSequence": snapshot_sequence,
                "expand": ["FXXTVG", "BLANK1"],
                "response": response,
            }
        ),
        text=True,
        capture_output=True,
        check=False,
        cwd=REPO,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "ReservationView render failed\n" + proc.stderr[-4000:]
        )
    return json.loads(proc.stdout)["rows"]


def _row(rows, pnr, first_name_prefix):
    matches = [
        row
        for row in rows
        if row["pnr"] == pnr and first_name_prefix in row["name"]
    ]
    if len(matches) != 1:
        shown = [(row["pnr"], row["name"], row["type"]) for row in rows]
        raise AssertionError(f"expected one {pnr} {first_name_prefix} row, saw {shown}")
    return matches[0]


def test_reservation_type_column_distinguishes_infant_standard_and_omitted():
    trip_xml = _strip_security_header(
        open(TRIP_SEARCH, encoding="utf-8").read()
    )
    trip_body = _parse_response_body(trip_xml, "Trip_SearchRS")
    stored = convert_reservations(trip_body, "GF", "2057", "BOM", "2026-03-19")
    infant_booking = next(
        item for item in stored["reservations"] if item["pnr"] == "FXXTVG"
    )

    omitted_body = _parse_response_body(OMITTED_NAME_TYPE_XML, "Trip_SearchRS")
    omitted_doc = convert_reservations(
        omitted_body, "GF", "2057", "BOM", "2026-03-19"
    )
    omitted_booking = omitted_doc["reservations"][0]

    stored = copy.copy(stored)
    stored["reservations"] = [infant_booking, omitted_booking]
    stored["totalResults"] = 2

    checkin_xml = _strip_security_header(
        open(PASSENGER_LIST, encoding="utf-8").read()
    )
    checkin_body = _parse_response_body(checkin_xml, "GetPassengerListRS")
    checkin = convert_passenger_list(
        checkin_body, "GF", "2056", "2026-03-16", "DMM"
    )
    standby = [
        passenger
        for passenger in checkin["passengers"]
        if passenger["lastName"] == "ALMEIDA"
        and passenger["firstName"].startswith("FELIX")
    ]
    assert len(standby) == 1
    assert standby[0]["passengerType"] == "S"
    assert standby[0]["isStandby"] is True

    # Current document and the as-of snapshot view share the Type column.
    problems = []
    for snapshot_sequence in (None, 1):
        response = _api_json(stored, snapshot_sequence)
        rows = _reservation_rows(response, snapshot_sequence)
        infant = _row(rows, "FXXTVG", "HAMNA")
        standard = _row(rows, "FXXTVG", "FATIMA")
        omitted = _row(rows, "BLANK1", "PAX")
        view = "current" if snapshot_sequence is None else f"snapshot {snapshot_sequence}"
        if infant["type"] != "Infant":
            problems.append(f"{view} infant type is {infant['type']!r}, expected 'Infant'")
        if standard["type"] != "Standard adult" or standard["type"] in {"S", "Standby"}:
            problems.append(
                f"{view} standard adult type is {standard['type']!r}, expected 'Standard adult'"
            )
        if omitted["type"] not in {"", "Unknown"} or omitted["type"] == "ADT":
            problems.append(
                f"{view} omitted name type is {omitted['type']!r}, expected '' or 'Unknown'"
            )
    assert not problems, problems


if __name__ == "__main__":
    test_reservation_type_column_distinguishes_infant_standard_and_omitted()
    print("PASS")
