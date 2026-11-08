"""Builds the v1.0.0 BASELINE: the naive first implementation (single-instance assumptions) from the final code.
Removed on purpose: explicit write transactions, DB overlap trigger, UNIQUE constraints used for de-duplication, optimistic version check."""
import re, shutil, sys, os
src, dst = sys.argv[1], sys.argv[2]
shutil.rmtree(dst, ignore_errors=True); shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "tests"))
sch = open(f"{dst}/schema.sql").read()
sch = re.sub(r"-- DB-level defence in depth.*?(?=CREATE TRIGGER IF NOT EXISTS trg_audit_no_update)", "", sch, flags=re.S)   # drops both overlap triggers
sch = sch.replace("UNIQUE(user_id, idempotency_key), CHECK(end_ts > start_ts)", "CHECK(end_ts > start_ts)")
sch = sch.replace("event_id TEXT UNIQUE NOT NULL", "event_id TEXT NOT NULL").replace("source_ref TEXT UNIQUE", "source_ref TEXT")
open(f"{dst}/schema.sql", "w").write(sch)
app = open(f"{dst}/app.py").read()
app = re.sub(r'@contextlib.contextmanager\ndef write_tx\(\):.*?\n\ndef audit\(', '@contextlib.contextmanager\ndef write_tx():\n    yield db()          # BASELINE: autocommit, check-then-write is NOT atomic\n\ndef audit(', app, flags=re.S)
app = app.replace('if t["version"] != ev: raise ApiError(409, "STALE_VERSION", "ticket changed since you read it", current_version=t["version"], current_status=t["status"])', 'ev = t["version"]   # BASELINE: last write wins')
app = app.replace('def expected_version(d):\n        v = d.get("expected_version")\n        if not isinstance(v, int): raise ApiError(400, "MISSING_FIELD", "integer \'expected_version\' is required", field="expected_version")\n        return v', 'def expected_version(d):\n        return d.get("expected_version")')
open(f"{dst}/app.py", "w").write(app)
print("baseline written to", dst, "| triggers left:", sch.count("OVERLAP"), "| write_tx patched:", "autocommit" in app, "| lww:", "last write wins" in app)
