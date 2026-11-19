"""The passenger-tree headline must be souls actually on board.

These tests follow the path the dashboard runs: Sabre SOAP XML is parsed with
the client's parser, normalized by the feeder converters, stored as the flight
status and passenger-list documents, then returned by the dashboard and tree
routes. They fail while the visible tree root labeled "Passengers on Board"
is the full manifest (every passenger plus every lap infant) plus every
jump-seat count. They pass when that headline is:

- passengers whose boarded flag is true
- lap infants of those boarded passengers
- jump seats whose in-use flag is true

The full manifest total stays on overview.totalSouls, which the workbench
labels separately from passengers on board. overview.soulsOnBoard stays the
boarded-passenger figure and does not add jump seats.

Cabin and business nodes are manifest cabin counts under cabin labels, so
they are not asserted here. Reporting sums of totalSouls outside the tree
are also left untested.
"""

import copy

from backend.api.routes import flights as flight_routes
from backend.api.routes.flights import get_flight_dashboard, get_flight_tree
from backend.feeder.converter import convert_flight_status, convert_passenger_list
from backend.sabre.client import SabreClient


FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"


class _Collection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find_one(self, query, projection=None, sort=None):
        for doc in self.docs:
            if _matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query, projection=None):
        return [copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)]


class _Db(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _Collection([])
        return dict.__getitem__(self, name)


def _matches(doc, query):
    for key, expected in query.items():
        if doc.get(key) != expected:
            return False
    return True


def _passenger_xml(passengers):
    body = "\n".join(passengers)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS>
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>{FLIGHT}</Flight>
          <DepartureDate>{DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DXB</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passenger(*, last, first, pnr, passenger_id, cabin, boarded, checked_in,
               edit_codes, revenue=True, passenger_type="F"):
    indicators = []
    if checked_in or boarded:
        indicators.append("CheckedIn")
    if boarded:
        indicators.append("Boarded")
    if revenue:
        indicators.append("Revenue")
    indicator_xml = "".join(f"<Indicator>{item}</Indicator>" for item in indicators)
    edit_xml = "".join(f"<EditCode>{code}</EditCode>" for code in edit_codes)
    checked = "true" if checked_in or boarded else "false"
    board = "true" if boarded else "false"
    return f"""
    <PassengerInfo>
      <Name_Details>
        <LastName>{last}</LastName>
        <FirstName>{first}</FirstName>
      </Name_Details>
      <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
      <PassengerID>{passenger_id}</PassengerID>
      <BookingClass>Y</BookingClass>
      <Cabin>{cabin}</Cabin>
      <PassengerType>{passenger_type}</PassengerType>
      <Indicators>{indicator_xml}</Indicators>
      <CheckIn_Info>
        <CheckInStatus>{checked}</CheckInStatus>
      </CheckIn_Info>
      <Boarding_Info>
        <BoardStatus>{board}</BoardStatus>
      </Boarding_Info>
      <EditCodeList>{edit_xml}</EditCodeList>
    </PassengerInfo>
    """


def _flight_xml(status, cockpit, cockpit_in_use, cabin, cabin_in_use):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:ACS_FlightDetailRS xmlns:ns3="http://services.sabre.com/ACS/BSO/flightDetail/v3">
      <ItineraryResponseList>
        <ItineraryInfoResponse>
          <Airline>GF</Airline>
          <Flight>{FLIGHT}</Flight>
          <Origin>{ORIGIN}</Origin>
          <DepartureDate>{DATE}</DepartureDate>
          <FlightStatus>{status}</FlightStatus>
          <DepartureGate>A1</DepartureGate>
        </ItineraryInfoResponse>
      </ItineraryResponseList>
      <LegInfoList>
        <LegInfo><City>{ORIGIN}</City></LegInfo>
        <LegInfo><City>DXB</City></LegInfo>
      </LegInfoList>
      <JumpSeat>
        <Cockpit inUse="{str(cockpit_in_use).lower()}">{cockpit}</Cockpit>
        <Cabin inUse="{str(cabin_in_use).lower()}">{cabin}</Cabin>
      </JumpSeat>
    </ns3:ACS_FlightDetailRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_body(xml_text, response_key):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, response_key)


def _boarding_manifest():
    """Six manifest records, four of whom are not a full on-board headcount.

    Boarded souls: ALI plus lap infant (2), NOOR (1), CREW (1) = 4.
    Manifest souls: six records plus two lap infants = 8.
    SARA is checked in and not boarded. OMAR is not checked in.
    HADI is checked in, not boarded, and has a lap infant who is also not on board.
    """
    passengers = [
        _passenger(last="ALI", first="A", pnr="AAA111", passenger_id="P1",
                   cabin="Y", boarded=True, checked_in=True, edit_codes=["INF"]),
        _passenger(last="SARA", first="B", pnr="BBB222", passenger_id="P2",
                   cabin="Y", boarded=False, checked_in=True, edit_codes=["ET"]),
        _passenger(last="OMAR", first="C", pnr="CCC333", passenger_id="P3",
                   cabin="Y", boarded=False, checked_in=False, edit_codes=["CHD"]),
        _passenger(last="NOOR", first="D", pnr="DDD444", passenger_id="P4",
                   cabin="J", boarded=True, checked_in=True, edit_codes=["ET"]),
        _passenger(last="HADI", first="E", pnr="EEE555", passenger_id="P5",
                   cabin="J", boarded=False, checked_in=True, edit_codes=["INF"]),
        _passenger(last="CREW", first="F", pnr="FFF666", passenger_id="P6",
                   cabin="Y", boarded=True, checked_in=True, edit_codes=["ET"],
                   revenue=False, passenger_type="E"),
    ]
    return passengers, 4, 8


def _load(monkeypatch, *, status, cockpit, cockpit_in_use, cabin, cabin_in_use):
    passenger_xml, boarded_souls, manifest_souls = _boarding_manifest()
    passenger_body = _parse_body(_passenger_xml(passenger_xml), "GetPassengerListRS")
    status_body = _parse_body(
        _flight_xml(status, cockpit, cockpit_in_use, cabin, cabin_in_use),
        "ns3:ACS_FlightDetailRS",
    )
    passenger_doc = convert_passenger_list(
        passenger_body, "GF", FLIGHT, DATE, ORIGIN)
    status_doc = convert_flight_status(
        status_body, "GF", FLIGHT, ORIGIN, DATE)
    db = _Db()
    db["passenger_list"] = _Collection([passenger_doc])
    db["flight_status"] = _Collection([status_doc])
    monkeypatch.setattr(flight_routes, "get_db", lambda: db)
    flight_routes._dashboard_cache.clear()
    return passenger_doc, status_doc, boarded_souls, manifest_souls


def _root(tree):
    matches = [node for node in tree["nodes"] if node["id"] == "root"]
    assert len(matches) == 1
    return matches[0]


def _dashboard():
    flight_routes._dashboard_cache.clear()
    return get_flight_dashboard(FLIGHT, ORIGIN, DATE, snapshot_sequence=None)


def _tree():
    flight_routes._dashboard_cache.clear()
    return get_flight_tree(FLIGHT, ORIGIN, DATE, snapshot_sequence=None)


def _assert_on_board_headline(dashboard, tree, boarded_souls, manifest_souls, in_use_jump_seats):
    expected = boarded_souls + in_use_jump_seats
    for payload in (dashboard["tree"], tree):
        root = _root(payload)
        assert root["display"] is True
        assert root["label"] == "Passengers on Board"
        assert root["value"] == expected, (
            f"on-board headline {root['value']} != boarded souls {boarded_souls} "
            f"+ in-use jump seats {in_use_jump_seats}"
        )
    assert dashboard["overview"]["soulsOnBoard"] == boarded_souls
    assert dashboard["overview"]["totalSouls"] == manifest_souls


def test_boarding_tree_counts_boarded_souls_and_in_use_jump_seats(monkeypatch):
    passenger_doc, status_doc, boarded_souls, manifest_souls = _load(
        monkeypatch,
        status="BOARDING",
        cockpit=2,
        cockpit_in_use=False,
        cabin=1,
        cabin_in_use=True,
    )
    assert passenger_doc["totalSouls"] == manifest_souls
    assert passenger_doc["totalPassengers"] == 6
    assert passenger_doc["infantCount"] == 2
    stored = {pax["lastName"]: pax for pax in passenger_doc["passengers"]}
    assert stored["ALI"]["isBoarded"] is True and stored["ALI"]["hasInfant"] is True
    assert stored["SARA"]["isCheckedIn"] is True and stored["SARA"]["isBoarded"] is False
    assert stored["OMAR"]["isChild"] is True and stored["OMAR"]["isBoarded"] is False
    assert stored["HADI"]["isBoarded"] is False and stored["HADI"]["hasInfant"] is True
    assert stored["CREW"]["isBoarded"] is True and stored["CREW"]["isRevenue"] is False
    assert status_doc["status"] == "BOARDING"

    dashboard = _dashboard()
    tree = _tree()
    _assert_on_board_headline(
        dashboard, tree, boarded_souls, manifest_souls, in_use_jump_seats=1)


def test_unused_jump_seats_are_excluded_from_passengers_on_board(monkeypatch):
    _passenger_doc, status_doc, boarded_souls, manifest_souls = _load(
        monkeypatch,
        status="BOARDING",
        cockpit=2,
        cockpit_in_use=False,
        cabin=3,
        cabin_in_use=False,
    )
    assert status_doc["status"] == "BOARDING"
    dashboard = _dashboard()
    tree = _tree()
    _assert_on_board_headline(
        dashboard, tree, boarded_souls, manifest_souls, in_use_jump_seats=0)
    assert dashboard["overview"]["soulsOnBoard"] < dashboard["overview"]["totalSouls"]
    assert _root(tree)["value"] == dashboard["overview"]["soulsOnBoard"]


def test_closed_flight_tree_uses_the_same_boarded_headline(monkeypatch):
    _passenger_doc, _status_doc, boarded_souls, manifest_souls = _load(
        monkeypatch,
        status="PDC",
        cockpit=1,
        cockpit_in_use=True,
        cabin=2,
        cabin_in_use=False,
    )
    dashboard = _dashboard()
    tree = _tree()
    _assert_on_board_headline(
        dashboard, tree, boarded_souls, manifest_souls, in_use_jump_seats=1)


def test_fully_boarded_flight_headline_matches_manifest_without_unused_seats(monkeypatch):
    passengers = [
        _passenger(last="ALI", first="A", pnr="AAA111", passenger_id="P1",
                   cabin="Y", boarded=True, checked_in=True, edit_codes=["INF"]),
        _passenger(last="NOOR", first="D", pnr="DDD444", passenger_id="P4",
                   cabin="J", boarded=True, checked_in=True, edit_codes=["ET"]),
    ]
    passenger_body = _parse_body(_passenger_xml(passengers), "GetPassengerListRS")
    status_body = _parse_body(
        _flight_xml("PDC", cockpit=2, cockpit_in_use=False, cabin=2, cabin_in_use=False),
        "ns3:ACS_FlightDetailRS",
    )
    passenger_doc = convert_passenger_list(passenger_body, "GF", FLIGHT, DATE, ORIGIN)
    status_doc = convert_flight_status(status_body, "GF", FLIGHT, ORIGIN, DATE)
    db = _Db()
    db["passenger_list"] = _Collection([passenger_doc])
    db["flight_status"] = _Collection([status_doc])
    monkeypatch.setattr(flight_routes, "get_db", lambda: db)
    flight_routes._dashboard_cache.clear()

    dashboard = _dashboard()
    tree = _tree()
    # Two boarded records plus one lap infant. Unused jump-seat capacity stays off the headline.
    _assert_on_board_headline(dashboard, tree, boarded_souls=3, manifest_souls=3, in_use_jump_seats=0)
    assert _root(tree)["value"] == dashboard["overview"]["totalSouls"]
