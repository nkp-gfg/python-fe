"""Missing-bag tags from GetPassengerList must survive ingestion.

Sabre's checked-bag display (BT) returns PassengerInfo/MissingBagList with an
airline, a bag tag number, and two comment fields. These tests run the feeder
the ingest route runs: the SOAP body is parsed by SabreClient, the four
display-code responses are merged, convert_passenger_list builds the manifest,
and the passenger API returns that manifest.

They fail while a missing tag or comment is dropped. A passenger who is new on
the BT response, and a passenger already returned by the booked display, must
both keep every missing-bag airline, tag number, and comment on the stored
passenger object. The values have to be on that passenger, not only inside the
raw Sabre payload, because the passenger API removes the raw payload before
the flight board reads the manifest.

Left untested, and why:
- The React flight board (passenger-table.tsx, bottom-detail-panel.tsx) and
  the passenger-detail baggage tab. Those views only render bag counts or
  baggage routes. This environment has no frontend test runner. The API
  responses below are the payload those views fetch.
- convert_passenger_data / GetPassengerDataRQ. That schema has no
  MissingBagList element.
- The standby-list projection, which builds a reduced queue entry and is not
  the baggage manifest.
- diff_passenger_list, which compares bag counts and does not read missing-bag
  fields.
"""

import copy
import json
import os
import unittest
from unittest import mock

# Placeholders only. The HTTP client is replaced below, so these values are
# never sent to Sabre. setdefault keeps a developer environment intact.
os.environ.setdefault("SABRE_BASE_URL", "https://placeholder.invalid")
os.environ.setdefault("SABRE_CPAID", "XX")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "XXXX")
os.environ.setdefault("SABRE_ORGANIZATION", "XX")
os.environ.setdefault("SABRE_DOMAIN", "XX")
os.environ.setdefault("MONGODB_URI", "mongodb://placeholder.invalid:27017")

from backend.api.routes.passengers import get_passenger_by_pnr, get_passengers
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient


FLIGHT = "2130"
ORIGIN = "DMM"
DEPARTURE_DATE = "2026-03-18"

# Distinctive values that appear nowhere else in the fixture.
SMITH_TAG_1 = "991122001"
SMITH_AIRLINE_1 = "EY"
SMITH_COMMENT_1A = "LEFT-ON-BELT-4"
SMITH_COMMENT_1B = "TRACED-TO-TRANSFER"
SMITH_TAG_2 = "991122002"
SMITH_AIRLINE_2 = "LH"
SMITH_COMMENT_2A = "HANDLE-DAMAGED-ONLY"
JONES_TAG = "991133003"
JONES_AIRLINE = "QR"
JONES_COMMENT_1 = "NO-SHOW-AT-CAROUSEL"
JONES_COMMENT_2 = "CLAIM-DESK-NOTE"


class _HttpResponse:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code


