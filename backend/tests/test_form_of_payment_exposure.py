"""Form-of-payment exposure.

These tests follow the product path from a Trip_Search SOAP body through
SabreClient parsing, convert_reservations, reservation storage, and the
unauthenticated flight routes. They fail while raw form-of-payment text is
stored and returned, and pass once only a card brand is kept and the
per-record response omits the field.

The account and expiry tokens below are placeholders, not payment data.
"""

import copy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import flights as flights_routes
from backend.api.routes import reservations as reservations_routes
from backend.feeder import storage
from backend.feeder.converter import convert_reservations
from backend.sabre.client import SabreClient

ACCOUNT_A = "SENTINELACCOUNT"
EXPIRY_A = "SENTINELEXPIRY"
ACCOUNT_B = "SENTINELACCOUNTB"
EXPIRY_B = "SENTINELEXPIRYB"
FLIGHT = "2057"
ORIGIN = "BOM"
DATE = "2026-03-18"
PNR_A = "TSTPN1"
PNR_B = "TSTPN2"


def _match(doc, query):
    if not query:
        return True
    for key, expected in query.items():
        if key == "reservations.pnr":
            found = any(
                reservation.get("pnr") == expected
                for reservation in doc.get("reservations") or []
            )
            if not found:
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _MemoryCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        return type("InsertResult", (), {"inserted_id": len(self.docs)})()

    def find_one(self, filter=None, projection=None, sort=None, **kwargs):
        sort = kwargs.get("sort", sort)
        matches = [doc for doc in self.docs if _match(doc, filter or {})]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(
                    key=lambda doc: (doc.get(field) is None, doc.get(field)),
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, filter=None, projection=None, **kwargs):
        return [
            copy.deepcopy(doc)
            for doc in self.docs
            if _match(doc, filter or {})
        ]


class _MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _MemoryCollection()
        return self._collections[name]


def _trip_search_xml():
    """SOAP body shaped like Trip_SearchRS.

    Form of payment, email, and phone elements sit on Reservation, which is
    where the checked-in Trip Search sample places them. Passenger names sit
    on PassengerReservation, which is where the converter reads them.
    """
    def reservation(pnr, last_name, account, expiry):
        text = f"*CA/{account}/{expiry}"
        return f"""
          <Reservation Locator="{pnr}">
            <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19"
                                    xmlns:or114="http://services.sabre.com/res/or/v1_14">
              <stl19:Reservation numberInParty="1" numberOfInfants="0">
                <stl19:BookingDetails>
                  <stl19:RecordLocator>{pnr}</stl19:RecordLocator>
                  <stl19:CreationTimestamp>2026-03-16T22:53:00</stl19:CreationTimestamp>
                </stl19:BookingDetails>
                <stl19:PassengerReservation>
                  <stl19:Passengers>
                    <stl19:Passenger nameId="01.01" nameType="S">
                      <stl19:LastName>{last_name}</stl19:LastName>
                      <stl19:FirstName>TRAVELER</stl19:FirstName>
                    </stl19:Passenger>
                  </stl19:Passengers>
                </stl19:PassengerReservation>
                <stl19:EmailAddresses>
                  <stl19:EmailAddress>
                    <stl19:Address>traveler@example.test</stl19:Address>
                  </stl19:EmailAddress>
                </stl19:EmailAddresses>
                <stl19:PhoneNumbers>
                  <stl19:PhoneNumber>
                    <stl19:Number>0000000000</stl19:Number>
                  </stl19:PhoneNumber>
                </stl19:PhoneNumbers>
                <stl19:OpenReservationElements>
                  <or114:OpenReservationElement type="FP">
                    <or114:FormOfPayment>
                      <or114:Other>
                        <or114:Text>{text}</or114:Text>
                      </or114:Other>
                    </or114:FormOfPayment>
                  </or114:OpenReservationElement>
                </stl19:OpenReservationElements>
              </stl19:Reservation>
            </stl19:GetReservationRS>
          </Reservation>
        """

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="2">
        <Reservations>
          {reservation(PNR_A, "EXAMPLE", ACCOUNT_A, EXPIRY_A)}
          {reservation(PNR_B, "SAMPLE", ACCOUNT_B, EXPIRY_B)}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _ingest():
    parsed = SabreClient._parse_xml(_trip_search_xml())
    raw = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(raw, "GF", FLIGHT, ORIGIN, DATE)


