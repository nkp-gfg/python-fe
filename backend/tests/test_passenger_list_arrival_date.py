"""Overnight passenger-list arrival dates must survive conversion and the API.

Sabre GetPassengerListRS carries Scheduled_ArrivalDate and
Estimated_ArrivalDate on ItineraryInfo/DepartureArrival_Dates, beside
ArrivalTime. convert_passenger_list must store those two dates on the
passenger-list document, and GET /flights/{flight}/passengers must return
them. The dates are the values Sabre sent. They are not derived from the
departure date, from each other, or from comparing the clocks.

Stored names match the departure fields already on that document:
scheduledArrival and estimatedArrival are the date strings, and arrivalTime
stays the clock.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.passengers import get_passengers
from backend.api.snapshot_versioning import get_snapshot_data_as_of
from backend.feeder.converter import convert_passenger_list
from backend.feeder.runner import (
    _extract_passenger_info_list,
    _pax_dedup_key,
    _set_passenger_info_list,
)
from backend.feeder import storage
from backend.sabre.client import SabreClient

SAMPLE_XML = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "GetPassengerListRS_GF2006_LHR_2026-03-18.xml",
)

AIRLINE = "GF"
FLIGHT = "2006"
ORIGIN = "LHR"
DEPARTURE_DATE = "2026-03-18"


class _MemoryCollection:
    """insert_one / find_one with equality and $lte. No field rewriting."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        return type("InsertResult", (), {"inserted_id": len(self.docs)})()

    def find_one(self, query=None, sort=None, projection=None):
        matched = [doc for doc in self.docs if _matches(doc, query or {})]
        if sort:
            for key, direction in reversed(list(sort)):
                matched.sort(
                    key=lambda doc, key=key: _sort_key(doc.get(key)),
                    reverse=int(direction) < 0,
                )
        if not matched:
            return None
        return copy.deepcopy(matched[0])


class _MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _MemoryCollection()
        return self._collections[name]


def _matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
        elif actual != expected:
            return False
    return True


def _sort_key(value):
    if value is None:
        return (0, "")
    return (1, value)


def _parse_passenger_list(xml_text):
    """Same parse and body extract get_passenger_list uses after the HTTP call."""
    parsed = SabreClient._parse_xml(xml_text)
    client = SabreClient.__new__(SabreClient)
    return client._extract_body(parsed, "GetPassengerListRS")


def _load_sample_body():
    with open(SAMPLE_XML, encoding="utf-8") as handle:
        return _parse_passenger_list(handle.read())


def _itinerary_dates(raw):
    itin = raw.get("ItineraryInfo", {})
    return itin.get("DepartureArrival_Dates", {})


def _soap(date_elements):
    """Minimal GetPassengerList envelope. date_elements is tag -> text or None to omit."""
    parts = []
    for tag, text in date_elements:
        if text is None:
            continue
        parts.append(f"<{tag}>{text}</{tag}>")
    dates = "".join(parts)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2006</Flight>
          <DepartureDate>2026-03-18</DepartureDate>
          <Origin>LHR</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
        <DepartureArrival_Dates>{dates}</DepartureArrival_Dates>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>ABDULLA</LastName>
            <FirstName>JABER MR</FirstName>
          </Name_Details>
          <PNRLocator>IHMHOG</PNRLocator>
          <PassengerID>P1</PassengerID>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _convert(raw):
    return convert_passenger_list(raw, AIRLINE, FLIGHT, DEPARTURE_DATE, ORIGIN)


