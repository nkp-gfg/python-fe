# Audit Rules

## Overview

`backend/api/audit_rules.py` implements a rule-based engine that evaluates passenger data against 12 operational rules to detect process violations and discrepancies.

**Entry point:** `run_audit(passenger_doc, reservations_doc, changes_list)` → `{alerts, summary, totalAlerts}`

**Invoked by:** `GET /flights/{fn}/audit` route (fetches latest passenger_list, reservations, all changes, then runs `run_audit()`).

## Severity Levels

| Level      | Meaning                                                       |
| ---------- | ------------------------------------------------------------- |
| `CRITICAL` | Safety-critical or compliance violation — immediate attention |
| `WARNING`  | Operational discrepancy — should be investigated              |
| `INFO`     | Notable but non-urgent observation                            |

## Rules

### CRITICAL

| #   | Rule                                 | Detects                                                            |
| --- | ------------------------------------ | ------------------------------------------------------------------ |
| 2   | `rule_boarded_without_checkin`       | Passenger marked boarded but `isCheckedIn` is false                |
| 3   | `rule_boarded_without_boarding_pass` | Passenger boarded without `boardingPassIssued`                     |
| 4   | `rule_cabin_overcapacity`            | More passengers in cabin than `authorized` capacity                |
| 5   | `rule_duplicate_seats`               | Same seat assigned to multiple passengers                          |
| 11  | `rule_informal_cabin_move`           | Cabin changed but `bookingClass` not updated (undocumented move)   |
| 12  | `rule_staff_in_premium_no_upgrade`   | Staff (`corpId='T'`) in J/F cabin with no upgrade event in changes |

### WARNING

| #   | Rule                                   | Detects                                                                                     |
| --- | -------------------------------------- | ------------------------------------------------------------------------------------------- |
| 1   | `rule_desired_upgrade_not_processed`   | Boarded passenger has `desiredBookingClass` set but cabin unchanged                         |
| 6   | `rule_cabin_available_mismatch`        | Sabre-reported `available` ≠ `authorized - actual_count`                                    |
| 7   | `rule_boarded_without_docs`            | Boarded passenger missing `DOCS` edit code                                                  |
| 8   | `rule_checkedin_without_boarding_pass` | Checked-in but no boarding pass issued                                                      |
| 9   | `rule_party_size_mismatch`             | Reservation `numberInParty` doesn't match manifest count for PNR                            |
| 10  | `rule_upgrade_queue_skipped`           | Lower-priority passenger upgraded while higher-priority passenger in same cabin was skipped |

## Cross-Database Audit (`backend/api/routes/data_audit.py`)

`GET /data-audit/{fn}/compare` performs field-level comparison between PostgreSQL OTP and MongoDB Sabre data.

Uses `ThreadPoolExecutor(max_workers=2)` to query both databases in parallel.

**19 fields compared:**
Flight Number, Sequence Number, Origin, Destination, Flight Date, Flight Status, Aircraft Type/Registration, Scheduled/Estimated/Actual Departure/Arrival (UTC), Total Passengers, Actual Origin/Destination, Service Type, Departure Gate.

**Match categories:** `match`, `mismatch`, `pg_only`, `mongo_only`

Additional endpoint: `GET /data-audit/{fn}/passengers` for passenger-level cross-DB comparison.
