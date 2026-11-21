"""Document-edit codes must keep their Sabre meanings on every timeline.

Sabre's GetPassengerList guide defines DOCS as standard travel documentation,
DOCA as address documentation, and only DOCV as documentation that has been
verified. Change detection emits DOCUMENT_ADDED for those codes (and DCVI).
The passenger timeline, flight timeline, and activity feed all describe that
event. These tests fail while every code is rendered as "Document verified".
"""

import copy
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.api.routes import changes as changes_routes
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient

FLIGHT = "2006"
ORIGIN = "LHR"
DATE = "2026-03-18"
AIRLINE = "GF"
SAMPLE_XML = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "sample_data",
        "sample_xmlfiles",
        "GetPassengerListRS_GF2006_LHR_2026-03-18.xml",
    )
)

# Codes the differ treats as documents. Only DOCV means verified.
DOCUMENT_CODES = ("DOCS", "DOCA", "DOCV", "DCVI")


def _parse_passenger_list_body(xml_text):
    """Parse a SOAP envelope the way SabreClient.get_passenger_list does.

    Uses SabreClient._parse_xml and SabreClient._extract_body. The client is
    not constructed, so no Sabre credentials are read. The SOAP header is
    discarded before the body is returned.
    """
    parsed = SabreClient._parse_xml(xml_text)
    envelope = parsed.get("soap-env:Envelope")
    if not isinstance(envelope, dict):
        raise AssertionError("SOAP envelope was not parsed")
    envelope.pop("soap-env:Header", None)
    return SabreClient._extract_body(None, parsed, "GetPassengerListRS")


def _passenger_xml(pnr, last_name, first_name, edit_codes):
    codes = "\n".join(f"            <ns2:EditCode>{code}</ns2:EditCode>" for code in edit_codes)
    edit_list = f"          <EditCodeList>\n{codes}\n          </EditCodeList>" if edit_codes else ""
    return f"""        <PassengerInfo>
          <Name_Details>
            <LastName>{last_name}</LastName>
            <FirstName>{first_name}</FirstName>
          </Name_Details>
          <PNRLocator nameAssociationID="1">{pnr}</PNRLocator>
          <PassengerID>{pnr}ID</PassengerID>
          <LineNumber>1</LineNumber>
          <BookingClass>Y</BookingClass>
          <Cabin>Y</Cabin>
          <Seat>21A</Seat>
          <Destination>DMM</Destination>
          <PassengerType>F</PassengerType>
          <BagCount>0</BagCount>
          <Indicators>
            <Indicator>NotBoarded</Indicator>
          </Indicators>
          <CheckIn_Info>
            <CheckInStatus>false</CheckInStatus>
          </CheckIn_Info>
          <Boarding_Info>
            <BoardStatus>false</BoardStatus>
          </Boarding_Info>
{edit_list}
        </PassengerInfo>"""


def _manifest_xml(passengers):
    body = "\n".join(passengers)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <eb:MessageHeader xmlns:eb="http://www.ebxml.org/namespaces/messageHeader">
      <eb:Action>GetPassengerListRQ</eb:Action>
    </eb:MessageHeader>
  </soap-env:Header>
  <soap-env:Body>
    <GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" xmlns:ns2="http://services.sabre.com/STL/v3" version="4.0.0">
      <ItineraryInfo>
        <Itinerary>
          <Airline>{AIRLINE}</Airline>
          <Flight>{FLIGHT}</Flight>
          <DepartureDate>{DATE}</DepartureDate>
          <Origin>{ORIGIN}</Origin>
          <Destination>DMM</Destination>
        </Itinerary>
      </ItineraryInfo>
      <PassengerInfoList>
{body}
      </PassengerInfoList>
    </GetPassengerListRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _convert_manifest(xml_text):
    raw = _parse_passenger_list_body(xml_text)
    return convert_passenger_list(raw, AIRLINE, FLIGHT, DATE, ORIGIN)


def _diff_manifests(before_data, after_data):
    flight_info = {
        "airline": AIRLINE,
        "flightNumber": FLIGHT,
        "origin": ORIGIN,
        "departureDate": DATE,
    }
    # Same shape detect_changes receives from the ingestion pipeline:
    # a stored snapshot document with snapshotId and data.
    return detect_changes(
        {"snapshotId": "snap-before", "data": before_data},
        {"snapshotId": "snap-after", "data": after_data},
        "passenger_list",
        flight_info,
    )


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=direction == -1)
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    def __iter__(self):
        return iter(self._docs)


