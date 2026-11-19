"""Spec for the desired-upgrade audit false positive.

These tests fail while the audit treats a cabin letter and a class-of-service
letter as the same value. They pass once a boarded passenger is reported as
an unprocessed upgrade only when all of the following are true:

- desiredBookingClass is set
- the passenger is boarded
- that record locator has no UPGRADE_CONFIRMED change and no CABIN_CHANGE
  whose metadata.direction is UPGRADE
- bookingClass is not already the desired class of service
- the current cabin is still the cabin implied by the original booking class
- the current cabin is not the cabin implied by the desired class

Cabin and class of service are different Sabre fields. This product groups
class letters J, C, D, I, and R (flights insights) and J, C, D, Z, R, and F
(revenue and insight charts) into the J cabin. Every other class letter used
here implies the Y cabin. Desired classes F, C, D, and I therefore belong to
cabin J even though those letters are not the cabin letter J.

The tests call GET /flights/{flightNumber}/audit's handler on a passenger
list produced by the Sabre XML parser and convert_passenger_list. Change
history, where a test uses it, is produced by diff_passenger_list.

Run from the repository root:

    PYTHONPATH=. python3 -m unittest backend.tests.test_desired_upgrade_audit -v
"""

import unittest
from unittest.mock import patch

from backend.api.routes.audit import get_flight_audit
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import diff_passenger_list
from backend.sabre.client import SabreClient

AIRLINE = "GF"
FLIGHT = "2057"
ORIGIN = "BAH"
DATE = "2026-03-19"
FLIGHT_INFO = {
    "flightNumber": FLIGHT,
    "origin": ORIGIN,
    "departureDate": DATE,
}

# Class-of-service letters this product places in the business cabin.
# backend/api/routes/flights.py: {"J", "C", "D", "I", "R"}
# frontend revenue-tab.tsx and flight-insight-tabs.tsx: {"J", "C", "D", "Z", "R", "F"}
J_CABIN_CLASSES = frozenset({"J", "C", "D", "I", "R", "Z", "F"})
REQUESTED_CLASSES_NAMED_BY_FINDING = ("F", "C", "D", "I")


def cabin_for_class(class_letter):
    """Cabin implied by a class-of-service letter in this product."""
    if class_letter in J_CABIN_CLASSES:
        return "J"
    return "Y"


def _passenger_xml(
    *,
    pnr,
    last_name,
    first_name,
    line,
    booking_class,
    cabin,
    desired="",
    boarded=True,
    seniority="2019-01-01",
    seat="1A",
):
    desired_xml = f"<DesiredBookingClass>{desired}</DesiredBookingClass>" if desired else ""
    boarded_text = "true" if boarded else "false"
    return f"""
            <PassengerInfo>
               <Name_Details>
                  <LastName>{last_name}</LastName>
                  <FirstName>{first_name}</FirstName>
               </Name_Details>
               <PNRLocator nameAssociationID="{line}">{pnr}</PNRLocator>
               <PassengerID>PAX{line:04d}</PassengerID>
               <LineNumber>{line}</LineNumber>
               <PriorityCode>UPG</PriorityCode>
               <BookingClass>{booking_class}</BookingClass>
               {desired_xml}
               <Cabin>{cabin}</Cabin>
               <Seat>{seat}</Seat>
               <Destination>LHR</Destination>
               <BoardingPassFlag>{boarded_text}</BoardingPassFlag>
               <PassengerType>F</PassengerType>
               <BagCount>0</BagCount>
               <SeniorityDate>{seniority}</SeniorityDate>
               <EditCodeList>
                  <EditCode>DOCS</EditCode>
               </EditCodeList>
               <CheckIn_Info>
                  <CheckInStatus>{boarded_text}</CheckInStatus>
               </CheckIn_Info>
               <Boarding_Info>
                  <BoardStatus>{boarded_text}</BoardStatus>
               </Boarding_Info>
            </PassengerInfo>"""


