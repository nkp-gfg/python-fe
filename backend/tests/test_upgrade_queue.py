"""Upgrade queue must follow Sabre UpgradeCode, not only the text UPG.

These tests fail while the defect is present. They parse GetPassengerListRS
the same way the Sabre client does, normalize it with convert_passenger_list,
store that document, and call the standby-list and dashboard handlers.

A passenger is an upgrade when Sabre sent a non-empty UpgradeCode, or when
PriorityCode is UPG and UpgradeCode is absent. An empty PriorityCode does
not drop an upgrade. A staff standby code such as B3 does not keep an
UpgradeCode off the upgrade list, and the stored priority code stays intact.
The dashboard upgrade total uses that same set, counted once per passenger.

Not asserted here: snapshot_sequence loads the passenger document through
get_snapshot_data_as_of and then runs the same handlers. The flight tree
returns the cabin tree, not standbyUpgrade. Change detection already
classifies a stored upgradeCode and is not the queue the agents read.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.routes.flights import get_flight_dashboard
from backend.api.routes.passengers import get_standby_list
from backend.feeder.converter import _ensure_list, _strip_ns, convert_passenger_list
from backend.sabre.client import SabreClient

_SAMPLE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "sample_data", "sample_xmlfiles")
)

_GULF_AIR_LISTS = (
    "GetPassengerListRS_GF2006_LHR_2026-03-18.xml",
    "GetPassengerListRS_GF2056_DMM_2026-03-16.xml",
    "GetPassengerListRS_GF2130_DMM_2026-03-18.xml",
)


class _Collection:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self._docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc: doc.get(field) or "",
                    reverse=direction < 0,
                )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query, projection=None):
        return [
            copy.deepcopy(doc)
            for doc in self._docs
            if _matches(doc, query)
        ]


class _Database:
    def __init__(self, collections):
        self._collections = {
            name: list(docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return _Collection(self._collections.setdefault(name, []))


def _matches(doc, query):
    for key, expected in query.items():
        if doc.get(key) != expected:
            return False
    return True


def _parse_passenger_list(xml_text):
    """Run the client XML parse and SOAP body extract. No session is opened."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _load_sample(filename):
    path = os.path.join(_SAMPLE_DIR, filename)
    with open(path, encoding="utf-8") as handle:
        return _parse_passenger_list(handle.read())


def _pnr(passenger):
    locator = passenger.get("PNRLocator")
    if isinstance(locator, dict):
        return locator.get("#text", "")
    return str(passenger.get("PNRLocator", ""))


def _classify_manifest(body):
    """Read UpgradeCode and PriorityCode from the parsed Sabre body."""
    data = _strip_ns(body)
    passengers = _ensure_list(
        (data.get("PassengerInfoList") or {}).get("PassengerInfo", [])
    )
    upgrades = {}
    standby_only = set()
    ordinary = set()
    for passenger in passengers:
        name = passenger.get("Name_Details") or {}
        identity = (
            _pnr(passenger),
            name.get("LastName", ""),
            name.get("FirstName", ""),
        )
        priority = passenger.get("PriorityCode") or ""
        upgrade_code = passenger.get("UpgradeCode") or ""
        if upgrade_code or priority == "UPG":
            upgrades[identity] = {
                "priorityCode": priority,
                "upgradeCode": upgrade_code,
                "lineNumber": int(passenger.get("LineNumber") or 0),
            }
        elif priority:
            standby_only.add(identity)
        else:
            ordinary.add(identity)
    return upgrades, standby_only, ordinary


def _ingest(body):
    data = _strip_ns(body)
    itinerary = (data.get("ItineraryInfo") or {}).get("Itinerary") or {}
    flight_number = str(itinerary.get("Flight", ""))
    origin = itinerary.get("Origin", "")
    departure_date = itinerary.get("DepartureDate", "")
    document = convert_passenger_list(
        body,
        itinerary.get("Airline", ""),
        flight_number,
        departure_date,
        origin,
    )
    return flight_number, origin, departure_date, document


def _install_passenger_list(monkeypatch, document):
    database = _Database({"passenger_list": [document]})

    def get_db():
        return database

    monkeypatch.setattr("backend.api.routes.passengers.get_db", get_db)
    monkeypatch.setattr("backend.api.routes.flights.get_db", get_db)
    from backend.api.routes import flights

    flights._dashboard_cache.clear()
    return database


def _identity(entry):
    return (entry.get("pnr", ""), entry.get("lastName", ""), entry.get("firstName", ""))


