"""Shared-surname travelers must keep their own reservation attributes.

Ingestion stores every traveler on a booking as a separate reservation
passenger. The passenger-list handler then copies nationality, meal,
wheelchair, emergency-contact, and loyalty fields onto manifest rows.
Those fields belong to one traveler. A second traveler who only shares
the record locator and surname must not receive them.

These tests fail while that copy is keyed only by record locator and
surname.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["MONGODB_URI"] = os.environ.get(
    "MONGODB_URI", "mongodb://127.0.0.1:27017")

from fastapi.testclient import TestClient

from backend.api.database import get_db as get_api_db
from backend.api.main import app
from backend.api.routes.flights import _dashboard_cache
from backend.feeder.converter import convert_passenger_list, convert_reservations
from backend.feeder import storage
from backend.sabre.client import SabreClient

_client = TestClient(app)

FLIGHT = "9001"
GENDER_FLIGHT = "9002"
ORIGIN = "BAH"
DATE = "2026-04-02"
AIRLINE = "GF"


def _parse_body(xml_text, response_key):
    """Run the Sabre client's SOAP parse and body extract without a session."""
    client = SabreClient.__new__(SabreClient)
    parsed = SabreClient._parse_xml(xml_text)
    return client._extract_body(parsed, response_key)


def _reservation_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="2">
        <Reservations>
          <Reservation Locator="SAME01">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="4">
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="1.1" nameType="S">
                      <stl19:LastName>ALKHALIFA</stl19:LastName>
                      <stl19:FirstName>HASSAN</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:Gender>M</stl19:Gender>
                            <stl19:DateOfBirth>1980-01-02</stl19:DateOfBirth>
                            <stl19:DocumentNationalityCountry>SA</stl19:DocumentNationalityCountry>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                        <stl19:SpecialMealRequest>
                          <stl19:MealType>KSML</stl19:MealType>
                        </stl19:SpecialMealRequest>
                        <stl19:WheelchairRequest>
                          <stl19:WheelchairCode>WCHC</stl19:WheelchairCode>
                        </stl19:WheelchairRequest>
                        <stl19:EmergencyContactRequest>
                          <stl19:PassengerRefusal>true</stl19:PassengerRefusal>
                        </stl19:EmergencyContactRequest>
                      </stl19:SpecialRequests>
                      <stl19:FrequentFlyer>
                        <stl19:FrequentFlyerNumber>111111</stl19:FrequentFlyerNumber>
                        <stl19:AirlineCode>GF</stl19:AirlineCode>
                        <stl19:TierLevelNumber>2</stl19:TierLevelNumber>
                        <stl19:ShortText>SILVER</stl19:ShortText>
                        <stl19:StatusCode>OK</stl19:StatusCode>
                      </stl19:FrequentFlyer>
                    </stl19:Passenger>
                    <stl19:Passenger nameId="1.2" nameType="S">
                      <stl19:LastName>ALKHALIFA</stl19:LastName>
                      <stl19:FirstName>NOOR</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:Gender>F</stl19:Gender>
                            <stl19:DateOfBirth>2012-05-05</stl19:DateOfBirth>
                            <stl19:DocumentNationalityCountry></stl19:DocumentNationalityCountry>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                      </stl19:SpecialRequests>
                    </stl19:Passenger>
                    <stl19:Passenger nameId="1.3" nameType="S">
                      <stl19:LastName>ALKHALIFA</stl19:LastName>
                      <stl19:FirstName>FATIMA</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:Gender>F</stl19:Gender>
                            <stl19:DateOfBirth>1982-03-03</stl19:DateOfBirth>
                            <stl19:DocumentNationalityCountry>BH</stl19:DocumentNationalityCountry>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                        <stl19:SpecialMealRequest>
                          <stl19:MealType>VGML</stl19:MealType>
                        </stl19:SpecialMealRequest>
                        <stl19:WheelchairRequest>
                          <stl19:WheelchairCode>WCHR</stl19:WheelchairCode>
                        </stl19:WheelchairRequest>
                        <stl19:EmergencyContactRequest>
                          <stl19:PassengerRefusal>false</stl19:PassengerRefusal>
                        </stl19:EmergencyContactRequest>
                      </stl19:SpecialRequests>
                      <stl19:FrequentFlyer>
                        <stl19:FrequentFlyerNumber>333333</stl19:FrequentFlyerNumber>
                        <stl19:AirlineCode>GF</stl19:AirlineCode>
                        <stl19:TierLevelNumber>1</stl19:TierLevelNumber>
                        <stl19:ShortText>GOLD</stl19:ShortText>
                        <stl19:StatusCode>ELT</stl19:StatusCode>
                      </stl19:FrequentFlyer>
                    </stl19:Passenger>
                    <stl19:Passenger nameId="1.4" nameType="S">
                      <stl19:LastName>JONES</stl19:LastName>
                      <stl19:FirstName>MARY</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:Gender>F</stl19:Gender>
                            <stl19:DateOfBirth>1990-04-04</stl19:DateOfBirth>
                            <stl19:DocumentNationalityCountry>GB</stl19:DocumentNationalityCountry>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                        <stl19:SpecialMealRequest>
                          <stl19:MealType>GFML</stl19:MealType>
                        </stl19:SpecialMealRequest>
                        <stl19:WheelchairRequest>
                          <stl19:WheelchairCode>WCHS</stl19:WheelchairCode>
                        </stl19:WheelchairRequest>
                      </stl19:SpecialRequests>
                      <stl19:FrequentFlyer>
                        <stl19:FrequentFlyerNumber>444444</stl19:FrequentFlyerNumber>
                        <stl19:AirlineCode>GF</stl19:AirlineCode>
                        <stl19:TierLevelNumber>3</stl19:TierLevelNumber>
                        <stl19:ShortText>BLUE</stl19:ShortText>
                        <stl19:StatusCode>ACT</stl19:StatusCode>
                      </stl19:FrequentFlyer>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
          <Reservation Locator="OTHER9">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="1">
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="2.1" nameType="S">
                      <stl19:LastName>ALKHALIFA</stl19:LastName>
                      <stl19:FirstName>HASSAN</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:Gender>M</stl19:Gender>
                            <stl19:DateOfBirth>1975-06-06</stl19:DateOfBirth>
                            <stl19:DocumentNationalityCountry>AE</stl19:DocumentNationalityCountry>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                        <stl19:SpecialMealRequest>
                          <stl19:MealType>DBML</stl19:MealType>
                        </stl19:SpecialMealRequest>
                        <stl19:WheelchairRequest>
                          <stl19:WheelchairCode>WCHS</stl19:WheelchairCode>
                        </stl19:WheelchairRequest>
                      </stl19:SpecialRequests>
                      <stl19:FrequentFlyer>
                        <stl19:FrequentFlyerNumber>555555</stl19:FrequentFlyerNumber>
                        <stl19:AirlineCode>GF</stl19:AirlineCode>
                        <stl19:TierLevelNumber>4</stl19:TierLevelNumber>
                        <stl19:ShortText>BLACK</stl19:ShortText>
                        <stl19:StatusCode>BLK</stl19:StatusCode>
                      </stl19:FrequentFlyer>
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