def _response_xml(passenger_xml):
    """GetPassengerListRS body in the shape SabreClient._extract_body reads.

    The security header from the published sample is omitted. No credential
    is required to parse a response body.
    """
    return f"""<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
   <soap-env:Body>
      <GetPassengerListRS version="4.0.0">
         <ItineraryInfo>
            <Itinerary>
               <Airline>{AIRLINE}</Airline>
               <Flight>{FLIGHT}</Flight>
               <DepartureDate>{DATE}</DepartureDate>
               <Origin>{ORIGIN}</Origin>
               <AircraftType>789</AircraftType>
            </Itinerary>
            <DepartureGate>A1</DepartureGate>
            <CabinInfoList>
               <CabinInfo>
                  <Destination>LHR</Destination>
                  <Cabin>J</Cabin>
                  <Count>4</Count>
                  <Authorized>20</Authorized>
                  <Available>16</Available>
               </CabinInfo>
               <CabinInfo>
                  <Destination>LHR</Destination>
                  <Cabin>Y</Cabin>
                  <Count>4</Count>
                  <Authorized>200</Authorized>
                  <Available>196</Available>
               </CabinInfo>
            </CabinInfoList>
         </ItineraryInfo>
         <PassengerInfoList>
            {passenger_xml}
         </PassengerInfoList>
      </GetPassengerListRS>
   </soap-env:Body>
</soap-env:Envelope>"""


def ingest(passenger_xml):
    """Parse a GetPassengerListRS the way the Sabre client does, then convert it.

    Storage inserts that converted document unchanged (store_passenger_list).
    """
    parsed = SabreClient._parse_xml(_response_xml(passenger_xml))
    body = SabreClient._extract_body(object(), parsed, "GetPassengerListRS")
    return convert_passenger_list(body, AIRLINE, FLIGHT, DATE, ORIGIN)


def diff_manifests(before_doc, after_doc):
    return diff_passenger_list(
        {"snapshotId": "snap-before", "data": before_doc},
        {"snapshotId": "snap-after", "data": after_doc},
        FLIGHT_INFO,
    )


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self._docs)