def _assert_upgrade_queue(flight_number, origin, departure_date, upgrades, standby_only, ordinary):
    standby = get_standby_list(
        flight_number,
        origin=origin,
        date=departure_date,
        snapshot_sequence=None,
    )
    upgrade_entries = standby["upgrade"]["passengers"]
    standby_entries = standby["standby"]["passengers"]
    upgrade_ids = {_identity(entry) for entry in upgrade_entries}
    standby_ids = {_identity(entry) for entry in standby_entries}

    assert upgrade_ids == set(upgrades), (
        f"{flight_number} upgrade queue is {sorted(upgrade_ids)}, "
        f"expected {sorted(upgrades)}"
    )
    assert standby["upgrade"]["total"] == len(upgrades)
    assert standby_only <= standby_ids
    assert standby_ids.isdisjoint(ordinary)
    # Priority UPG is an upgrade code, not a second standby row.
    upg_priority = {
        identity
        for identity, info in upgrades.items()
        if info["priorityCode"] == "UPG"
    }
    assert standby_ids.isdisjoint(upg_priority)
    # Revenue upgrades have no standby priority and must not be filed there.
    upgrade_only = {
        identity
        for identity, info in upgrades.items()
        if not info["priorityCode"]
    }
    assert standby_ids.isdisjoint(upgrade_only)

    for entry in upgrade_entries:
        expected = upgrades[_identity(entry)]
        assert entry.get("upgradeCode", "") == expected["upgradeCode"]
        assert entry.get("priorityCode", "") == expected["priorityCode"]

    line_numbers = [entry.get("lineNumber", 0) for entry in upgrade_entries]
    assert line_numbers == sorted(line_numbers)

    dashboard = get_flight_dashboard(
        flight_number,
        origin=origin,
        date=departure_date,
        snapshot_sequence=None,
    )
    upgrade_total = dashboard["insights"]["standbyUpgrade"]["upgradeTotal"]
    assert upgrade_total == len(upgrades), (
        f"{flight_number} dashboard upgradeTotal is {upgrade_total}, "
        f"expected {len(upgrades)}"
    )


def test_gulf_air_manifests_keep_upgrade_codes_on_the_upgrade_queue(monkeypatch):
    """Committed Gulf Air lists: J06/J07 with an empty or staff priority, and UPG."""
    for filename in _GULF_AIR_LISTS:
        body = _load_sample(filename)
        upgrades, standby_only, ordinary = _classify_manifest(body)
        assert upgrades, filename
        flight_number, origin, departure_date, document = _ingest(body)
        _install_passenger_list(monkeypatch, document)
        _assert_upgrade_queue(
            flight_number,
            origin,
            departure_date,
            upgrades,
            standby_only,
            ordinary,
        )


def test_upgrade_code_and_priority_upg_are_both_upgrade_requests(monkeypatch):
    """Same rule on a manifest that mixes the Sabre shapes in one flight."""
    xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>9999</Flight>
          <DepartureDate>2026-04-01</DepartureDate>
          <Origin>BAH</Origin>
          <Destination>LHR</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        <PassengerInfo>
          <Name_Details>
            <LastName>REVENUE</LastName>
            <FirstName>WAITING</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">REV001</PNRLocator>
          <LineNumber>1</LineNumber>
          <UpgradeCode>J07</UpgradeCode>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <PassengerType>P</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details>
            <LastName>STAFF</LastName>
            <FirstName>BOTH</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">STF002</PNRLocator>
          <LineNumber>2</LineNumber>
          <PriorityCode>B3</PriorityCode>
          <UpgradeCode>J06</UpgradeCode>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <PassengerType>P</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details>
            <LastName>GATE</LastName>
            <FirstName>UPGONLY</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">UPG003</PNRLocator>
          <LineNumber>3</LineNumber>
          <PriorityCode>UPG</PriorityCode>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <PassengerType>P</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details>
            <LastName>STAFF</LastName>
            <FirstName>STANDBY</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">STF004</PNRLocator>
          <LineNumber>4</LineNumber>
          <PriorityCode>B01</PriorityCode>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <PassengerType>S</PassengerType>
          <Indicators><Indicator>NotBoarded</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
        <PassengerInfo>
          <Name_Details>
            <LastName>PLAIN</LastName>
            <FirstName>BOOKED</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">PLN005</PNRLocator>
          <LineNumber>5</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <PassengerType>P</PassengerType>
          <Indicators><Indicator>Revenue</Indicator></Indicators>
          <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
        </PassengerInfo>
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""
    body = _parse_passenger_list(xml_text)
    upgrades, standby_only, ordinary = _classify_manifest(body)
    assert set(upgrades) == {
        ("REV001", "REVENUE", "WAITING"),
        ("STF002", "STAFF", "BOTH"),
        ("UPG003", "GATE", "UPGONLY"),
    }
    assert upgrades[("REV001", "REVENUE", "WAITING")]["upgradeCode"] == "J07"
    assert upgrades[("REV001", "REVENUE", "WAITING")]["priorityCode"] == ""
    assert upgrades[("STF002", "STAFF", "BOTH")] == {
        "priorityCode": "B3",
        "upgradeCode": "J06",
        "lineNumber": 2,
    }
    assert standby_only == {("STF004", "STAFF", "STANDBY")}
    assert ordinary == {("PLN005", "PLAIN", "BOOKED")}

    flight_number, origin, departure_date, document = _ingest(body)
    _install_passenger_list(monkeypatch, document)
    _assert_upgrade_queue(
        flight_number,
        origin,
        departure_date,
        upgrades,
        standby_only,
        ordinary,
    )
