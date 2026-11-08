"""Same race scenarios run against any backend directory (baseline v1.0.0 vs revised v1.1.0).
Usage: BACKEND_DIR=/tmp/baseline_backend python experiments/race_checks.py"""
import os, sqlite3, subprocess, sys, tempfile, threading, time, uuid
import requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.environ.get("BACKEND_DIR", os.path.join(ROOT, "backend"))
PW = os.environ["SEED_PASSWORD"]; tmp = tempfile.mkdtemp(); DB = os.path.join(tmp, "r.db")
env = {**os.environ, "DATABASE_PATH": DB}; PORTS = {"A": 5201, "B": 5202}; procs = []
def base(i): return f"http://127.0.0.1:{PORTS[i]}"
def hdr(t, **x): return {"Authorization": "Bearer " + t, **x}
def q(sql, *a): c = sqlite3.connect(DB); r = c.execute(sql, a).fetchall(); c.close(); return r
def race(fns):
    b = threading.Barrier(len(fns)); out = [None] * len(fns)
    def run(k): b.wait(); out[k] = fns[k]()
    ts = [threading.Thread(target=run, args=(k,)) for k in range(len(fns))]; [t.start() for t in ts]; [t.join() for t in ts]; return out
subprocess.run([sys.executable, os.path.join(BACKEND, "seed.py")], check=True, env=env, stdout=subprocess.DEVNULL)
for i in "AB":
    procs.append(subprocess.Popen([sys.executable, os.path.join(BACKEND, "serve.py"), str(PORTS[i]), i], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
for i in "AB":
    for _ in range(100):
        try: requests.get(base(i) + "/api/health", timeout=1); break
        except requests.RequestException: time.sleep(0.1)
who = ["student", "student2", "faculty", "faculty2", "manager", "tech1"]
T = {w: {i: requests.post(base(i) + "/api/login", json={"email": f"{w}@campus.test", "password": PW}).json()["token"] for i in "AB"} for w in who}
res = {}
# S1: 16 simultaneous overlapping requests for one slot across two instances (x5 rounds)
dbl = 0; ok_total = 0
for rd in range(5):
    rs = race([(lambda k=k: requests.post(base("AB"[k % 2]) + "/api/bookings", json={"facility_id": 6, "start": f"2032-0{rd+1}-01T09:00", "end": f"2032-0{rd+1}-01T10:00"},
               headers=hdr(T[who[k % 4]]["AB"[k % 2]], **{"Idempotency-Key": uuid.uuid4().hex}))) for k in range(16)])
    ok = sum(1 for r in rs if r.status_code == 201); ok_total += ok; dbl += max(0, ok - 1)
res["S1 overlapping bookings accepted (5 rounds x 16 requests; correct = 5)"] = ok_total
res["S1 double-booked slots created"] = dbl
# S2: one booking retried 10x in parallel with the same idempotency key
k = uuid.uuid4().hex
race([(lambda i=i: requests.post(base("AB"[i % 2]) + "/api/bookings", json={"facility_id": 4, "start": "2033-01-01T09:00", "end": "2033-01-01T10:00"}, headers=hdr(T["faculty"]["AB"[i % 2]], **{"Idempotency-Key": k}))) for i in range(10)])
res["S2 booking rows after 10 parallel retries (correct = 1)"] = q("SELECT COUNT(*) FROM bookings WHERE facility_id=4")[0][0]
# S3: one sensor event delivered 10x in parallel
race([(lambda i=i: requests.post(base("AB"[i % 2]) + "/api/sensor-events", json={"event_id": "DUP-1", "facility_id": 6, "sensor_type": "water_leak", "value": 1, "ts": "2033-01-01T00:00"}, headers=hdr(T["manager"]["AB"[i % 2]]))) for i in range(10)])
res["S3 tickets after 10 parallel deliveries of one sensor event (correct = 1)"] = q("SELECT COUNT(*) FROM tickets WHERE source_ref='sensor:DUP-1'")[0][0]
res["S3 sensor alerts stored (correct = 1)"] = q("SELECT COUNT(*) FROM sensor_alerts WHERE event_id='DUP-1'")[0][0]
# S4: two incompatible simultaneous ticket updates (10 trials)
techid = q("SELECT id FROM users WHERE email='tech1@campus.test'")[0][0]; both_ok = 0; audit_mismatch = 0
for n in range(10):
    tid = requests.post(base("A") + "/api/sensor-events", json={"event_id": f"T{n}", "facility_id": 5, "sensor_type": "temperature_c", "value": 40, "ts": "2031-01-01T00:00"}, headers=hdr(T["manager"]["A"])).json()["ticket"]["id"]
    a = requests.post(base("A") + f"/api/tickets/{tid}/assign", json={"assignee_id": techid, "priority": "high", "category": "hvac", "expected_version": 1}, headers=hdr(T["manager"]["A"])).json()
    v = requests.post(base("A") + f"/api/tickets/{tid}/transition", json={"to": "in_inspection", "expected_version": a["version"]}, headers=hdr(T["tech1"]["A"])).json()["version"]
    r = race([lambda: requests.post(base("A") + f"/api/tickets/{tid}/transition", json={"to": "pending_work", "expected_version": v, "inspection_notes": "waiting for part"}, headers=hdr(T["tech1"]["A"])),
              lambda: requests.post(base("B") + f"/api/tickets/{tid}/transition", json={"to": "resolved", "expected_version": v, "inspection_notes": "fixed", "resolution_notes": "reset breaker"}, headers=hdr(T["tech1"]["B"]))])
    if all(x.status_code == 200 for x in r): both_ok += 1
    froms = [x[0] for x in q("SELECT from_state FROM audit_events WHERE entity='ticket' AND entity_id=? AND action='transition' ORDER BY id", tid)]
    if froms.count("in_inspection") > 1: audit_mismatch += 1
res["S4 trials where BOTH conflicting updates were accepted (correct = 0 of 10)"] = both_ok
res["S4 trials with contradictory audit history (correct = 0 of 10)"] = audit_mismatch
for k_, v_ in res.items(): print(f"{k_}: {v_}")
for p in procs: p.kill()
