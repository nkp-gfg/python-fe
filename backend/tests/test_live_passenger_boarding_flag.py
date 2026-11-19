"""Live passenger detail must keep Sabre's boarded-versus-checked-in flag.

GetPassengerData places BoardingPassFlag on each PassengerItinerary segment
when the name match is exact:

- ``*`` means a boarding pass was issued and the passenger is checked in,
  but has not boarded.
- ``-`` means a boarding pass was issued, the passenger is checked in, and
  the passenger has boarded.

The live detail route (``GET /flights/{flight}/passengers/{pnr}/detail``)
parses that SOAP body with ``SabreClient`` and returns ``convert_passenger_data``
unchanged. Each itinerary segment in that response must expose:

- ``boardingPassFlag``: the raw flag, or ``""`` when Sabre omitted it
- ``isCheckedIn``: true for ``*`` and ``-``, false otherwise
- ``isBoarded``: true only for ``-``

These tests fail while the converter drops the element. They do not call Sabre.
``SabreClient._post`` is replaced with the SOAP body under test; session open
and close are replaced so the test does not send credentials. Parsing,
extraction, conversion, and the route return value are the product code.

Not covered: ``convert_passenger_list`` maps a different GetPassengerList
``BoardingPassFlag`` (the boolean boarding-pass-issued marker) and takes
boarded state from ``Boarding_Info/BoardStatus``. That path already keeps
both values and is not this defect.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Placeholders only. The test never sends them: the SOAP POST is stubbed.
for _name, _value in {
    "SABRE_BASE_URL": "http://127.0.0.1:9",
    "SABRE_CPAID": "TEST",
    "SABRE_USERNAME": "placeholder",
    "SABRE_PASSWORD": "placeholder",
    "SABRE_PSEUDO_CITY_CODE": "TEST",
    "SABRE_ORGANIZATION": "TEST",
    "SABRE_DOMAIN": "TEST",
}.items():
    os.environ.setdefault(_name, _value)

from backend.api.routes.passengers import get_passenger_detail
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient


def _passenger_data_xml(passengers):
    """SOAP GetPassengerDataRS shaped like the v4 response the client extracts.

    ``flag`` is omitted from a segment when the key is absent. A present flag
    is written as element text, which is how Sabre sends ``*`` and ``-``.
    """
    blocks = []
    for pax in passengers:
        segments = []
        for seg in pax["segments"]:
            flag = ""
            if "flag" in seg:
                flag = f"<BoardingPassFlag>{seg['flag']}</BoardingPassFlag>"
            segments.append(
                f"""
                <PassengerItinerary>
                  <SegmentID>{seg["id"]}</SegmentID>
                  <Airline>{seg["airline"]}</Airline>
                  <Flight>{seg["flight"]}</Flight>
                  <BookingClass>Y</BookingClass>
                  <Origin>{seg["origin"]}</Origin>
                  <Destination>{seg["destination"]}</Destination>
                  <DepartureDate>2016-03-16</DepartureDate>
                  <Cabin>{seg["cabin"]}</Cabin>
                  <Seat>{seg["seat"]}</Seat>
                  {flag}
                  <PassengerType>F</PassengerType>
                  <CheckInNumber>{seg.get("checkin", 1)}</CheckInNumber>
                  <RequiredInfoList>
                    <RequiredInfo>
                      <Code>DOCS</Code>
                      <DetailStatus>OK</DetailStatus>
                      <FreeText>passport</FreeText>
                    </RequiredInfo>
                  </RequiredInfoList>
                </PassengerItinerary>
                """
            )
        blocks.append(
            f"""
            <PassengerDataResponse>
              <LineNumber>1</LineNumber>
              <LastName>{pax["last"]}</LastName>
              <FirstName>{pax["first"]}</FirstName>
              <PassengerID>{pax["id"]}</PassengerID>
              <PNRLocator nameAssociationID="1">{pax["pnr"]}</PNRLocator>
              <PassengerItineraryList>
                {"".join(segments)}
              </PassengerItineraryList>
            </PassengerDataResponse>
            """
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4">
      <PassengerDataResponseList>
        {"".join(blocks)}
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _passengers():
    """Two records so a fix that copies the flag onto only the first one fails.

    The first record has both Sabre flag values on different segments. The
    second has no flag, and a boolean ``true`` like the passenger-list API,
    which is not a boarded marker on this response.
    """
    return [
        {
            "last": "STEVE",
            "first": "J",
            "id": "B784C60F0001",
            "pnr": "OJLCOK",
            "segments": [
                {
                    "id": "1",
                    "airline": "AZ",
                    "flight": "4",
                    "origin": "FCO",
                    "destination": "TRN",
                    "cabin": "Y",
                    "seat": "42C",
                    "flag": "*",
                    "checkin": 1,
                },
                {
                    "id": "2",
                    "airline": "AZ",
                    "flight": "9",
                    "origin": "TRN",
                    "destination": "FCO",
                    "cabin": "C",
                    "seat": "2A",
                    "flag": "-",
                    "checkin": 4,
                },
            ],
        },
        {
            "last": "OTHER",
            "first": "K",
            "id": "B784C60F0002",
            "pnr": "ZZZXXX",
            "segments": [
                {
                    "id": "1",
                    "airline": "AZ",
                    "flight": "4",
                    "origin": "FCO",
                    "destination": "TRN",
                    "cabin": "Y",
                    "seat": "10B",
                },
                {
                    "id": "2",
                    "airline": "AZ",
                    "flight": "9",
                    "origin": "TRN",
                    "destination": "FCO",
                    "cabin": "Y",
                    "seat": "11C",
                    "flag": "true",
                },
            ],
        },
    ]


def _parse_body(xml):
    client = SabreClient()
    parsed = SabreClient._parse_xml(xml)
    return client._extract_body(parsed, "GetPassengerDataRS")


def _assert_checked_in_not_boarded(seg):
    assert seg["seat"]
    assert seg["cabin"]
    assert seg["requiredInfo"][0]["code"] == "DOCS"
    assert seg["boardingPassFlag"] == "*"
    assert seg["isCheckedIn"] is True
    assert seg["isBoarded"] is False


def _assert_boarded(seg):
    assert seg["boardingPassFlag"] == "-"
    assert seg["isCheckedIn"] is True
    assert seg["isBoarded"] is True


def _assert_not_checked_in(seg, flag=""):
    assert seg["boardingPassFlag"] == flag
    assert seg["isCheckedIn"] is False
    assert seg["isBoarded"] is False


def test_converter_keeps_star_and_hyphen_on_each_segment():
    xml = _passenger_data_xml(_passengers())
    doc = convert_passenger_data(
        _parse_body(xml), "AZ", "4", "2016-03-16", "FCO")

    first, second = doc["passengers"]
    not_boarded, boarded = first["itinerary"]
    absent, boolean_true = second["itinerary"]

    assert not_boarded["seat"] == "42C"
    assert boarded["seat"] == "2A"
    _assert_checked_in_not_boarded(not_boarded)
    _assert_boarded(boarded)
    _assert_not_checked_in(absent)
    # GetPassengerData does not use the passenger-list boolean. "true" is copied
    # and is neither checked-in nor boarded.
    _assert_not_checked_in(boolean_true, flag="true")


class _PassengerList:
    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "4"
        assert query["passengers.pnr"] == "OJLCOK"
        assert query["departureDate"] == "2016-03-16"
        return {
            "passengers": [
                {"pnr": "OJLCOK", "lastName": "STEVE", "firstName": "J"},
                {"pnr": "ZZZXXX", "lastName": "OTHER", "firstName": "K"},
            ]
        }


class _Db:
    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList()


def test_live_detail_route_returns_boarding_flag_from_sabre_body(monkeypatch):
    xml = _passenger_data_xml(_passengers())
    posted = {}

    def fake_post(self, action, body, timeout=30, endpoint=None):
        posted["action"] = action
        posted["body"] = body
        return xml, 200, 1, body

    monkeypatch.setattr(SabreClient, "API_CALL_DELAY", 0)
    monkeypatch.setattr(SabreClient, "create_session", lambda self: "placeholder-token")
    monkeypatch.setattr(SabreClient, "close_session", lambda self: None)
    monkeypatch.setattr(SabreClient, "_post", fake_post)
    monkeypatch.setattr(
        "backend.api.routes.passengers.get_db", lambda: _Db())

    result = get_passenger_detail(
        "4", "OJLCOK", origin="FCO", date="2016-03-16", airline="AZ")

    assert posted["action"] == "GetPassengerDataRQ"
    assert "<LastName>STEVE</LastName>" in posted["body"]
    assert "<PNRLocator>OJLCOK</PNRLocator>" in posted["body"]
    assert result["origin"] == "FCO"
    assert result["flightNumber"] == "4"

    first, second = result["passengers"]
    not_boarded, boarded = first["itinerary"]
    absent, boolean_true = second["itinerary"]

    assert not_boarded["seat"] == "42C"
    assert not_boarded["cabin"] == "Y"
    assert boarded["cabin"] == "C"
    _assert_checked_in_not_boarded(not_boarded)
    _assert_boarded(boarded)
    _assert_not_checked_in(absent)
    _assert_not_checked_in(boolean_true, flag="true")


def test_vendor_sample_asterisk_survives_live_detail_parse():
    """The published exact-name sample puts ``*`` on the itinerary segment.

    The sample security header is removed before parsing so this test does
    not keep that header's token. The response body is unchanged.
    """
    sample = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "sabre_docs",
        "GetPassengerDataRQ_v4-0-4_Samples",
        "GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_2_1.xml",
    )
    text = open(sample, encoding="utf-8").read()
    start = text.index("<!--Response-->")
    chunk = text[start:]
    env_start = chunk.index("<soap-env:Envelope")
    env_end = chunk.index("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    xml = chunk[env_start:env_end]
    header_start = xml.index("<soap-env:Header>")
    header_end = xml.index("</soap-env:Header>") + len("</soap-env:Header>")
    xml = xml[:header_start] + xml[header_end:]
    del text, chunk

    doc = convert_passenger_data(
        _parse_body(xml), "AZ", "4", "2016-03-16", "FCO")
    seg = doc["passengers"][0]["itinerary"][0]
    assert seg["seat"] == "42C"
    assert seg["cabin"] == "Y"
    assert str(seg["checkInNumber"]) == "1"
    assert seg["boardingPassFlag"] == "*"
    assert seg["isCheckedIn"] is True
    assert seg["isBoarded"] is False
