"""Ingestion must store Sabre named transfer lists and drive connection figures from them.

The operations board warns that connecting passengers may miss onward flights.
That warning is only true when the stored flight contains the named inbound
report (TRIN) and the named outbound report (TRON): connecting flight, class
counts, passenger name, and record locator. A passenger who is merely marked
thru on the check-in list is not a substitute for those reports.

These tests follow POST /flights/ingest with no credentials, which is the
route the product exposes. The Sabre HTTP exchange is replaced at the socket
boundary with the Trip_ReportsRS shape from the Trip Reports user guide
(ConnectingFlightEntry / Transferee). Session, flight detail, passenger list,
reservations, and schedule responses only exist so the feeder can reach the
trip-report calls. MongoDB is replaced with an in-memory collection because
store_trip_reports inserts the converted document unchanged.

The same run_feeder function serves the synchronous ingest route, the Celery
batch task (backend/tasks/ingestion.py), and the CLI entry point. The Celery
job bookkeeping and the CLI argument parser are not covered here.

frontend/components/dashboard/briefing-tab.tsx and
frontend/components/dashboard/decision-briefing.tsx only interpolate
insights.connectionRisk. They are not executed; the dashboard payload below
is the document they render.

Expected to fail until ingestion requests TRIN and TRON, stores both transfer
lists on the trip report the dashboard reads, and counts connection figures
from those lists.
"""

import os
import sys
import copy
import re
from contextlib import asynccontextmanager

# Placeholders only. Set before importing the app so a local .env cannot
# override them and the test cannot open a real Sabre, Mongo, Postgres, or Redis connection.
os.environ["SABRE_BASE_URL"] = "http://sabre.invalid"
os.environ["SABRE_CPAID"] = "PLACEHOLDER"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-not-a-secret"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "PLACE"
os.environ["SABRE_ORGANIZATION"] = "PLACEHOLDER"
os.environ["SABRE_DOMAIN"] = "PLACEHOLDER"
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["POSTGRES_URI"] = ""
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:9/unused"
os.environ["REDIS_URL"] = "redis://127.0.0.1:9/0"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.feeder import storage  # noqa: E402
from backend.sabre.client import SabreClient  # noqa: E402
from backend.api.routes import flights as flights_routes  # noqa: E402
from backend.api.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


ORIGIN = "BAH"
DESTINATION = "DXB"
FLIGHT = "131"
DEPARTURE_DATE = "2026-02-04"
# Onward flight leaves 35 minutes after this flight arrives (11:30).
ONWARD_DEPARTURE = "2026-02-04T12:05:00"
INBOUND_ARRIVAL = "2026-02-04T10:50:00"