def _passenger_info(last, first, pnr, name_assoc, passenger_id, line):
    return f"""
    <PassengerInfo>
      <Name_Details>
        <LastName>{last}</LastName>
        <FirstName>{first}</FirstName>
      </Name_Details>
      <PNRLocator nameAssociationID="{name_assoc}">{pnr}</PNRLocator>
      <PassengerID>{passenger_id}</PassengerID>
      <LineNumber>{line}</LineNumber>
      <BookingClass>Y</BookingClass>
      <Cabin>Y</Cabin>
      <Seat>{line}A</Seat>
      <Destination>LHR</Destination>
      <PassengerType>F</PassengerType>
      <Indicators>
        <Indicator>Revenue</Indicator>
      </Indicators>
      <CheckIn_Info>
        <CheckInStatus>false</CheckInStatus>
      </CheckIn_Info>
      <Boarding_Info>
        <BoardStatus>false</BoardStatus>
      </Boarding_Info>
      <BoardingPassFlag>false</BoardingPassFlag>
    </PassengerInfo>
    """


def _passenger_list_xml():
    rows = "\n".join([
        _passenger_info("ALKHALIFA", "HASSAN", "SAME01", "1.1", "P-HASSAN", 1),
        _passenger_info("ALKHALIFA", "NOOR", "SAME01", "1.2", "P-NOOR", 2),
        _passenger_info("ALKHALIFA", "FATIMA", "SAME01", "1.3", "P-FATIMA", 3),
        _passenger_info("JONES", "MARY", "SAME01", "1.4", "P-MARY", 4),
        _passenger_info("ALKHALIFA", "HASSAN", "OTHER9", "2.1", "P-OTHER", 5),
        _passenger_info("SMITH", "JOHN", "NONE99", "9.1", "P-SMITH", 6),
    ])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Destination>LHR</Destination>
          <AircraftType>32N</AircraftType>
        </Itinerary>
        <DepartureArrival_Dates>
          <Scheduled_DepartureDate>{DATE}</Scheduled_DepartureDate>
        </DepartureArrival_Dates>
      </ItineraryInfo>
      <PassengerInfoList>
        {rows}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _gender_reservation_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="2">
        <Reservations>
          <Reservation Locator="NADI01">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="2">
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="3.1" nameType="S">
                      <stl19:LastName>NADI</stl19:LastName>
                      <stl19:FirstName>AHMED</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:Gender>M</stl19:Gender>
                            <stl19:DocumentNationalityCountry>BH</stl19:DocumentNationalityCountry>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                      </stl19:SpecialRequests>
                    </stl19:Passenger>
                    <stl19:Passenger nameId="3.2" nameType="S">
                      <stl19:LastName>NADI</stl19:LastName>
                      <stl19:FirstName>LAYLA</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:Gender>F</stl19:Gender>
                            <stl19:DocumentNationalityCountry>BH</stl19:DocumentNationalityCountry>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                      </stl19:SpecialRequests>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
          <Reservation Locator="OMAR01">
            <stl19:GetReservationRS>
              <stl19:Reservation numberInParty="1">
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="4.1" nameType="S">
                      <stl19:LastName>YOUSIF</stl19:LastName>
                      <stl19:FirstName>OMAR</stl19:FirstName>
                      <stl19:SpecialRequests>
                        <stl19:APISRequest>
                          <stl19:DOCSEntry>
                            <stl19:Gender>M</stl19:Gender>
                            <stl19:DocumentNationalityCountry>BH</stl19:DocumentNationalityCountry>
                          </stl19:DOCSEntry>
                        </stl19:APISRequest>
                      </stl19:SpecialRequests>
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