def _assert_no_payment_tokens(text):
    for token in (ACCOUNT_A, EXPIRY_A, ACCOUNT_B, EXPIRY_B):
        assert token not in text


def _passenger_list():
    return {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
        "fetchedAt": "2026-03-18T00:00:00Z",
        "totalPassengers": 1,
        "passengers": [
            {
                "pnr": PNR_A,
                "lastName": "EXAMPLE",
                "firstName": "TRAVELER",
                "cabin": "Y",
                "isCheckedIn": True,
                "isBoarded": False,
                "isRevenue": True,
            }
        ],
    }


def _client_and_store():
    converted = _ingest()
    db = _MemoryDB()
    storage.init_db(db)
    storage.store_reservations(converted)
    db["passenger_list"].insert_one(_passenger_list())
    storage.store_snapshot(
        "request-placeholder", "GF", FLIGHT, ORIGIN, DATE,
        "reservations", converted,
    )

    flights_routes.get_db = lambda: db
    reservations_routes.get_db = lambda: db
    flights_routes._dashboard_cache.clear()

    app = FastAPI()
    app.include_router(flights_routes.router)
    app.include_router(reservations_routes.router)
    return TestClient(app), converted


def test_converter_does_not_keep_account_or_expiry():
    converted = _ingest()
    by_pnr = {reservation["pnr"]: reservation for reservation in converted["reservations"]}
    assert set(by_pnr) == {PNR_A, PNR_B}
    assert by_pnr[PNR_A]["passengers"][0]["lastName"] == "EXAMPLE"
    for reservation in converted["reservations"]:
        stored = reservation.get("formOfPayment", "")
        assert ACCOUNT_A not in stored
        assert EXPIRY_A not in stored
        assert ACCOUNT_B not in stored
        assert EXPIRY_B not in stored


def test_dashboard_hides_raw_form_of_payment_without_credentials():
    client, _converted = _client_and_store()
    response = client.get(
        f"/flights/{FLIGHT}/dashboard",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("insights"), dict)
    for key in payload["insights"].get("paymentMethods") or {}:
        assert ACCOUNT_A not in key
        assert EXPIRY_A not in key
        assert ACCOUNT_B not in key
        assert EXPIRY_B not in key
    _assert_no_payment_tokens(response.text)


def test_reservation_by_pnr_omits_form_of_payment():
    client, _converted = _client_and_store()
    response = client.get(
        f"/flights/{FLIGHT}/reservations/{PNR_A}",
        params={"date": DATE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reservations"][0]["pnr"] == PNR_A
    assert body["reservations"][0]["passengers"][0]["lastName"] == "EXAMPLE"
    assert "formOfPayment" not in body["reservations"][0]
    _assert_no_payment_tokens(response.text)


def test_reservation_list_hides_raw_form_of_payment():
    client, _converted = _client_and_store()
    response = client.get(
        f"/flights/{FLIGHT}/reservations",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200
    body = response.json()
    names = [
        passenger["lastName"]
        for reservation in body["reservations"]
        for passenger in reservation["passengers"]
    ]
    assert "EXAMPLE" in names
    for reservation in body["reservations"]:
        assert "formOfPayment" not in reservation
    _assert_no_payment_tokens(response.text)

    historical = client.get(
        f"/flights/{FLIGHT}/reservations",
        params={"origin": ORIGIN, "date": DATE, "snapshot_sequence": 1},
    )
    assert historical.status_code == 200
    _assert_no_payment_tokens(historical.text)
    for reservation in historical.json()["reservations"]:
        assert "formOfPayment" not in reservation
