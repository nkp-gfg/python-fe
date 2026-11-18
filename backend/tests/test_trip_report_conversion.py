"""Trip report ingest must keep the passengers Sabre returns in the report body.

These tests build Trip_ReportsRS the way the Trip Reports 1.3.0 user guide and
schema describe it, wrap it in the SOAP envelope SabreClient parses, and run
that response through SabreClient.get_trip_report and run_feeder. They fail
while convert_trip_report ignores the report body.

Run from the repository root:

    python3 backend/tests/test_trip_report_conversion.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.api.routes.flights import _build_dashboard_payload
from backend.feeder import storage
from backend.feeder.runner import run_feeder
from backend.sabre.client import SabreClient, SabreError

_PLACEHOLDER_ENV = {
    "SABRE_BASE_URL": "https://placeholder.invalid",
    "SABRE_CPAID": "GF",
    "SABRE_USERNAME": "placeholder-user",
    "SABRE_PASSWORD": "placeholder-password",
    "SABRE_PSEUDO_CITY_CODE": "placeholder",
    "SABRE_ORGANIZATION": "placeholder",
    "SABRE_DOMAIN": "placeholder",
}


def _envelope(body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap-env:Header/>"
        "<soap-env:Body>"
        f"{body}"
        "</soap-env:Body>"
        "</soap-env:Envelope>"
    )


def _report(report_name, report_format, results, success=True):
    """Build a Trip_ReportsRS body. results is a list of <ReportResult> fragments."""
    head = "<Success>Success</Success>" if success else (
        "<Errors><Error ErrorCode=\"ERR\" Severity=\"Error\" "
        "ErrorMessage=\"report failed\">"
        "<ErrorInfo><Message>report failed</Message></ErrorInfo>"
        "</Error></Errors>"
    )
    inner = "".join(results)
    return (
        '<Trip_ReportsRS Version="1.3.0" '
        'xmlns="http://webservices.sabre.com/triprecord">'
        f"{head}"
        "<Report>"
        f"<ReportName>{report_name}</ReportName>"
        f"<ReportFormat>{report_format}</ReportFormat>"
        f"{inner}"
        "</Report>"
        "</Trip_ReportsRS>"
    )


def _group(name, locator, sequence):
    return (
        f'<GroupEntry sequenceId="{sequence}">'
        "<PnrIndicatorType/>"
        "<NumberInParty>1</NumberInParty>"
        f"<Name>{name}</Name>"
        f"<Locator>{locator}</Locator>"
        "<ClassOfService>N</ClassOfService>"
        "<StatusCode>HK</StatusCode>"
        "<NumberOfPassengers>1</NumberOfPassengers>"
        "<AgentId/>"
        "<UpdateDateTime>2018-01-02T18:47:03</UpdateDateTime>"
        "</GroupEntry>"
    )


def _segment_header():
    """EntryHeader from the MLX sample. It names a segment, not a passenger."""
    return (
        "<EntryHeader>"
        "<DepartureAirport>SCL</DepartureAirport>"
        "<ArrivalAirport>GRU</ArrivalAirport>"
        "<EntryType>SEGMENT</EntryType>"
        "<DepartureLegTime>2018-01-24T00:00:00</DepartureLegTime>"
        "</EntryHeader>"
    )


def _named_header(last, first, locator):
    return (
        "<EntryHeader>"
        f"<LastName>{last}</LastName>"
        f"<FirstName>{first}</FirstName>"
        f"<Locator>{locator}</Locator>"
        "</EntryHeader>"
    )


def _reservation(last, first, locator):
    return (
        "<ReservationEntry>"
        f"<Locator>{locator}</Locator>"
        f"<FirstName>{first}</FirstName>"
        f"<LastName>{last}</LastName>"
        "<PnrCreateDate>2018-01-01T00:00:00</PnrCreateDate>"
        "<TicketTimeLimitDate>2018-01-20T00:00:00</TicketTimeLimitDate>"
        "<StationCode>SCL</StationCode>"
        "<HomeCityCode>SCL</HomeCityCode>"
        "<AgentSine>A1</AgentSine>"
        "</ReservationEntry>"
    )


def _result(entries):
    body = "".join(entries)
    return (
        "<ReportResult>"
        "<Header>"
        "<AirlineCode>GF</AirlineCode>"
        "<FlightNumber>750</FlightNumber>"
        "<DepartureDateTime>2018-01-24T00:00:00</DepartureDateTime>"
        "<DepartureAirport>SCL</DepartureAirport>"
        "<ArrivalAirport>GRU</ArrivalAirport>"
        "</Header>"
        "<Details>"
        f"{body}"
        "</Details>"
        "</ReportResult>"
    )


def _entry(*children):
    return "<Entry>" + "".join(children) + "</Entry>"


DOCUMENTED_MLX = _report("MLX", "X", [
    _result([
        _entry(
            _segment_header(),
            _group("ALBERTSON/HENRY JAMES MR", "BDABDA", 1),
            _group("ALBERTSON/MARIA LUCIA MRS", "BDABDA", 2),
        ),
        _entry(_named_header("DOE", "JANE", "HDRLOC")),
    ]),
    _result([
        _entry(_reservation("ROE", "RICHARD", "RSVLOC")),
    ]),
])

DOCUMENTED_MLC = _report("MLC", "C", [
    _result([
        _entry(
            _segment_header(),
            _group("SMITH/JOHN", "ABC123", 1),
        ),
    ]),
    _result([
        _entry(_group("JONES/MARY", "XYZ789", 1)),
    ]),
])

CANCELLED = {
    ("ALBERTSON", "HENRY JAMES MR", "BDABDA"),
    ("ALBERTSON", "MARIA LUCIA MRS", "BDABDA"),
    ("DOE", "JANE", "HDRLOC"),
    ("ROE", "RICHARD", "RSVLOC"),
}

EVER_BOOKED = {
    ("SMITH", "JOHN", "ABC123"),
    ("JONES", "MARY", "XYZ789"),
}


def _identities(passengers):
    return {
        (p.get("lastName", ""), p.get("firstName", ""), p.get("pnr", ""))
        for p in passengers
    }


def _flight():
    return {
        "airline": "GF",
        "flightNumber": "750",
        "origin": "SCL",
        "departureDate": "2018-01-24",
        "departureDateTime": "2018-01-24T00:00:00",
        "flightSequenceNumber": 1,
    }


def _manifest():
    return {
        "origin": "SCL",
        "departureDate": "2018-01-24",
        "fetchedAt": "2018-01-24T12:00:00Z",
        "totalPassengers": 1,
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
        "totalSouls": 1,
        "cabinSummary": [],
        "groupBookings": [],
        "passengers": [{
            "pnr": "ABC123",
            "lastName": "SMITH",
            "firstName": "JOHN",
            "cabin": "Y",
            "passengerType": "F",
            "isCheckedIn": True,
            "isBoarded": True,
            "isRevenue": True,
            "editCodes": [],
        }],
    }


def _flight_status(status):
    return {
        "status": status,
        "origin": "SCL",
        "departureDate": "2018-01-24",
        "legs": [{"city": "GRU"}],
        "aircraft": {},
        "boarding": {},
        "jumpSeat": {"cockpit": 0, "cabin": 0},
        "gate": "",
        "codeshareInfo": [],
        "freeTextRemarks": [],
    }


def _dashboard(trip_doc, status):
    return _build_dashboard_payload(
        _flight_status(status),
        _manifest(),
        "SCL",
        "2018-01-24",
        {},
        None,
        trip_doc,
        None,
    )


def _others(payload):
    return payload["stateSummary"]["others"]


def _card(payload, card_id):
    cards = payload["tree"]["statusCards"]
    return next(card for card in cards if card["id"] == card_id)


class _FeederRun:
    """Run one flight through run_feeder with Trip_ReportsRS supplied as SOAP.

    Other Sabre calls raise SabreError. Trip_ReportsRQ is answered by the real
    client parser. Mongo writes are captured instead of executed.
    """

    def __init__(self, mlx_body, mlc_body):
        self.mlx_soap = _envelope(mlx_body)
        self.mlc_soap = _envelope(mlc_body)
        self.stored = []
        self.report_names = []

    def __enter__(self):
        os.environ["SABRE_API_DELAY_SECONDS"] = "0"
        for key, value in _PLACEHOLDER_ENV.items():
            os.environ.setdefault(key, value)
        self._delay = SabreClient.API_CALL_DELAY
        SabreClient.API_CALL_DELAY = 0
        self._create = SabreClient.create_session
        self._close = SabreClient.close_session
        self._post = SabreClient._post
        self._store = storage.store_trip_reports
        self._raw = storage.store_raw_request
        feeder = self

        def _post(client, action, body, timeout=30, endpoint=None):
            return feeder._post_impl(client, action, body, timeout=timeout, endpoint=endpoint)

        def _store(doc):
            return feeder._store_impl(doc)

        self._post_fn = _post
        self._store_fn = _store
        SabreClient.create_session = _create_session
        SabreClient.close_session = lambda self: None
        SabreClient._post = _post
        storage.store_trip_reports = _store
        storage.store_raw_request = lambda *args, **kwargs: "placeholder-request-id"
        return self

    def __exit__(self, exc_type, exc, tb):
        SabreClient.API_CALL_DELAY = self._delay
        SabreClient.create_session = self._create
        SabreClient.close_session = self._close
        SabreClient._post = self._post
        storage.store_trip_reports = self._store
        storage.store_raw_request = self._raw
        return False

    def _post_impl(self, client, action, body, timeout=30, endpoint=None):
        if action != "Trip_ReportsRQ":
            raise SabreError(action)
        if "<ReportName>MLX</ReportName>" in body:
            self.report_names.append("MLX")
            return self.mlx_soap, 200, 1, body
        if "<ReportName>MLC</ReportName>" in body:
            self.report_names.append("MLC")
            return self.mlc_soap, 200, 1, body
        raise SabreError("unexpected trip report request")

    def _store_impl(self, doc):
        self.stored.append(doc)
        return "placeholder-trip-id"

    def execute(self):
        return run_feeder([_flight()])


def _create_session(self):
    self._token = "placeholder-token"
    self._conversation_id = "placeholder-conversation"
    return self._token


def _trip(result):
    return result["results"][0]["apis"]["tripReports"]


def test_documented_mlx_and_mlc_bodies_reach_dashboard():
    """MLX/MLC passengers in the report body are stored and shown as available."""
    with _FeederRun(DOCUMENTED_MLX, DOCUMENTED_MLC) as feeder:
        result = feeder.execute()
        stored = list(feeder.stored)
        requested = list(feeder.report_names)

    assert requested == ["MLX", "MLC"]
    trip = _trip(result)
    assert trip["status"] == "success"
    assert trip["cancelledCount"] == 4
    assert trip["everBookedCount"] == 2
    assert len(stored) == 1
    doc = stored[0]
    assert doc["airline"] == "GF"
    assert doc["flightNumber"] == "750"
    assert doc["origin"] == "SCL"
    assert doc["departureDate"] == "2018-01-24"
    assert len(doc["cancelledPassengers"]) == 4
    assert doc["cancelledCount"] == 4
    assert _identities(doc["cancelledPassengers"]) == CANCELLED
    assert len(doc["everBookedPassengers"]) == 2
    assert doc["everBookedCount"] == 2
    assert _identities(doc["everBookedPassengers"]) == EVER_BOOKED

    final = _dashboard(doc, "FINAL")
    others = _others(final)
    assert others["offloaded"] == 4
    assert others["offloadedAvailable"] is True
    assert others["noShow"] == 1
    assert others["noShowAvailable"] is True
    assert _card(final, "offloaded")["value"] == 4
    assert _card(final, "offloaded")["subLabel"] == "MLX report"
    assert _card(final, "noShow")["value"] == 1
    assert _card(final, "noShow")["subLabel"] == "MLC vs manifest"

    pdc = _others(_dashboard(doc, "PDC"))
    assert pdc["offloaded"] == 4
    assert pdc["offloadedAvailable"] is True
    assert pdc["noShow"] == 1
    assert pdc["noShowAvailable"] is True

    open_flight = _others(_dashboard(doc, "OPEN"))
    assert open_flight["offloaded"] == 4
    assert open_flight["offloadedAvailable"] is True
    assert open_flight["noShowAvailable"] is False


def test_single_group_entry_is_counted():
    """One GroupEntry is a dict after XML parsing and is still one passenger."""
    mlx = _report("MLX", "X", [
        _result([
            _entry(_group("NGUYEN/LINH", "SINGLE1", 1)),
        ]),
    ])
    mlc = _report("MLC", "C", [
        _result([
            _entry(_segment_header()),
        ]),
    ])
    with _FeederRun(mlx, mlc) as feeder:
        result = feeder.execute()
        stored = list(feeder.stored)

    trip = _trip(result)
    assert trip["status"] == "success"
    assert trip["cancelledCount"] == 1
    assert trip["everBookedCount"] == 0
    assert len(stored) == 1
    doc = stored[0]
    assert len(doc["cancelledPassengers"]) == 1
    assert doc["cancelledCount"] == 1
    assert _identities(doc["cancelledPassengers"]) == {("NGUYEN", "LINH", "SINGLE1")}
    assert doc["everBookedPassengers"] == []
    assert doc["everBookedCount"] == 0

    others = _others(_dashboard(doc, "FINAL"))
    assert others["offloaded"] == 1
    assert others["offloadedAvailable"] is True
    assert others["noShow"] == 0
    assert others["noShowAvailable"] is True


def test_response_without_success_is_not_authoritative():
    """A body with passengers but no Success element is not a successful report."""
    ghost = _report("MLX", "X", [
        _result([
            _entry(_group("GHOST/PAX", "GHOST1", 1)),
        ]),
    ], success=False)
    ever = _report("MLC", "C", [
        _result([
            _entry(_group("MISSING/NOSE", "MISS01", 1)),
        ]),
    ], success=False)
    with _FeederRun(ghost, ever) as feeder:
        result = feeder.execute()
        stored = list(feeder.stored)

    trip = _trip(result)
    assert trip["status"] != "success"
    doc = stored[-1] if stored else None
    others = _others(_dashboard(doc, "FINAL"))
    assert others["offloadedAvailable"] is False
    assert others["noShowAvailable"] is False
    assert _card(_dashboard(doc, "FINAL"), "offloaded")["subLabel"] != "MLX report"
    assert _card(_dashboard(doc, "FINAL"), "noShow")["subLabel"] != "MLC vs manifest"


def test_successful_report_with_no_passenger_entries_is_zero():
    """Success plus a segment header and no passenger entries is a real zero."""
    empty = _report("MLX", "X", [
        _result([
            _entry(_segment_header()),
        ]),
    ])
    with _FeederRun(empty, empty) as feeder:
        result = feeder.execute()
        stored = list(feeder.stored)

    trip = _trip(result)
    assert trip["status"] == "success"
    assert trip["cancelledCount"] == 0
    assert trip["everBookedCount"] == 0
    assert len(stored) == 1
    doc = stored[0]
    assert doc["cancelledPassengers"] == []
    assert doc["cancelledCount"] == 0
    assert doc["everBookedPassengers"] == []
    assert doc["everBookedCount"] == 0
    others = _others(_dashboard(doc, "FINAL"))
    assert others["offloaded"] == 0
    assert others["offloadedAvailable"] is True
    assert others["noShow"] == 0
    assert others["noShowAvailable"] is True
    assert _card(_dashboard(doc, "FINAL"), "offloaded")["subLabel"] == "MLX report"


def _main():
    tests = [
        test_documented_mlx_and_mlc_bodies_reach_dashboard,
        test_single_group_entry_is_counted,
        test_response_without_success_is_not_authoritative,
        test_successful_report_with_no_passenger_entries_is_zero,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    if failed:
        print(f"{failed} failed, {len(tests) - failed} passed")
        sys.exit(1)
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    _main()