def _gender_passenger_list_xml():
    rows = "\n".join([
        _passenger_info("NADI", "AHMED", "NADI01", "3.1", "G-AHMED", 1),
        _passenger_info("NADI", "LAYLA", "NADI01", "3.2", "G-LAYLA", 2),
        _passenger_info("YOUSIF", "OMAR", "OMAR01", "4.1", "G-OMAR", 3),
    ])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary><Destination>LHR</Destination></Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {rows}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _reset_db():
    import backend.api.database as api_db
    api_db._client = None
    api_db._db = None
    storage._client = None
    storage._db = None
    storage._owns_connection = False
    _dashboard_cache.clear()
    db = get_api_db()
    db.client.drop_database("falconeye")
    storage.init_db(db)
    return db


def _ingest(flight_number, reservation_xml, passenger_list_xml):
    reservation_body = _parse_body(reservation_xml, "Trip_SearchRS")
    passenger_body = _parse_body(passenger_list_xml, "GetPassengerListRS")
    reservations = convert_reservations(
        reservation_body, AIRLINE, flight_number, ORIGIN, DATE)
    passenger_list = convert_passenger_list(
        passenger_body, AIRLINE, flight_number, DATE, ORIGIN)
    storage.store_reservations(reservations)
    storage.store_passenger_list(passenger_list)
    storage.store_snapshot(
        request_id="req-res",
        airline=AIRLINE,
        flight_number=flight_number,
        origin=ORIGIN,
        departure_date=DATE,
        snapshot_type="reservations",
        data=reservations,
    )
    storage.store_snapshot(
        request_id="req-pax",
        airline=AIRLINE,
        flight_number=flight_number,
        origin=ORIGIN,
        departure_date=DATE,
        snapshot_type="passenger_list",
        data=passenger_list,
    )
    return reservations, passenger_list


def _row(passengers, passenger_id):
    matches = [p for p in passengers if p.get("passengerId") == passenger_id]
    assert len(matches) == 1, passenger_id
    return matches[0]


def _assert_own_attributes(row, expected):
    label = passenger_label(row)
    assert row["firstName"] == expected["firstName"], label
    assert row["lastName"] == expected["lastName"], label
    assert row["pnr"] == expected["pnr"], label
    assert row["nationality"] == expected["nationality"], (
        label, row["nationality"], expected["nationality"])
    for field in (
        "specialMeal",
        "wheelchairCode",
        "hasEmergencyContact",
        "ffTierLevel",
        "ffTierName",
        "ffStatus",
    ):
        if field in expected:
            assert row.get(field) == expected[field], (
                label, field, row.get(field), expected[field])
        else:
            assert field not in row, (label, field, row.get(field))


def passenger_label(row):
    return f"{row.get('lastName')}/{row.get('firstName')}/{row.get('pnr')}"


