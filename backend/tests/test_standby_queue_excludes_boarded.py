"""Pending standby and upgrade queues must omit passengers already checked in or boarded.

The lists are built from a Sabre GetPassengerListRS the same way ingestion does:
SOAP XML is parsed by SabreClient, normalized by convert_passenger_list, and
stored as the passenger_list document (or as snapshot.data). GET
/flights/{flight}/passengers/standby-list and the dashboard insights queue
summary then read that document.

The standby panel badges render upgrade.passengers.length and
standby.passengers.length from that response, so those arrays are the totals
the badges show.

Not covered here: insights.priorityPassengers, which counts every priority
code on the manifest and is not the clearance queue, and the passenger-table
isStandby filter, which filters the full manifest in the browser.
"""

import copy
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import flights, passengers
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

SAMPLE_XML = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "GetPassengerListRS_GF2057_BOM_2026-03-19.xml"
)


class MemoryCollection:
    """Equality and $lte find/find_one used by the queue handlers.

    pymongo Collection.find_one returns the first stored document that
    matches the filter after sort, or None. store_passenger_list inserts
    the converter document unchanged, and store_snapshot wraps it in data.
    """

    def __init__(self, docs):
        self.docs = [copy.deepcopy(doc) for doc in docs]

    def find_one(self, filter=None, *args, **kwargs):
        matched = self._matched(filter or {})
        sort = kwargs.get("sort")
        if sort:
            for key, direction in reversed(list(sort)):
                matched.sort(
                    key=lambda doc, field=key: _sort_value(doc, field),
                    reverse=direction < 0,
                )
        if not matched:
            return None
        return copy.deepcopy(matched[0])

    def find(self, filter=None, projection=None, **kwargs):
        docs = [copy.deepcopy(doc) for doc in self._matched(filter or {})]
        if isinstance(projection, dict):
            include = {key for key, flag in projection.items() if flag}
            docs = [{key: doc.get(key) for key in include} for doc in docs]
        return docs

    def _matched(self, query):
        return [doc for doc in self.docs if _matches(doc, query)]


