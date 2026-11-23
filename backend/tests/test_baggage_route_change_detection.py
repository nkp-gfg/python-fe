"""Baggage-route changes must be recorded even when the bag count is unchanged.

These tests follow the passenger-list path the feeder runs after Sabre returns
GetPassengerListRS: parse the SOAP body, convert it, and diff the two snapshots.
A route edit changes the snapshot checksum, so the runner does not treat the
second ingest as a duplicate and does call change detection. The activity-feed
text is the description that endpoint builds from each stored change.

They fail while change detection compares only bagCount. A fix should record
the stored route identity — airline, flight, origin, destination, and segment
status — and the feed description has to name the old and new routing.

Not covered here, because those paths do not share this comparison:

- GetPassengerData (convert_passenger_data) is returned live and is not diffed.
- Reservation segments are the booking itinerary, not the baggage-route list.
- The four display-code passenger calls only append whole passenger dicts
  before conversion; they do not rewrite BaggageRouteList.
"""

import unittest

from backend.api.routes.changes import _categorize_event, _describe_event
from backend.feeder.converter import convert_passenger_list
from backend.feeder.differ import detect_changes
from backend.feeder.storage import _compute_checksum
from backend.sabre.client import SabreClient

FLIGHT_INFO = {
    "airline": "GF",
    "flightNumber": "2057",
    "origin": "BOM",
    "departureDate": "2026-03-19",
}

# The committed GF2057 manifest carries this connection: BOM-DMM on 2057,
# then DMM-BAH on 5023. The passenger below is synthetic.
ONWARD_TO_BAHRAIN = (
    {"airline": "GF", "flight": "2057", "origin": "BOM", "destination": "DMM", "status": "HK"},
    {"airline": "GF", "flight": "5023", "origin": "DMM", "destination": "BAH", "status": "HK"},
)


def _passenger_xml(pnr, last_name, first_name, line, bag_count, routes):
    route_xml = []
    for index, route in enumerate(routes, start=1):
        route_xml.append(
            "<ns2:BaggageRoute>"
            f"<ns2:SegmentID>{index}</ns2:SegmentID>"
            f"<ns2:Airline>{route['airline']}</ns2:Airline>"
            f"<ns2:Flight>{route['flight']}</ns2:Flight>"
            "<ns2:BookingClass>Q</ns2:BookingClass>"
            f"<ns2:Origin>{route['origin']}</ns2:Origin>"
            f"<ns2:Destination>{route['destination']}</ns2:Destination>"
            "<ns2:DepartureDate>2026-03-19</ns2:DepartureDate>"
            f"<ns2:DepartureTime>{route.get('departureTime', '21:40')}</ns2:DepartureTime>"
            "<ns2:ArrivalDate>2026-03-20</ns2:ArrivalDate>"
            "<ns2:ArrivalTime>01:40</ns2:ArrivalTime>"
            f"<ns2:SegmentStatus>{route['status']}</ns2:SegmentStatus>"
            f"<ns2:PassengerID>PAX{line}{index}</ns2:PassengerID>"
            "</ns2:BaggageRoute>"
        )
    routes_block = "".join(route_xml)
    return (
        "<PassengerInfo>"
        "<Name_Details>"
        f"<LastName>{last_name}</LastName>"
        f"<FirstName>{first_name}</FirstName>"
        "</Name_Details>"
        f'<PNRLocator nameAssociationID="1">{pnr}</PNRLocator>'
        f"<PassengerID>PAX{line}</PassengerID>"
        "<ThruIndicator>false</ThruIndicator>"
        f"<LineNumber>{line}</LineNumber>"
        "<BookingClass>Q</BookingClass>"
        "<Cabin>Y</Cabin>"
        "<Seat>30E</Seat>"
        "<Destination>DMM</Destination>"
        "<BoardingPassFlag>true</BoardingPassFlag>"
        "<PassengerType>F</PassengerType>"
        f"<BagCount>{bag_count}</BagCount>"
        "<Indicators><Indicator>Revenue</Indicator></Indicators>"
        "<CheckIn_Info>"
        "<CheckInNumber>37</CheckInNumber>"
        "<CheckInDate>2026-03-19</CheckInDate>"
        "<CheckInTime>04:10</CheckInTime>"
        "<CheckInStatus>true</CheckInStatus>"
        "</CheckIn_Info>"
        "<Boarding_Info><BoardStatus>false</BoardStatus></Boarding_Info>"
        "<BaggageRouteList>"
        f"{routes_block}"
        "</BaggageRouteList>"
        "</PassengerInfo>"
    )


