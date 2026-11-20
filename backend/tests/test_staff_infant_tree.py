"""Staff lap infants must survive passenger-list conversion into the tree.

A lap infant is an INF edit code on the accompanying traveler, not a separate
manifest row. These tests feed a GetPassengerListRS SOAP body through the
Sabre client's parser and the passenger-list converter, then through every
handler that publishes the cabin staff breakdown:

- GET /flights/{flight_number}/dashboard
- GET /flights/{flight_number}/tree
- GET /flights/{flight_number}/phase-journey

They fail while a staff traveler's infant is dropped from that cabin's staff
infant total and from the staff tile badges. The root infant total and soul
count already include the infant; these tests keep that.

The compare page staff line and the passenger matrix INF column are separate
displays and are not covered here.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights
from backend.feeder.converter import convert_passenger_list
from backend.sabre.client import SabreClient

AIRLINE = "GF"
ORIGIN = "BAH"
DEST = "DXB"
DATE = "2026-11-20"


def _scenario_rows():
    """Manifest rows. `staff` is the classification the tree must use.

    Staff is passenger type E, or any row whose indicators do not contain
    Revenue. An INF edit code is a lap infant of that traveler.
    """
    return [
        {
            "last": "ALI", "first": "SARA", "pnr": "STAF01", "assoc": "1",
            "cabin": "Y", "ptype": "E", "gender": "FI",
            "indicators": ["CheckedIn", "Boarded", "NonRevenue"],
            "edits": ["INF", "ETI"],
            "checked_in": True, "boarded": True,
        },
        {
            "last": "KHAN", "first": "OMAR", "pnr": "STAF02", "assoc": "2",
            "cabin": "Y", "ptype": "F", "gender": "M",
            "indicators": ["NotBoarded"],
            "edits": ["INF"],
            "checked_in": False, "boarded": False,
        },
        {
            "last": "REED", "first": "ANNA", "pnr": "PAX001", "assoc": "3",
            "cabin": "Y", "ptype": "F", "gender": "F",
            "indicators": ["CheckedIn", "NotBoarded", "Revenue"],
            "edits": ["INF", "DOCS"],
            "checked_in": True, "boarded": False,
        },
        {
            "last": "NOOR", "first": "HANA", "pnr": "STAF03", "assoc": "4",
            "cabin": "Y", "ptype": "E", "gender": "F",
            "indicators": ["CheckedIn", "NonRevenue"],
            "edits": ["DOCS"],
            "checked_in": True, "boarded": False,
        },
        {
            "last": "EMPL", "first": "RASHID", "pnr": "STAF04", "assoc": "5",
            "cabin": "J", "ptype": "E", "gender": "",
            "indicators": ["NotBoarded", "Revenue"],
            "edits": ["INF"],
            "checked_in": False, "boarded": False,
        },
        {
            "last": "STBY", "first": "LEILA", "pnr": "STAF05", "assoc": "6",
            "cabin": "J", "ptype": "S", "gender": "M",
            "indicators": ["NotBoarded", "NonRevenue"],
            "edits": ["INF", "ETI"],
            "checked_in": False, "boarded": False,
        },
        {
            "last": "CHLD", "first": "NOAH", "pnr": "STAF06", "assoc": "7",
            "cabin": "J", "ptype": "E", "gender": "F",
            "indicators": ["NotBoarded", "NonRevenue"],
            "edits": ["CHD", "INF"],
            "checked_in": False, "boarded": False,
        },
        {
            "last": "ADLT", "first": "JAMES", "pnr": "PAX002", "assoc": "8",
            "cabin": "J", "ptype": "F", "gender": "M",
            "indicators": ["NotBoarded", "Revenue"],
            "edits": ["DOCS"],
            "checked_in": False, "boarded": False,
        },
    ]


def _zero_infant_rows():
    """Staff are present, but nobody is accompanying a lap infant."""
    return [
        {
            "last": "NOOR", "first": "HANA", "pnr": "STAF03", "assoc": "1",
            "cabin": "Y", "ptype": "E", "gender": "F",
            "indicators": ["NonRevenue"],
            "edits": ["DOCS"],
            "checked_in": False, "boarded": False,
        },
        {
            "last": "ADLT", "first": "JAMES", "pnr": "PAX002", "assoc": "2",
            "cabin": "J", "ptype": "F", "gender": "M",
            "indicators": ["Revenue"],
            "edits": ["DOCS"],
            "checked_in": False, "boarded": False,
        },
    ]


def _is_staff(row):
    return row["ptype"] == "E" or "Revenue" not in row["indicators"]


def _has_infant(row):
    return "INF" in row["edits"]


def _is_child(row):
    return "CHD" in row["edits"]


def _expected(rows):
    def cabin_rows(cabin, staff):
        return [r for r in rows if r["cabin"] == cabin and _is_staff(r) is staff]

    def infants(group):
        return sum(1 for r in group if _has_infant(r))

    def gendered(group, gender):
        return sum(
            1 for r in group
            if not _is_child(r) and r["gender"] == gender
        )

    econ_staff = cabin_rows("Y", True)
    econ_pax = cabin_rows("Y", False)
    biz_staff = cabin_rows("J", True)
    biz_pax = cabin_rows("J", False)
    infant_count = sum(1 for r in rows if _has_infant(r))
    return {
        "infant_count": infant_count,
        "total_souls": len(rows) + infant_count,
        "economy_staff_infants": infants(econ_staff),
        "economy_pax_infants": infants(econ_pax),
        "business_staff_infants": infants(biz_staff),
        "business_pax_infants": infants(biz_pax),
        "economy_staff_total": len(econ_staff),
        "business_staff_total": len(biz_staff),
        "economy_staff_male": gendered(econ_staff, "M"),
        "economy_staff_female": gendered(econ_staff, "F") + gendered(econ_staff, "FI"),
        "business_staff_male": gendered(biz_staff, "M"),
        "business_staff_female": gendered(biz_staff, "F") + gendered(biz_staff, "FI"),
        "economy_souls": len(cabin_rows("Y", True)) + len(cabin_rows("Y", False)) + infants(econ_staff) + infants(econ_pax),
        "business_souls": len(cabin_rows("J", True)) + len(cabin_rows("J", False)) + infants(biz_staff) + infants(biz_pax),
    }


def _xml_list(tag, values, ns=""):
    name = f"{ns}{tag}" if ns else tag
    return "".join(f"<{name}>{value}</{name}>" for value in values)


def _passenger_xml(row):
    indicators = _xml_list("Indicator", row["indicators"])
    edits = _xml_list("EditCode", row["edits"], ns="ns2:")
    return f"""
        <PassengerInfo>
          <Name_Details>
            <LastName>{row["last"]}</LastName>
            <FirstName>{row["first"]}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="{row["assoc"]}">{row["pnr"]}</PNRLocator>
          <PassengerID>PAX{row["assoc"]}</PassengerID>
          <LineNumber>{row["assoc"]}</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>{row["cabin"]}</Cabin>
          <Destination>{DEST}</Destination>
          <PassengerType>{row["ptype"]}</PassengerType>
          <Indicators>{indicators}</Indicators>
          <CheckIn_Info>
            <CheckInStatus>{"true" if row["checked_in"] else "false"}</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>{"true" if row["boarded"] else "false"}</BoardStatus>
          </Boarding_Info>
          <EditCodeList>{edits}</EditCodeList>
        </PassengerInfo>
    """


def _passenger_list_xml(rows):
    """SOAP envelope in the shape stored Sabre responses use."""
    body = "".join(_passenger_xml(row) for row in rows)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4"
                        xmlns:ns2="http://services.sabre.com/STL/v3">
      <ItineraryInfo>
        <Itinerary>
          <Airline>{AIRLINE}</Airline>
          <Flight>2057</Flight>
          <Origin>{ORIGIN}</Origin>
          <Destination>{DEST}</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
        {body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _parse_passenger_list(xml_text):
    """Same parse the Sabre client uses after GetPassengerListRQ."""
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _convert(rows, flight_number):
    raw = _parse_passenger_list(_passenger_list_xml(rows))
    return convert_passenger_list(raw, AIRLINE, flight_number, DATE, ORIGIN)


def _reservation_doc(rows, flight_number):
    by_pnr = {}
    for row in rows:
        if not row["gender"]:
            continue
        by_pnr.setdefault(row["pnr"], []).append(
            {"lastName": row["last"], "gender": row["gender"]}
        )
    return {
        "flightNumber": flight_number,
        "departureAirport": ORIGIN,
        "departureDate": DATE,
        "reservations": [
            {"pnr": pnr, "passengers": pax}
            for pnr, pax in by_pnr.items()
        ],
    }


def _flight_status(flight_number):
    return {
        "status": "OPEN",
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DATE,
        "jumpSeat": {"cockpit": 0, "cabin": 0},
    }


def _node(tree, node_id):
    matches = [node for node in tree["nodes"] if node["id"] == node_id]
    assert len(matches) == 1, node_id
    return matches[0]


def _badges(node):
    return {badge["type"]: badge["value"] for badge in node["badges"]}


def _assert_staff_infants_visible(payload, rows):
    expected = _expected(rows)
    summary = payload["passengerSummary"]
    analysis = payload["analysis"]
    tree = payload["tree"]

    assert summary["infantCount"] == expected["infant_count"]
    assert summary["totalSouls"] == expected["total_souls"]
    assert payload["overview"]["totalSouls"] == expected["total_souls"]
    assert analysis["totalInfants"] == expected["infant_count"]
    assert analysis["cabinTotals"]["economy"]["souls"] == expected["economy_souls"]
    assert analysis["cabinTotals"]["business"]["souls"] == expected["business_souls"]

    econ_staff = analysis["economy"]["staff"]
    biz_staff = analysis["business"]["staff"]
    assert econ_staff["total"] == expected["economy_staff_total"]
    assert biz_staff["total"] == expected["business_staff_total"]
    assert econ_staff["male"] == expected["economy_staff_male"]
    assert econ_staff["female"] == expected["economy_staff_female"]
    assert biz_staff["male"] == expected["business_staff_male"]
    assert biz_staff["female"] == expected["business_staff_female"]
    assert analysis["economy"]["passengers"]["infants"] == expected["economy_pax_infants"]
    assert analysis["business"]["passengers"]["infants"] == expected["business_pax_infants"]
    assert econ_staff["infants"] == expected["economy_staff_infants"]
    assert biz_staff["infants"] == expected["business_staff_infants"]

    root = _badges(_node(tree, "root"))
    # The root badge list omits a zero. A positive infant total must be present.
    if expected["infant_count"]:
        assert root["I"] == expected["infant_count"]
    else:
        assert root.get("I", 0) == 0
    assert _node(tree, "root")["value"] == expected["total_souls"]

    econ_staff_badges = _badges(_node(tree, "econStaff"))
    biz_staff_badges = _badges(_node(tree, "bizStaff"))
    assert econ_staff_badges["M"] == expected["economy_staff_male"]
    assert econ_staff_badges["F"] == expected["economy_staff_female"]
    assert econ_staff_badges["I"] == expected["economy_staff_infants"]
    assert biz_staff_badges["M"] == expected["business_staff_male"]
    assert biz_staff_badges["F"] == expected["business_staff_female"]
    assert biz_staff_badges["I"] == expected["business_staff_infants"]

    econ_pax_badges = _badges(_node(tree, "econPassengers"))
    biz_pax_badges = _badges(_node(tree, "bizPassengers"))
    assert econ_pax_badges["I"] == expected["economy_pax_infants"]
    assert biz_pax_badges["I"] == expected["business_pax_infants"]

    staff_infant_badges = (
        econ_staff_badges["I"] + biz_staff_badges["I"]
        + econ_pax_badges["I"] + biz_pax_badges["I"]
    )
    assert staff_infant_badges == expected["infant_count"]


def _assert_phase_summary(summary, rows):
    expected = _expected(rows)
    assert summary["infantCount"] == expected["infant_count"]
    assert summary["totalSouls"] == expected["total_souls"]
    assert summary["totalInfants"] == expected["infant_count"]
    assert summary["economy"]["staff"]["infants"] == expected["economy_staff_infants"]
    assert summary["business"]["staff"]["infants"] == expected["business_staff_infants"]
    assert summary["economy"]["passengers"]["infants"] == expected["economy_pax_infants"]
    assert summary["business"]["passengers"]["infants"] == expected["business_pax_infants"]


class _Query:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, _n):
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query, projection=None, sort=None):
        for doc in self._docs:
            if doc is not None and _matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query, projection=None):
        return _Query(
            copy.deepcopy(doc) for doc in self._docs if _matches(doc, query)
        )


def _matches(doc, query):
    for key, value in query.items():
        if doc.get(key) != value:
            return False
    return True


class _Database:
    """In-memory stand-in for the collections the handlers read.

    Reads return copies. The documents are the converter output and the
    reservation shape `_build_gender_lookup` reads; nothing here rewrites
    passenger fields.
    """

    def __init__(self, collections):
        self._collections = {
            name: _Collection(docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return self._collections.get(name, _Collection([]))


def _install_db(monkeypatch, flight_number, rows):
    passenger_list = _convert(rows, flight_number)
    reservations = _reservation_doc(rows, flight_number)
    snapshot = {
        "snapshotType": "passenger_list",
        "flightNumber": flight_number,
        "origin": ORIGIN,
        "departureDate": DATE,
        "sequenceNumber": 1,
        "capturedAt": "2026-11-20T08:00:00Z",
        "data": passenger_list,
    }
    db = _Database({
        "passenger_list": [passenger_list],
        "reservations": [reservations],
        "flight_status": [_flight_status(flight_number)],
        "trip_reports": [],
        "flight_schedules": [],
        "flights": [],
        "changes": [],
        "snapshots": [snapshot],
    })
    monkeypatch.setattr(flights, "get_db", lambda: db)
    flights._dashboard_cache.clear()
    return passenger_list


def test_converter_marks_lap_infants_on_staff_and_revenue_rows():
    rows = _scenario_rows()
    converted = _convert(rows, "2057")
    by_pnr = {pax["pnr"]: pax for pax in converted["passengers"]}

    assert len(by_pnr) == len(rows)
    for row in rows:
        pax = by_pnr[row["pnr"]]
        assert pax["hasInfant"] is _has_infant(row)
        assert pax["isChild"] is _is_child(row)
        assert pax["passengerType"] == row["ptype"]
        assert pax["isRevenue"] is ("Revenue" in row["indicators"])
        assert pax["cabin"] == row["cabin"]

    expected = _expected(rows)
    assert converted["infantCount"] == expected["infant_count"]
    assert converted["totalSouls"] == expected["total_souls"]
    assert converted["totalPassengers"] == len(rows)


def test_dashboard_payload_keeps_staff_lap_infants_on_cabin_tiles():
    rows = _scenario_rows()
    passenger_list = _convert(rows, "2057")
    payload = flights._build_dashboard_payload(
        _flight_status("2057"),
        passenger_list,
        ORIGIN,
        DATE,
        {},
        reservation_doc=_reservation_doc(rows, "2057"),
    )
    _assert_staff_infants_visible(payload, rows)


def test_phase_summary_keeps_staff_lap_infants():
    rows = _scenario_rows()
    passenger_list = _convert(rows, "2057")
    summary = flights._compute_phase_summary(
        passenger_list["passengers"],
        flights._build_gender_lookup(_reservation_doc(rows, "2057")),
    )
    _assert_phase_summary(summary, rows)


def test_staff_tiles_include_zero_infant_badge():
    """Passenger tiles always render an infant badge. Staff tiles must too."""
    rows = _zero_infant_rows()
    passenger_list = _convert(rows, "2058")
    payload = flights._build_dashboard_payload(
        _flight_status("2058"),
        passenger_list,
        ORIGIN,
        DATE,
        {},
        reservation_doc=_reservation_doc(rows, "2058"),
    )
    _assert_staff_infants_visible(payload, rows)
    assert _badges(_node(payload["tree"], "econStaff"))["I"] == 0
    assert _badges(_node(payload["tree"], "bizStaff"))["I"] == 0


def _client():
    """Mount the flight router the way the app does, without a database lifespan."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(flights.router)
    return TestClient(app)