def _assert_manifest_keeps_each_traveler(body):
    passengers = body["passengers"]
    _assert_own_attributes(_row(passengers, "P-HASSAN"), {
        "firstName": "HASSAN",
        "lastName": "ALKHALIFA",
        "pnr": "SAME01",
        "nationality": "SA",
        "specialMeal": "KSML",
        "wheelchairCode": "WCHC",
        "ffTierLevel": "2",
        "ffTierName": "SILVER",
        "ffStatus": "OK",
    })
    _assert_own_attributes(_row(passengers, "P-NOOR"), {
        "firstName": "NOOR",
        "lastName": "ALKHALIFA",
        "pnr": "SAME01",
        "nationality": "",
    })
    _assert_own_attributes(_row(passengers, "P-FATIMA"), {
        "firstName": "FATIMA",
        "lastName": "ALKHALIFA",
        "pnr": "SAME01",
        "nationality": "BH",
        "specialMeal": "VGML",
        "wheelchairCode": "WCHR",
        "hasEmergencyContact": True,
        "ffTierLevel": "1",
        "ffTierName": "GOLD",
        "ffStatus": "ELT",
    })
    _assert_own_attributes(_row(passengers, "P-MARY"), {
        "firstName": "MARY",
        "lastName": "JONES",
        "pnr": "SAME01",
        "nationality": "GB",
        "specialMeal": "GFML",
        "wheelchairCode": "WCHS",
        "ffTierLevel": "3",
        "ffTierName": "BLUE",
        "ffStatus": "ACT",
    })
    _assert_own_attributes(_row(passengers, "P-OTHER"), {
        "firstName": "HASSAN",
        "lastName": "ALKHALIFA",
        "pnr": "OTHER9",
        "nationality": "AE",
        "specialMeal": "DBML",
        "wheelchairCode": "WCHS",
        "ffTierLevel": "4",
        "ffTierName": "BLACK",
        "ffStatus": "BLK",
    })
    _assert_own_attributes(_row(passengers, "P-SMITH"), {
        "firstName": "JOHN",
        "lastName": "SMITH",
        "pnr": "NONE99",
        "nationality": "",
    })


def test_latest_passenger_list_keeps_each_travelers_attributes():
    _reset_db()
    reservations, _passenger_list = _ingest(
        FLIGHT, _reservation_xml(), _passenger_list_xml())

    by_identity = {}
    for record in reservations["reservations"]:
        for pax in record["passengers"]:
            by_identity[(record["pnr"], pax["lastName"], pax["firstName"])] = pax
    assert by_identity[("SAME01", "ALKHALIFA", "HASSAN")]["wheelchairCode"] == "WCHC"
    assert by_identity[("SAME01", "ALKHALIFA", "FATIMA")]["wheelchairCode"] == "WCHR"
    assert by_identity[("SAME01", "ALKHALIFA", "NOOR")]["wheelchairCode"] == ""
    assert by_identity[("SAME01", "ALKHALIFA", "HASSAN")]["nameId"] == "1.1"
    assert by_identity[("SAME01", "ALKHALIFA", "FATIMA")]["nameId"] == "1.3"

    response = _client.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200, response.text
    _assert_manifest_keeps_each_traveler(response.json())


def test_snapshot_passenger_list_keeps_each_travelers_attributes():
    _reset_db()
    _ingest(FLIGHT, _reservation_xml(), _passenger_list_xml())
    response = _client.get(
        f"/flights/{FLIGHT}/passengers",
        params={"origin": ORIGIN, "date": DATE, "snapshot_sequence": 1},
    )
    assert response.status_code == 200, response.text
    _assert_manifest_keeps_each_traveler(response.json())


def _assert_gender_counts(analysis):
    assert analysis["totalMale"] == 2, (
        "male count", analysis["totalMale"], "female count", analysis["totalFemale"])
    assert analysis["totalFemale"] == 1, (
        "male count", analysis["totalMale"], "female count", analysis["totalFemale"])
    economy = analysis["economy"]["passengers"]
    assert economy["male"] == 2, economy
    assert economy["female"] == 1, economy


def test_dashboard_counts_each_same_surname_gender():
    _reset_db()
    _ingest(GENDER_FLIGHT, _gender_reservation_xml(), _gender_passenger_list_xml())
    response = _client.get(
        f"/flights/{GENDER_FLIGHT}/dashboard",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200, response.text
    _assert_gender_counts(response.json()["analysis"])


def test_phase_journey_counts_each_same_surname_gender():
    _reset_db()
    _ingest(GENDER_FLIGHT, _gender_reservation_xml(), _gender_passenger_list_xml())
    response = _client.get(
        f"/flights/{GENDER_FLIGHT}/phase-journey",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["phases"]) == 1
    _assert_gender_counts(body["phases"][0]["summary"])


if __name__ == "__main__":
    tests = [
        test_latest_passenger_list_keeps_each_travelers_attributes,
        test_snapshot_passenger_list_keeps_each_travelers_attributes,
        test_dashboard_counts_each_same_surname_gender,
        test_phase_journey_counts_each_same_surname_gender,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    if failed:
        sys.exit(1)
    print("ALL TESTS PASS")