class MemoryDB:
    def __init__(self, collections):
        self._collections = {
            name: MemoryCollection(docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return self._collections.setdefault(name, MemoryCollection([]))


def _sort_value(doc, field):
    value = doc.get(field)
    return "" if value is None else value


def _matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _passenger_xml(
    *,
    last,
    first,
    pnr,
    passenger_id,
    line,
    priority="",
    passenger_type="F",
    cabin="Y",
    seniority="",
    checkin=None,
    boarded=None,
):
    priority_xml = f"<PriorityCode>{priority}</PriorityCode>" if priority else ""
    seniority_xml = f"<SeniorityDate>{seniority}</SeniorityDate>" if seniority else ""
    checkin_xml = ""
    if checkin is not None:
        status = "true" if checkin else "false"
        checkin_xml = f"<CheckIn_Info><CheckInStatus>{status}</CheckInStatus></CheckIn_Info>"
    boarded_xml = ""
    if boarded is not None:
        status = "true" if boarded else "false"
        boarded_xml = f"<Boarding_Info><BoardStatus>{status}</BoardStatus></Boarding_Info>"
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{last}</LastName>
            <FirstName>{first}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>{passenger_id}</PassengerID>
          <LineNumber>{line}</LineNumber>
          {priority_xml}
          <BookingClass>Y</BookingClass>
          <Cabin>{cabin}</Cabin>
          <Destination>DMM</Destination>
          <PassengerType>{passenger_type}</PassengerType>
          {seniority_xml}
          {checkin_xml}
          {boarded_xml}
        </PassengerInfo>
    """


def _list_xml(passengers_xml):
    joined = "\n".join(passengers_xml)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4"
                        xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>GF</Airline>
          <Flight>2058</Flight>
          <DepartureDate>2026-03-19</DepartureDate>
          <Origin>BOM</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
        <CabinInfoList>
          <ns2:CabinInfo>
            <ns2:Cabin>Y</ns2:Cabin>
            <ns2:Authorized>180</ns2:Authorized>
            <ns2:Available>20</ns2:Available>
            <ns2:Destination>DMM</ns2:Destination>
          </ns2:CabinInfo>
        </CabinInfoList>
      </ItineraryInfo>
      <PassengerInfoList>
        {joined}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def ingest_passenger_list(xml_text, flight_number, departure_date, origin):
    """Run the production parse and normalize steps for one passenger list."""
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    return convert_passenger_list(body, "GF", flight_number, departure_date, origin)


def mixed_manifest():
    """One pending queue plus passengers who have already finished the process.

    EARLY has a later line number but an earlier seniority date than LATE.
    BOARDED has an earlier seniority date than both, so leaving that row in
    the list would put a finished passenger at the front of the queue.
    NOSEN omits CheckIn_Info and Boarding_Info; the converter stores those
    flags as false and the passenger stays pending.
    """
    rows = [
        _passenger_xml(
            last="BOARDED", first="EMPLOYEE", pnr="BORD01", passenger_id="P1",
            line=1, priority="B01", passenger_type="E", seniority="2010-01-01",
            checkin=True, boarded=True,
        ),
        _passenger_xml(
            last="CHECKED", first="ONLY", pnr="CHK001", passenger_id="P2",
            line=2, priority="B03", passenger_type="E", seniority="2012-05-01",
            checkin=True, boarded=False,
        ),
        _passenger_xml(
            last="GATE", first="ONLY", pnr="GATE01", passenger_id="P3",
            line=4, priority="B04", passenger_type="E", seniority="2011-04-01",
            checkin=False, boarded=True,
        ),
        _passenger_xml(
            last="EARLY", first="WAIT", pnr="EARL01", passenger_id="P4",
            line=20, priority="B02", passenger_type="E", seniority="2018-01-01",
            checkin=False, boarded=False,
        ),
        _passenger_xml(
            last="LATE", first="WAIT", pnr="LATE01", passenger_id="P5",
            line=10, priority="B05", passenger_type="E", seniority="2020-06-01",
            checkin=False, boarded=False,
        ),
        _passenger_xml(
            last="NOSEN", first="WAIT", pnr="NOSN01", passenger_id="P6",
            line=30, priority="B08", passenger_type="S", cabin="Y",
        ),
        _passenger_xml(
            last="STANDBYDONE", first="WAIT", pnr="SDON01", passenger_id="P7",
            line=7, priority="B09", passenger_type="S", cabin="J",
            checkin=True, boarded=True,
        ),
        _passenger_xml(
            last="STANDBYCI", first="WAIT", pnr="SCI001", passenger_id="P8",
            line=8, priority="B11", passenger_type="S", cabin="Y",
            checkin=True, boarded=False,
        ),
        _passenger_xml(
            last="UPGDONE", first="REV", pnr="UPGD01", passenger_id="P9",
            line=1, priority="UPG", passenger_type="F",
            checkin=True, boarded=True,
        ),
        _passenger_xml(
            last="UPGCI", first="REV", pnr="UPGC01", passenger_id="P10",
            line=2, priority="UPG", passenger_type="F",
            checkin=True, boarded=False,
        ),
        _passenger_xml(
            last="UPGSECOND", first="REV", pnr="UPGS01", passenger_id="P11",
            line=5, priority="UPG", passenger_type="F",
            checkin=False, boarded=False,
        ),
        _passenger_xml(
            last="UPGFIRST", first="REV", pnr="UPGF01", passenger_id="P12",
            line=3, priority="UPG", passenger_type="F",
            checkin=False, boarded=False,
        ),
        _passenger_xml(
            last="ORDINARY", first="PAX", pnr="ORD001", passenger_id="P13",
            line=50, passenger_type="F",
            checkin=False, boarded=False,
        ),
    ]
    doc = ingest_passenger_list(_list_xml(rows), "2058", "2026-03-19", "BOM")
    by_name = {pax["lastName"]: pax for pax in doc["passengers"]}
    assert by_name["BOARDED"]["isCheckedIn"] is True
    assert by_name["BOARDED"]["isBoarded"] is True
    assert by_name["CHECKED"]["isCheckedIn"] is True
    assert by_name["CHECKED"]["isBoarded"] is False
    assert by_name["GATE"]["isCheckedIn"] is False
    assert by_name["GATE"]["isBoarded"] is True
    assert by_name["NOSEN"]["isCheckedIn"] is False
    assert by_name["NOSEN"]["isBoarded"] is False
    assert by_name["NOSEN"]["isStandby"] is True
    assert by_name["EARLY"]["priorityCode"] == "B02"
    return doc


def _snapshot(doc, sequence):
    return {
        "flightNumber": doc["flightNumber"],
        "origin": doc["origin"],
        "departureDate": doc["departureDate"],
        "snapshotType": "passenger_list",
        "sequenceNumber": sequence,
        "capturedAt": doc["fetchedAt"],
        "data": copy.deepcopy(doc),
    }


def _client(monkeypatch, db):
    monkeypatch.setattr(passengers, "get_db", lambda: db)
    monkeypatch.setattr(flights, "get_db", lambda: db)
    flights._dashboard_cache.clear()
    app = FastAPI()
    app.include_router(passengers.router)
    app.include_router(flights.router)
    return TestClient(app)


def _names(section):
    return [row["lastName"] for row in section["passengers"]]


def test_current_list_keeps_only_passengers_still_waiting(monkeypatch):
    doc = mixed_manifest()
    client = _client(monkeypatch, MemoryDB({"passenger_list": [doc]}))

    response = client.get(
        "/flights/2058/passengers/standby-list",
        params={"origin": "BOM", "date": "2026-03-19"},
    )

    assert response.status_code == 200
    body = response.json()
    # Panel badges use these array lengths, and the API total must match them.
    assert _names(body["standby"]) == ["EARLY", "LATE", "NOSEN"]
    assert body["standby"]["total"] == 3
    assert _names(body["upgrade"]) == ["UPGFIRST", "UPGSECOND"]
    assert body["upgrade"]["total"] == 2
    finished = {"BOARDED", "CHECKED", "GATE", "UPGDONE", "UPGCI", "STANDBYDONE", "STANDBYCI"}
    listed = set(_names(body["standby"])) | set(_names(body["upgrade"]))
    assert listed.isdisjoint(finished)
    assert "ORDINARY" not in listed


def test_snapshot_queue_uses_the_selected_version_and_the_same_filter(monkeypatch):
    current = mixed_manifest()
    earlier = ingest_passenger_list(
        _list_xml([
            _passenger_xml(
                last="BOARDED", first="EMPLOYEE", pnr="BORD01", passenger_id="P1",
                line=1, priority="B01", passenger_type="E", seniority="2010-01-01",
                checkin=True, boarded=True,
            ),
        ]),
        "2058",
        "2026-03-19",
        "BOM",
    )
    # A newer current-state row for another date must not be what the
    # historical request reads.
    decoy = copy.deepcopy(current)
    decoy["departureDate"] = "2026-03-20"
    db = MemoryDB({
        "passenger_list": [decoy],
        "snapshots": [_snapshot(earlier, 1), _snapshot(current, 4)],
    })
    client = _client(monkeypatch, db)

    latest = client.get(
        "/flights/2058/passengers/standby-list",
        params={"origin": "BOM", "date": "2026-03-19", "snapshot_sequence": 4},
    )
    previous = client.get(
        "/flights/2058/passengers/standby-list",
        params={"origin": "BOM", "date": "2026-03-19", "snapshot_sequence": 1},
    )

    assert latest.status_code == 200
    latest_body = latest.json()
    assert _names(latest_body["standby"]) == ["EARLY", "LATE", "NOSEN"]
    assert latest_body["standby"]["total"] == 3
    assert _names(latest_body["upgrade"]) == ["UPGFIRST", "UPGSECOND"]
    assert latest_body["upgrade"]["total"] == 2

    assert previous.status_code == 200
    previous_body = previous.json()
    assert previous_body["standby"]["passengers"] == []
    assert previous_body["standby"]["total"] == 0
    assert previous_body["upgrade"]["passengers"] == []
    assert previous_body["upgrade"]["total"] == 0


def test_dashboard_queue_summary_skips_finished_passengers(monkeypatch):
    doc = mixed_manifest()
    client = _client(monkeypatch, MemoryDB({"passenger_list": [doc]}))

    response = client.get(
        "/flights/2058/dashboard",
        params={"origin": "BOM", "date": "2026-03-19"},
    )

    assert response.status_code == 200
    summary = response.json()["insights"]["standbyUpgrade"]
    # Standby here is passenger type S. Finished type-S rows stay out of the
    # total and the cabin breakdown. Upgrade is still the UPG priority code.
    assert summary["standbyTotal"] == 1
    assert summary["standbyCabins"] == {"Y": 1}
    assert summary["upgradeTotal"] == 2
    assert summary["standbyPct"] == round(1 / doc["totalPassengers"] * 100, 1)


def test_recorded_gf2057_boarded_employee_is_not_in_the_queue(monkeypatch):
    xml_text = SAMPLE_XML.read_text(encoding="utf-8")
    doc = ingest_passenger_list(xml_text, "2057", "2026-03-19", "BOM")
    queued = [pax for pax in doc["passengers"] if pax.get("priorityCode")]
    assert len(queued) == 1
    employee = queued[0]
    assert employee["lastName"] == "RAHEEM"
    assert employee["priorityCode"] == "B13"
    assert employee["isCheckedIn"] is True
    assert employee["isBoarded"] is True
    assert employee["seniorityDate"] == "2015-02-07"

    client = _client(monkeypatch, MemoryDB({"passenger_list": [doc]}))
    response = client.get(
        "/flights/2057/passengers/standby-list",
        params={"origin": "BOM", "date": "2026-03-19"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["standby"]["passengers"] == []
    assert body["standby"]["total"] == 0
    assert body["upgrade"]["passengers"] == []
    assert body["upgrade"]["total"] == 0


def test_missing_passenger_list_is_not_an_empty_queue(monkeypatch):
    client = _client(monkeypatch, MemoryDB({}))
    response = client.get("/flights/2058/passengers/standby-list")
    assert response.status_code == 404
