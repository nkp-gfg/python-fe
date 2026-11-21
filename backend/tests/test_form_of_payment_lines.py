"""Every Sabre form-of-payment line must survive reservation conversion.

These tests fail while conversion keeps only the first FP element. They pass
once every FP text is stored as a list, change detection compares that list,
the dashboard counts every line without using a card number as a chart key,
and the reservation APIs return the full list.

Run:
    python -m pytest backend/tests/test_form_of_payment_lines.py -q
"""

import asyncio
import re
from pathlib import Path

from fastapi.routing import serialize_response

from backend.api.routes.flights import _build_insights
from backend.api.routes.reservations import get_reservation_by_pnr, router as reservations_router
from backend.feeder.converter import _ensure_list, _strip_ns, convert_reservations
from backend.feeder.differ import detect_changes
from backend.sabre.client import SabreClient

CARD_LINE = "TBM*AX000000XXXXX0000"
SECOND_LINE = "SECOND-PAYER"
THIRD_LINE = "THIRD-LINE"
CASH_LINE = "CASH VOUCHER"
REPLACED_LINE = "REPLACED-PAYER"

_CARD_DIGITS = re.compile(r"\d{6,}")
_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "sample_xmlfiles"
    / "Trip_SearchRS_GF2057_BOM_2026-03-19.xml"
)
_FLIGHT = {
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}


def _fp_texts(inner):
    """Read every FP text from a namespace-stripped GetReservationRS reservation."""
    elements = _ensure_list(
        (inner.get("OpenReservationElements") or {}).get("OpenReservationElement", [])
    )
    lines = []
    for element in elements:
        if not isinstance(element, dict) or element.get("@type") != "FP":
            continue
        form = element.get("FormOfPayment") or {}
        if isinstance(form, list):
            forms = form
        else:
            forms = [form]
        for form in forms:
            other = form.get("Other") if isinstance(form, dict) else None
            if isinstance(other, dict):
                lines.append(other.get("Text") or "")
            elif isinstance(other, str):
                lines.append(other)
            elif isinstance(other, list):
                for item in other:
                    if isinstance(item, dict):
                        lines.append(item.get("Text") or "")
                    elif isinstance(item, str):
                        lines.append(item)
    return lines


def _reservation_bodies(raw_data):
    data = _strip_ns(raw_data)
    reservations = _ensure_list(
        ((data.get("ReservationsList") or {}).get("Reservations") or {}).get(
            "Reservation", []
        )
    )
    bodies = []
    for reservation in reservations:
        inner = (reservation.get("GetReservationRS") or {}).get("Reservation") or {}
        bodies.append(inner)
    return bodies


def _convert_xml(xml_text, flight_number="2057", origin="BOM", departure_date="2026-03-19"):
    """Parse a Trip_Search SOAP body the way SabreClient.get_reservations does."""
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(None, parsed, "Trip_SearchRS")
    return convert_reservations(body, "GF", flight_number, origin, departure_date), body


def _assert_lines(stored, expected, where):
    if not isinstance(stored, list) or stored != expected:
        stored_count = len(stored) if isinstance(stored, (list, str)) else None
        raise AssertionError(
            f"{where}: expected {len(expected)} form-of-payment lines, "
            f"stored type {type(stored).__name__} length {stored_count}"
        )


def _soap(reservations_xml):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <Trip_SearchRS xmlns="http://webservices.sabre.com/triprecord">
      <ReservationsList NumberResults="1">
        <Reservations>
          {reservations_xml}
        </Reservations>
      </ReservationsList>
    </Trip_SearchRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _reservation(locator, party, payment_xml):
    payment_block = ""
    if payment_xml.strip():
        payment_block = (
            "<stl19:OpenReservationElements>"
            f"{payment_xml}"
            "</stl19:OpenReservationElements>"
        )
    return f"""
<Reservation Locator="{locator}">
  <stl19:GetReservationRS xmlns:stl19="http://webservices.sabre.com/pnrbuilder/v1_19"
      xmlns:or114="http://services.sabre.com/res/or/v1_14">
    <stl19:Reservation numberInParty="{party}" numberOfInfants="0">
      <stl19:BookingDetails>
        <stl19:RecordLocator>{locator}</stl19:RecordLocator>
        <stl19:CreationTimestamp>2026-03-18T06:41:00</stl19:CreationTimestamp>
      </stl19:BookingDetails>
      <stl19:PassengerReservation>
        <stl19:Passengers>
          <stl19:Passenger>
            <stl19:LastName>DOE</stl19:LastName>
            <stl19:FirstName>JANE</stl19:FirstName>
          </stl19:Passenger>
        </stl19:Passengers>
      </stl19:PassengerReservation>
      {payment_block}
    </stl19:Reservation>
  </stl19:GetReservationRS>
</Reservation>
"""