def test_dashboard_and_tree_handlers(monkeypatch):
    rows = _scenario_rows()
    flight_number = "2057"
    _install_db(monkeypatch, flight_number, rows)
    client = _client()
    params = {"origin": ORIGIN, "date": DATE}

    tree_response = client.get(f"/flights/{flight_number}/tree", params=params)
    dashboard_response = client.get(
        f"/flights/{flight_number}/dashboard", params=params)
    cached_tree_response = client.get(
        f"/flights/{flight_number}/tree", params=params)

    assert tree_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert cached_tree_response.status_code == 200

    dashboard = dashboard_response.json()
    _assert_staff_infants_visible(dashboard, rows)
    assert tree_response.json() == dashboard["tree"]
    assert cached_tree_response.json() == dashboard["tree"]


def test_phase_journey_handler(monkeypatch):
    rows = _scenario_rows()
    flight_number = "2057"
    _install_db(monkeypatch, flight_number, rows)
    client = _client()
    response = client.get(
        f"/flights/{flight_number}/phase-journey",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200
    journey = response.json()
    assert len(journey["phases"]) == 1
    _assert_phase_summary(journey["phases"][0]["summary"], rows)


def test_zero_infant_staff_badges_on_tree_handler(monkeypatch):
    rows = _zero_infant_rows()
    flight_number = "2058"
    _install_db(monkeypatch, flight_number, rows)
    client = _client()
    response = client.get(
        f"/flights/{flight_number}/tree",
        params={"origin": ORIGIN, "date": DATE},
    )
    assert response.status_code == 200
    tree = response.json()
    assert _badges(_node(tree, "econStaff"))["I"] == 0
    assert _badges(_node(tree, "bizStaff"))["I"] == 0
