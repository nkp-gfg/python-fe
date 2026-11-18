"""Live passenger detail must keep the Timatic boarding decision and visa text.

These tests fail while convert_passenger_data reads Country and Text from
TimaticInfo. Sabre puts timaticStatus on TimaticInfo and on each
TimaticCountryInfo, and puts the country and visa text under
TimaticCountryInfo / SectionInfo / SubSectionInfo. The detail route returns
that converted object, and the Timatic card prints `country` and `text`.

They pass once each TimaticCountryInfo is returned as one timaticInfo entry,
in document order, with:

- status: the parent TimaticInfo timaticStatus
- countryStatus: that TimaticCountryInfo timaticStatus
- country: Country countryName (the card heading)
- countryCode: Country countryCode
- text: a string containing status, countryStatus, every SectionName,
  SectionText, SubSectionName, SubSectionType, and SubSectionText
  (the card body; this is the only place the card shows the decision)
- sections: sectionName, sectionText ("" when the element is absent), and
  subsections of subSectionName, subSectionType, subSectionText
  ("" when that element is absent)

The React card is not executed here. It renders those two strings and nothing
else from the entry (passenger-detail-sheet.tsx). PassengerDataResponse in
backend/sabre/models.py is unused by this route and is not covered.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Set before SabreClient is imported. API_CALL_DELAY is read at class definition.
os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "TEST")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "TEST")
os.environ.setdefault("SABRE_ORGANIZATION", "TEST")
os.environ.setdefault("SABRE_DOMAIN", "TEST")

from backend.api.routes.passengers import get_passenger_detail
from backend.feeder.converter import convert_passenger_data
from backend.sabre.client import SabreClient

SAMPLE_RESPONSE = (
    ROOT
    / "sabre_docs"
    / "GetPassengerDataRQ_v4-0-4_Samples"
    / "GetPassengerDataRQ_v4-0-2_SamplePayload_4_2_5a.xml.xml"
)

VISA_WARNING = (
    'PASSENGERS WITH AN "ENTRY CLEARANCE" THAT HAS THE OBSERVATION '
    '"INDEFINITE LEAVE TO ENTER THE UK": - MUST USE IT FOR THE FIRST TIME '
    "WITHIN THE VALIDITY DATES, FOLLOWING ENTRIES ARE ALLOWED AFTER THE "
    "EXPIRATION DATE; AND- MUST RETURN WITHIN TWO YEARS OF LAST DEPARTURE."
)

SESSION_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
      <wsse:BinarySecurityToken>placeholder-session-token</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body/>
</soap-env:Envelope>"""


