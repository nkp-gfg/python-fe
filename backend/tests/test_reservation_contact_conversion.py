"""Trip Search contact fields must survive conversion and the reservations API.

The committed Gulf Air responses place phone numbers, email addresses, and
remarks on the reservation, place a passenger email and mobile contact request
on the passenger, and place the emergency-contact name and phone on
EmergencyContactRequest. These tests parse those responses with the same
xmltodict entry points SabreClient.get_reservations uses, then require
convert_reservations and the reservations response model to keep the values.

They fail while that data is dropped.
"""

from pathlib import Path

from backend.api.routes.passengers import _build_nationality_lookup

_REPO_ROOT = Path(__file__).resolve().parents[2]
from backend.feeder.converter import _strip_ns, convert_reservations
from backend.sabre.client import SabreClient
from backend.sabre.models import ReservationsResponse

_SAMPLES = (
    (
        _REPO_ROOT / "sample_data/sample_xmlfiles/Trip_SearchRS_GF2057_BOM_2026-03-18.xml",
        "GF",
        "2057",
        "BOM",
        "2026-03-18",
    ),
    (
        _REPO_ROOT / "sample_data/sample_xmlfiles/Trip_SearchRS_GF2057_BOM_2026-03-19.xml",
        "GF",
        "2057",
        "BOM",
        "2026-03-19",
    ),
)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_trip_search(path):
    """Parse a saved Trip_SearchRS envelope the way get_reservations does."""
    parsed = SabreClient._parse_xml(path.read_text(encoding="utf-8"))
    return SabreClient._extract_body(None, parsed, "Trip_SearchRS")


def _remark_text(remark):
    lines = _as_list((remark.get("RemarkLines") or {}).get("RemarkLine"))
    parts = []
    for line in lines:
        if isinstance(line, dict) and line.get("Text"):
            parts.append(line["Text"])
        elif isinstance(line, str) and line:
            parts.append(line)
    return "\n".join(parts)


def _ssr_from_generic(node):
    return {
        "code": node.get("Code") or "",
        "text": node.get("FreeText") or "",
        "status": node.get("ActionCode") or "",
        "airline": node.get("AirlineCode") or "",
    }


def _expected_contacts(inner):
    """Contact values present on one parsed reservation element."""
    phones = []
    for phone in _as_list((inner.get("PhoneNumbers") or {}).get("PhoneNumber")):
        if isinstance(phone, dict) and phone.get("Number"):
            phones.append(phone["Number"])

    emails = []
    for email in _as_list((inner.get("EmailAddresses") or {}).get("EmailAddress")):
        if isinstance(email, dict) and email.get("Address"):
            emails.append(email["Address"])

    remarks = []
    for remark in _as_list((inner.get("Remarks") or {}).get("Remark")):
        if not isinstance(remark, dict):
            continue
        text = _remark_text(remark)
        if text:
            remarks.append({"type": remark.get("@type") or "", "text": text})

    ssrs = []
    for node in _as_list(inner.get("GenericSpecialRequests")):
        if isinstance(node, dict):
            ssrs.append(_ssr_from_generic(node))

    passengers = []
    raw_passengers = _as_list(
        ((inner.get("PassengerReservation") or {}).get("Passengers") or {}).get(
            "Passenger"
        )
    )
    for passenger in raw_passengers:
        if not isinstance(passenger, dict):
            continue
        email_node = passenger.get("EmailAddress") or {}
        if isinstance(email_node, list):
            email_node = email_node[0] if email_node else {}
        email = email_node.get("Address") or "" if isinstance(email_node, dict) else ""
        if email:
            emails.append(email)
        special = passenger.get("SpecialRequests") or {}
        for node in _as_list(special.get("GenericSpecialRequest")):
            if isinstance(node, dict):
                ssrs.append(_ssr_from_generic(node))
        emergency_nodes = [
            node
            for node in _as_list(special.get("EmergencyContactRequest"))
            if isinstance(node, dict) and node
        ]
        emergency = emergency_nodes[0] if emergency_nodes else {}
        passengers.append(
            {
                "lastName": passenger.get("LastName") or "",
                "firstName": passenger.get("FirstName") or "",
                "email": email,
                "hasEmergencyContact": emergency.get("PassengerRefusal") == "false",
                "emergencyContactName": emergency.get("ContactName") or "",
                "emergencyContactPhone": emergency.get("PhoneNumber") or "",
            }
        )
    return {
        "phones": phones,
        "emails": emails,
        "remarks": remarks,
        "ssrs": ssrs,
        "passengers": passengers,
    }


class _ReservationsDb:
    """Stand-in for the collection handle _build_nationality_lookup reads."""

    def __init__(self, document):
        self._document = document

    def __getitem__(self, name):
        if name != "reservations":
            raise KeyError(name)
        return self

    def find_one(self, query, sort=None):
        return self._document


def _converted_samples():
    converted = []
    for path, airline, flight_number, origin, departure_date in _SAMPLES:
        body = _parse_trip_search(path)
        document = convert_reservations(
            body, airline, flight_number, origin, departure_date
        )
        stripped = _strip_ns(body)
        raw_reservations = _as_list(
            (stripped.get("ReservationsList") or {})
            .get("Reservations", {})
            .get("Reservation")
        )
        converted.append((path.name, document, raw_reservations))
    return converted