def _lookup(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class _Collection:
    """In-memory stand-in for the Mongo collections the timeline routes query.

    store_changes inserts the differ dicts unchanged, so the documents here
    are those dicts. find supports equality matches on dotted fields, an
    exclusion projection, sort, and limit — the operations the three routes use.
    """

    def __init__(self, docs):
        self._docs = docs

    def find(self, query, projection=None):
        matched = []
        for doc in self._docs:
            if all(_lookup(doc, key) == expected for key, expected in query.items()):
                copied = copy.deepcopy(doc)
                if projection:
                    for field, include in projection.items():
                        if include == 0:
                            copied.pop(field, None)
                matched.append(copied)
        return _Cursor(matched)

    def find_one(self, query, sort=None, projection=None):
        cursor = self.find(query, projection=projection)
        if sort:
            for key, direction in reversed(sort):
                cursor.sort(key, direction)
        for doc in cursor:
            return doc
        return None


class _Database:
    def __init__(self, changes):
        self._collections = {"changes": _Collection(changes), "snapshots": _Collection([])}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _Collection([]))


def _install_db(changes):
    database = _Database(changes)
    previous = changes_routes.get_db
    changes_routes.get_db = lambda: database
    return previous


def _restore_db(previous):
    changes_routes.get_db = previous


def _document_events(events, type_field):
    """Group DOCUMENT_ADDED descriptions by Sabre code.

    The passenger and flight timelines copy newValue into details. The
    activity feed does not; the code is only in the description sentence.
    Prefer details when it carries a document code, and otherwise take the
    single document code written in the sentence.
    """
    found = {}
    for event in events:
        if event.get(type_field) != "DOCUMENT_ADDED":
            continue
        description = event["description"]
        details_code = (event.get("details") or {}).get("newValue")
        if details_code in DOCUMENT_CODES:
            code = details_code
        else:
            present = [code for code in DOCUMENT_CODES if code in description]
            code = present[0] if len(present) == 1 else None
        found.setdefault(code, []).append(description)
    return found


def _assert_wording(description, code):
    """Spec for the sentence operators read.

    DOCS is travel documentation present, DOCA is address documentation
    present, and only DOCV is documents verified. The Sabre code stays in
    the sentence. DCVI is stored as a document code but the guide does not
    define it as verification, so it must not be described as verified.
    """
    assert code in description
    folded = description.casefold()
    if code == "DOCS":
        assert "travel documentation present" in folded
        assert "verif" not in folded
    elif code == "DOCA":
        assert "address documentation present" in folded
        assert "verif" not in folded
    elif code == "DOCV":
        assert "documents verified" in folded
    elif code == "DCVI":
        assert "verif" not in folded
    else:
        raise AssertionError(f"unexpected document code {code}")
    return folded.replace(code.casefold(), "«code»")


def _assert_codes_are_not_collapsed(descriptions_by_code):
    skeletons = {}
    for code, descriptions in descriptions_by_code.items():
        assert descriptions, code
        skeletons[code] = _assert_wording(descriptions[0], code)
        for description in descriptions[1:]:
            assert _assert_wording(description, code) == skeletons[code]
    assert skeletons["DOCS"] != skeletons["DOCA"]
    assert skeletons["DOCS"] != skeletons["DOCV"]
    assert skeletons["DOCA"] != skeletons["DOCV"]
    assert skeletons["DCVI"] != skeletons["DOCV"]


