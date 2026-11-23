"""Build the live passenger-detail payload the board renders.

The SOAP body follows the vendor GetPassengerData sample: CheckInNumber sits
on each PassengerItinerary, and two travelers on one flight receive 1 and 2.
Parsing uses SabreClient._parse_xml and _extract_body, then the passenger
detail route, which is what the sheet calls. No session token is included.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.sabre.client import SabreClient  # noqa: E402
import backend.api.routes.passengers as passengers  # noqa: E402


def _itinerary_xml(segment: dict) -> str:
    check_in = ""
    if segment.get("check_in_number") is not None:
        check_in = f"<CheckInNumber>{segment['check_in_number']}</CheckInNumber>"
    return f"""
            <PassengerItinerary>
              <Airline>{segment['airline']}</Airline>
              <Flight>{segment['flight']}</Flight>
              <BookingClass>{segment['booking_class']}</BookingClass>
              <Origin>{segment['origin']}</Origin>
              <Destination>{segment['destination']}</Destination>
              <DepartureDate>{segment['departure_date']}</DepartureDate>
              <AircraftType>{segment['aircraft_type']}</AircraftType>
              <Cabin>{segment['cabin']}</Cabin>
              <Seat>{segment['seat']}</Seat>
              <BagCount>{segment['bag_count']}</BagCount>
              <EditCodeList>
                <ns2:EditCode>M</ns2:EditCode>
                <ns2:EditCode>DOCS</ns2:EditCode>
              </EditCodeList>
              {check_in}
            </PassengerItinerary>"""


def _passenger_xml(passenger: dict) -> str:
    segments = "\n".join(_itinerary_xml(segment) for segment in passenger["segments"])
    return f"""
        <PassengerDataResponse>
          <LineNumber>{passenger['line_number']}</LineNumber>
          <LastName>{passenger['last_name']}</LastName>
          <FirstName>{passenger['first_name']}</FirstName>
          <PassengerID>{passenger['passenger_id']}</PassengerID>
          <PNRLocator nameAssociationID="{passenger['name_association_id']}">{passenger['pnr']}</PNRLocator>
          <PassengerItineraryList>
            {segments}
          </PassengerItineraryList>
        </PassengerDataResponse>"""


def soap_response(passengers_payload: list[dict]) -> str:
    """SOAP envelope shaped like the vendor sample response, without a token."""
    body = "\n".join(_passenger_xml(passenger) for passenger in passengers_payload)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        {body}
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def parse_passenger_data_body(xml_text: str) -> dict:
    """Run the same parse and body extraction as SabreClient.get_passenger_data."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "GetPassengerDataRS")


class _PassengerList:
    def __init__(self, document: dict):
        self._document = document

    def find_one(self, query, sort=None):
        return self._document


class _Database:
    def __init__(self, document: dict):
        self._document = document

    def __getitem__(self, name):
        if name != "passenger_list":
            raise KeyError(name)
        return _PassengerList(self._document)


class _SabreClient:
    def __init__(self, body: dict, captured: dict):
        self._body = body
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_passenger_data(self, **kwargs):
        self._captured.update(kwargs)
        return self._body, "", {}


def detail_response(passengers_payload: list[dict], stored_passenger: dict) -> dict:
    """Return the JSON the detail route sends for this Sabre body.

    stored_passenger is the manifest row the route reads the last name from.
    SabreClient is replaced so the test does not open a session or the network.
    The parsed body is still the one get_passenger_data would return.
    """
    xml_text = soap_response(passengers_payload)
    body = parse_passenger_data_body(xml_text)
    captured: dict = {}
    document = {
        "flightNumber": "2071",
        "origin": "LIN",
        "departureDate": "2016-03-16",
        "passengers": [stored_passenger],
    }

    original_get_db = passengers.get_db
    original_client = passengers.SabreClient
    passengers.get_db = lambda: _Database(document)
    passengers.SabreClient = lambda: _SabreClient(body, captured)
    try:
        result = passengers.get_passenger_detail(
            "2071",
            stored_passenger["pnr"],
            origin="LIN",
            date="2016-03-16",
            airline="AZ",
        )
    finally:
        passengers.get_db = original_get_db
        passengers.SabreClient = original_client

    return {
        "response": json.loads(json.dumps(result)),
        "sabreRequest": captured,
    }


JOSE = {
    "line_number": 9,
    "last_name": "JOSE",
    "first_name": "P",
    "passenger_id": "B79C9CA60001",
    "name_association_id": "1",
    "pnr": "GFLARH",
    "segments": [
        {
            "airline": "AZ",
            "flight": "2071",
            "booking_class": "Y",
            "origin": "LIN",
            "destination": "FCO",
            "departure_date": "2016-03-16",
            "aircraft_type": "E90",
            "cabin": "Y",
            "seat": "12A",
            "bag_count": "3",
            "check_in_number": 1,
        },
        {
            "airline": "AZ",
            "flight": "2072",
            "booking_class": "Y",
            "origin": "FCO",
            "destination": "JFK",
            "departure_date": "2016-03-17",
            "aircraft_type": "E90",
            "cabin": "Y",
            "seat": "14C",
            "bag_count": "0",
            "check_in_number": 15,
        },
        {
            "airline": "AZ",
            "flight": "2073",
            "booking_class": "Y",
            "origin": "JFK",
            "destination": "BOS",
            "departure_date": "2016-03-18",
            "aircraft_type": "E90",
            "cabin": "Y",
            "seat": "1A",
            "bag_count": "0",
        },
    ],
}

RICHARDS = {
    "line_number": 2,
    "last_name": "RICHARDS",
    "first_name": "V",
    "passenger_id": "B79C9CA60002",
    "name_association_id": "2",
    "pnr": "GFLARH",
    "segments": [
        {
            "airline": "AZ",
            "flight": "2071",
            "booking_class": "Y",
            "origin": "LIN",
            "destination": "FCO",
            "departure_date": "2016-03-16",
            "aircraft_type": "E90",
            "cabin": "Y",
            "seat": "12B",
            "bag_count": "0",
            "check_in_number": 2,
        },
    ],
}


def live_details() -> dict:
    """Detail responses for the two travelers in the vendor sample.

    The shared response lists JOSE first and RICHARDS second, which is the
    order in the sample. The sheet renders passengers[0], so RICHARDS is also
    returned from a lookup whose Sabre body lists him first.
    """
    stored_jose = {
        "pnr": "GFLARH",
        "lastName": "JOSE",
        "firstName": "P",
    }
    stored_richards = {
        "pnr": "GFLARH",
        "lastName": "RICHARDS",
        "firstName": "V",
    }
    return {
        "bothTravelers": detail_response([JOSE, RICHARDS], stored_jose),
        "richardsOnly": detail_response([RICHARDS], stored_richards),
    }


def main() -> None:
    destination = Path(sys.argv[1])
    destination.write_text(json.dumps(live_details()))


if __name__ == "__main__":
    main()