def _list_xml(passengers_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Body>"
        '<GetPassengerListRS xmlns="http://services.sabre.com/checkin/getPassengerList/v4" '
        'xmlns:ns2="http://services.sabre.com/STL/v3">'
        "<ItineraryInfo><Itinerary>"
        "<Airline>GF</Airline><Flight>2057</Flight>"
        "<Origin>BOM</Origin><Destination>DMM</Destination>"
        "</Itinerary></ItineraryInfo>"
        "<PassengerInfoList>"
        f"{passengers_xml}"
        "</PassengerInfoList>"
        "</GetPassengerListRS>"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _snapshot(xml_text, snapshot_id):
    """Parse and convert the way SabreClient.get_passenger_list feeds the runner."""
    parsed = SabreClient._parse_xml(xml_text)
    body = SabreClient._extract_body(None, parsed, "GetPassengerListRS")
    data = convert_passenger_list(body, "GF", "2057", "2026-03-19", "BOM")
    return {"snapshotId": snapshot_id, "data": data}


def _stored_passenger(snapshot, pnr):
    matches = [p for p in snapshot["data"]["passengers"] if p.get("pnr") == pnr]
    if len(matches) != 1:
        raise AssertionError(f"expected one passenger {pnr}, found {len(matches)}")
    return matches[0]


def _checksums_differ(before, after):
    """True when the runner would not treat the second snapshot as a duplicate.

    store_snapshot skips detect_changes only when this checksum matches the
    previous snapshot. fetchedAt is whole seconds, so two identical
    conversions in the same second are duplicates. A baggage-route edit is
    part of the checksummed document and must not be.
    """
    return _compute_checksum(before["data"]) != _compute_checksum(after["data"])


def _diff(before, after):
    return detect_changes(before, after, "passenger_list", FLIGHT_INFO)


def _feed_events(changes):
    """Same fields the activity feed returns for each stored change."""
    events = []
    for change in changes:
        events.append({
            "flightNumber": change.get("flightNumber"),
            "origin": change.get("origin"),
            "departureDate": change.get("departureDate"),
            "category": _categorize_event(change.get("changeType", "")),
            "eventType": change.get("changeType"),
            "description": _describe_event(change),
            "passenger": change.get("passenger"),
            "field": change.get("field"),
            "oldValue": change.get("oldValue"),
            "newValue": change.get("newValue"),
        })
    return events


def _route_events(changes, pnr):
    return [
        event for event in _feed_events(changes)
        if (event.get("passenger") or {}).get("pnr") == pnr
        and event.get("eventType") != "BAG_COUNT_CHANGE"
    ]


def _assert_feed_names_route(changes, pnr, tokens):
    events = _route_events(changes, pnr)
    descriptions = [event["description"] for event in events]
    blob = "\n".join(descriptions)
    missing = [token for token in tokens if token not in blob]
    assert not missing, (
        f"activity feed for {pnr} does not mention {missing}. "
        f"descriptions={descriptions!r}"
    )
    for event in events:
        assert event["flightNumber"] == "2057"
        assert event["origin"] == "BOM"
        assert event["departureDate"] == "2026-03-19"
    return events


class BaggageRouteChangeDetectionTest(unittest.TestCase):
    def test_converter_keeps_two_segment_route_from_namespaced_list(self):
        xml = _list_xml(_passenger_xml(
            "SYN001", "DOE", "JANE", "10", 4, ONWARD_TO_BAHRAIN,
        ))
        snapshot = _snapshot(xml, "before")
        passenger = _stored_passenger(snapshot, "SYN001")
        self.assertEqual(passenger["bagCount"], 4)
        self.assertEqual(
            passenger["baggageRoutes"],
            [
                {
                    "airline": "GF",
                    "flight": "2057",
                    "origin": "BOM",
                    "destination": "DMM",
                    "segmentStatus": "HK",
                },
                {
                    "airline": "GF",
                    "flight": "5023",
                    "origin": "DMM",
                    "destination": "BAH",
                    "segmentStatus": "HK",
                },
            ],
        )

    def test_onward_flight_change_is_in_history_when_bag_count_stays(self):
        before_routes = ONWARD_TO_BAHRAIN
        after_routes = (
            before_routes[0],
            {**before_routes[1], "flight": "5099"},
        )
        changes = self._changes_for("SYN001", 4, before_routes, 4, after_routes)
        self.assertFalse(self._bag_count_events(changes, "SYN001"))
        _assert_feed_names_route(changes, "SYN001", ["5023", "5099", "BAH", "DMM"])

    def test_onward_destination_change_is_in_history_when_bag_count_stays(self):
        before_routes = ONWARD_TO_BAHRAIN
        after_routes = (
            before_routes[0],
            {**before_routes[1], "destination": "MCT"},
        )
        changes = self._changes_for("SYN001", 4, before_routes, 4, after_routes)
        self.assertFalse(self._bag_count_events(changes, "SYN001"))
        _assert_feed_names_route(changes, "SYN001", ["5023", "BAH", "MCT"])

    def test_onward_airline_change_is_in_history_when_bag_count_stays(self):
        before_routes = ONWARD_TO_BAHRAIN
        after_routes = (
            before_routes[0],
            {**before_routes[1], "airline": "EY"},
        )
        changes = self._changes_for("SYN001", 4, before_routes, 4, after_routes)
        self.assertFalse(self._bag_count_events(changes, "SYN001"))
        _assert_feed_names_route(changes, "SYN001", ["5023", "EY", "BAH"])

    def test_onward_segment_status_change_is_in_history_when_bag_count_stays(self):
        before_routes = ONWARD_TO_BAHRAIN
        after_routes = (
            before_routes[0],
            {**before_routes[1], "status": "WK"},
        )
        changes = self._changes_for("SYN001", 4, before_routes, 4, after_routes)
        self.assertFalse(self._bag_count_events(changes, "SYN001"))
        _assert_feed_names_route(changes, "SYN001", ["5023", "HK", "WK", "BAH"])

    def test_dropped_onward_segment_is_in_history_when_bag_count_stays(self):
        changes = self._changes_for(
            "SYN001", 4, ONWARD_TO_BAHRAIN, 4, (ONWARD_TO_BAHRAIN[0],),
        )
        self.assertFalse(self._bag_count_events(changes, "SYN001"))
        events = _assert_feed_names_route(changes, "SYN001", ["5023", "BAH", "DMM"])
        new_values = " ".join(event.get("newValue") or "" for event in events)
        self.assertNotIn("5023", new_values)

    def test_added_onward_segment_is_in_history_when_bag_count_stays(self):
        changes = self._changes_for(
            "SYN001", 4, (ONWARD_TO_BAHRAIN[0],), 4, ONWARD_TO_BAHRAIN,
        )
        self.assertFalse(self._bag_count_events(changes, "SYN001"))
        events = _assert_feed_names_route(changes, "SYN001", ["5023", "BAH"])
        old_values = " ".join(event.get("oldValue") or "" for event in events)
        self.assertNotIn("5023", old_values)

    def test_single_route_element_destination_change_is_in_history(self):
        """xmltodict leaves a single BaggageRoute as a dict; conversion must still diff it."""
        before_routes = (
            {"airline": "GF", "flight": "2057", "origin": "BOM", "destination": "DMM", "status": "HK"},
        )
        after_routes = (
            {"airline": "GF", "flight": "2057", "origin": "BOM", "destination": "BAH", "status": "HK"},
        )
        before = _snapshot(_list_xml(_passenger_xml(
            "SYN001", "DOE", "JANE", "10", 1, before_routes,
        )), "before")
        stored = _stored_passenger(before, "SYN001")["baggageRoutes"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["destination"], "DMM")
        after = _snapshot(_list_xml(_passenger_xml(
            "SYN001", "DOE", "JANE", "10", 1, after_routes,
        )), "after")
        self.assertTrue(_checksums_differ(before, after))
        changes = _diff(before, after)
        _assert_feed_names_route(changes, "SYN001", ["DMM", "BAH", "2057"])

    def test_route_order_change_is_in_history(self):
        reversed_routes = (ONWARD_TO_BAHRAIN[1], ONWARD_TO_BAHRAIN[0])
        changes = self._changes_for(
            "SYN001", 4, ONWARD_TO_BAHRAIN, 4, reversed_routes,
        )
        events = _assert_feed_names_route(changes, "SYN001", ["2057", "5023"])
        old_values = " ".join(event.get("oldValue") or "" for event in events)
        new_values = " ".join(event.get("newValue") or "" for event in events)
        self.assertLess(old_values.index("2057"), old_values.index("5023"))
        self.assertLess(new_values.index("5023"), new_values.index("2057"))

    def test_only_the_passenger_whose_route_changed_is_reported(self):
        stable = (
            {"airline": "GF", "flight": "2057", "origin": "BOM", "destination": "DMM", "status": "HK"},
        )
        before_xml = _list_xml(
            _passenger_xml("SYN001", "DOE", "JANE", "10", 4, ONWARD_TO_BAHRAIN)
            + _passenger_xml("SYN002", "ROE", "ALEX", "11", 1, stable)
        )
        after_routes = (
            ONWARD_TO_BAHRAIN[0],
            {**ONWARD_TO_BAHRAIN[1], "flight": "5099", "destination": "DOH"},
        )
        after_xml = _list_xml(
            _passenger_xml("SYN001", "DOE", "JANE", "10", 4, after_routes)
            + _passenger_xml("SYN002", "ROE", "ALEX", "11", 1, stable)
        )
        before = _snapshot(before_xml, "before")
        after = _snapshot(after_xml, "after")
        self.assertTrue(_checksums_differ(before, after))
        changes = _diff(before, after)
        _assert_feed_names_route(changes, "SYN001", ["5023", "5099", "BAH", "DOH"])
        self.assertFalse(_route_events(changes, "SYN002"))

    def test_route_change_is_recorded_together_with_a_bag_count_change(self):
        after_routes = (
            ONWARD_TO_BAHRAIN[0],
            {**ONWARD_TO_BAHRAIN[1], "flight": "5099"},
        )
        changes = self._changes_for("SYN001", 4, ONWARD_TO_BAHRAIN, 5, after_routes)
        bag_events = self._bag_count_events(changes, "SYN001")
        self.assertTrue(bag_events)
        self.assertEqual(bag_events[0]["oldValue"], "4")
        self.assertEqual(bag_events[0]["newValue"], "5")
        _assert_feed_names_route(changes, "SYN001", ["5023", "5099", "BAH"])

    def test_identical_routes_do_not_create_a_passenger_change(self):
        xml = _list_xml(_passenger_xml(
            "SYN001", "DOE", "JANE", "10", 4, ONWARD_TO_BAHRAIN,
        ))
        before = _snapshot(xml, "before")
        after = _snapshot(xml, "after")
        self.assertEqual(
            _stored_passenger(before, "SYN001")["baggageRoutes"],
            _stored_passenger(after, "SYN001")["baggageRoutes"],
        )
        self.assertEqual(_diff(before, after), [])

    def test_departure_time_is_not_part_of_the_stored_route_identity(self):
        """Converter drops departure time, so a time-only edit is not a route change."""
        before_routes = ONWARD_TO_BAHRAIN
        after_routes = (
            before_routes[0],
            {**before_routes[1], "departureTime": "23:15"},
        )
        before = _snapshot(_list_xml(_passenger_xml(
            "SYN001", "DOE", "JANE", "10", 4, before_routes,
        )), "before")
        after = _snapshot(_list_xml(_passenger_xml(
            "SYN001", "DOE", "JANE", "10", 4, after_routes,
        )), "after")
        self.assertEqual(
            _stored_passenger(before, "SYN001")["baggageRoutes"],
            _stored_passenger(after, "SYN001")["baggageRoutes"],
        )
        self.assertEqual(_diff(before, after), [])

    def _changes_for(self, pnr, before_bags, before_routes, after_bags, after_routes):
        before = _snapshot(_list_xml(_passenger_xml(
            pnr, "DOE", "JANE", "10", before_bags, before_routes,
        )), "before")
        after = _snapshot(_list_xml(_passenger_xml(
            pnr, "DOE", "JANE", "10", after_bags, after_routes,
        )), "after")
        stored_before = _stored_passenger(before, pnr)
        self.assertEqual(stored_before["bagCount"], before_bags)
        self.assertEqual(
            [(r["airline"], r["flight"], r["origin"], r["destination"], r["segmentStatus"])
             for r in stored_before["baggageRoutes"]],
            [(r["airline"], r["flight"], r["origin"], r["destination"], r["status"])
             for r in before_routes],
        )
        self.assertTrue(
            _checksums_differ(before, after),
            "route edit was checksum-identical, so the runner would skip the diff",
        )
        return _diff(before, after)

    def _bag_count_events(self, changes, pnr):
        return [
            event for event in _feed_events(changes)
            if event.get("eventType") == "BAG_COUNT_CHANGE"
            and (event.get("passenger") or {}).get("pnr") == pnr
        ]


if __name__ == "__main__":
    unittest.main()
