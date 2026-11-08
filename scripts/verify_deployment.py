"""Deployment verification using only the standard library.
  python scripts/verify_deployment.py create   -> exercises the workflow through the public URL (default http://localhost:8080)
  python scripts/verify_deployment.py check    -> run AFTER restarting containers: proves the data survived
Fixed idempotency keys make 'create' safe to repeat."""
import json, os, sys, urllib.request, urllib.error
BASE = os.environ.get("BASE_URL", "http://localhost:8080"); PW = os.environ["SEED_PASSWORD"]
def call(method, path, body=None, token=None, key=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    if key: h["Idempotency-Key"] = key
    req = urllib.request.Request(BASE + "/api" + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status, json.load(r), r.headers.get("X-Instance-Id")
    except urllib.error.HTTPError as e: return e.code, json.load(e), e.headers.get("X-Instance-Id")
def login(who): return call("POST", "/login", {"email": f"{who}@campus.test", "password": PW})[1]["token"]
def expect(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  [{extra}]" if extra else "")); 
    if not cond: sys.exit(1)
mode = sys.argv[1] if len(sys.argv) > 1 else "create"; mgr, stu = login("manager"), login("student")
if mode == "create":
    s, b, inst = call("POST", "/bookings", {"facility_id": 4, "start": "2040-01-10T09:00", "end": "2040-01-10T10:00", "purpose": "verify"}, stu, key="verify-booking-1")
    expect("booking accepted (201) or replayed (200)", s in (200, 201), f"HTTP {s} via {inst}")
    s2, b2, inst2 = call("POST", "/bookings", {"facility_id": 4, "start": "2040-01-10T09:00", "end": "2040-01-10T10:00", "purpose": "verify"}, stu, key="verify-booking-1")
    expect("retry with same key returns the same booking", s2 == 200 and b2["booking"]["id"] == b["booking"]["id"], f"id {b['booking']['id']} via {inst2}")
    s3, b3, _ = call("POST", "/bookings", {"facility_id": 4, "start": "2040-01-10T09:30", "end": "2040-01-10T10:30"}, login("student2"))
    expect("overlapping booking by another user rejected (409 OVERLAP)", s3 == 409 and b3["error"]["code"] == "OVERLAP")
    s4, e1, _ = call("POST", "/sensor-events", {"event_id": "VERIFY-EVT-1", "facility_id": 5, "sensor_type": "temperature_c", "value": 35, "ts": "2040-01-10T09:00"}, mgr)
    s5, e2, _ = call("POST", "/sensor-events", {"event_id": "VERIFY-EVT-1", "facility_id": 5, "sensor_type": "temperature_c", "value": 35, "ts": "2040-01-10T09:00"}, mgr)
    expect("sensor breach creates exactly one ticket; duplicate delivery reuses it", e1["ticket"]["id"] == e2["ticket"]["id"] and e2["duplicate"], f"ticket {e1['ticket']['id']}")
    s6, _, _ = call("POST", "/tickets/1/assign", {}, stu)
    expect("student cannot assign tickets (403)", s6 == 403)
else:
    s, bl, inst = call("GET", "/bookings?mine=1", token=stu)
    expect("booking survived restart", any(x["start_ts"].startswith("2040-01-10T09:00") for x in bl), f"served by {inst}")
    s, tl, inst = call("GET", "/tickets", token=mgr)
    expect("sensor-generated ticket survived restart", any(t["source_ref"] == "sensor:VERIFY-EVT-1" for t in tl), f"served by {inst}")
    s, au, _ = call("GET", "/audit?entity=booking", token=mgr)
    expect("audit history survived restart", len(au) >= 1, f"{len(au)} booking audit rows")
print("ALL CHECKS PASSED")