def _fp(text, other_as_text=False):
    if other_as_text:
        other = f"<or114:Other>{text}</or114:Other>"
    else:
        other = f"<or114:Other><or114:Text>{text}</or114:Text></or114:Other>"
    return f"""
<or114:OpenReservationElement type="FP">
  <or114:FormOfPayment migrated="false">
    {other}
  </or114:FormOfPayment>
</or114:OpenReservationElement>
"""


def _element(element_type, text):
    return f"""
<or114:OpenReservationElement type="{element_type}">
  <or114:FormOfPayment>
    <or114:Other><or114:Text>{text}</or114:Text></or114:Other>
  </or114:FormOfPayment>
</or114:OpenReservationElement>
"""


def _multi_line_xml(second_line=SECOND_LINE):
    payments = (
        _element("SR", "NOT-A-PAYMENT")
        + _fp(CARD_LINE)
        + _element("XX", "STILL-NOT-A-PAYMENT")
        + _fp(second_line)
        + _fp(THIRD_LINE)
    )
    cash = _fp(CASH_LINE, other_as_text=True)
    return _soap(
        _reservation("ABC123", 1, payments)
        + _reservation("CASH01", 2, cash)
        + _reservation("NONE01", 1, "")
    )


def _by_pnr(converted):
    return {item["pnr"]: item for item in converted["reservations"]}


def test_conversion_keeps_every_form_of_payment_line():
    """A booking with three FP lines, one cash line, and a line-free booking."""
    converted, body = _convert_xml(_multi_line_xml())
    expected = {}
    for inner in _reservation_bodies(body):
        locator = (inner.get("BookingDetails") or {}).get("RecordLocator")
        expected[locator] = _fp_texts(inner)

    assert set(expected) == {"ABC123", "CASH01", "NONE01"}
    assert expected["ABC123"] == [CARD_LINE, SECOND_LINE, THIRD_LINE]
    assert expected["CASH01"] == [CASH_LINE]
    assert expected["NONE01"] == []

    stored = _by_pnr(converted)
    for locator, lines in expected.items():
        _assert_lines(stored[locator].get("formOfPayment"), lines, locator)


def test_later_payment_line_change_is_detected():
    """A change that touches only a later FP line must be reported."""
    before_doc, _ = _convert_xml(_multi_line_xml(SECOND_LINE))
    after_doc, _ = _convert_xml(_multi_line_xml(REPLACED_LINE))
    before_booking = _by_pnr(before_doc)["ABC123"]
    after_booking = _by_pnr(after_doc)["ABC123"]
    changes = detect_changes(
        {"snapshotId": "before", "data": before_doc},
        {"snapshotId": "after", "data": after_doc},
        "reservations",
        _FLIGHT,
    )
    payment_changes = [
        change for change in changes if "formOfPayment" in str(change.get("field") or "")
    ]
    if len(payment_changes) != 1:
        raise AssertionError(
            f"expected one formOfPayment change, found {len(payment_changes)} "
            f"of {len(changes)} changes"
        )
    old_value = str(payment_changes[0].get("oldValue"))
    new_value = str(payment_changes[0].get("newValue"))
    if SECOND_LINE not in old_value or REPLACED_LINE not in new_value or REPLACED_LINE in old_value:
        raise AssertionError("formOfPayment change does not record the later line")
    if CARD_LINE not in old_value or CARD_LINE not in new_value:
        raise AssertionError("formOfPayment change dropped an unchanged payment line")
    if THIRD_LINE not in old_value or THIRD_LINE not in new_value:
        raise AssertionError("formOfPayment change dropped a later unchanged payment line")
    _assert_lines(
        before_booking.get("formOfPayment"),
        [CARD_LINE, SECOND_LINE, THIRD_LINE],
        "before",
    )
    _assert_lines(
        after_booking.get("formOfPayment"),
        [CARD_LINE, REPLACED_LINE, THIRD_LINE],
        "after",
    )


def test_party_size_change_still_detected():
    before_doc, _ = _convert_xml(_multi_line_xml())
    larger = _soap(
        _reservation("ABC123", 4, _fp(CARD_LINE) + _fp(SECOND_LINE) + _fp(THIRD_LINE))
        + _reservation("CASH01", 2, _fp(CASH_LINE, other_as_text=True))
        + _reservation("NONE01", 1, "")
    )
    after_doc, _ = _convert_xml(larger)
    changes = detect_changes(
        {"snapshotId": "before", "data": before_doc},
        {"snapshotId": "after", "data": after_doc},
        "reservations",
        _FLIGHT,
    )
    party = [
        change for change in changes
        if change.get("changeType") == "RESERVATION_PARTY_CHANGE"
        and change.get("field") == "reservation.ABC123.numberInParty"
    ]
    assert len(party) == 1
    assert party[0]["oldValue"] == "1"
    assert party[0]["newValue"] == "4"