def _envelope(body_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header>"
        '<wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">'
        "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
        "</wsse:Security>"
        "</soap-env:Header>"
        "<soap-env:Body>"
        f"{body_xml}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _passenger_xml(pnr, last_name, passenger_id, bag_count, missing_bag_xml):
    return f"""
          <PassengerInfo>
            <Name_Details>
              <LastName>{last_name}</LastName>
              <FirstName>TEST</FirstName>
            </Name_Details>
            <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
            <PassengerID>{passenger_id}</PassengerID>
            <LineNumber>1</LineNumber>
            <BookingClass>Y</BookingClass>
            <Cabin>Y</Cabin>
            <Seat>40A</Seat>
            <Destination>DEL</Destination>
            <PassengerType>F</PassengerType>
            <BagCount>{bag_count}</BagCount>
            <BaggageRouteList>
              <BaggageRoute>
                <Airline>GF</Airline>
                <Flight>100</Flight>
                <Origin>DMM</Origin>
                <Destination>BAH</Destination>
                <SegmentStatus>HK</SegmentStatus>
              </BaggageRoute>
            </BaggageRouteList>
            {missing_bag_xml}
          </PassengerInfo>"""


def _list_response(passengers_xml):
    body = f"""
      <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4"
                          xmlns:ns2="http://services.sabre.com/STL/v3"
                          xmlns:v4="http://services.sabre.com/checkin/getPassengerList/v4">
        <ItineraryInfo>
          <Itinerary>
            <Airline>GF</Airline>
            <Flight>{FLIGHT}</Flight>
            <DepartureDate>{DEPARTURE_DATE}</DepartureDate>
            <Origin>{ORIGIN}</Origin>
            <Destination>DEL</Destination>
          </Itinerary>
        </ItineraryInfo>
        <PassengerInfoList>
          {passengers_xml}
        </PassengerInfoList>
      </GetPassengerListRS>"""
    return _envelope(body)


def _smith_missing_bags():
    """Two MissingBagDetails, the shape xmltodict turns into a list.

    The second comment on the second bag is omitted on purpose: the schema
    marks every field optional, and a tag with only one comment still counts.
    """
    return f"""
            <MissingBagList>
              <MissingBagDetails>
                <Airline>{SMITH_AIRLINE_1}</Airline>
                <BagTagNumber>{SMITH_TAG_1}</BagTagNumber>
                <Comments1>{SMITH_COMMENT_1A}</Comments1>
                <Comments2>{SMITH_COMMENT_1B}</Comments2>
              </MissingBagDetails>
              <MissingBagDetails>
                <Airline>{SMITH_AIRLINE_2}</Airline>
                <BagTagNumber>{SMITH_TAG_2}</BagTagNumber>
                <Comments1>{SMITH_COMMENT_2A}</Comments1>
              </MissingBagDetails>
            </MissingBagList>"""


def _jones_missing_bag():
    """One prefixed MissingBagDetails, the shape xmltodict leaves as a dict.

    The design sample qualifies these elements. The converter strips prefixes
    before it reads each passenger, so a stored row still has to keep them.
    """
    return f"""
            <v4:MissingBagList>
              <v4:MissingBagDetails>
                <v4:Airline>{JONES_AIRLINE}</v4:Airline>
                <v4:BagTagNumber>{JONES_TAG}</v4:BagTagNumber>
                <v4:Comments1>{JONES_COMMENT_1}</v4:Comments1>
                <v4:Comments2>{JONES_COMMENT_2}</v4:Comments2>
              </v4:MissingBagDetails>
            </v4:MissingBagList>"""


BOOKED_XML = _list_response(
    _passenger_xml("ABC123", "SMITH", "P-SMITH", 1, "")
    + _passenger_xml("N0BAGS", "NOBAGS", "P-NOBAGS", 2, "")
)
CHECKED_IN_XML = _list_response(
    _passenger_xml("ABC123", "SMITH", "P-SMITH", 1, _smith_missing_bags())
    + _passenger_xml("XYZ789", "JONES", "P-JONES", 0, _jones_missing_bag())
)
EMPTY_XML = _list_response("")


def _display_code(body, code):
    return f"<v4:DisplayCode>{code}</v4:DisplayCode>" in body


class _MemoryDB:
    """Just enough of a Mongo database for the passenger read routes."""

    def __init__(self, passenger_list, snapshot):
        self._passenger_list = passenger_list
        self._snapshot = snapshot

    def __getitem__(self, name):
        return _MemoryCollection(self, name)


class _MemoryCollection:
    def __init__(self, db, name):
        self._db = db
        self._name = name

    def find_one(self, query, sort=None, projection=None):
        if self._name == "passenger_list":
            return copy.deepcopy(self._db._passenger_list)
        if self._name == "snapshots" and query.get("snapshotType") == "passenger_list":
            return copy.deepcopy(self._db._snapshot)
        return None


def _require(passenger, *needles):
    blob = json.dumps(passenger, default=str)
    missing = [needle for needle in needles if needle not in blob]
    if missing:
        raise AssertionError(
            "stored passenger {pnr} is missing {missing}".format(
                pnr=passenger.get("pnr"), missing=missing
            )
        )


def _one(passengers, pnr):
    matches = [pax for pax in passengers if pax.get("pnr") == pnr]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one stored passenger for {pnr}, found {len(matches)}"
        )
    return matches[0]


class MissingBagListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        saved = {}
        calls = []

        def post(url, data=None, headers=None, timeout=None, **kwargs):
            action = (headers or {}).get("SOAPAction", "")
            body = data.decode() if isinstance(data, bytes) else (data or "")
            if action == "GetPassengerListRQ":
                calls.append(body)
                if _display_code(body, "BT"):
                    return _HttpResponse(CHECKED_IN_XML, 200)
                if _display_code(body, "RV"):
                    return _HttpResponse(BOOKED_XML, 200)
                return _HttpResponse(EMPTY_XML, 200)
            if action == "SessionCreateRQ":
                return _HttpResponse(_envelope(""), 200)
            if action == "SessionCloseRQ":
                return _HttpResponse(_envelope(""), 200)
            return _HttpResponse("not-this-call", 500)

        def store_raw_request(**kwargs):
            if kwargs.get("api_type") == "PassengerList":
                saved["raw"] = copy.deepcopy(kwargs.get("response_json"))
            return "request-1"

        def store_snapshot(**kwargs):
            if kwargs.get("snapshot_type") == "passenger_list":
                saved["snapshot_data"] = copy.deepcopy(kwargs.get("data"))
            return ("snap-1", "checksum", False)

        def store_passenger_list(doc):
            saved["passenger_list"] = copy.deepcopy(doc)
            return "passenger-list-1"

        SabreClient.API_CALL_DELAY = 0
        storage_patches = {
            "store_raw_request": store_raw_request,
            "store_snapshot": store_snapshot,
            "store_passenger_list": store_passenger_list,
            "store_flight_status": lambda doc: None,
            "store_reservations": lambda doc: None,
            "store_trip_reports": lambda doc: None,
            "store_flight_schedule": lambda doc: None,
            "get_previous_snapshot": lambda *args, **kwargs: None,
            "store_changes": lambda changes: 0,
            "update_flight_state": lambda *args, **kwargs: None,
        }
        with mock.patch("backend.sabre.client.requests.post", post), mock.patch.multiple(
            "backend.feeder.runner.storage", **storage_patches
        ):
            cls.result = run_feeder(
                [
                    {
                        "airline": "GF",
                        "flightNumber": FLIGHT,
                        "origin": ORIGIN,
                        "departureDate": DEPARTURE_DATE,
                        "departureDateTime": f"{DEPARTURE_DATE}T13:15:00",
                        "flightSequenceNumber": 1,
                    }
                ]
            )
        cls.saved = saved
        cls.calls = calls
        cls.passenger_list = saved.get("passenger_list")
        snapshot_data = saved.get("snapshot_data")
        cls.snapshot = None
        if snapshot_data is not None:
            cls.snapshot = {
                "flightNumber": FLIGHT,
                "origin": ORIGIN,
                "departureDate": DEPARTURE_DATE,
                "snapshotType": "passenger_list",
                "sequenceNumber": 1,
                "capturedAt": "2026-03-18T00:00:00Z",
                "data": snapshot_data,
            }

    def test_checked_bag_display_is_requested_and_parsed(self):
        """The ingest path really does ask Sabre for BT, and the parser keeps the tag."""
        self.assertTrue(
            any(_display_code(body, "BT") for body in self.calls),
            "feeder never sent display code BT",
        )
        passenger_list = self.result["results"][0]["apis"].get("passengerList", {})
        self.assertEqual(passenger_list.get("status"), "success", passenger_list)
        raw_passengers = json.dumps(self.saved.get("raw"), default=str)
        self.assertIn(
            JONES_TAG,
            raw_passengers,
            "BT response was parsed but the raw merged payload lost the tag",
        )

    def test_new_bt_passenger_keeps_missing_bag_on_manifest(self):
        """Jones exists only on the BT response. Bag count is 0. The tag must still be stored."""
        passengers = self.passenger_list["passengers"]
        jones = _one(passengers, "XYZ789")
        self.assertEqual(jones["bagCount"], 0)
        _require(
            jones,
            JONES_TAG,
            JONES_AIRLINE,
            JONES_COMMENT_1,
            JONES_COMMENT_2,
        )
        self.assertEqual(self.snapshot["data"]["passengers"], passengers)

    def test_booked_passenger_keeps_missing_bags_added_by_bt(self):
        """Smith is returned twice. The BT copy is the one with the missing bags.

        Dedup has to keep those bags on the single stored passenger, together
        with the baggage route already known from the booked display.
        """
        smith = _one(self.passenger_list["passengers"], "ABC123")
        self.assertEqual(smith["bagCount"], 1)
        routes = json.dumps(smith.get("baggageRoutes"), default=str)
        self.assertIn("BAH", routes)
        _require(
            smith,
            SMITH_TAG_1,
            SMITH_AIRLINE_1,
            SMITH_COMMENT_1A,
            SMITH_COMMENT_1B,
            SMITH_TAG_2,
            SMITH_AIRLINE_2,
            SMITH_COMMENT_2A,
        )

    def test_passenger_without_missing_bags_is_unchanged(self):
        nobags = _one(self.passenger_list["passengers"], "N0BAGS")
        self.assertEqual(nobags["bagCount"], 2)
        self.assertEqual(nobags["lastName"], "NOBAGS")

    def test_passenger_api_returns_missing_bags_for_latest_and_historical(self):
        """The flight board reads these routes. The raw payload is not part of the response."""
        db = _MemoryDB(self.passenger_list, self.snapshot)
        with mock.patch("backend.api.routes.passengers.get_db", return_value=db):
            latest = get_passengers(FLIGHT, origin=ORIGIN, date=DEPARTURE_DATE)
            historical = get_passengers(
                FLIGHT,
                origin=ORIGIN,
                date=DEPARTURE_DATE,
                snapshot_sequence=1,
            )
            by_pnr = get_passenger_by_pnr(FLIGHT, "XYZ789", date=DEPARTURE_DATE)

        for response in (latest, historical):
            self.assertNotIn("_raw", response)
            jones = _one(response["passengers"], "XYZ789")
            smith = _one(response["passengers"], "ABC123")
            _require(jones, JONES_TAG, JONES_AIRLINE, JONES_COMMENT_1, JONES_COMMENT_2)
            _require(
                smith,
                SMITH_TAG_1,
                SMITH_COMMENT_1A,
                SMITH_COMMENT_1B,
                SMITH_TAG_2,
                SMITH_COMMENT_2A,
            )
        jones = _one(by_pnr["passengers"], "XYZ789")
        _require(jones, JONES_TAG, JONES_COMMENT_1, JONES_COMMENT_2)


if __name__ == "__main__":
    unittest.main()
