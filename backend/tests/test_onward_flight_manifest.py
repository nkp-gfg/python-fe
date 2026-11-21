"""Onward flight from GetPassengerList must survive onto the manifest.

Sabre can return PrimaryAirlineCode and OutboundFlight on each PassengerInfo.
Those values are the primary carrier and the onward flight number. The stored
passenger row, the live passenger-list response, the historical passenger-list
response, and the single-PNR response must copy them.

Stored names follow the rest of the passenger document: primaryAirlineCode and
outboundFlight. When Sabre omits an element, or sends it empty, the stored
value is an empty string. Seat and the record locator stay as they are.

These tests fail while conversion drops the elements.
"""

import copy
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.passengers import get_passenger_by_pnr, get_passengers
from backend.feeder.converter import convert_passenger_list
from backend.feeder.runner import (
    _extract_passenger_info_list,
    _pax_dedup_key,
    _set_passenger_info_list,
)
from backend.sabre.client import SabreClient


AIRLINE = "GF"
FLIGHT_NUMBER = "2057"
ORIGIN = "BAH"
DEPARTURE_DATE = "2026-03-19"


def _passenger_xml(
    last_name,
    first_name,
    pnr,
    passenger_id,
    seat,
    *,
    prefix="",
    primary_airline=None,
    outbound_flight=None,
    empty_outbound=False,
):
    """One PassengerInfo element. prefix is '' or 'v4:'."""
    tag = f"{prefix}"
    extra = ""
    if primary_airline is not None:
        extra += f"<{tag}PrimaryAirlineCode>{primary_airline}</{tag}PrimaryAirlineCode>"
    if empty_outbound:
        extra += f"<{tag}OutboundFlight></{tag}OutboundFlight>"
    elif outbound_flight is not None:
        extra += f"<{tag}OutboundFlight>{outbound_flight}</{tag}OutboundFlight>"
    return f"""
        <{tag}PassengerInfo>
          <{tag}Name_Details>
            <{tag}LastName>{last_name}</{tag}LastName>
            <{tag}FirstName>{first_name}</{tag}FirstName>
          </{tag}Name_Details>
          <{tag}PNRLocator nameAssociationID="1">{pnr}</{tag}PNRLocator>
          <{tag}PassengerID>{passenger_id}</{tag}PassengerID>
          <{tag}LineNumber>1</{tag}LineNumber>
          <{tag}Seat>{seat}</{tag}Seat>
          <{tag}Destination>LHR</{tag}Destination>
          {extra}
        </{tag}PassengerInfo>
    """