# Guide sample travellers (Trip_Report_User_Guide.txt TRIN/TRON responses).
# SMITH is on the check-in list as thru and on neither transfer report.
THRU_PNR = "THRU01"
INBOUND_PNR = "ABCBCA"
OUTBOUND_PNR = "AAAAAA"
CANCELLED_PNR = "CANC01"


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class MemoryCollection:
    """Records documents the storage layer inserts. Equality filters only."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"id-{len(self.docs)}")
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def insert_many(self, docs):
        ids = []
        for doc in docs:
            ids.append(self.insert_one(doc).inserted_id)
        return _InsertManyResult(ids)

    def _matches(self, doc, query):
        for key, expected in (query or {}).items():
            if doc.get(key) != expected:
                return False
        return True

    def _sorted(self, docs, sort):
        ordered = list(docs)
        if not sort:
            return ordered
        for key, direction in reversed(list(sort)):
            ordered.sort(
                key=lambda doc, field=key: (doc.get(field) is None, doc.get(field)),
                reverse=direction < 0,
            )
        return ordered

    def find_one(self, query=None, *args, **kwargs):
        projection = kwargs.get("projection")
        sort = kwargs.get("sort")
        if args and isinstance(args[0], dict) and projection is None and sort is None:
            projection = args[0]
        matches = self._sorted(
            [doc for doc in self.docs if self._matches(doc, query)],
            sort,
        )
        if not matches:
            return None
        return copy.deepcopy(matches[0])

    def find(self, query=None, *args, **kwargs):
        sort = kwargs.get("sort")
        matches = self._sorted(
            [copy.deepcopy(doc) for doc in self.docs if self._matches(doc, query)],
            sort,
        )
        return _Cursor(matches)

    def update_one(self, query, update, upsert=False):
        matches = [doc for doc in self.docs if self._matches(doc, query)]
        if not matches:
            if not upsert:
                return
            doc = {"_id": f"id-{len(self.docs)}"}
            doc.update(copy.deepcopy(update.get("$setOnInsert", {})))
            doc.update(copy.deepcopy(update.get("$set", {})))
            for key, amount in update.get("$inc", {}).items():
                doc[key] = doc.get(key, 0) + amount
            self.docs.append(doc)
            return
        doc = matches[0]
        for key, value in update.get("$set", {}).items():
            doc[key] = copy.deepcopy(value)
        for key, amount in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + amount

    def create_index(self, *args, **kwargs):
        return None

    def drop_index(self, name):
        return None

    def index_information(self):
        return {}


class MemoryDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MemoryCollection()
        return self._collections[name]

    def command(self, name):
        return {"ok": 1}


def _envelope(inner_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap-env:Body>{inner_xml}</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _passenger_list_xml():
    # Three manifest rows. Only SMITH is thru. NOWAK and ROSSI are the transfer
    # passengers and are not marked thru, so a thru-only count leaves them off.
    return _envelope(f"""
        <GetPassengerListRS>
          <ItineraryInfo>
            <Itinerary>
              <Destination>{DESTINATION}</Destination>
            </Itinerary>
          </ItineraryInfo>
          <PassengerInfoList>
            <PassengerInfo>
              <Name_Details><LastName>SMITH</LastName><FirstName>ANN</FirstName></Name_Details>
              <PNRLocator>{THRU_PNR}</PNRLocator>
              <ThruIndicator>true</ThruIndicator>
              <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
              <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
            </PassengerInfo>
            <PassengerInfo>
              <Name_Details><LastName>ROSSI</LastName><FirstName>DANIELA</FirstName></Name_Details>
              <PNRLocator>{INBOUND_PNR}</PNRLocator>
              <ThruIndicator>false</ThruIndicator>
              <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
              <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
            </PassengerInfo>
            <PassengerInfo>
              <Name_Details><LastName>NOWAK</LastName><FirstName>ADAM</FirstName></Name_Details>
              <PNRLocator>{OUTBOUND_PNR}</PNRLocator>
              <ThruIndicator>false</ThruIndicator>
              <CheckIn_Info><CheckInStatus>false</CheckInStatus></CheckIn_Info>
              <Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>
            </PassengerInfo>
          </PassengerInfoList>
        </GetPassengerListRS>
    """)


def _flight_status_xml():
    return _envelope(f"""
        <ns3:ACS_FlightDetailRS>
          <ItineraryResponseList>
            <ItineraryInfoResponse>
              <FlightStatus>OPEN</FlightStatus>
              <ScheduledArrivalDate>{DEPARTURE_DATE}</ScheduledArrivalDate>
              <ScheduledArrivalTime>11:30:00</ScheduledArrivalTime>
            </ItineraryInfoResponse>
          </ItineraryResponseList>
          <LegInfoList>
            <LegInfo><City>{ORIGIN}</City></LegInfo>
            <LegInfo><City>{DESTINATION}</City></LegInfo>
          </LegInfoList>
        </ns3:ACS_FlightDetailRS>
    """)


def _mlx_xml():
    return _envelope(f"""
        <Trip_ReportsRS>
          <PassengerList>
            <Passenger>
              <Name><LastName>CANCELLED</LastName><FirstName>PAX</FirstName></Name>
              <PNRLocator>{CANCELLED_PNR}</PNRLocator>
            </Passenger>
          </PassengerList>
        </Trip_ReportsRS>
    """)


def _empty_trip_xml():
    # xmltodict turns an empty element into None. The converter reads
    # PassengerList as a dict, so keep a child that is not a Passenger.
    return _envelope(
        "<Trip_ReportsRS><PassengerList><Placeholder>0</Placeholder></PassengerList></Trip_ReportsRS>"
    )


def _trin_xml():
    # Shape from Trip_Report_User_Guide.txt TRIN response (ConnectingFlightEntry INBOUND).
    return _envelope(f"""
        <Trip_ReportsRS>
          <Success>Success</Success>
          <Report>
            <ReportName>TRIN</ReportName>
            <ReportFormat>TRIN</ReportFormat>
            <ReportResult>
              <Header>
                <AirlineCode>GF</AirlineCode>
                <FlightNumber>{FLIGHT}</FlightNumber>
                <DepartureDateTime>{DEPARTURE_DATE}T08:00:00</DepartureDateTime>
                <DisplayType>TRIN</DisplayType>
                <InboundAirport>{ORIGIN}</InboundAirport>
              </Header>
              <Details>
                <Entry>
                  <ConnectingFlightEntry Type="INBOUND">
                    <AirlineCode>GF</AirlineCode>
                    <FlightNumber>536</FlightNumber>
                    <ArrivalDateTime>{INBOUND_ARRIVAL}</ArrivalDateTime>
                    <TotalPassengers>1</TotalPassengers>
                    <ClassList>
                      <Class Name="T">1</Class>
                    </ClassList>
                    <ConnectingAirport Name="SCL">
                      <ClassList><Class Name="T">1</Class></ClassList>
                      <Transferees>
                        <Transferee>
                          <Name>ROSSI/DANIELA</Name>
                          <Locator>{INBOUND_PNR}</Locator>
                          <ClassOfService>T</ClassOfService>
                          <NumberInParty>1</NumberInParty>
                          <NumberOfPassengers>1</NumberOfPassengers>
                        </Transferee>
                      </Transferees>
                    </ConnectingAirport>
                  </ConnectingFlightEntry>
                </Entry>
              </Details>
            </ReportResult>
          </Report>
        </Trip_ReportsRS>
    """)


def _tron_xml():
    # Shape from Trip_Report_User_Guide.txt TRON response (ConnectingFlightEntry OUTBOUND).
    return _envelope(f"""
        <Trip_ReportsRS>
          <Success>Success</Success>
          <Report>
            <ReportName>TRON</ReportName>
            <ReportFormat>TRON</ReportFormat>
            <ReportResult>
              <Header>
                <AirlineCode>GF</AirlineCode>
                <FlightNumber>{FLIGHT}</FlightNumber>
                <ArrivalDateTime>{DEPARTURE_DATE}T11:30:00</ArrivalDateTime>
                <DisplayType>TRON</DisplayType>
                <OutboundAirport>{DESTINATION}</OutboundAirport>
              </Header>
              <Details>
                <Entry>
                  <ConnectingFlightEntry Type="OUTBOUND">
                    <AirlineCode>GF</AirlineCode>
                    <FlightNumber>2462</FlightNumber>
                    <DepartureDateTime>{ONWARD_DEPARTURE}</DepartureDateTime>
                    <TotalPassengers>1</TotalPassengers>
                    <ClassList>
                      <Class Name="B">1</Class>
                    </ClassList>
                    <ConnectingAirport Name="MIA">
                      <ClassList><Class Name="B">1</Class></ClassList>
                      <Transferees>
                        <Transferee>
                          <Name>NOWAK/ADAM</Name>
                          <Locator>{OUTBOUND_PNR}</Locator>
                          <ClassOfService>B</ClassOfService>
                          <NumberInParty>1</NumberInParty>
                          <NumberOfPassengers>1</NumberOfPassengers>
                        </Transferee>
                      </Transferees>
                    </ConnectingAirport>
                  </ConnectingFlightEntry>
                </Entry>
              </Details>
            </ReportResult>
          </Report>
        </Trip_ReportsRS>
    """)


def _trip_response(request_body):
    """Return a named transfer list only when the request asks for it correctly.

    TRIN needs ReportFormat TRIN and InboundAirport of the board point.
    TRON needs ReportFormat TRON and OutboundAirport of the off point.
    A DepartureAirport-only template, or ReportFormat X, receives an empty list.
    """
    if "<ReportName>MLX</ReportName>" in request_body:
        return _mlx_xml()
    if "<ReportName>MLC</ReportName>" in request_body:
        return _empty_trip_xml()
    if (
        "<ReportName>TRIN</ReportName>" in request_body
        and "<ReportFormat>TRIN</ReportFormat>" in request_body
        and f"<InboundAirport>{ORIGIN}</InboundAirport>" in request_body
    ):
        return _trin_xml()
    if (
        "<ReportName>TRON</ReportName>" in request_body
        and "<ReportFormat>TRON</ReportFormat>" in request_body
        and f"<OutboundAirport>{DESTINATION}</OutboundAirport>" in request_body
    ):
        return _tron_xml()
    return _empty_trip_xml()


def _sabre_post(captured):
    def _post(url, data=None, headers=None, timeout=None):
        body = data.decode() if isinstance(data, bytes) else (data or "")
        action = (headers or {}).get("SOAPAction", "")
        captured.append({"action": action, "body": body, "url": url})
        if action == "SessionCreateRQ":
            xml = (
                '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
                "<soap-env:Header><wsse:Security>"
                "<wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>"
                "</wsse:Security></soap-env:Header>"
                "<soap-env:Body><SessionCreateRS/></soap-env:Body>"
                "</soap-env:Envelope>"
            )
        elif action == "ACS_FlightDetailRQ":
            xml = _flight_status_xml()
        elif action == "GetPassengerListRQ":
            xml = _passenger_list_xml()
        elif action == "Trip_SearchRQ":
            xml = _envelope('<Trip_SearchRS><ReservationsList NumberResults="0"/></Trip_SearchRS>')
        elif action == "Trip_ReportsRQ":
            xml = _trip_response(body)
        elif action == "VerifyFlightDetailsLLSRQ":
            xml = _envelope(
                "<VerifyFlightDetailsRS><ApplicationResults status=\"Incomplete\">"
                "<Error><SystemSpecificResults><Message>schedule not used</Message>"
                "</SystemSpecificResults></Error></ApplicationResults></VerifyFlightDetailsRS>"
            )
        else:
            xml = _envelope("<SessionCloseRS/>")

        class _Response:
            status_code = 200
            text = xml

        return _Response()

    return _post


def _install_memory(monkeypatch, memory, captured):
    SabreClient.API_CALL_DELAY = 0
    storage.init_db(memory)
    monkeypatch.setattr(flights_routes, "get_db", lambda: memory)
    from backend.sabre import client as sabre_client
    monkeypatch.setattr(sabre_client.requests, "post", _sabre_post(captured))

    @asynccontextmanager
    async def _lifespan(_app):
        storage.init_db(memory)
        storage.ensure_indexes()
        yield

    app.router.lifespan_context = _lifespan
    flights_routes._dashboard_cache.clear()


def _ingest(monkeypatch):
    memory = MemoryDB()
    captured = []
    _install_memory(monkeypatch, memory, captured)
    payload = {
        "airline": "GF",
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DEPARTURE_DATE,
        "departureDateTime": f"{DEPARTURE_DATE}T08:00:00",
    }
    with TestClient(app) as client:
        response = client.post("/flights/ingest", json=payload)
    return response, memory, captured


def _trip_bodies(captured):
    return [call["body"] for call in captured if call["action"] == "Trip_ReportsRQ"]


def _latest_trip_report(memory):
    return memory["trip_reports"].find_one(
        {"flightNumber": FLIGHT, "origin": ORIGIN, "departureDate": DEPARTURE_DATE},
        sort=[("fetchedAt", -1)],
    )


def _passengers_by_pnr(entries):
    found = {}
    for entry in entries or []:
        for passenger in entry.get("passengers", []):
            found[passenger.get("pnr")] = (entry, passenger)
    return found


class _Monkey:
    """Minimal monkeypatch for running this file without pytest."""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=None):
        if value is None and not isinstance(target, str):
            raise TypeError("setattr target, name, value")
        if isinstance(target, str):
            value = name
            module_name, attr = target.rsplit(".", 1)
            module = sys.modules.get(module_name)
            if module is None:
                __import__(module_name)
                module = sys.modules[module_name]
            target = module
            name = attr
        old = getattr(target, name)
        setattr(target, name, value)
        self._undo.append(lambda target=target, name=name, old=old: setattr(target, name, old))

    def undo(self):
        while self._undo:
            self._undo.pop()()


def test_unauthenticated_ingest_requests_named_transfer_reports(monkeypatch=None):
    """A caller with no account ingests one flight, which must request TRIN and TRON."""
    own = monkeypatch is None
    if own:
        monkeypatch = _Monkey()
    try:
        response, memory, captured = _ingest(monkeypatch)
        assert response.status_code == 200, response.text
        assert "authorization" not in {key.lower() for key in response.request.headers}

        operation = app.openapi()["paths"]["/flights/ingest"]["post"]
        assert "security" not in operation

        bodies = _trip_bodies(captured)
        names = re.findall(r"<ReportName>([^<]+)</ReportName>", "\n".join(bodies))
        assert names.count("MLX") == 1 and names.count("MLC") == 1, names

        trin = [body for body in bodies if "<ReportName>TRIN</ReportName>" in body]
        tron = [body for body in bodies if "<ReportName>TRON</ReportName>" in body]
        assert trin, f"no TRIN request in trip reports: {names}"
        assert "<ReportFormat>TRIN</ReportFormat>" in trin[0]
        assert f"<InboundAirport>{ORIGIN}</InboundAirport>" in trin[0]
        assert tron, f"no TRON request in trip reports: {names}"
        assert "<ReportFormat>TRON</ReportFormat>" in tron[0]
        assert f"<OutboundAirport>{DESTINATION}</OutboundAirport>" in tron[0]

        stored = _latest_trip_report(memory)
        assert stored is not None
        cancelled = {p.get("pnr") for p in stored.get("cancelledPassengers", [])}
        assert CANCELLED_PNR in cancelled
    finally:
        if own:
            monkeypatch.undo()


def test_connection_figures_include_transfer_passengers_the_thru_flag_misses(monkeypatch=None):
    """The board's connection counts must come from the stored TRIN and TRON lists.

    NOWAK connects onward to GF 2462 at 12:05, thirty-five minutes after arrival,
    and is not marked thru. ROSSI connects inbound from GF 536 and is not marked
    thru. SMITH is marked thru and is on neither transfer report, so SMITH must
    not be the connection list.
    """
    own = monkeypatch is None
    if own:
        monkeypatch = _Monkey()
    try:
        response, memory, _captured = _ingest(monkeypatch)
        assert response.status_code == 200, response.text
        assert "authorization" not in {key.lower() for key in response.request.headers}

        stored = _latest_trip_report(memory)
        assert stored is not None, "ingest stored no trip report"

        flights_routes._dashboard_cache.clear()
        dashboard = flights_routes.get_flight_dashboard(
            FLIGHT, origin=ORIGIN, date=DEPARTURE_DATE, snapshot_sequence=None,
        )
        manifest = {
            pax["pnr"]: pax
            for pax in (memory["passenger_list"].find_one(
                {"flightNumber": FLIGHT}, sort=[("fetchedAt", -1)],
            ) or {}).get("passengers", [])
        }
        assert manifest[THRU_PNR]["isThru"] is True
        assert manifest[OUTBOUND_PNR]["isThru"] is False
        assert manifest[OUTBOUND_PNR]["isCheckedIn"] is False
        assert manifest[INBOUND_PNR]["isThru"] is False

        insights = dashboard["insights"]
        connecting = insights["connectingPassengers"]
        risk = insights["connectionRisk"]
        listed = {pax.get("pnr"): pax for pax in risk.get("transferPassengers") or []}
        assert OUTBOUND_PNR in listed and INBOUND_PNR in listed and THRU_PNR not in listed, {
            "connectionRisk": risk,
            "connectingPassengers": connecting,
            "storedTripReportKeys": sorted(stored.keys()),
        }

        inbound = _passengers_by_pnr(stored.get("inboundTransfers"))
        outbound = _passengers_by_pnr(stored.get("outboundTransfers"))

        assert INBOUND_PNR in inbound, stored
        inbound_entry, inbound_pax = inbound[INBOUND_PNR]
        assert inbound_entry.get("reportName") == "TRIN"
        assert inbound_entry.get("connectingAirline") == "GF"
        assert str(inbound_entry.get("connectingFlight")) == "536"
        assert inbound_entry.get("connectionDateTime") == INBOUND_ARRIVAL
        assert inbound_entry.get("classCounts") == {"T": 1}
        assert inbound_pax.get("name") == "ROSSI/DANIELA"
        assert inbound_pax.get("bookingClass") == "T"

        assert OUTBOUND_PNR in outbound, stored
        outbound_entry, outbound_pax = outbound[OUTBOUND_PNR]
        assert outbound_entry.get("reportName") == "TRON"
        assert outbound_entry.get("connectingAirline") == "GF"
        assert str(outbound_entry.get("connectingFlight")) == "2462"
        assert outbound_entry.get("connectionDateTime") == ONWARD_DEPARTURE
        assert outbound_entry.get("classCounts") == {"B": 1}
        assert outbound_pax.get("name") == "NOWAK/ADAM"
        assert outbound_pax.get("bookingClass") == "B"

        assert THRU_PNR not in inbound and THRU_PNR not in outbound

        assert connecting["connecting"] == 2, connecting
        assert connecting["local"] == 1, connecting
        assert risk["totalConnecting"] == 2, risk
        assert risk["atRiskCount"] == 2, risk
        assert risk["riskPct"] == 100.0, risk

        assert listed.get(OUTBOUND_PNR, {}).get("name") == "NOWAK/ADAM", risk
        assert listed[OUTBOUND_PNR]["direction"] == "outbound"
        assert listed[OUTBOUND_PNR]["connectingAirline"] == "GF"
        assert str(listed[OUTBOUND_PNR]["connectingFlight"]) == "2462"
        assert listed[OUTBOUND_PNR]["connectionDateTime"] == ONWARD_DEPARTURE

        assert listed.get(INBOUND_PNR, {}).get("name") == "ROSSI/DANIELA", risk
        assert listed[INBOUND_PNR]["direction"] == "inbound"
        assert listed[INBOUND_PNR]["connectingAirline"] == "GF"
        assert str(listed[INBOUND_PNR]["connectingFlight"]) == "536"
        assert listed[INBOUND_PNR]["connectionDateTime"] == INBOUND_ARRIVAL

        assert THRU_PNR not in listed, risk
    finally:
        if own:
            monkeypatch.undo()


# pytest passes a MonkeyPatch fixture when this argument is named monkeypatch.
# Running the file directly supplies none, and the functions build their own.


def _run_direct():
    failures = 0
    for test in (
        test_unauthenticated_ingest_requests_named_transfer_reports,
        test_connection_figures_include_transfer_passengers_the_thru_flag_misses,
    ):
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_direct()
