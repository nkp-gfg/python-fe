"""Historical phase demographics must use the gender on file when each snapshot was stored.

A later Trip Search document correction is ingested through the same pipeline
as any other reservation fetch. The phase-journey API must keep the male and
female counts from the reservation that was current for that phase. These
tests fail while every phase is scored with the newest reservation.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.feeder import storage
from backend.feeder.converter import (
    convert_flight_status,
    convert_passenger_list,
    convert_reservations,
)
from backend.feeder.runner import _process_api_call
from backend.sabre.client import SabreClient

AIRLINE = "GF"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-11-20"
META = {
    "requestXml": "<Request/>",
    "httpStatus": 200,
    "durationMs": 1,
    "sessionToken": None,
    "conversationId": None,
}


def _soap(body_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header/>"
        "<soap-env:Body>"
        f"{body_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _parse_body(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _flight_info(flight_number):
    return {
        "airline": AIRLINE,
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
    }


def _ingest(flight_number, api_type, snapshot_type, raw, converter, converter_args):
    _process_api_call(
        api_type,
        snapshot_type,
        _flight_info(flight_number),
        raw,
        "<Response/>",
        META,
        converter,
        converter_args,
    )


def _flight_status_xml(boarding):
    if boarding:
        boarding_time = '<BoardingTime boardingIndicator="BDG">10:15AM</BoardingTime>'
    else:
        boarding_time = "<BoardingTime>09:00AM</BoardingTime>"
    xml = _soap(
        "<ns3:ACS_FlightDetailRS>"
        "<ItineraryResponseList><ItineraryInfoResponse>"
        "<FlightStatus>OPENCI</FlightStatus>"
        "<DepartureGate>A3</DepartureGate>"
        f"{boarding_time}"
        "</ItineraryInfoResponse></ItineraryResponseList>"
        "</ns3:ACS_FlightDetailRS>"
    )
    return _parse_body(xml, "ns3:ACS_FlightDetailRS")


def _passenger_xml(last_name, first_name, pnr, cabin, boarded, edit_code):
    board = "true" if boarded else "false"
    return (
        "<PassengerInfo>"
        "<Name_Details>"
        f"<LastName>{last_name}</LastName>"
        f"<FirstName>{first_name}</FirstName>"
        "</Name_Details>"
        f'<PNRLocator nameAssociationID="1">{pnr}</PNRLocator>'
        f"<PassengerID>{pnr}</PassengerID>"
        "<LineNumber>1</LineNumber>"
        f"<Cabin>{cabin}</Cabin>"
        "<PassengerType>F</PassengerType>"
        "<Indicators><Indicator>Revenue</Indicator></Indicators>"
        "<CheckIn_Info><CheckInStatus>true</CheckInStatus></CheckIn_Info>"
        f"<Boarding_Info><BoardStatus>{board}</BoardStatus></Boarding_Info>"
        f"<EditCodeList><EditCode>{edit_code}</EditCode></EditCodeList>"
        "</PassengerInfo>"
    )


def _passenger_list_xml(passengers, boarded):
    body = "".join(
        _passenger_xml(
            pax["last_name"],
            pax["first_name"],
            pax["pnr"],
            pax["cabin"],
            boarded,
            pax["edit_code"],
        )
        for pax in passengers
    )
    xml = _soap(
        "<GetPassengerListRS>"
        "<ItineraryInfo><Itinerary><Destination>LHR</Destination></Itinerary>"
        "<DepartureGate>A3</DepartureGate></ItineraryInfo>"
        f"<PassengerInfoList>{body}</PassengerInfoList>"
        "</GetPassengerListRS>"
    )
    return _parse_body(xml, "GetPassengerListRS")


def _docs_xml(gender):
    if not gender:
        return ""
    return (
        "<stl19:SpecialRequests><stl19:APISRequest><stl19:DOCSEntry>"
        f"<stl19:Gender>{gender}</stl19:Gender>"
        "<stl19:DateOfBirth>1990-01-01</stl19:DateOfBirth>"
        "<stl19:DocumentNationalityCountry>BH</stl19:DocumentNationalityCountry>"
        "</stl19:DOCSEntry></stl19:APISRequest></stl19:SpecialRequests>"
    )


def _reservation_xml(passengers, genders):
    blocks = []
    for pax in passengers:
        gender = genders.get(pax["pnr"], "")
        blocks.append(
            f'<Reservation Locator="{pax["pnr"]}">'
            "<stl19:GetReservationRS><stl19:Reservation>"
            "<stl19:BookingDetails><stl19:Header>TEST</stl19:Header></stl19:BookingDetails>"
            "<stl19:PassengerReservation><stl19:Passengers>"
            '<stl19:Passenger nameId="01.01">'
            f"<stl19:LastName>{pax['last_name']}</stl19:LastName>"
            f"<stl19:FirstName>{pax['first_name']}</stl19:FirstName>"
            f"{_docs_xml(gender)}"
            "</stl19:Passenger>"
            "</stl19:Passengers></stl19:PassengerReservation>"
            "</stl19:Reservation></stl19:GetReservationRS>"
            "</Reservation>"
        )
    xml = _soap(
        '<Trip_SearchRS><ReservationsList NumberResults="1">'
        f"<Reservations>{''.join(blocks)}</Reservations>"
        "</ReservationsList></Trip_SearchRS>"
    )
    return _parse_body(xml, "Trip_SearchRS")


def _ingest_flight_status(flight_number, boarding):
    raw = _flight_status_xml(boarding)
    _ingest(
        flight_number,
        "FlightStatus",
        "flight_status",
        raw,
        convert_flight_status,
        (raw, AIRLINE, flight_number, ORIGIN, DEPARTURE_DATE),
    )


def _ingest_passenger_list(flight_number, passengers, boarded):
    raw = _passenger_list_xml(passengers, boarded)
    _ingest(
        flight_number,
        "PassengerList",
        "passenger_list",
        raw,
        convert_passenger_list,
        (raw, AIRLINE, flight_number, DEPARTURE_DATE, ORIGIN),
    )


def _ingest_reservations(flight_number, passengers, genders):
    raw = _reservation_xml(passengers, genders)
    _ingest(
        flight_number,
        "Reservations",
        "reservations",
        raw,
        convert_reservations,
        (raw, AIRLINE, flight_number, ORIGIN, DEPARTURE_DATE),
    )


def _reset_db_clients():
    """Drop clients closed by the API lifespan so the next call reconnects."""
    from backend.api import database as api_database

    api_database.close_db()
    storage._db = None
    storage._client = None
    storage._owns_connection = False


def _purge(flight_number):
    _reset_db_clients()
    db = storage.get_db()
    for name in (
        "snapshots",
        "reservations",
        "passenger_list",
        "flight_status",
        "flights",
        "changes",
    ):
        db[name].delete_many({"flightNumber": flight_number})
    db["sabre_requests"].delete_many({"flight.flightNumber": flight_number})


def _gender_of(reservation_doc, pnr):
    for res in reservation_doc.get("reservations", []):
        if res.get("pnr") != pnr:
            continue
        for pax in res.get("passengers", []):
            return pax.get("gender", "")
    return None


def _phase_journey(flight_number):
    with TestClient(app) as client:
        response = client.get(
            f"/flights/{flight_number}/phase-journey",
            params={"origin": ORIGIN, "date": DEPARTURE_DATE},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    _reset_db_clients()
    return {phase["phase"]: phase["summary"] for phase in body["phases"]}


def _assert_split(summary, male, female, econ_male, econ_female, biz_male, biz_female):
    assert summary["totalMale"] == male, summary
    assert summary["totalFemale"] == female, summary
    economy = summary["economy"]["passengers"]
    business = summary["business"]["passengers"]
    assert economy["male"] == econ_male, summary
    assert economy["female"] == econ_female, summary
    assert business["male"] == biz_male, summary
    assert business["female"] == biz_female, summary


def test_later_document_correction_does_not_rewrite_earlier_phase_genders():
    """Check-in and boarding keep the genders stored before a later correction.

    Every passenger list row carries edit code TKT. Those codes are meal and
    fare markers, so a count that follows them stays at zero.
    """
    flight_number = "9918"
    passengers = [
        {"last_name": "SMITH", "first_name": "JOHN", "pnr": "AAA111", "cabin": "Y", "edit_code": "TKT"},
        {"last_name": "NOOR", "first_name": "SARA", "pnr": "BBB222", "cabin": "Y", "edit_code": "TKT"},
        {"last_name": "KHAN", "first_name": "AMINA", "pnr": "CCC333", "cabin": "Y", "edit_code": "TKT"},
        {"last_name": "ALI", "first_name": "HASSAN", "pnr": "DDD444", "cabin": "J", "edit_code": "TKT"},
    ]
    on_file_at_check_in = {"AAA111": "M", "BBB222": "M", "CCC333": "F", "DDD444": "F"}
    on_file_at_boarding = {"AAA111": "M", "BBB222": "FI", "CCC333": "F", "DDD444": "F"}
    corrected_later = {"AAA111": "F", "BBB222": "M", "CCC333": "M", "DDD444": "M"}

    _purge(flight_number)
    try:
        _ingest_reservations(flight_number, passengers, on_file_at_check_in)
        _ingest_flight_status(flight_number, boarding=False)
        _ingest_passenger_list(flight_number, passengers, boarded=False)

        time.sleep(1.1)
        _ingest_reservations(flight_number, passengers, on_file_at_boarding)
        _ingest_flight_status(flight_number, boarding=True)
        _ingest_passenger_list(flight_number, passengers, boarded=True)

        time.sleep(1.1)
        _ingest_reservations(flight_number, passengers, corrected_later)

        latest = storage.get_db()["reservations"].find_one(
            {
                "flightNumber": flight_number,
                "departureAirport": ORIGIN,
                "departureDate": DEPARTURE_DATE,
            },
            sort=[("fetchedAt", -1)],
        )
        assert _gender_of(latest, "AAA111") == "F"
        assert _gender_of(latest, "BBB222") == "M"

        phases = _phase_journey(flight_number)
        # Check-in documents: SMITH M, NOOR M, KHAN F, ALI F.
        _assert_split(phases["CHECK_IN"], 2, 2, 2, 1, 0, 1)
        # Boarding documents: NOOR's document is FI, which counts as female.
        _assert_split(phases["BOARDING"], 1, 3, 1, 2, 0, 1)
    finally:
        _purge(flight_number)


def test_gender_added_after_check_in_counts_only_on_later_phase():
    """A document gender added after check-in does not fill in the check-in phase."""
    flight_number = "9919"
    passengers = [
        {"last_name": "REED", "first_name": "ALEX", "pnr": "EEE555", "cabin": "Y", "edit_code": "TKT"},
    ]

    _purge(flight_number)
    try:
        _ingest_reservations(flight_number, passengers, {"EEE555": ""})
        _ingest_flight_status(flight_number, boarding=False)
        _ingest_passenger_list(flight_number, passengers, boarded=False)

        time.sleep(1.1)
        _ingest_reservations(flight_number, passengers, {"EEE555": "F"})
        _ingest_flight_status(flight_number, boarding=True)
        _ingest_passenger_list(flight_number, passengers, boarded=True)

        time.sleep(1.1)
        _ingest_reservations(flight_number, passengers, {"EEE555": "M"})

        latest = storage.get_db()["reservations"].find_one(
            {
                "flightNumber": flight_number,
                "departureAirport": ORIGIN,
                "departureDate": DEPARTURE_DATE,
            },
            sort=[("fetchedAt", -1)],
        )
        assert _gender_of(latest, "EEE555") == "M"

        phases = _phase_journey(flight_number)
        _assert_split(phases["CHECK_IN"], 0, 0, 0, 0, 0, 0)
        _assert_split(phases["BOARDING"], 0, 1, 0, 1, 0, 0)
    finally:
        _purge(flight_number)
