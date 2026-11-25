"""Build flightStatus.schedule the way the dashboard serves it.

Parses an ACS_FlightDetailRS envelope with SabreClient, normalizes it with
convert_flight_status, strips the stored document the way the flight route
does, and returns the schedule object _build_dashboard_payload puts on
flightStatus. Reads a JSON object of {case: {xml element: text}} from stdin
and writes {case: schedule} to stdout.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.routes.flights import _build_dashboard_payload, _strip_id
from backend.feeder.converter import convert_flight_status
from backend.sabre.client import SabreClient


def envelope(fields: dict) -> str:
    inner = "\n".join(f"<{key}>{value}</{key}>" for key, value in fields.items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          {inner}
        </ItineraryInfoResponse>
      </ItineraryResponseList>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def schedule_for(fields: dict) -> dict:
    parsed = SabreClient._parse_xml(envelope(fields))
    data = SabreClient._extract_body(object(), parsed, "ns3:ACS_FlightDetailRS")
    fs = convert_flight_status(
        data,
        fields.get("Airline", "GF"),
        fields.get("Flight", ""),
        fields.get("Origin", ""),
        fields.get("DepartureDate", ""),
    )
    fs = _strip_id(fs)
    payload = _build_dashboard_payload(
        fs,
        None,
        fields.get("Origin", ""),
        fields.get("DepartureDate", ""),
        {},
    )
    return payload["flightStatus"]["schedule"]


def main() -> None:
    request = json.load(sys.stdin)
    json.dump({name: schedule_for(fields) for name, fields in request.items()}, sys.stdout)


if __name__ == "__main__":
    main()