def _problems_for_reservation(pnr, expected, actual):
    problems = []
    actual_phones = [phone.get("number") for phone in actual.get("phones") or []]
    for number in expected["phones"]:
        if number not in actual_phones:
            problems.append(f"{pnr} phone {number!r} missing from {actual_phones!r}")
    actual_emails = list(actual.get("emails") or [])
    for address in expected["emails"]:
        if address not in actual_emails:
            problems.append(f"{pnr} email {address!r} missing from {actual_emails!r}")
    for remark in expected["remarks"]:
        if not any(
            item.get("type") == remark["type"] and item.get("text") == remark["text"]
            for item in actual.get("remarks") or []
        ):
            problems.append(
                f"{pnr} remark {remark['type']!r} {remark['text']!r} missing"
            )
    for ssr in expected["ssrs"]:
        if not any(
            item.get("code") == ssr["code"]
            and item.get("text") == ssr["text"]
            and (item.get("status") or "") == ssr["status"]
            and (item.get("airline") or "") == ssr["airline"]
            for item in actual.get("ssrRequests") or []
        ):
            problems.append(
                f"{pnr} special request {ssr['code']!r} {ssr['text']!r} missing"
            )
    actual_passengers = list(actual.get("passengers") or [])
    for expected_passenger in expected["passengers"]:
        matches = [
            passenger
            for passenger in actual_passengers
            if passenger.get("lastName") == expected_passenger["lastName"]
            and passenger.get("firstName") == expected_passenger["firstName"]
        ]
        if not matches:
            problems.append(
                f"{pnr} passenger {expected_passenger['lastName']} missing"
            )
            continue
        actual_passenger = matches.pop(0)
        actual_passengers.remove(actual_passenger)
        if expected_passenger["email"] and actual_passenger.get("email") != expected_passenger["email"]:
            problems.append(
                f"{pnr} {expected_passenger['lastName']} email "
                f"{expected_passenger['email']!r} missing from passenger"
            )
        if expected_passenger["emergencyContactPhone"] or expected_passenger["emergencyContactName"]:
            if actual_passenger.get("hasEmergencyContact") is not expected_passenger["hasEmergencyContact"]:
                problems.append(
                    f"{pnr} {expected_passenger['lastName']} emergency-contact flag "
                    f"is {actual_passenger.get('hasEmergencyContact')!r}"
                )
            if actual_passenger.get("emergencyContactName") != expected_passenger["emergencyContactName"]:
                problems.append(
                    f"{pnr} {expected_passenger['lastName']} emergency contact name "
                    f"{expected_passenger['emergencyContactName']!r} missing"
                )
            if actual_passenger.get("emergencyContactPhone") != expected_passenger["emergencyContactPhone"]:
                problems.append(
                    f"{pnr} {expected_passenger['lastName']} emergency contact phone "
                    f"{expected_passenger['emergencyContactPhone']!r} missing"
                )
    return problems


def test_trip_search_samples_keep_contact_fields():
    """Stored reservations and the reservations response model keep Sabre contacts."""
    saw_phone = saw_email = saw_remark = saw_ssr = saw_emergency = False
    problems = []
    for name, document, raw_reservations in _converted_samples():
        assert raw_reservations, name
        api_document = ReservationsResponse.model_validate(document).model_dump()
        stored_by_pnr = {item["pnr"]: item for item in document["reservations"]}
        api_by_pnr = {item["pnr"]: item for item in api_document["reservations"]}
        for raw in raw_reservations:
            pnr = raw.get("@Locator") or ""
            inner = (raw.get("GetReservationRS") or {}).get("Reservation") or {}
            expected = _expected_contacts(inner)
            if expected["phones"]:
                saw_phone = True
            if expected["emails"]:
                saw_email = True
            if expected["remarks"]:
                saw_remark = True
            if expected["ssrs"]:
                saw_ssr = True
            if any(passenger["emergencyContactPhone"] for passenger in expected["passengers"]):
                saw_emergency = True
            if pnr not in stored_by_pnr:
                problems.append(f"{name} {pnr} missing from converted reservations")
                continue
            problems.extend(
                f"{name} stored {item}"
                for item in _problems_for_reservation(pnr, expected, stored_by_pnr[pnr])
            )
            problems.extend(
                f"{name} api {item}"
                for item in _problems_for_reservation(pnr, expected, api_by_pnr.get(pnr, {}))
            )
    assert saw_phone and saw_email and saw_remark and saw_ssr and saw_emergency
    assert not problems, "\n".join(problems[:40])


def test_passenger_list_enrichment_keeps_emergency_contact_phone():
    """The passenger-list lookup must copy the emergency contact, not only the flag."""
    problems = []
    checked = 0
    for name, document, raw_reservations in _converted_samples():
        lookup = _build_nationality_lookup(
            _ReservationsDb(document),
            document["flightNumber"],
            document["departureDate"],
            origin=document["departureAirport"],
        )
        for raw in raw_reservations:
            pnr = raw.get("@Locator") or ""
            inner = (raw.get("GetReservationRS") or {}).get("Reservation") or {}
            for passenger in _expected_contacts(inner)["passengers"]:
                if not passenger["emergencyContactPhone"]:
                    continue
                checked += 1
                entry = lookup.get((pnr, passenger["lastName"].upper()), {})
                if entry.get("emergencyContactPhone") != passenger["emergencyContactPhone"]:
                    problems.append(
                        f"{name} {pnr} {passenger['lastName']} phone "
                        f"{passenger['emergencyContactPhone']!r} missing from {entry!r}"
                    )
                if entry.get("emergencyContactName") != passenger["emergencyContactName"]:
                    problems.append(
                        f"{name} {pnr} {passenger['lastName']} name "
                        f"{passenger['emergencyContactName']!r} missing from passenger lookup"
                    )
                if entry.get("hasEmergencyContact") is not True:
                    problems.append(
                        f"{name} {pnr} {passenger['lastName']} emergency flag missing"
                    )
    assert checked > 0
    assert not problems, "\n".join(problems[:40])