def _assert_arrival_dates(doc, scheduled, estimated, arrival_time, departure_time):
    """Compare only the itinerary clocks. Do not dump the rest of the document."""
    actual = {
        "scheduledDeparture": doc.get("scheduledDeparture", "<missing>"),
        "estimatedDeparture": doc.get("estimatedDeparture", "<missing>"),
        "departureTime": doc.get("departureTime", "<missing>"),
        "scheduledArrival": doc.get("scheduledArrival", "<missing>"),
        "estimatedArrival": doc.get("estimatedArrival", "<missing>"),
        "arrivalTime": doc.get("arrivalTime", "<missing>"),
        "departureDate": doc.get("departureDate", "<missing>"),
    }
    assert actual["departureDate"] == DEPARTURE_DATE, actual
    assert actual["scheduledDeparture"] == DEPARTURE_DATE, actual
    assert actual["departureTime"] == departure_time, actual
    assert actual["arrivalTime"] == arrival_time, actual
    assert actual["scheduledArrival"] == scheduled, actual
    assert actual["estimatedArrival"] == estimated, actual
    # A reader who pairs the arrival clock with the departure date must be
    # able to use the stored arrival date instead. The arrival date is the
    # Sabre value, including when that value is the departure date itself.
    if scheduled and arrival_time and departure_time:
        departure_stamp = f"{DEPARTURE_DATE}T{departure_time}"
        arrival_stamp = f"{scheduled}T{arrival_time}"
        if scheduled > DEPARTURE_DATE:
            assert arrival_stamp > departure_stamp, actual
        elif scheduled == DEPARTURE_DATE:
            assert arrival_stamp > departure_stamp or arrival_time > departure_time, actual


def _bind_db():
    db = _MemoryDB()
    storage.init_db(db)
    import backend.api.routes.passengers as passengers

    passengers.get_db = lambda: db
    return db


def _store_and_read(doc, snapshot_sequence=None):
    _bind_db()
    storage.store_passenger_list(doc)
    storage.store_snapshot(
        request_id="req-arrival",
        airline=AIRLINE,
        flight_number=FLIGHT,
        origin=ORIGIN,
        departure_date=DEPARTURE_DATE,
        snapshot_type="passenger_list",
        data=doc,
    )
    return get_passengers(
        FLIGHT,
        origin=ORIGIN,
        date=DEPARTURE_DATE,
        snapshot_sequence=snapshot_sequence,
    )


def test_parsed_sample_still_contains_next_day_arrival_dates():
    """The drop is in conversion, after the SOAP body has been extracted."""
    raw = _load_sample_body()
    dates = _itinerary_dates(raw)
    assert dates.get("Scheduled_DepartureDate") == "2026-03-18"
    assert dates.get("Estimated_DepartureDate") == "2026-03-18"
    assert dates.get("DepartureTime") == "21:50"
    assert dates.get("Scheduled_ArrivalDate") == "2026-03-19"
    assert dates.get("Estimated_ArrivalDate") == "2026-03-19"
    assert dates.get("ArrivalTime") == "07:20"


def test_convert_passenger_list_stores_sample_arrival_dates():
    raw = _load_sample_body()
    doc = _convert(raw)
    _assert_arrival_dates(
        doc,
        scheduled="2026-03-19",
        estimated="2026-03-19",
        arrival_time="07:20",
        departure_time="21:50",
    )


def test_get_passengers_returns_sample_arrival_dates():
    raw = _load_sample_body()
    doc = _convert(raw)
    live = _store_and_read(doc)
    assert "_raw" not in live
    _assert_arrival_dates(
        live,
        scheduled="2026-03-19",
        estimated="2026-03-19",
        arrival_time="07:20",
        departure_time="21:50",
    )


def test_get_passengers_snapshot_returns_sample_arrival_dates():
    raw = _load_sample_body()
    doc = _convert(raw)
    historical = _store_and_read(doc, snapshot_sequence=1)
    assert historical.get("snapshotSequenceNumber") == 1
    assert "_raw" not in historical
    _assert_arrival_dates(
        historical,
        scheduled="2026-03-19",
        estimated="2026-03-19",
        arrival_time="07:20",
        departure_time="21:50",
    )
    # The snapshot helper itself returns the stored document, not a rebuilt one.
    db = storage.get_db()
    as_of = get_snapshot_data_as_of(
        db,
        flight_number=FLIGHT,
        snapshot_type="passenger_list",
        snapshot_sequence=1,
        origin=ORIGIN,
        departure_date=DEPARTURE_DATE,
    )
    _assert_arrival_dates(
        as_of,
        scheduled="2026-03-19",
        estimated="2026-03-19",
        arrival_time="07:20",
        departure_time="21:50",
    )


