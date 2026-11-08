-- Smart Campus Operations Platform: schema (SQLite, WAL mode)
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','facility_manager','technician','approver','faculty','student')),
  department TEXT, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS facilities(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
  campus TEXT NOT NULL, building TEXT, capacity INTEGER, restricted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sla_policies(
  priority TEXT PRIMARY KEY CHECK(priority IN ('critical','high','medium','low')),
  response_hours INTEGER NOT NULL, resolve_hours INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sensor_alerts(
  id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, facility_id INTEGER NOT NULL REFERENCES facilities(id),
  sensor_type TEXT NOT NULL, value REAL NOT NULL, threshold REAL, breached INTEGER NOT NULL,
  event_ts TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY, facility_id INTEGER NOT NULL REFERENCES facilities(id),
  user_id INTEGER NOT NULL REFERENCES users(id), start_ts TEXT NOT NULL, end_ts TEXT NOT NULL,
  purpose TEXT,
  status TEXT NOT NULL CHECK(status IN ('requested','approved','confirmed','in_use','completed','cancelled')),
  version INTEGER NOT NULL DEFAULT 1, idempotency_key TEXT, request_hash TEXT, created_at TEXT NOT NULL,
  CHECK(end_ts > start_ts));
CREATE INDEX IF NOT EXISTS ix_bookings_slot ON bookings(facility_id, start_ts, end_ts);
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY, facility_id INTEGER NOT NULL REFERENCES facilities(id),
  source_type TEXT NOT NULL CHECK(source_type IN ('sensor','booking','manual')),
  source_ref TEXT, booking_id INTEGER REFERENCES bookings(id), sensor_alert_id INTEGER REFERENCES sensor_alerts(id),
  title TEXT NOT NULL, description TEXT,
  suggested_category TEXT, suggested_priority TEXT, triage_explanation TEXT,
  category TEXT, priority TEXT,
  status TEXT NOT NULL CHECK(status IN ('new','assigned','in_inspection','pending_work','resolved','closed')),
  assignee_id INTEGER REFERENCES users(id), version INTEGER NOT NULL DEFAULT 1, sla_due TEXT,
  inspection_notes TEXT, resolution_notes TEXT, created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL, resolved_at TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, subject_type TEXT NOT NULL CHECK(subject_type IN ('booking','loan')),
  subject_id INTEGER NOT NULL, requested_by INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','withdrawn')),
  decided_by INTEGER REFERENCES users(id), justification TEXT, created_at TEXT NOT NULL, decided_at TEXT,
  UNIQUE(subject_type, subject_id));
CREATE TABLE IF NOT EXISTS equipment_loans(
  id INTEGER PRIMARY KEY, item TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
  user_id INTEGER NOT NULL REFERENCES users(id), start_ts TEXT NOT NULL, end_ts TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('requested','approved','rejected','returned')),
  version INTEGER NOT NULL DEFAULT 1, idempotency_key TEXT, created_at TEXT NOT NULL,
  UNIQUE(user_id, idempotency_key));
CREATE TABLE IF NOT EXISTS integration_links(
  id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id), system TEXT NOT NULL,
  external_id TEXT, mode TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending_retry','linked','delivered')),
  idempotency_key TEXT UNIQUE NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Simulated external tracker (stands in for GitHub Issues/Jira; idempotent on idem_key like a real API)
CREATE TABLE IF NOT EXISTS mock_tracker_issues(
  id INTEGER PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL, title TEXT, state TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS audit_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor_id INTEGER, entity TEXT NOT NULL,
  entity_id INTEGER, action TEXT NOT NULL, from_state TEXT, to_state TEXT, detail TEXT);

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT,'AUDIT_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT,'AUDIT_IMMUTABLE'); END;