def _assert_chart(counts, lines):
    if not isinstance(counts, dict):
        raise AssertionError(
            f"paymentMethods must be a dict, got {type(counts).__name__}"
        )
    present = [line for line in lines if line]
    non_card = [line for line in present if not _CARD_DIGITS.search(line)]
    for line in non_card:
        if counts.get(line) != present.count(line):
            raise AssertionError(
                f"non-card payment line missing from chart; "
                f"lines={len(present)} keys={len(counts)} total={sum(counts.values())}"
            )
    if sum(counts.values()) != len(present):
        raise AssertionError(
            f"chart counted {sum(counts.values())} payments, expected {len(present)} lines"
        )
    for key in counts:
        if _CARD_DIGITS.search(str(key)):
            raise AssertionError("payment chart key contains a card number")


def test_dashboard_counts_every_payment_line_without_card_numbers():
    converted, body = _convert_xml(_multi_line_xml())
    lines = []
    for inner in _reservation_bodies(body):
        lines.extend(_fp_texts(inner))
    insights = _build_insights(
        [{"isThru": False, "editCodes": []}],
        {"reservations": converted["reservations"]},
        None,
        {"departureDate": "2026-03-19"},
        {},
    )
    _assert_chart(insights.get("paymentMethods"), lines)


def _list_route():
    for route in reservations_router.routes:
        if route.path == "/flights/{flight_number}/reservations" and "GET" in route.methods:
            return route
    raise AssertionError("reservations list route is missing")


def test_reservations_list_response_includes_every_payment_line():
    converted, body = _convert_xml(_multi_line_xml())
    payload = {
        "airline": converted["airline"],
        "flightNumber": converted["flightNumber"],
        "departureAirport": converted["departureAirport"],
        "departureDate": converted["departureDate"],
        "fetchedAt": converted["fetchedAt"],
        "totalResults": converted["totalResults"],
        "reservations": converted["reservations"],
    }
    serialized = asyncio.run(
        serialize_response(
            field=_list_route().response_field,
            response_content=payload,
            is_coroutine=True,
        )
    )
    stored = {item["pnr"]: item for item in serialized["reservations"]}
    expected = {}
    for inner in _reservation_bodies(body):
        locator = (inner.get("BookingDetails") or {}).get("RecordLocator")
        expected[locator] = _fp_texts(inner)
    for locator, lines in expected.items():
        record = stored[locator]
        if "formOfPayment" not in record:
            raise AssertionError(f"{locator}: reservations response omits formOfPayment")
        _assert_lines(record.get("formOfPayment"), lines, f"list response {locator}")


def test_reservation_by_pnr_returns_every_payment_line(monkeypatch):
    converted, _ = _convert_xml(_multi_line_xml())

    class _Reservations:
        def find_one(self, query, sort=None):
            return converted

    monkeypatch.setattr(
        "backend.api.routes.reservations.get_db",
        lambda: {"reservations": _Reservations()},
    )
    response = get_reservation_by_pnr("2057", "ABC123", date="2026-03-19")
    matches = response["reservations"]
    assert len(matches) == 1
    _assert_lines(
        matches[0].get("formOfPayment"),
        [CARD_LINE, SECOND_LINE, THIRD_LINE],
        "by-pnr",
    )


def test_committed_trip_search_sample_keeps_later_payment_lines():
    """The committed GF2057 Trip_Search response is parsed by the product path."""
    xml_text = _SAMPLE.read_text(encoding="utf-8")
    converted, body = _convert_xml(xml_text)
    stored = _by_pnr(converted)
    checked = []
    for inner in _reservation_bodies(body):
        locator = (inner.get("BookingDetails") or {}).get("RecordLocator")
        lines = _fp_texts(inner)
        checked.append((len(lines), len(set(lines)), locator, lines))
    distinct_multi = sum(1 for count, distinct, _, _ in checked if count >= 2 and distinct >= 2)
    if distinct_multi < 1:
        raise AssertionError(
            "sample Trip_Search body has no booking with two different payment lines"
        )
    for _, _, locator, lines in sorted(checked, key=lambda item: -item[0]):
        record = stored.get(locator)
        if record is None:
            raise AssertionError(f"{locator}: converted reservation missing")
        _assert_lines(record.get("formOfPayment"), lines, locator)

    lines = []
    for inner in _reservation_bodies(body):
        lines.extend(_fp_texts(inner))
    insights = _build_insights(
        [{"isThru": False, "editCodes": []}],
        {"reservations": converted["reservations"]},
        None,
        {"departureDate": "2026-03-19"},
        {},
    )
    _assert_chart(insights.get("paymentMethods"), lines)