def _sample_response_xml():
    """Return the GetPassengerDataRS envelope from Sabre's published sample."""
    text = SAMPLE_RESPONSE.read_text(encoding="utf-8")
    start = text.index("<soap-env:Envelope")
    end = text.index("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    return text[start:end]


def _response_xml(passengers_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <ns3:GetPassengerDataRS xmlns:ns3="http://services.sabre.com/checkin/getPassengerData/v4" xmlns:ns2="http://services.sabre.com/STL/v3">
      <PassengerDataResponseList>
        {passengers_xml}
      </PassengerDataResponseList>
    </ns3:GetPassengerDataRS>
  </soap-env:Body>
</soap-env:Envelope>"""


def _convert(xml, airline="EY", flight_number="11", departure_date="2017-01-04", origin="AUH"):
    """Run the same parse and extract the live client uses, then convert."""
    parsed = SabreClient._parse_xml(xml)
    data = SabreClient._extract_body(None, parsed, "GetPassengerDataRS")
    return convert_passenger_data(data, airline, flight_number, departure_date, origin)


def _assert_country(entries, *, status, country_status, country, country_code, sections, visible):
    matches = [
        entry for entry in entries
        if entry.get("country") == country and entry.get("countryCode") == country_code
    ]
    assert len(matches) == 1, entries
    entry = matches[0]
    assert entry["status"] == status
    assert entry["countryStatus"] == country_status
    text = entry["text"]
    assert isinstance(text, str)
    for piece in visible:
        assert piece in text, (piece, text)
    assert entry["sections"] == sections
    return entry


def test_published_sample_keeps_boarding_decision_and_visa_text():
    """Sabre sample 4.2.5a: OK TO BOARD plus the United Kingdom visa warning."""
    doc = _convert(_sample_response_xml())
    assert doc["passengers"][0]["lastName"] == "CHARLES"
    assert doc["passengers"][0]["pnr"] == "OLBJTY"
    entries = doc["passengers"][0]["timaticInfo"]
    assert len(entries) == 1
    _assert_country(
        entries,
        status="OK TO BOARD",
        country_status="OK TO BOARD",
        country="UNITED KINGDOM",
        country_code="GB",
        visible=["OK TO BOARD", "VISA", "WARNING", "INFORMATION", VISA_WARNING],
        sections=[
            {
                "sectionName": "VISA",
                "sectionText": "",
                "subsections": [
                    {
                        "subSectionName": "WARNING",
                        "subSectionType": "INFORMATION",
                        "subSectionText": VISA_WARNING,
                    }
                ],
            }
        ],
    )


def test_repeated_timatic_elements_keep_every_country_and_status():
    """xmltodict returns a list when a Timatic element repeats and a dict when it does not.

    A mapper that only reads the single-node shape, or only the first country,
    drops the other boarding decision.
    """
    xml = _response_xml(
        """
        <PassengerDataResponse>
          <LastName>DOE</LastName>
          <FirstName>JANE</FirstName>
          <PNRLocator>ABC123</PNRLocator>
          <TimaticInfoList>
            <TimaticInfo timaticStatus="DO NOT BOARD">
              <TimaticCountryInfo timaticStatus="DO NOT BOARD">
                <Country countryName="UNITED KINGDOM" countryCode="GB"/>
                <SectionInfo>
                  <SectionName>VISA</SectionName>
                  <SectionText>CHECK VISA</SectionText>
                  <SubSectionInfo>
                    <SubSectionName>WARNING</SubSectionName>
                    <SubSectionType>INFORMATION</SubSectionType>
                    <SubSectionText>VISA REQUIRED</SubSectionText>
                  </SubSectionInfo>
                </SectionInfo>
                <SectionInfo>
                  <SectionName>HEALTH</SectionName>
                  <SubSectionInfo>
                    <SubSectionName>NOTE</SubSectionName>
                    <SubSectionText>YELLOW FEVER</SubSectionText>
                  </SubSectionInfo>
                  <SubSectionInfo>
                    <SubSectionName>NOTE</SubSectionName>
                    <SubSectionType>REQUIREMENT</SubSectionType>
                    <SubSectionText>BLANK PAGES REQUIRED</SubSectionText>
                  </SubSectionInfo>
                </SectionInfo>
              </TimaticCountryInfo>
              <TimaticCountryInfo timaticStatus="CONDITIONAL">
                <Country countryName="FRANCE" countryCode="FR"/>
                <SectionInfo>
                  <SectionName>PASSPORT</SectionName>
                  <SectionText>MUST BE VALID</SectionText>
                  <SubSectionInfo>
                    <SubSectionName>REQUIREMENT</SubSectionName>
                    <SubSectionType>REQUIREMENT</SubSectionType>
                    <SubSectionText>PASSPORT MUST BE VALID</SubSectionText>
                  </SubSectionInfo>
                </SectionInfo>
              </TimaticCountryInfo>
            </TimaticInfo>
            <TimaticInfo timaticStatus="REFER TO SUPERVISOR">
              <TimaticCountryInfo timaticStatus="REFER">
                <Country countryName="UNITED STATES" countryCode="US"/>
                <SectionInfo>
                  <SectionName>VISA</SectionName>
                  <SubSectionInfo>
                    <SubSectionText>ESTA REQUIRED</SubSectionText>
                  </SubSectionInfo>
                </SectionInfo>
              </TimaticCountryInfo>
            </TimaticInfo>
          </TimaticInfoList>
        </PassengerDataResponse>
        """
    )
    entries = _convert(xml)["passengers"][0]["timaticInfo"]
    assert len(entries) == 3
    assert [entry["countryCode"] for entry in entries] == ["GB", "FR", "US"]
    _assert_country(
        entries,
        status="DO NOT BOARD",
        country_status="DO NOT BOARD",
        country="UNITED KINGDOM",
        country_code="GB",
        visible=[
            "DO NOT BOARD",
            "VISA",
            "CHECK VISA",
            "WARNING",
            "INFORMATION",
            "VISA REQUIRED",
            "HEALTH",
            "YELLOW FEVER",
            "BLANK PAGES REQUIRED",
            "REQUIREMENT",
        ],
        sections=[
            {
                "sectionName": "VISA",
                "sectionText": "CHECK VISA",
                "subsections": [
                    {
                        "subSectionName": "WARNING",
                        "subSectionType": "INFORMATION",
                        "subSectionText": "VISA REQUIRED",
                    }
                ],
            },
            {
                "sectionName": "HEALTH",
                "sectionText": "",
                "subsections": [
                    {
                        "subSectionName": "NOTE",
                        "subSectionType": "",
                        "subSectionText": "YELLOW FEVER",
                    },
                    {
                        "subSectionName": "NOTE",
                        "subSectionType": "REQUIREMENT",
                        "subSectionText": "BLANK PAGES REQUIRED",
                    },
                ],
            },
        ],
    )
    _assert_country(
        entries,
        status="DO NOT BOARD",
        country_status="CONDITIONAL",
        country="FRANCE",
        country_code="FR",
        visible=["DO NOT BOARD", "CONDITIONAL", "PASSPORT", "MUST BE VALID", "PASSPORT MUST BE VALID"],
        sections=[
            {
                "sectionName": "PASSPORT",
                "sectionText": "MUST BE VALID",
                "subsections": [
                    {
                        "subSectionName": "REQUIREMENT",
                        "subSectionType": "REQUIREMENT",
                        "subSectionText": "PASSPORT MUST BE VALID",
                    }
                ],
            }
        ],
    )
    _assert_country(
        entries,
        status="REFER TO SUPERVISOR",
        country_status="REFER",
        country="UNITED STATES",
        country_code="US",
        visible=["REFER TO SUPERVISOR", "REFER", "VISA", "ESTA REQUIRED"],
        sections=[
            {
                "sectionName": "VISA",
                "sectionText": "",
                "subsections": [
                    {
                        "subSectionName": "",
                        "subSectionType": "",
                        "subSectionText": "ESTA REQUIRED",
                    }
                ],
            }
        ],
    )


def test_detail_without_timatic_stays_empty():
    xml = _response_xml(
        """
        <PassengerDataResponse>
          <LastName>DOE</LastName>
          <FirstName>JANE</FirstName>
          <PNRLocator>ABC123</PNRLocator>
        </PassengerDataResponse>
        """
    )
    assert _convert(xml)["passengers"][0]["timaticInfo"] == []


class _PassengerList:
    def __init__(self, document):
        self._document = document

    def find_one(self, query, sort=None):
        assert query["flightNumber"] == "11"
        assert query["departureDate"] == "2017-01-04"
        assert query["passengers.pnr"] == "OLBJTY"
        return self._document


class _DB:
    def __init__(self, document):
        self._document = document

    def __getitem__(self, name):
        assert name == "passenger_list"
        return _PassengerList(self._document)


def test_detail_route_returns_sample_timatic_from_sabre_response():
    """The live detail handler asks Sabre for Timatic and returns the converted payload."""
    response_xml = _sample_response_xml()
    captured = []

    def fake_post(self, action, body, timeout=30, endpoint=None):
        captured.append((action, body))
        if action == "SessionCreateRQ":
            return SESSION_RESPONSE, 200, 1, body
        if action == "GetPassengerDataRQ":
            return response_xml, 200, 1, body
        if action == "SessionCloseRQ":
            return "<ok/>", 200, 1, body
        raise AssertionError(action)

    stored = {
        "flightNumber": "11",
        "departureDate": "2017-01-04",
        "origin": "AUH",
        "passengers": [
            {"pnr": "OLBJTY", "lastName": "CHARLES", "firstName": "JANE"},
            {"pnr": "OTHER1", "lastName": "NOMATCH", "firstName": "X"},
        ],
    }
    with patch("backend.api.routes.passengers.get_db", return_value=_DB(stored)), patch.object(
        SabreClient, "_post", fake_post
    ):
        result = get_passenger_detail(
            flight_number="11",
            pnr="OLBJTY",
            origin="AUH",
            date="2017-01-04",
            airline="EY",
        )

    data_calls = [body for action, body in captured if action == "GetPassengerDataRQ"]
    assert len(data_calls) == 1
    request = data_calls[0]
    assert 'includeTimaticInfo="true"' in request
    assert "<LastName>CHARLES</LastName>" in request
    assert "<FirstName>JANE</FirstName>" in request
    assert "<PNRLocator>OLBJTY</PNRLocator>" in request
    assert "<Airline>EY</Airline>" in request
    assert "<Flight>11</Flight>" in request
    assert "<DepartureDate>2017-01-04</DepartureDate>" in request
    assert "<Origin>AUH</Origin>" in request

    assert result["passengers"][0]["pnr"] == "OLBJTY"
    _assert_country(
        result["passengers"][0]["timaticInfo"],
        status="OK TO BOARD",
        country_status="OK TO BOARD",
        country="UNITED KINGDOM",
        country_code="GB",
        visible=["OK TO BOARD", "VISA", "WARNING", VISA_WARNING],
        sections=[
            {
                "sectionName": "VISA",
                "sectionText": "",
                "subsections": [
                    {
                        "subSectionName": "WARNING",
                        "subSectionType": "INFORMATION",
                        "subSectionText": VISA_WARNING,
                    }
                ],
            }
        ],
    )