def test_merge_then_convert_keeps_itinerary_arrival_dates():
    """Runner merge appends passengers onto the first GetPassengerList body."""
    merged = _load_sample_body()
    extra = {
        "PassengerInfoList": {
            "PassengerInfo": {
                "PNRLocator": "ZZTEST",
                "Name_Details": {"LastName": "EXTRA", "FirstName": "PAX"},
                "PassengerID": "P-EXTRA",
            }
        }
    }
    base_pax = _extract_passenger_info_list(merged)
    existing = {_pax_dedup_key(pax) for pax in base_pax}
    for pax in _extract_passenger_info_list(extra):
        if _pax_dedup_key(pax) not in existing:
            base_pax.append(pax)
            existing.add(_pax_dedup_key(pax))
    _set_passenger_info_list(merged, base_pax)
    doc = _convert(merged)
    assert doc["totalPassengers"] == len(base_pax)
    assert any(pax.get("pnr") == "ZZTEST" for pax in doc["passengers"])
    _assert_arrival_dates(
        doc,
        scheduled="2026-03-19",
        estimated="2026-03-19",
        arrival_time="07:20",
        departure_time="21:50",
    )


def test_scheduled_and_estimated_arrival_dates_stay_independent():
    raw = _parse_passenger_list(_soap([
        ("Scheduled_DepartureDate", "2026-03-18"),
        ("Estimated_DepartureDate", "2026-03-18"),
        ("DepartureTime", "21:50"),
        ("Scheduled_ArrivalDate", "2026-03-19"),
        ("Estimated_ArrivalDate", "2026-03-20"),
        ("ArrivalTime", "07:20"),
    ]))
    dates = _itinerary_dates(raw)
    assert dates.get("Scheduled_ArrivalDate") == "2026-03-19"
    assert dates.get("Estimated_ArrivalDate") == "2026-03-20"
    doc = _convert(raw)
    _assert_arrival_dates(
        doc,
        scheduled="2026-03-19",
        estimated="2026-03-20",
        arrival_time="07:20",
        departure_time="21:50",
    )
    response = _store_and_read(doc)
    _assert_arrival_dates(
        response,
        scheduled="2026-03-19",
        estimated="2026-03-20",
        arrival_time="07:20",
        departure_time="21:50",
    )


def test_same_day_arrival_date_is_not_rolled_to_the_next_day():
    raw = _parse_passenger_list(_soap([
        ("Scheduled_DepartureDate", "2026-03-18"),
        ("Estimated_DepartureDate", "2026-03-18"),
        ("DepartureTime", "08:00"),
        ("Scheduled_ArrivalDate", "2026-03-18"),
        ("Estimated_ArrivalDate", "2026-03-18"),
        ("ArrivalTime", "11:00"),
    ]))
    doc = _convert(raw)
    _assert_arrival_dates(
        doc,
        scheduled="2026-03-18",
        estimated="2026-03-18",
        arrival_time="11:00",
        departure_time="08:00",
    )


def test_omitted_estimated_arrival_date_is_not_inferred():
    raw = _parse_passenger_list(_soap([
        ("Scheduled_DepartureDate", "2026-03-18"),
        ("Estimated_DepartureDate", "2026-03-18"),
        ("DepartureTime", "21:50"),
        ("Scheduled_ArrivalDate", "2026-03-19"),
        ("Estimated_ArrivalDate", None),
        ("ArrivalTime", "07:20"),
    ]))
    dates = _itinerary_dates(raw)
    assert dates.get("Scheduled_ArrivalDate") == "2026-03-19"
    assert "Estimated_ArrivalDate" not in dates
    doc = _convert(raw)
    _assert_arrival_dates(
        doc,
        scheduled="2026-03-19",
        estimated="",
        arrival_time="07:20",
        departure_time="21:50",
    )