def _envelope(passengers_xml, *, prefixed=False):
    """SOAP body in the shape SabreClient.get_passenger_list parses."""
    if prefixed:
        body = f"""
        <v4:GetPassengerListRS xmlns:v4="http://services.sabre.com/checkin/getPassengerList/v4">
          <v4:ItineraryInfo>
            <v4:Itinerary>
              <v4:Airline>{AIRLINE}</v4:Airline>
              <v4:Flight>{FLIGHT_NUMBER}</v4:Flight>
              <v4:Origin>{ORIGIN}</v4:Origin>
              <v4:Destination>LHR</v4:Destination>
            </v4:Itinerary>
          </v4:ItineraryInfo>
          <v4:PassengerInfoList>
            {passengers_xml}
          </v4:PassengerInfoList>
        </v4:GetPassengerListRS>
        """
    else:
        body = f"""
        <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4">
          <ItineraryInfo>
            <Itinerary>
              <Airline>{AIRLINE}</Airline>
              <Flight>{FLIGHT_NUMBER}</Flight>
              <Origin>{ORIGIN}</Origin>
              <Destination>LHR</Destination>
            </Itinerary>
          </ItineraryInfo>
          <PassengerInfoList>
            {passengers_xml}
          </PassengerInfoList>
        </GetPassengerListRS>
        """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    {body}
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_passenger_list(xml_text):
    """Same parse and body extract as SabreClient.get_passenger_list."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(SabreClient, parsed, "GetPassengerListRS")


def _booked_response():
    passengers = "".join([
        _passenger_xml(
            "HASSAN", "LAYLA", "AAA111", "PAX-HASSAN", "12A",
            primary_airline="GF", outbound_flight="K012",
        ),
        _passenger_xml(
            "NOOR", "OMAR", "BBB222", "PAX-NOOR", "14C",
        ),
        _passenger_xml(
            "KHALID", "SARA", "DDD444", "PAX-KHALID", "2A",
            primary_airline="BA",
        ),
        _passenger_xml(
            "FARID", "NINA", "EEE555", "PAX-FARID", "16F",
            empty_outbound=True,
        ),
    ])
    return _parse_passenger_list(_envelope(passengers))


def _checked_in_response():
    """Later display-code call.

    Repeats HASSAN without the onward elements, and adds SARA who was not
    on the booked call. The runner keeps the first copy of a duplicate and
    appends a new passenger.
    """
    passengers = "".join([
        _passenger_xml(
            "HASSAN", "LAYLA", "AAA111", "PAX-HASSAN", "12A",
        ),
        _passenger_xml(
            "SARA", "AMAL", "CCC333", "PAX-SARA", "18B",
            outbound_flight="221",
        ),
    ])
    return _parse_passenger_list(_envelope(passengers))


def _merge_display_code_calls(base, subsequent):
    """Same merge run_feeder uses after each GetPassengerList display-code call."""
    merged = copy.deepcopy(base)
    base_pax = _extract_passenger_info_list(merged)
    existing_keys = set()
    for passenger in base_pax:
        existing_keys.add(_pax_dedup_key(passenger))
    for passenger in _extract_passenger_info_list(subsequent):
        if _pax_dedup_key(passenger) not in existing_keys:
            base_pax.append(passenger)
            existing_keys.add(_pax_dedup_key(passenger))
    _set_passenger_info_list(merged, base_pax)
    return merged


def _convert(raw):
    return convert_passenger_list(
        raw, AIRLINE, FLIGHT_NUMBER, DEPARTURE_DATE, ORIGIN,
    )


def _by_pnr(passengers):
    return {p.get("pnr"): p for p in passengers}


class _Collection:
    """find_one for the queries the passenger routes issue.

    Equality match, dotted passengers.pnr, and sequenceNumber $lte. Returns a
    deep copy of the stored document, which is what the route then shapes.
    """

    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query=None, sort=None, projection=None):
        query = query or {}
        matched = [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                matched.sort(
                    key=lambda doc: _sort_value(doc.get(field)),
                    reverse=direction < 0,
                )
        if not matched:
            return None
        return matched[0]


def _sort_value(value):
    if value is None:
        return (1, "")
    if isinstance(value, (int, float)):
        return (0, value)
    return (0, str(value))


def _matches(doc, query):
    for key, expected in query.items():
        if isinstance(expected, dict) and set(expected) == {"$lte"}:
            actual = doc.get(key)
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if "." in key:
            head, tail = key.split(".", 1)
            items = doc.get(head) or []
            if not any(
                isinstance(item, dict) and item.get(tail) == expected
                for item in items
            ):
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


class _Db:
    def __init__(self, passenger_doc):
        snapshot = {
            "flightNumber": FLIGHT_NUMBER,
            "origin": ORIGIN,
            "departureDate": DEPARTURE_DATE,
            "snapshotType": "passenger_list",
            "sequenceNumber": 2,
            "capturedAt": passenger_doc.get("fetchedAt", ""),
            "data": copy.deepcopy(passenger_doc),
        }
        self._collections = {
            "passenger_list": _Collection([passenger_doc]),
            "reservations": _Collection([]),
            "snapshots": _Collection([snapshot]),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _manifest_from_merged_calls():
    merged = _merge_display_code_calls(_booked_response(), _checked_in_response())
    # The duplicate booked passenger must still be the copy that carried
    # the onward flight. Merge keeps that dict; conversion has to read it.
    raw_passengers = _extract_passenger_info_list(merged)
    hassan = next(
        p for p in raw_passengers
        if (p.get("Name_Details") or {}).get("LastName") == "HASSAN"
    )
    assert hassan.get("OutboundFlight") == "K012"
    assert hassan.get("PrimaryAirlineCode") == "GF"
    assert hassan.get("Seat") == "12A"
    return _convert(merged)


def _assert_onward_rows(passengers):
    rows = _by_pnr(passengers)
    hassan = rows["AAA111"]
    assert hassan["lastName"] == "HASSAN"
    assert hassan["seat"] == "12A"
    assert hassan["outboundFlight"] == "K012"
    assert hassan["primaryAirlineCode"] == "GF"

    noor = rows["BBB222"]
    assert noor["seat"] == "14C"
    assert noor["outboundFlight"] == ""
    assert noor["primaryAirlineCode"] == ""

    khalid = rows["DDD444"]
    assert khalid["outboundFlight"] == ""
    assert khalid["primaryAirlineCode"] == "BA"

    farid = rows["EEE555"]
    assert farid["outboundFlight"] == ""
    assert farid["primaryAirlineCode"] == ""

    sara = rows["CCC333"]
    assert sara["seat"] == "18B"
    assert sara["outboundFlight"] == "221"
    assert sara["primaryAirlineCode"] == ""


def test_merged_passenger_list_stores_onward_flight():
    doc = _manifest_from_merged_calls()
    _assert_onward_rows(doc["passengers"])


def test_live_and_historical_manifest_return_onward_flight():
    doc = _manifest_from_merged_calls()
    db = _Db(doc)

    with patch("backend.api.routes.passengers.get_db", lambda: db):
        _assert_live_and_historical()


def _assert_live_and_historical():
    live = get_passengers(
        FLIGHT_NUMBER,
        origin=ORIGIN,
        date=DEPARTURE_DATE,
        snapshot_sequence=None,
    )
    _assert_onward_rows(live["passengers"])

    historical = get_passengers(
        FLIGHT_NUMBER,
        origin=ORIGIN,
        date=DEPARTURE_DATE,
        snapshot_sequence=2,
    )
    _assert_onward_rows(historical["passengers"])

    by_pnr = get_passenger_by_pnr(FLIGHT_NUMBER, "AAA111", date=DEPARTURE_DATE)
    assert len(by_pnr["passengers"]) == 1
    assert by_pnr["passengers"][0]["outboundFlight"] == "K012"
    assert by_pnr["passengers"][0]["primaryAirlineCode"] == "GF"
    assert by_pnr["passengers"][0]["seat"] == "12A"


def test_prefixed_design_response_stores_onward_flight():
    """Vendor design sample uses a v4 prefix on the same elements."""
    xml = _envelope(
        _passenger_xml(
            "RAHMAN", "YUSUF", "FFF666", "PAX-RAHMAN", "4C",
            prefix="v4:",
            primary_airline="K0",
            outbound_flight="334",
        ),
        prefixed=True,
    )
    doc = _convert(_parse_passenger_list(xml))
    row = _by_pnr(doc["passengers"])["FFF666"]
    assert row["seat"] == "4C"
    assert row["pnr"] == "FFF666"
    assert row["outboundFlight"] == "334"
    assert row["primaryAirlineCode"] == "K0"


if __name__ == "__main__":
    test_merged_passenger_list_stores_onward_flight()
    test_live_and_historical_manifest_return_onward_flight()
    test_prefixed_design_response_stores_onward_flight()
    print("PASS")
