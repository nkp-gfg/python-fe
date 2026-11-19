"""Cancelled passengers who still have checked bags must survive ingest.

Sabre places those travelers on GetPassengerListRS/CancelledPassengerInfoList
when the checked-in call asks for display code BT. The feeder merges every
GetPassengerList response and stores one passenger-list snapshot. These tests
drive that path: the SOAP body is parsed by SabreClient, merged by the feeder,
converted, stored, and read back by the dashboard and change detector.

They fail until each cancelled traveler is stored on the passenger-list
snapshot with cancelledWithBags set to true, a numeric bagCount, and a
contribution to the dashboard baggage total.

Run from the repository root:

    python -m unittest backend.tests.test_cancelled_checked_bags
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Placeholders only. Set before the feeder imports so a local env file
# cannot replace them, and so the SOAP client never sends a real secret.
os.environ["SABRE_BASE_URL"] = "https://sabre.invalid"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "test-user"
os.environ["SABRE_PASSWORD"] = "test-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "TEST"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient


ORIGIN = "BAH"
DEPARTURE_DATE = "2026-03-19"
ACTIVE_BAGS = 2


class _InsertOne:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertMany:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        return _Cursor(self._docs[:count])

    def __iter__(self):
        return iter(self._docs)


def _matches(doc, filt):
    return all(doc.get(key) == value for key, value in (filt or {}).items())


class _Collection:
    """In-memory stand-in for the Mongo collections the feeder writes."""

    def __init__(self):
        self.docs = []
        self._next_id = 1

    def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = self._next_id
            self._next_id += 1
        self.docs.append(doc)
        return _InsertOne(doc["_id"])

    def insert_many(self, docs):
        return _InsertMany([self.insert_one(doc).inserted_id for doc in docs])

    def find(self, filt=None, projection=None, sort=None, **_kwargs):
        del projection
        matched = [doc for doc in self.docs if _matches(doc, filt)]
        for key, direction in reversed(sort or []):
            matched.sort(key=lambda doc, key=key: doc.get(key), reverse=direction < 0)
        return _Cursor(matched)

    def find_one(self, filt=None, projection=None, sort=None, **kwargs):
        found = list(self.find(filt, projection=projection, sort=sort, **kwargs))
        return found[0] if found else None

    def update_one(self, filt, update, upsert=False):
        matches = [doc for doc in self.docs if _matches(doc, filt)]
        if not matches and upsert:
            created = {}
            created.update(update.get("$setOnInsert") or {})
            self.docs.append(created)
            matches = [created]
        for doc in matches:
            doc.update(update.get("$set") or {})
            for key, amount in (update.get("$inc") or {}).items():
                doc[key] = doc.get(key, 0) + amount


class _MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection()
        return self._collections[name]


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _envelope(body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap-env:Body>{body}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _active_passenger_xml(prefix=""):
    """One traveling passenger, shaped like a captured GetPassengerListRS row."""
    return f"""
        <{prefix}PassengerInfo>
          <{prefix}Name_Details>
            <{prefix}LastName>AAQIB</{prefix}LastName>
            <{prefix}FirstName>HADID MR</{prefix}FirstName>
          </{prefix}Name_Details>
          <{prefix}PNRLocator nameAssociationID="76">LACREE</{prefix}PNRLocator>
          <{prefix}PassengerID>88F502A000A6</{prefix}PassengerID>
          <{prefix}LineNumber>1</{prefix}LineNumber>
          <{prefix}BookingClass>Y</{prefix}BookingClass>
          <{prefix}Cabin>Y</{prefix}Cabin>
          <{prefix}Seat>34C</{prefix}Seat>
          <{prefix}Destination>HYD</{prefix}Destination>
          <{prefix}PassengerType>F</{prefix}PassengerType>
          <{prefix}BagCount>{ACTIVE_BAGS}</{prefix}BagCount>
          <{prefix}BoardingPassFlag>true</{prefix}BoardingPassFlag>
          <{prefix}Indicators>
            <{prefix}Indicator>CheckedIn</{prefix}Indicator>
            <{prefix}Indicator>Revenue</{prefix}Indicator>
          </{prefix}Indicators>
          <{prefix}CheckIn_Info>
            <{prefix}CheckInStatus>true</{prefix}CheckInStatus>
          </{prefix}CheckIn_Info>
        </{prefix}PassengerInfo>
    """


def _cancelled_passenger_xml(last_name, first_name, bag_count, line_number, prefix="", text_prefix=""):
    """One CancelledPassengerInfo row from the vendor design sample."""
    text_prefix = text_prefix or prefix
    return f"""
        <{prefix}CancelledPassengerInfo>
          <{prefix}LineNumber>{line_number}</{prefix}LineNumber>
          <{prefix}LastName>{last_name}</{prefix}LastName>
          <{prefix}FirstName>{first_name}</{prefix}FirstName>
          <{prefix}BagCount>{bag_count}</{prefix}BagCount>
          <{prefix}FreeTextInfoList>
            <{text_prefix}FreeTextInfo>
              <{text_prefix}TextLine>
                <{text_prefix}Text>K0 FRA    441361481</{text_prefix}Text>
              </{text_prefix}TextLine>
            </{text_prefix}FreeTextInfo>
          </{prefix}FreeTextInfoList>
        </{prefix}CancelledPassengerInfo>
    """


def _passenger_list_rs(passengers_xml, cancelled_xml="", prefixed=False):
    if prefixed:
        cancelled_block = ""
        if cancelled_xml:
            cancelled_block = f"<v4:CancelledPassengerInfoList>{cancelled_xml}</v4:CancelledPassengerInfoList>"
        body = f"""
        <v4:GetPassengerListRS xmlns:v4="http://services.sabre.com/checkin/getPassengerList/v4"
                               xmlns:v3="http://services.sabre.com/STL/v3"
                               version="4.0.0" timeStamp="2026-03-19T09:06:18Z" messageID="MSG-1">
          <v4:ItineraryInfo>
            <v4:Itinerary>
              <v4:Airline>GF</v4:Airline>
              <v4:Flight>1001</v4:Flight>
              <v4:DepartureDate>{DEPARTURE_DATE}</v4:DepartureDate>
              <v4:Origin>{ORIGIN}</v4:Origin>
              <v4:Destination>HYD</v4:Destination>
            </v4:Itinerary>
          </v4:ItineraryInfo>
          <v4:PassengerInfoList>{passengers_xml}</v4:PassengerInfoList>
          {cancelled_block}
          <v4:Result><v3:Status>Success</v3:Status></v4:Result>
        </v4:GetPassengerListRS>
        """
    else:
        cancelled_block = ""
        if cancelled_xml:
            cancelled_block = f"<CancelledPassengerInfoList>{cancelled_xml}</CancelledPassengerInfoList>"
        body = f"""
        <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4"
                            version="4.0.0" timeStamp="2026-03-19T09:06:18Z" messageID="MSG-1">
          <ItineraryInfo>
            <Itinerary>
              <Airline>GF</Airline>
              <Flight>1001</Flight>
              <DepartureDate>{DEPARTURE_DATE}</DepartureDate>
              <Origin>{ORIGIN}</Origin>
              <Destination>HYD</Destination>
            </Itinerary>
          </ItineraryInfo>
          <PassengerInfoList>{passengers_xml}</PassengerInfoList>
          {cancelled_block}
          <Result><Status>Success</Status></Result>
        </GetPassengerListRS>
        """
    return _envelope(body)


SESSION_XML = _envelope(
    "<wsse:Security xmlns:wsse=\"http://schemas.xmlsoap.org/ws/2002/12/secext\">"
    "<wsse:BinarySecurityToken>placeholder-token</wsse:BinarySecurityToken>"
    "</wsse:Security>"
)


class _SabreScript:
    def __init__(self, checked_in_xml, booked_fails=False, include_cancelled=True, plain_checked_in_xml=None):
        self.checked_in_xml = checked_in_xml
        self.booked_fails = booked_fails
        self.include_cancelled = include_cancelled
        self.plain_checked_in_xml = plain_checked_in_xml or _passenger_list_rs(_active_passenger_xml())
        self.booked_xml = _passenger_list_rs(_active_passenger_xml())
        self.other_xml = _passenger_list_rs(_active_passenger_xml())

    def passenger_list(self, request_body):
        if "DisplayCode>BT<" in request_body:
            if self.include_cancelled:
                return 200, self.checked_in_xml
            return 200, self.plain_checked_in_xml
        if "DisplayCode>RV<" in request_body and self.booked_fails:
            return 500, "booked display unavailable"
        if "DisplayCode>RV<" in request_body:
            return 200, self.booked_xml
        return 200, self.other_xml


class CancelledCheckedBagTests(unittest.TestCase):
    def setUp(self):
        self.db = _MemoryDB()
        storage._client = None
        storage._owns_connection = False
        storage.init_db(self.db)
        self._delay = SabreClient.API_CALL_DELAY
        SabreClient.API_CALL_DELAY = 0
        self.script = None
        self.calls = []
        self._post = patch("backend.sabre.client.requests.post", self._http_post)
        self._post.start()

    def tearDown(self):
        self._post.stop()
        SabreClient.API_CALL_DELAY = self._delay
        storage._db = None
        storage._client = None
        storage._owns_connection = False

    def _http_post(self, url, data=None, headers=None, timeout=None):
        del url, timeout
        action = (headers or {}).get("SOAPAction", "")
        body = data.decode() if isinstance(data, bytes) else (data or "")
        self.calls.append((action, body))
        if action == "SessionCreateRQ":
            return _Response(200, SESSION_XML)
        if action == "SessionCloseRQ":
            return _Response(200, "<closed/>")
        if action == "GetPassengerListRQ":
            status, text = self.script.passenger_list(body)
            return _Response(status, text)
        return _Response(500, "not used by this test")

    def _ingest(self, flight_number, script):
        self.script = script
        self.calls = []
        run_feeder([{
            "airline": "GF",
            "flightNumber": flight_number,
            "origin": ORIGIN,
            "departureDate": DEPARTURE_DATE,
            "departureDateTime": f"{DEPARTURE_DATE}T10:00:00",
            "flightSequenceNumber": 4242,
        }])
        checked_in = [
            body for action, body in self.calls
            if action == "GetPassengerListRQ" and "DisplayCode>BT<" in body
        ]
        self.assertEqual(len(checked_in), 1, "feeder did not make the BP/BT checked-in call")
        self.assertIn("DisplayCode>BP<", checked_in[0])

    def _latest_manifest(self, flight_number):
        legacy_rows = [
            doc for doc in self.db["passenger_list"].docs
            if doc.get("flightNumber") == flight_number
        ]
        snapshots = [
            doc for doc in self.db["snapshots"].docs
            if doc.get("flightNumber") == flight_number and doc.get("snapshotType") == "passenger_list"
        ]
        self.assertTrue(legacy_rows, "passenger_list collection has no manifest")
        self.assertTrue(snapshots, "passenger_list snapshot was not stored")
        legacy_rows.sort(key=lambda doc: doc.get("fetchedAt", ""))
        snapshots.sort(key=lambda doc: doc.get("sequenceNumber", 0))
        return legacy_rows[-1], snapshots[-1]["data"]

    def _assert_cancelled_kept(self, flight_number, expected):
        legacy, snapshot = self._latest_manifest(flight_number)
        for doc in (legacy, snapshot):
            passengers = doc.get("passengers") or []
            by_name = {}
            for passenger in passengers:
                by_name.setdefault(passenger.get("lastName"), []).append(passenger)

            active = by_name.get("AAQIB") or []
            self.assertEqual(len(active), 1, doc)
            self.assertEqual(active[0].get("bagCount"), ACTIVE_BAGS)
            self.assertEqual(active[0].get("pnr"), "LACREE")

            for row in expected:
                matches = by_name.get(row["lastName"]) or []
                self.assertEqual(
                    len(matches), 1,
                    f"{row['lastName']} missing from stored passengers: "
                    f"{[p.get('lastName') for p in passengers]}",
                )
                stored = matches[0]
                self.assertEqual(stored.get("firstName"), row["firstName"])
                self.assertEqual(stored.get("bagCount"), row["bagCount"])
                self.assertIs(stored.get("cancelledWithBags"), True)
                self.assertEqual(stored.get("lineNumber"), row["lineNumber"])
                self.assertFalse(stored.get("isBoarded", False))

            self.assertEqual(doc.get("totalPassengers"), 1 + len(expected))
            self.assertEqual(len(passengers), 1 + len(expected))

        dashboard = _build_dashboard_payload(None, legacy, ORIGIN, DEPARTURE_DATE, {})
        expected_bags = ACTIVE_BAGS + sum(row["bagCount"] for row in expected)
        self.assertEqual(dashboard["insights"]["baggage"]["totalBags"], expected_bags)
        self.assertEqual(dashboard["overview"]["manifestRecords"], 1 + len(expected))

        state = self.db["flights"].find_one({"flightNumber": flight_number, "origin": ORIGIN})
        self.assertIsNotNone(state)
        self.assertEqual(state["summary"]["totalPax"], 1 + len(expected))

    def test_booked_then_checked_in_keeps_cancelled_passenger_with_bags(self):
        """Booked succeeds first. The BT response then adds one cancelled traveler.

        xmltodict represents a single CancelledPassengerInfo child as a dict.
        """
        cancelled = _cancelled_passenger_xml("SMITH", "SAM", 1, 9)
        checked_in = _passenger_list_rs(
            _active_passenger_xml(),
            cancelled,
        )
        self._ingest("9101", _SabreScript(checked_in))
        self._assert_cancelled_kept("9101", [{
            "lastName": "SMITH",
            "firstName": "SAM",
            "bagCount": 1,
            "lineNumber": 9,
        }])

    def test_prefixed_bt_response_keeps_cancelled_passenger_with_bags(self):
        """Vendor design sample uses v4: and v3: prefixes on the BT response."""
        cancelled = _cancelled_passenger_xml(
            "SMITH", "SAM", 1, 1, prefix="v4:", text_prefix="v3:",
        )
        checked_in = _passenger_list_rs(
            _active_passenger_xml("v4:"),
            cancelled,
            prefixed=True,
        )
        self._ingest("9102", _SabreScript(checked_in))
        self._assert_cancelled_kept("9102", [{
            "lastName": "SMITH",
            "firstName": "SAM",
            "bagCount": 1,
            "lineNumber": 1,
        }])

    def test_checked_in_first_success_keeps_every_cancelled_passenger(self):
        """Booked fails, so the BT body is the manifest base and holds two cancelled rows."""
        cancelled = (
            _cancelled_passenger_xml("SMITH", "SAM", 1, 4)
            + _cancelled_passenger_xml("JONES", "ALEX", 4, 5)
        )
        checked_in = _passenger_list_rs(_active_passenger_xml(), cancelled)
        self._ingest("9103", _SabreScript(checked_in, booked_fails=True))
        self._assert_cancelled_kept("9103", [
            {"lastName": "SMITH", "firstName": "SAM", "bagCount": 1, "lineNumber": 4},
            {"lastName": "JONES", "firstName": "ALEX", "bagCount": 4, "lineNumber": 5},
        ])

    def test_later_ingest_records_cancelled_passenger_as_added(self):
        """A traveler who appears only on a later BT response must show up as a change."""
        cancelled = _cancelled_passenger_xml("SMITH", "SAM", 1, 9)
        checked_in = _passenger_list_rs(_active_passenger_xml(), cancelled)
        script = _SabreScript(checked_in, include_cancelled=False)
        self._ingest("9104", script)
        script.include_cancelled = True
        self._ingest("9104", script)

        added = [
            change for change in self.db["changes"].docs
            if change.get("flightNumber") == "9104" and change.get("changeType") == "PASSENGER_ADDED"
        ]
        added_names = sorted(
            (change.get("passenger") or {}).get("lastName") for change in added
        )
        self.assertEqual(added_names, ["SMITH"])
        self._assert_cancelled_kept("9104", [{
            "lastName": "SMITH",
            "firstName": "SAM",
            "bagCount": 1,
            "lineNumber": 9,
        }])


if __name__ == "__main__":
    unittest.main()