class _Collection:
    """In-memory stand-in for the collections the audit route reads.

    store_passenger_list and store_changes insert the converter and differ
    documents without rewriting passenger cabin or class fields. find_one
    and find return those documents. The route drops _id from change
    documents and flattens a wrapper that contains a changes array.
    """

    def __init__(self, docs):
        self.docs = list(docs)

    def find_one(self, query, sort=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(key=lambda doc: doc.get(field) or "", reverse=direction < 0)
        return matches[0] if matches else None

    def find(self, query, projection=None):
        matches = []
        for doc in self.docs:
            if not _matches(doc, query):
                continue
            copied = dict(doc)
            if projection and projection.get("_id") == 0:
                copied.pop("_id", None)
            matches.append(copied)
        return _Cursor(matches)


class _Database:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _matches(doc, query):
    return all(doc.get(key) == value for key, value in query.items())


def run_audit_route(passenger_doc, changes):
    database = _Database({
        "passenger_list": _Collection([passenger_doc]),
        "reservations": _Collection([]),
        "changes": _Collection(changes),
    })
    with patch("backend.api.routes.audit.get_db", return_value=database):
        return get_flight_audit(FLIGHT, origin=ORIGIN, date=DATE)


def alerts(result, rule_id, pnr=None):
    found = []
    for alert in result["alerts"]:
        if alert["ruleId"] != rule_id:
            continue
        if pnr is not None and alert.get("pnr") != pnr:
            continue
        found.append(alert)
    return found


def passenger_by_pnr(doc, pnr):
    matches = [pax for pax in doc["passengers"] if pax["pnr"] == pnr]
    if len(matches) != 1:
        raise AssertionError(f"expected one stored passenger for {pnr}, found {len(matches)}")
    return matches[0]


class DesiredUpgradeAuditTests(unittest.TestCase):
    def test_ingestion_keeps_cabin_distinct_from_desired_class(self):
        """Sabre Cabin and DesiredBookingClass survive parsing and conversion."""
        doc = ingest(_passenger_xml(
            pnr="MOVE01",
            last_name="ALI",
            first_name="SARA",
            line=1,
            booking_class="Y",
            cabin="J",
            desired="C",
            seat="2A",
        ))
        stored = passenger_by_pnr(doc, "MOVE01")
        self.assertEqual(stored["cabin"], "J")
        self.assertEqual(stored["bookingClass"], "Y")
        self.assertEqual(stored["desiredBookingClass"], "C")
        self.assertTrue(stored["isBoarded"])
        self.assertNotEqual(stored["cabin"], stored["desiredBookingClass"])

    def test_moved_passenger_in_cabin_j_is_not_an_unprocessed_upgrade(self):
        """Boarded in J, original class Y, requested class F/C/D/I, no upgrade change.

        The passenger has left the Y cabin implied by booking class Y and is
        already in the J cabin those requested classes belong to. The letters
        differ, and the audit must not warn that the upgrade was not processed.
        """
        fragments = []
        for index, desired in enumerate(REQUESTED_CLASSES_NAMED_BY_FINDING, start=1):
            fragments.append(_passenger_xml(
                pnr=f"MOVE{index:02d}",
                last_name="MOVED",
                first_name=f"PAX{desired}",
                line=index,
                booking_class="Y",
                cabin="J",
                desired=desired,
                seat=f"{index}A",
            ))
        doc = ingest("".join(fragments))
        for index, desired in enumerate(REQUESTED_CLASSES_NAMED_BY_FINDING, start=1):
            stored = passenger_by_pnr(doc, f"MOVE{index:02d}")
            self.assertEqual(stored["cabin"], "J")
            self.assertEqual(stored["bookingClass"], "Y")
            self.assertEqual(stored["desiredBookingClass"], desired)
            self.assertEqual(cabin_for_class(stored["bookingClass"]), "Y")
            self.assertEqual(cabin_for_class(desired), "J")

        result = run_audit_route(doc, changes=[])
        for index, desired in enumerate(REQUESTED_CLASSES_NAMED_BY_FINDING, start=1):
            pnr = f"MOVE{index:02d}"
            self.assertEqual(
                alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", pnr),
                [],
                f"boarded passenger {pnr} is in cabin J with requested class "
                f"{desired} and was reported as an unprocessed upgrade: "
                f"{alerts(result, 'UPGRADE_DESIRED_NOT_PROCESSED', pnr)}",
            )

    def test_boarding_change_without_upgrade_does_not_create_the_warning(self):
        """A BOARDED change is not an upgrade, and the move is still not a miss."""
        before = ingest(_passenger_xml(
            pnr="BOARD1",
            last_name="NASSER",
            first_name="HALA",
            line=1,
            booking_class="Y",
            cabin="J",
            desired="F",
            boarded=False,
            seat="3A",
        ))
        after = ingest(_passenger_xml(
            pnr="BOARD1",
            last_name="NASSER",
            first_name="HALA",
            line=1,
            booking_class="Y",
            cabin="J",
            desired="F",
            boarded=True,
            seat="3A",
        ))
        changes = diff_manifests(before, after)
        self.assertIn("BOARDED", [change["changeType"] for change in changes])
        self.assertFalse(any(
            change["changeType"] in ("UPGRADE_CONFIRMED", "CABIN_CHANGE")
            for change in changes
        ))
        result = run_audit_route(after, changes)
        self.assertEqual(alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "BOARD1"), [])

    def test_class_already_equal_to_desired_is_not_an_unprocessed_upgrade(self):
        """bookingClass already is the requested class; the cabin letter differs."""
        fragments = []
        for index, desired in enumerate(REQUESTED_CLASSES_NAMED_BY_FINDING, start=1):
            fragments.append(_passenger_xml(
                pnr=f"SAME{index:02d}",
                last_name="EQUAL",
                first_name=f"PAX{desired}",
                line=index,
                booking_class=desired,
                cabin="J",
                desired=desired,
                seat=f"{index}C",
            ))
        doc = ingest("".join(fragments))
        result = run_audit_route(doc, changes=[])
        for index, desired in enumerate(REQUESTED_CLASSES_NAMED_BY_FINDING, start=1):
            pnr = f"SAME{index:02d}"
            stored = passenger_by_pnr(doc, pnr)
            self.assertEqual(stored["bookingClass"], desired)
            self.assertEqual(stored["cabin"], "J")
            self.assertNotEqual(stored["cabin"], desired)
            self.assertEqual(
                alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", pnr),
                [],
                f"{pnr} already holds class {desired} in cabin J but was flagged",
            )

    def test_business_cabin_with_different_business_class_letter_is_not_unprocessed(self):
        """Attack path: cabin J, requested class F, C, D, or I, booking class J."""
        fragments = []
        for index, desired in enumerate(REQUESTED_CLASSES_NAMED_BY_FINDING, start=1):
            fragments.append(_passenger_xml(
                pnr=f"BIZJ{index:02d}",
                last_name="JAMAL",
                first_name=f"PAX{desired}",
                line=index,
                booking_class="J",
                cabin="J",
                desired=desired,
                seat=f"{index}D",
            ))
        doc = ingest("".join(fragments))
        result = run_audit_route(doc, changes=[])
        for index, desired in enumerate(REQUESTED_CLASSES_NAMED_BY_FINDING, start=1):
            pnr = f"BIZJ{index:02d}"
            stored = passenger_by_pnr(doc, pnr)
            self.assertEqual(stored["cabin"], "J")
            self.assertEqual(stored["bookingClass"], "J")
            self.assertEqual(stored["desiredBookingClass"], desired)
            self.assertEqual(cabin_for_class(desired), stored["cabin"])
            self.assertEqual(
                alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", pnr),
                [],
                f"{pnr} is boarded in cabin J with requested class {desired} "
                f"and was reported as an unprocessed upgrade",
            )

    def test_still_in_original_economy_cabin_is_an_unprocessed_upgrade(self):
        """Sample pairing: booking class Y, desired class F, still in cabin Y."""
        before = ingest(_passenger_xml(
            pnr="WAIT01",
            last_name="TURNE",
            first_name="MIKE",
            line=2,
            booking_class="Y",
            cabin="Y",
            desired="F",
            boarded=False,
            seat="21A",
        ))
        after = ingest(_passenger_xml(
            pnr="WAIT01",
            last_name="TURNE",
            first_name="MIKE",
            line=2,
            booking_class="Y",
            cabin="Y",
            desired="F",
            boarded=True,
            seat="21A",
        ))
        changes = diff_manifests(before, after)
        change_types = [change["changeType"] for change in changes]
        self.assertIn("BOARDED", change_types)
        self.assertFalse(any(
            change_type in ("UPGRADE_CONFIRMED", "CABIN_CHANGE", "CLASS_CHANGE")
            for change_type in change_types
        ))
        result = run_audit_route(after, changes)
        found = alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "WAIT01")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "warning")
        self.assertEqual(found[0]["details"]["currentCabin"], "Y")
        self.assertEqual(found[0]["details"]["bookingClass"], "Y")
        self.assertEqual(found[0]["details"]["desiredBookingClass"], "F")
        self.assertIn("WAIT01", result["passengerAlerts"])
        self.assertIn("UPGRADE_DESIRED_NOT_PROCESSED", result["passengerAlerts"]["WAIT01"])

    def test_design_sample_classes_still_in_economy_are_unprocessed(self):
        """Design sample uses booking class K and desired class J. Still in Y."""
        doc = ingest(_passenger_xml(
            pnr="WAITK1",
            last_name="NIKE",
            first_name="ALEX",
            line=1,
            booking_class="K",
            cabin="Y",
            desired="J",
            seat="40H",
        ))
        stored = passenger_by_pnr(doc, "WAITK1")
        self.assertEqual(cabin_for_class(stored["bookingClass"]), "Y")
        self.assertEqual(stored["cabin"], "Y")
        result = run_audit_route(doc, changes=[])
        found = alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "WAITK1")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["details"]["bookingClass"], "K")
        self.assertEqual(found[0]["details"]["desiredBookingClass"], "J")
        self.assertEqual(found[0]["details"]["currentCabin"], "Y")

    def test_passenger_who_is_not_boarded_is_not_flagged(self):
        doc = ingest(_passenger_xml(
            pnr="GROUND",
            last_name="KAREEM",
            first_name="NOOR",
            line=1,
            booking_class="Y",
            cabin="Y",
            desired="F",
            boarded=False,
            seat="22B",
        ))
        self.assertFalse(passenger_by_pnr(doc, "GROUND")["isBoarded"])
        result = run_audit_route(doc, changes=[])
        self.assertEqual(alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "GROUND"), [])

    def test_recorded_upgrade_suppresses_a_new_request_that_is_still_open(self):
        """An upgrade event for the record locator suppresses the warning.

        The latest manifest is a fresh request that is still in the original
        cabin. Historical UPGRADE_CONFIRMED from the differ still suppresses
        it, which is the rule's existing exception.
        """
        before = ingest(_passenger_xml(
            pnr="HIST01",
            last_name="FADHIL",
            first_name="AMAL",
            line=1,
            booking_class="Y",
            cabin="Y",
            desired="F",
            boarded=True,
            seat="30A",
        ))
        after_upgrade = ingest(_passenger_xml(
            pnr="HIST01",
            last_name="FADHIL",
            first_name="AMAL",
            line=1,
            booking_class="F",
            cabin="J",
            desired="",
            boarded=True,
            seat="4A",
        ))
        historical = diff_manifests(before, after_upgrade)
        self.assertTrue(any(change["changeType"] == "UPGRADE_CONFIRMED" for change in historical))
        latest = ingest(_passenger_xml(
            pnr="HIST01",
            last_name="FADHIL",
            first_name="AMAL",
            line=1,
            booking_class="Y",
            cabin="Y",
            desired="F",
            boarded=True,
            seat="30A",
        ))
        result = run_audit_route(latest, historical)
        self.assertEqual(alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "HIST01"), [])

    def test_wrapped_change_document_still_reaches_the_rule(self):
        """The route flattens a changes array before the rule sees it."""
        before = ingest(_passenger_xml(
            pnr="WRAP01",
            last_name="SALEH",
            first_name="LINA",
            line=1,
            booking_class="Y",
            cabin="Y",
            desired="C",
            boarded=True,
            seat="18C",
        ))
        after = ingest(_passenger_xml(
            pnr="WRAP01",
            last_name="SALEH",
            first_name="LINA",
            line=1,
            booking_class="C",
            cabin="J",
            desired="",
            boarded=True,
            seat="1A",
        ))
        inner = diff_manifests(before, after)
        self.assertTrue(any(change["changeType"] == "UPGRADE_CONFIRMED" for change in inner))
        wrapper = {
            "flightNumber": FLIGHT,
            "origin": ORIGIN,
            "departureDate": DATE,
            "sequenceNumber": 1,
            "changes": inner,
        }
        latest = ingest(_passenger_xml(
            pnr="WRAP01",
            last_name="SALEH",
            first_name="LINA",
            line=1,
            booking_class="Y",
            cabin="Y",
            desired="C",
            boarded=True,
            seat="18C",
        ))
        result = run_audit_route(latest, [wrapper])
        self.assertEqual(alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "WRAP01"), [])

    def test_queue_skip_ignores_passenger_already_in_the_desired_cabin(self):
        """Same cabin-versus-class comparison in rule_upgrade_queue_skipped.

        A higher-priority passenger already seated in cabin J, holding class
        C, must not be reported as skipped when a lower-priority passenger
        with the same desired class has a cabin upgrade on record.
        """
        high = dict(
            pnr="HIGH01", last_name="PRIORITY", first_name="HANA", line=1,
            booking_class="C", cabin="J", desired="C", seniority="2018-01-01", seat="1A",
        )
        low_before = dict(
            pnr="LOW001", last_name="LATER", first_name="OMAR", line=8,
            booking_class="Y", cabin="Y", desired="C", seniority="2024-06-01", seat="40A",
        )
        low_after = dict(low_before, cabin="J", seat="2A")
        before = ingest(_passenger_xml(**high) + _passenger_xml(**low_before))
        after = ingest(_passenger_xml(**high) + _passenger_xml(**low_after))
        changes = diff_manifests(before, after)
        upgrade = [
            change for change in changes
            if change["changeType"] == "CABIN_CHANGE"
            and change.get("metadata", {}).get("direction") == "UPGRADE"
            and change["passenger"]["pnr"] == "LOW001"
        ]
        self.assertEqual(len(upgrade), 1)
        result = run_audit_route(after, changes)
        self.assertEqual(alerts(result, "UPGRADE_QUEUE_SKIPPED", "HIGH01"), [])
        self.assertEqual(alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "HIGH01"), [])
        self.assertEqual(alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "LOW001"), [])

    def test_queue_skip_still_flags_a_passenger_waiting_in_economy(self):
        """A higher-priority passenger still in cabin Y is a real skip."""
        high = dict(
            pnr="HIGH02", last_name="WAITING", first_name="RANA", line=1,
            booking_class="Y", cabin="Y", desired="C", seniority="2018-01-01", seat="22A",
        )
        low_before = dict(
            pnr="LOW002", last_name="JUMPED", first_name="ADEL", line=8,
            booking_class="Y", cabin="Y", desired="C", seniority="2024-06-01", seat="41A",
        )
        low_after = dict(low_before, cabin="J", seat="2C")
        before = ingest(_passenger_xml(**high) + _passenger_xml(**low_before))
        after = ingest(_passenger_xml(**high) + _passenger_xml(**low_after))
        changes = diff_manifests(before, after)
        result = run_audit_route(after, changes)
        skipped = alerts(result, "UPGRADE_QUEUE_SKIPPED", "HIGH02")
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["severity"], "warning")
        self.assertEqual(skipped[0]["details"]["desiredCabin"], "C")
        self.assertEqual(skipped[0]["details"]["upgradedPnr"], "LOW002")
        waiting = alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "HIGH02")
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]["details"]["currentCabin"], "Y")
        self.assertEqual(waiting[0]["details"]["desiredBookingClass"], "C")
        self.assertEqual(alerts(result, "UPGRADE_DESIRED_NOT_PROCESSED", "LOW002"), [])


if __name__ == "__main__":
    unittest.main()
