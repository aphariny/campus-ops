# Pull request #1 - feature/concurrency-hardening -> main

**Author:** student (individual assignment)   **Reviewer:** none available - this is an AUTHOR SELF-REVIEW, recorded transparently.
It is not presented as review by another person. On a team, the checklist below would be completed by a second developer.

## Why
Race experiments against v1.0.0 (docs/race_baseline.txt) produced 58 double-booked slots, 10 duplicate tickets for one sensor event,
and 10/10 accepted conflicting ticket updates with contradictory audit history.

## What changed
- schema: database triggers refuse overlapping active bookings; UNIQUE constraints back idempotency, sensor event ids and ticket source refs
- api: every check-then-write runs in BEGIN IMMEDIATE; optimistic `expected_version` on bookings and tickets; Idempotency-Key replay; audit rows written in the same transaction
- tests: 27 unit tests; race and two-instance experiments

## Evidence
`docs/race_revised.txt`: all scenarios correct. `docs/unit_tests_output.txt`: 27 tests OK. `docs/experiment_output.txt`: restart and bounded-load results.

## Self-review checklist (author)
- [x] Passwords and tokens are not in source control (`.env` ignored; only sha256 of tokens stored)
- [x] Every write path is covered by a test or an experiment
- [x] Error codes are stable and documented (OVERLAP, STALE_VERSION, INVALID_TRANSITION, ...)
- [ ] Independent review (not possible in an individual assignment)

## Known limitations
SQLite serialises writers: adequate for one host and a few hundred users, not for multi-host deployment (see report, Q6/Q7).
