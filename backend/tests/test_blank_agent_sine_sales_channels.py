"""Sales-channel mix must include teletype bookings that omit AgentSine.

Trip Search sources for GDS teletype bookings carry BookingSource and
PseudoCityCode and leave AgentSine unset. The dashboard counts a reservation
only when agentSine is non-empty, so those seats never reach the online,
agent, corporate, or other totals. The commercial and revenue tabs divide
by those totals, which overstates the web share.

These tests fail while that skip is in place. They pass once every
reservation is counted: a non-empty agent sine stays the channel key, and a
blank agent sine is classified from the pseudo city, booking source, or CRS
code instead of being dropped.

Run:
    python -m pytest backend/tests/test_blank_agent_sine_sales_channels.py -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes import flights
from backend.feeder.converter import _ensure_list, _safe_int, _strip_ns, convert_reservations
from backend.sabre.client import SabreClient

ONLINE_SINES = {"WEB", "MOB", "APP", "ND1", "ND2", "NDC"}
CATEGORY_KEYS = ("online", "agent", "corporate", "other")

SAMPLE_XML = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "sample_data",
    "sample_xmlfiles",
    "Trip_SearchRS_GF2057_BOM_2026-03-18.xml",
)

# Same SOAP shape as a Trip_SearchRS body, with no session material.
# One web seat and a four-seat teletype booking that omits AgentSine.
SYNTHETIC_TRIP_SEARCH = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS>
      <ReservationsList NumberResults="2" TotalResults="2">
        <Reservations>
          <Reservation Locator="TTYPNR">
            <GetReservationRS>
              <Reservation numberInParty="4">
                <POS>
                  <Source BookingSource="A0A0" PseudoCityCode="TTY" HomePseudoCityCode="TTY" ISOCountry="US" AirlineVendorID="GF">
                    <TTYRecordLocator>
                      <CRSCode>1A</CRSCode>
                    </TTYRecordLocator>
                  </Source>
                </POS>
              </Reservation>
            </GetReservationRS>
          </Reservation>
          <Reservation Locator="WEBPNR">
            <GetReservationRS>
              <Reservation numberInParty="1">
                <POS>
                  <Source AgentSine="WEB" BookingSource="A0A0" PseudoCityCode="WGF" HomePseudoCityCode="WGF" ISOCountry="BH" AirlineVendorID="GF"/>
                </POS>
              </Reservation>
            </GetReservationRS>
          </Reservation>
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _text(value):
    if isinstance(value, dict):
        value = value.get("#text", "")
    if value is None:
        return ""
    return str(value).strip()


def _source_facts(raw_body):
    """Read party size and source attributes from a parsed Trip_SearchRS.

    This is the input oracle. It records the attributes Sabre sent, before
    the dashboard decides which reservations to count.
    """
    data = _strip_ns(raw_body)
    wrapper = data.get("ReservationsList", {})
    raw_reservations = _ensure_list(
        (wrapper.get("Reservations") or {}).get("Reservation", [])
    )
    facts = []
    for record in raw_reservations:
        inner = (record.get("GetReservationRS") or {}).get("Reservation") or {}
        if not isinstance(inner, dict):
            inner = {}
        pos_raw = inner.get("POS") or {}
        source = pos_raw.get("Source") if isinstance(pos_raw, dict) else {}
        if not isinstance(source, dict):
            source = {}
        tty = source.get("TTYRecordLocator") or {}
        if not isinstance(tty, dict):
            tty = {}
        facts.append({
            "pnr": _text(record.get("@Locator")),
            "party": _safe_int(inner.get("@numberInParty")),
            "agentSine": _text(source.get("@AgentSine")),
            "pseudoCityCode": _text(source.get("@PseudoCityCode")),
            "homePseudoCityCode": _text(source.get("@HomePseudoCityCode")),
            "bookingSource": _text(source.get("@BookingSource")),
            "crsCode": _text(tty.get("CRSCode")),
        })
    return facts


def _fallback_keys(fact):
    keys = set()
    for field in ("pseudoCityCode", "homePseudoCityCode", "bookingSource", "crsCode"):
        if fact[field]:
            keys.add(fact[field])
    return keys


def _parse_trip_search(xml_text):
    parsed = SabreClient._parse_xml(xml_text)
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _dashboard_for(xml_text, flight_number, origin, departure_date, monkeypatch):
    """Run the flight dashboard on reservations produced by the real converter.

    The passenger list is only the gate that makes the dashboard build
    insights. Channel totals come from the reservation document. `_raw` is
    removed first, matching the live reservations read.
    """
    raw_body = _parse_trip_search(xml_text)
    facts = _source_facts(raw_body)
    reservation_doc = convert_reservations(
        raw_body, "GF", flight_number, origin, departure_date,
    )
    reservation_doc.pop("_raw", None)
    reservation_doc.pop("_id", None)

    passenger_list = {
        "passengers": [{
            "pnr": facts[0]["pnr"],
            "lastName": "PASSENGER",
            "firstName": "TEST",
            "cabin": "Y",
        }],
        "totalPassengers": 1,
        "origin": origin,
        "destination": "DMM",
        "departureDate": departure_date,
    }

    def fetch(_db, flight_number_arg, origin_arg, date_arg, snapshot_sequence=None):
        assert flight_number_arg == flight_number
        assert origin_arg == origin
        assert date_arg == departure_date
        assert snapshot_sequence is None
        return {
            "flight_status": {"status": "OPEN", "origin": origin, "departureDate": departure_date},
            "passenger_list": passenger_list,
            "reservations": reservation_doc,
            "trip_reports": None,
            "schedule": None,
            "change_summary": {},
            "flight_meta": None,
        }

    monkeypatch.setattr(flights, "get_db", lambda: object())
    monkeypatch.setattr(flights, "_fetch_dashboard_data_parallel", fetch)
    monkeypatch.setattr(flights, "_set_cache", lambda *_args, **_kwargs: None)

    payload = flights.get_flight_dashboard(
        flight_number,
        origin=origin,
        date=departure_date,
        snapshot_sequence=None,
    )
    return facts, reservation_doc, payload


def _assert_every_seat_is_classified(facts, reservation_doc, payload):
    stored = {rv.get("pnr"): rv for rv in reservation_doc.get("reservations", [])}
    assert set(stored) == {fact["pnr"] for fact in facts}

    for fact in facts:
        assert stored[fact["pnr"]].get("numberInParty") == fact["party"]

    insights = payload["insights"]
    assert insights is not None
    categories = insights["bookingChannels"]["categories"]
    channels = insights["bookingChannels"]["channels"]
    assert set(categories) == set(CATEGORY_KEYS)

    total_party = sum(fact["party"] for fact in facts)
    online_party = sum(
        fact["party"] for fact in facts if fact["agentSine"].upper() in ONLINE_SINES
    )
    blank = [fact for fact in facts if not fact["agentSine"]]
    blank_party = sum(fact["party"] for fact in blank)
    assert blank_party > 0

    # The four family totals are what the commercial tab stacks and divides by.
    family_total = sum(categories[key] for key in CATEGORY_KEYS)
    assert family_total == total_party
    assert categories["online"] == online_party
    assert categories["online"] / family_total == online_party / total_party

    sine_parties = {}
    for fact in facts:
        if fact["agentSine"]:
            sine_parties[fact["agentSine"]] = sine_parties.get(fact["agentSine"], 0) + fact["party"]
    for sine, party in sine_parties.items():
        assert channels.get(sine) == party

    blank_allowed = set()
    for fact in blank:
        blank_allowed |= _fallback_keys(fact)
    blank_keys = [key for key in channels if key not in sine_parties]
    assert set(blank_keys) <= blank_allowed
    assert sum(channels[key] for key in blank_keys) == blank_party
    assert sum(channels.values()) == total_party

    # specialRequestsSummary.bookingSources is the other counter that skips
    # an empty agent sine. It counts reservations, not seats.
    sources = payload["specialRequestsSummary"]["bookingSources"]
    sine_counts = {}
    for fact in facts:
        if fact["agentSine"]:
            sine_counts[fact["agentSine"]] = sine_counts.get(fact["agentSine"], 0) + 1
    for sine, count in sine_counts.items():
        assert sources.get(sine) == count
    blank_source_keys = [key for key in sources if key not in sine_counts]
    assert set(blank_source_keys) <= blank_allowed
    assert sum(sources[key] for key in blank_source_keys) == len(blank)
    assert sum(sources.values()) == len(facts)


def test_gulf_air_tty_bookings_count_in_dashboard_channel_mix(monkeypatch):
    """GF2057's committed Trip Search response includes teletype bookings.

    Those sources have a booking source and pseudo city and no agent sine.
    Their party size has to land in the channel mix the dashboard returns.
    """
    with open(SAMPLE_XML, encoding="utf-8") as handle:
        xml_text = handle.read()

    facts, reservation_doc, payload = _dashboard_for(
        xml_text, "2057", "BOM", "2026-03-18", monkeypatch,
    )

    teletype = next(fact for fact in facts if fact["pnr"] == "GCPVKA")
    assert teletype["agentSine"] == ""
    assert teletype["bookingSource"] == "A0A0"
    assert teletype["pseudoCityCode"] == "TTY"
    assert teletype["homePseudoCityCode"] == "TTY"
    assert teletype["crsCode"] == "1A"
    assert teletype["party"] == 1

    stored = next(
        rv for rv in reservation_doc["reservations"] if rv["pnr"] == "GCPVKA"
    )
    assert stored["pointOfSale"]["agentSine"] == ""
    assert stored["pointOfSale"]["bookingSource"] == "A0A0"
    assert stored["pointOfSale"]["pseudoCityCode"] == "TTY"
    assert stored["numberInParty"] == 1

    _assert_every_seat_is_classified(facts, reservation_doc, payload)


def test_blank_agent_sine_contributes_its_party_size(monkeypatch):
    """A blank sine must add numberInParty, not disappear and not join WEB."""
    facts, reservation_doc, payload = _dashboard_for(
        SYNTHETIC_TRIP_SEARCH, "2057", "BOM", "2026-03-18", monkeypatch,
    )

    by_pnr = {fact["pnr"]: fact for fact in facts}
    assert by_pnr["TTYPNR"]["agentSine"] == ""
    assert by_pnr["TTYPNR"]["party"] == 4
    assert by_pnr["TTYPNR"]["pseudoCityCode"] == "TTY"
    assert by_pnr["TTYPNR"]["crsCode"] == "1A"
    assert by_pnr["WEBPNR"]["agentSine"] == "WEB"
    assert by_pnr["WEBPNR"]["party"] == 1

    _assert_every_seat_is_classified(facts, reservation_doc, payload)

    categories = payload["insights"]["bookingChannels"]["categories"]
    channels = payload["insights"]["bookingChannels"]["channels"]
    assert categories["online"] == 1
    assert sum(categories[key] for key in CATEGORY_KEYS) == 5
    assert channels["WEB"] == 1
    assert sum(count for key, count in channels.items() if key != "WEB") == 4