def test_ingested_edit_codes_keep_distinct_meanings_on_every_timeline():
    """Parse a GetPassengerList envelope, diff it, and read every timeline."""
    before_passengers = [
        _passenger_xml("DOCS01", "DOCS", "ONLY", ["ET"]),
        _passenger_xml("DOCA01", "DOCA", "ONLY", ["ET"]),
        _passenger_xml("DOCV01", "DOCV", "ONLY", ["ET"]),
        _passenger_xml("DCVI01", "DCVI", "ONLY", ["ET"]),
        _passenger_xml("BOTH01", "BOTH", "CODES", ["ET"]),
    ]
    after_passengers = [
        _passenger_xml("DOCS01", "DOCS", "ONLY", ["ET", "DOCS"]),
        _passenger_xml("DOCA01", "DOCA", "ONLY", ["ET", "DOCA"]),
        _passenger_xml("DOCV01", "DOCV", "ONLY", ["ET", "DOCV"]),
        _passenger_xml("DCVI01", "DCVI", "ONLY", ["ET", "DCVI"]),
        _passenger_xml("BOTH01", "BOTH", "CODES", ["ET", "DOCS", "DOCV"]),
    ]
    before = _convert_manifest(_manifest_xml(before_passengers))
    after = _convert_manifest(_manifest_xml(after_passengers))

    by_pnr = {pax["pnr"]: pax["editCodes"] for pax in after["passengers"]}
    assert by_pnr["DOCS01"] == ["ET", "DOCS"]
    assert "DOCV" not in by_pnr["DOCS01"]
    assert by_pnr["BOTH01"] == ["ET", "DOCS", "DOCV"]

    changes = _diff_manifests(before, after)
    added = [change for change in changes if change["changeType"] == "DOCUMENT_ADDED"]
    added_by_pnr = {}
    for change in added:
        added_by_pnr.setdefault(change["passenger"]["pnr"], set()).add(change["newValue"])
    assert added_by_pnr["DOCS01"] == {"DOCS"}
    assert added_by_pnr["DOCA01"] == {"DOCA"}
    assert added_by_pnr["DOCV01"] == {"DOCV"}
    assert added_by_pnr["DCVI01"] == {"DCVI"}
    assert added_by_pnr["BOTH01"] == {"DOCS", "DOCV"}
    assert all(change["newValue"] in DOCUMENT_CODES for change in added)

    previous = _install_db(changes)
    try:
        passenger = changes_routes.get_passenger_timeline(
            FLIGHT, "DOCS01", date=DATE, origin=ORIGIN,
        )
        both = changes_routes.get_passenger_timeline(
            FLIGHT, "BOTH01", date=DATE, origin=ORIGIN,
        )
        flight = changes_routes.get_flight_timeline(FLIGHT, origin=ORIGIN, date=DATE)
        feed = changes_routes.get_activity_feed(date=DATE, limit=50, categories=None)
    finally:
        _restore_db(previous)

    docs_only = _document_events(passenger["events"], "changeType")
    assert set(docs_only) == {"DOCS"}
    _assert_codes_are_not_collapsed(docs_only)

    both_codes = _document_events(both["events"], "changeType")
    assert set(both_codes) == {"DOCS", "DOCV"}
    assert _assert_wording(both_codes["DOCS"][0], "DOCS") != _assert_wording(
        both_codes["DOCV"][0], "DOCV",
    )

    flight_codes = _document_events(flight["events"], "eventType")
    feed_codes = _document_events(feed["events"], "eventType")
    for code in DOCUMENT_CODES:
        assert code in flight_codes
        assert code in feed_codes
    _assert_codes_are_not_collapsed(flight_codes)
    _assert_codes_are_not_collapsed(feed_codes)


def test_committed_gf2006_manifest_docs_code_is_not_called_verified():
    """The captured GF2006 LHR list includes DOCS. That code is not verification."""
    with open(SAMPLE_XML, encoding="utf-8") as handle:
        xml_text = handle.read()
    try:
        after = _convert_manifest(xml_text)
    except Exception as exc:
        raise AssertionError(
            f"sample passenger list did not parse: {type(exc).__name__}"
        ) from None
    finally:
        del xml_text

    before = copy.deepcopy(after)
    touched = 0
    for passenger in before["passengers"]:
        original = list(passenger.get("editCodes") or [])
        retained = [code for code in original if code not in DOCUMENT_CODES]
        if retained != original:
            passenger["editCodes"] = retained
            touched += 1
    assert touched > 0

    changes = _diff_manifests(before, after)
    added = [change for change in changes if change["changeType"] == "DOCUMENT_ADDED"]
    codes = {change["newValue"] for change in added}
    assert "DOCS" in codes
    assert "DOCA" in codes
    # The captured list's DOCS passengers are not DOCV passengers.
    docs_pnrs = {
        change["passenger"]["pnr"]
        for change in added
        if change["newValue"] == "DOCS"
    }
    docv_pnrs = {
        change["passenger"]["pnr"]
        for change in added
        if change["newValue"] == "DOCV"
    }
    assert docs_pnrs
    assert docs_pnrs - docv_pnrs

    sample_pnr = sorted(docs_pnrs - docv_pnrs)[0]
    previous = _install_db(changes)
    try:
        timeline = changes_routes.get_passenger_timeline(
            FLIGHT, sample_pnr, date=DATE, origin=ORIGIN,
        )
        flight = changes_routes.get_flight_timeline(FLIGHT, origin=ORIGIN, date=DATE)
        feed = changes_routes.get_activity_feed(date=DATE, limit=200, categories=None)
    finally:
        _restore_db(previous)

    for description in _document_events(timeline["events"], "changeType")["DOCS"]:
        _assert_wording(description, "DOCS")
    for events in (flight["events"], feed["events"]):
        described = _document_events(events, "eventType")
        assert "DOCS" in described
        assert "DOCA" in described
        for description in described["DOCS"]:
            _assert_wording(description, "DOCS")
        for description in described["DOCA"]:
            _assert_wording(description, "DOCA")
