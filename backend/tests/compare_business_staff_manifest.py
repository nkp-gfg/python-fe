"""Build comparison dashboards from Sabre SOAP the product already parses.

The comparison page reads ``analysis`` from the dashboard payload. This module
runs the same parse, convert, and ``_build_dashboard_payload`` steps the
ingestion and dashboard routes use, so a test can feed that payload to the
comparison column.
"""

from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.sabre.client import SabreClient


def _soap(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        f"{body}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _passenger(
    *,
    last_name: str,
    first_name: str,
    pnr: str,
    line: int,
    cabin: str,
    booking_class: str,
    passenger_type: str,
    revenue: bool,
    edit_codes: list[str] | None = None,
) -> str:
    indicators = ["CheckedIn"]
    indicators.append("Revenue" if revenue else "NonRevenue")
    indicator_xml = "".join(f"<Indicator>{item}</Indicator>" for item in indicators)
    codes = edit_codes or []
    edit_xml = ""
    if codes:
        edit_xml = "<EditCodeList>" + "".join(
            f"<EditCode>{code}</EditCode>" for code in codes
        ) + "</EditCodeList>"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>{first_name}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="{line}">{pnr}</PNRLocator>
          <PassengerID>PAX{line:04d}</PassengerID>
          <ThruIndicator>false</ThruIndicator>
          <LineNumber>{line}</LineNumber>
          <BookingClass>{booking_class}</BookingClass>
          <Cabin>{cabin}</Cabin>
          <Seat>{line}A</Seat>
          <Destination>DMM</Destination>
          <BoardingPassFlag>true</BoardingPassFlag>
          <PassengerType>{passenger_type}</PassengerType>
          <BagCount>0</BagCount>
          <Indicators>{indicator_xml}</Indicators>
          <CheckIn_Info>
            <CheckInStatus>true</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
          {edit_xml}
        </PassengerInfo>
    """


def _docs_passenger(last_name: str, first_name: str, gender: str) -> str:
    return f"""
      <Passenger>
        <LastName>{last_name}</LastName>
        <FirstName>{first_name}</FirstName>
        <SpecialRequests>
          <APISRequest>
            <DOCSEntry>
              <Gender>{gender}</Gender>
              <DocumentNationalityCountry>BH</DocumentNationalityCountry>
            </DOCSEntry>
          </APISRequest>
        </SpecialRequests>
      </Passenger>
    """


def _passenger_list_xml(passengers: list[str], flight_number: str) -> str:
    body = f"""
      <GetPassengerListRS>
        <ItineraryInfo>
          <Itinerary>
            <Airline>GF</Airline>
            <Flight>{flight_number}</Flight>
            <DepartureDate>2026-03-19</DepartureDate>
            <Origin>BOM</Origin>
            <Destination>DMM</Destination>
            <AircraftType>321</AircraftType>
          </Itinerary>
        </ItineraryInfo>
        <PassengerInfoList>
          {''.join(passengers)}
        </PassengerInfoList>
      </GetPassengerListRS>
    """
    return _soap(body)


def _reservation_xml(pnr: str, passengers: list[str]) -> str:
    body = f"""
      <Trip_SearchRS>
        <ReservationsList NumberResults="1">
          <Reservations>
            <Reservation Locator="{pnr}">
              <GetReservationRS>
                <Reservation>
                  <PassengerReservation>
                    <Passengers>
                      {''.join(passengers)}
                    </Passengers>
                  </PassengerReservation>
                </Reservation>
              </GetReservationRS>
            </Reservation>
          </Reservations>
        </ReservationsList>
      </Trip_SearchRS>
    """
    return _soap(body)


def _extract(xml_text: str, response_key: str) -> dict:
    parsed = SabreClient._parse_xml(xml_text)
    # _extract_body reads only the parsed envelope, not client credentials.
    return SabreClient._extract_body(None, parsed, response_key)


def build_dashboard(flight_number: str, manifest: list[dict], genders: list[dict]) -> dict:
    """Parse Sabre passenger-list and reservation XML into a dashboard payload."""
    pnr = "TST001"
    passenger_xml = [
        _passenger(
            last_name=row["last_name"],
            first_name=row["first_name"],
            pnr=pnr,
            line=index + 1,
            cabin=row["cabin"],
            booking_class=row["booking_class"],
            passenger_type=row["passenger_type"],
            revenue=row["revenue"],
            edit_codes=row.get("edit_codes"),
        )
        for index, row in enumerate(manifest)
    ]
    raw_passengers = _extract(
        _passenger_list_xml(passenger_xml, flight_number),
        "GetPassengerListRS",
    )
    passenger_doc = convert_passenger_list(
        raw_passengers, "GF", flight_number, "2026-03-19", "BOM",
    )
    raw_reservations = _extract(
        _reservation_xml(
            pnr,
            [
                _docs_passenger(row["last_name"], row["first_name"], row["gender"])
                for row in genders
            ],
        ),
        "Trip_SearchRS",
    )
    reservation_doc = convert_reservations(
        raw_reservations, "GF", flight_number, "BOM", "2026-03-19",
    )
    flight_status = {
        "status": "OPEN",
        "origin": "BOM",
        "departureDate": "2026-03-19",
        "aircraft": {"type": "321", "registration": "A9C-TEST"},
        "gate": "A1",
        "jumpSeat": {"cockpit": 0, "cabin": 0},
    }
    payload = _build_dashboard_payload(
        flight_status,
        passenger_doc,
        "BOM",
        "2026-03-19",
        {},
        reservation_doc,
    )
    payload.pop("schedule", None)
    return payload


WITH_BUSINESS_STAFF = [
    {
        "last_name": "YARD",
        "first_name": "OMAR",
        "cabin": "Y",
        "booking_class": "V",
        "passenger_type": "F",
        "revenue": True,
        "gender": "M",
    },
    {
        "last_name": "ECOSTAFF",
        "first_name": "LAYLA",
        "cabin": "Y",
        "booking_class": "Z",
        "passenger_type": "E",
        "revenue": False,
        "gender": "F",
    },
    {
        "last_name": "BIZREV",
        "first_name": "HASSAN",
        "cabin": "J",
        "booking_class": "D",
        "passenger_type": "F",
        "revenue": True,
        "gender": "M",
    },
    {
        "last_name": "BIZEMP",
        "first_name": "KARIM",
        "cabin": "J",
        "booking_class": "Z",
        "passenger_type": "E",
        "revenue": True,
        "gender": "M",
    },
    {
        "last_name": "BIZNR",
        "first_name": "NOOR",
        "cabin": "J",
        "booking_class": "Z",
        "passenger_type": "F",
        "revenue": False,
        "gender": "F",
    },
    {
        "last_name": "BIZEMPNR",
        "first_name": "SARA",
        "cabin": "J",
        "booking_class": "Z",
        "passenger_type": "E",
        "revenue": False,
        "gender": "F",
    },
]

WITHOUT_BUSINESS_STAFF = [
    {
        "last_name": "YARD",
        "first_name": "OMAR",
        "cabin": "Y",
        "booking_class": "V",
        "passenger_type": "F",
        "revenue": True,
        "gender": "M",
    },
    {
        "last_name": "ECOSTAFF",
        "first_name": "LAYLA",
        "cabin": "Y",
        "booking_class": "Z",
        "passenger_type": "E",
        "revenue": False,
        "gender": "F",
    },
    {
        "last_name": "BIZREV",
        "first_name": "HASSAN",
        "cabin": "J",
        "booking_class": "D",
        "passenger_type": "F",
        "revenue": True,
        "gender": "M",
    },
    {
        "last_name": "BIZREVF",
        "first_name": "MAHA",
        "cabin": "J",
        "booking_class": "D",
        "passenger_type": "F",
        "revenue": True,
        "gender": "F",
    },
]


def _genders(rows: list[dict]) -> list[dict]:
    return [
        {
            "last_name": row["last_name"],
            "first_name": row["first_name"],
            "gender": row["gender"],
        }
        for row in rows
    ]


def build_cases() -> dict:
    with_staff = build_dashboard("2057", WITH_BUSINESS_STAFF, _genders(WITH_BUSINESS_STAFF))
    without_staff = build_dashboard(
        "2006", WITHOUT_BUSINESS_STAFF, _genders(WITHOUT_BUSINESS_STAFF),
    )
    return {
        "withBusinessStaff": {
            "flightNumber": "2057",
            "origin": "BOM",
            "date": "2026-03-19",
            "dashboard": with_staff,
        },
        "withoutBusinessStaff": {
            "flightNumber": "2006",
            "origin": "BOM",
            "date": "2026-03-19",
            "dashboard": without_staff,
        },
    }


def assert_analyzer_counts(cases: dict) -> None:
    """The dashboard must already know about business staff before the page renders."""
    staffed = cases["withBusinessStaff"]["dashboard"]["analysis"]
    business = staffed["business"]
    economy = staffed["economy"]
    if business["staff"]["total"] != 3:
        raise SystemExit(
            f"expected 3 business staff, got {business['staff']['total']}"
        )
    if business["staff"]["male"] != 1 or business["staff"]["female"] != 2:
        raise SystemExit(f"unexpected business staff genders: {business['staff']}")
    if business["passengers"]["male"] != 1 or business["passengers"]["female"] != 0:
        raise SystemExit(f"unexpected business revenue boxes: {business['passengers']}")
    if business["total"] != business["passengers"]["total"] + business["staff"]["total"]:
        raise SystemExit("business heading total does not include staff")
    if economy["staff"]["total"] != 1 or economy["staff"]["female"] != 1:
        raise SystemExit(f"expected the economy staff row, got {economy['staff']}")
    accounted = (
        business["passengers"]["male"]
        + business["passengers"]["female"]
        + business["passengers"]["children"]
        + business["staff"]["total"]
    )
    if accounted != business["total"]:
        raise SystemExit("gendered revenue boxes plus staff do not equal the heading")

    clear = cases["withoutBusinessStaff"]["dashboard"]["analysis"]
    if clear["business"]["staff"]["total"] != 0:
        raise SystemExit("control flight should have no business staff")
    if clear["economy"]["staff"]["total"] != 1:
        raise SystemExit("control flight should still have economy staff")


def main() -> None:
    cases = build_cases()
    assert_analyzer_counts(cases)
    json.dump(cases, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
