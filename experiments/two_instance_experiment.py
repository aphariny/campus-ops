"""Concurrency / restart / bounded-load experiment against TWO real API processes sharing one SQLite database.
Usage: SEED_PASSWORD=... python experiments/two_instance_experiment.py
Everything printed here is measured, nothing is estimated."""
import os, signal, sqlite3, statistics, subprocess, sys, tempfile, threading, time, uuid
import requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PW = os.environ.get("SEED_PASSWORD", "TestOnly-Pass1"); os.environ["SEED_PASSWORD"] = PW
tmp = tempfile.mkdtemp(); DB = os.path.join(tmp, "exp.db"); os.environ["DATABASE_PATH"] = DB
PORTS = {"A": 5101, "B": 5102}; procs = {}
def base(i): return f"http://127.0.0.1:{PORTS[i]}"

def start(i):
    procs[i] = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "serve.py"), str(PORTS[i]), i], cwd=os.path.join(ROOT, "backend"),
                                env={**os.environ}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if requests.get(base(i) + "/api/health", timeout=1).status_code == 200: return
        except requests.RequestException: time.sleep(0.1)
    raise RuntimeError("instance did not start")

def hdr(tok, **x): return {"Authorization": "Bearer " + tok, **x}
def login(i, who): return requests.post(base(i) + "/api/login", json={"email": f"{who}@campus.test", "password": PW}).json()["token"]
def q(sql, *a): c = sqlite3.connect(DB); r = c.execute(sql, a).fetchall(); c.close(); return r
def race(fns):
    """Run callables simultaneously (barrier) and return their results."""
    b = threading.Barrier(len(fns)); out = [None] * len(fns)
    def run(k):
        b.wait(); out[k] = fns[k]()
    ts = [threading.Thread(target=run, args=(k,)) for k in range(len(fns))]
    [t.start() for t in ts]; [t.join() for t in ts]; return out
def day(n): return f"2031-{1 + n // 28:02d}-{1 + n % 28:02d}"

if __name__ == "__main__":
    subprocess.run([sys.executable, os.path.join(ROOT, "backend", "seed.py")], check=True, env=os.environ, stdout=subprocess.DEVNULL)
    start("A"); start("B")
    print(f"[setup] two API processes: A=:{PORTS['A']} (pid {procs['A'].pid}), B=:{PORTS['B']} (pid {procs['B'].pid}); shared DB file")
    T = {w: {"A": login("A", w), "B": login("B", w)} for w in ("student", "student2", "faculty", "faculty2", "manager", "tech1", "tech2", "admin", "approver")}
    print("[setup] a token issued by instance A is accepted by instance B:", requests.get(base("B") + "/api/me", headers=hdr(T["student"]["A"])).status_code == 200)

    # ---- E1: two users, same laboratory, overlapping slots (20 trials, instances alternate) ----
    wins = []
    for n in range(20):
        r = race([lambda: requests.post(base("A") + "/api/bookings", json={"facility_id": 5, "start": f"{day(n)}T09:00", "end": f"{day(n)}T11:00"}, headers=hdr(T["student"]["A"], **{"Idempotency-Key": uuid.uuid4().hex})),
                  lambda: requests.post(base("B") + "/api/bookings", json={"facility_id": 5, "start": f"{day(n)}T10:00", "end": f"{day(n)}T12:00"}, headers=hdr(T["student2"]["B"], **{"Idempotency-Key": uuid.uuid4().hex}))])
        wins.append(sorted(x.status_code for x in r))
    print(f"[E1] 20 trials of two simultaneous overlapping requests on different instances -> outcomes {set(map(tuple, wins))}  (expected only (201, 409))")
    # 16-way race for one slot
    rs = race([(lambda k=k: requests.post(base("AB"[k % 2]) + "/api/bookings", json={"facility_id": 6, "start": "2032-05-01T09:00", "end": "2032-05-01T10:00"},
                headers=hdr(T[["student", "student2", "faculty", "faculty2"][k % 4]]["AB"[k % 2]], **{"Idempotency-Key": uuid.uuid4().hex}))) for k in range(16)])
    codes = sorted(x.status_code for x in rs); print(f"[E1] 16-way race for one slot: {codes.count(201)} x 201, {codes.count(409)} x 409, other {len(codes) - codes.count(201) - codes.count(409)}")

    # ---- E2: conflicting ticket updates ----
    sev = []
    for n in range(10):
        r = requests.post(base("A") + "/api/sensor-events", json={"event_id": f"T{n}", "facility_id": 5, "sensor_type": "temperature_c", "value": 40, "ts": "2031-01-01T00:00"}, headers=hdr(T["manager"]["A"])).json()
        tid = r["ticket"]["id"]
        a = requests.post(base("A") + f"/api/tickets/{tid}/assign", json={"assignee_id": q("SELECT id FROM users WHERE email='tech1@campus.test'")[0][0], "priority": "high", "category": "hvac", "expected_version": 1}, headers=hdr(T["manager"]["A"])).json()
        v = requests.post(base("A") + f"/api/tickets/{tid}/transition", json={"to": "in_inspection", "expected_version": a["version"]}, headers=hdr(T["tech1"]["A"])).json()["version"]
        r2 = race([lambda: requests.post(base("A") + f"/api/tickets/{tid}/transition", json={"to": "pending_work", "expected_version": v, "inspection_notes": "waiting for part"}, headers=hdr(T["tech1"]["A"])),
                   lambda: requests.post(base("B") + f"/api/tickets/{tid}/transition", json={"to": "resolved", "expected_version": v, "inspection_notes": "fixed", "resolution_notes": "reset breaker"}, headers=hdr(T["tech1"]["B"]))])
        final = q("SELECT status,version FROM tickets WHERE id=?", tid)[0]; n_tr = q("SELECT COUNT(*) FROM audit_events WHERE entity='ticket' AND entity_id=? AND action='transition'", tid)[0][0]
        sev.append((tuple(sorted(x.status_code for x in r2)), final[1], n_tr))
    print(f"[E2] 10 trials of two incompatible simultaneous updates -> (status codes, final version, transitions audited): {set(sev)}  (expected ((200, 409), 4, 2))")

    # ---- E3: duplicates / retries ----
    k = uuid.uuid4().hex
    rs = race([(lambda i=i: requests.post(base("AB"[i % 2]) + "/api/bookings", json={"facility_id": 4, "start": "2033-01-01T09:00", "end": "2033-01-01T10:00"}, headers=hdr(T["faculty"]["AB"[i % 2]], **{"Idempotency-Key": k}))) for i in range(10)])
    print(f"[E3] same booking retried 10x in parallel (same Idempotency-Key): codes {sorted(x.status_code for x in rs)} -> rows in DB: {q('SELECT COUNT(*) FROM bookings WHERE facility_id=4 AND start_ts LIKE ?', '2033-01-01%')[0][0]}")
    rs = race([(lambda i=i: requests.post(base("AB"[i % 2]) + "/api/sensor-events", json={"event_id": "DUP-1", "facility_id": 6, "sensor_type": "water_leak", "value": 1, "ts": "2033-01-01T00:00"}, headers=hdr(T["manager"]["AB"[i % 2]]))) for i in range(10)])
    print(f"[E3] sensor event DUP-1 delivered 10x in parallel: codes {sorted(x.status_code for x in rs)} -> alerts: {q('SELECT COUNT(*) FROM sensor_alerts WHERE event_id=?', 'DUP-1')[0][0]}, tickets: {q('SELECT COUNT(*) FROM tickets WHERE source_ref=?', 'sensor:DUP-1')[0][0]}")
    tid = q("SELECT id FROM tickets WHERE source_ref='sensor:DUP-1'")[0][0]
    r = requests.post(base("A") + f"/api/tickets/{tid}/devops", json={"simulate_failure": "timeout"}, headers=hdr(T["manager"]["A"]))
    print(f"[E3] developer-issue creation, first attempt times out: HTTP {r.status_code}, local link status = {r.json()['link']['status']}")
    rs = race([(lambda i=i: requests.post(base("AB"[i % 2]) + f"/api/tickets/{tid}/devops", json={}, headers=hdr(T["manager"]["AB"[i % 2]]))) for i in range(8)])
    print(f"[E3] 8 parallel retries after the timeout: codes {sorted(x.status_code for x in rs)} -> tracker issues: {q('SELECT COUNT(*) FROM mock_tracker_issues')[0][0]}, links: {q('SELECT COUNT(*) FROM integration_links WHERE ticket_id=?', tid)[0][0]}, final link status: {q('SELECT status FROM integration_links WHERE ticket_id=?', tid)[0][0]}")

    # ---- E4: instance restart while the other keeps serving ----
    before = q("SELECT COUNT(*) FROM bookings")[0][0]
    procs["A"].send_signal(signal.SIGKILL); procs["A"].wait()
    try: requests.get(base("A") + "/api/health", timeout=1); down = False
    except requests.RequestException: down = True
    b_ok = requests.get(base("B") + "/api/bookings?mine=1", headers=hdr(T["student"]["B"])).status_code
    print(f"[E4] instance A killed (SIGKILL): A unreachable={down}; B still serves GET -> {b_ok}")
    # failover client: try A, on connection error retry the SAME idempotency key on B (what the nginx upstream does)
    keys, results, lock = [uuid.uuid4().hex for _ in range(40)], {}, threading.Lock()
    def worker(n):
        for inst in ("A", "B", "B"):
            try:
                r = requests.post(base(inst) + "/api/bookings", json={"facility_id": 3, "start": f"2034-{1 + n // 28:02d}-{1 + n % 28:02d}T09:00", "end": f"2034-{1 + n // 28:02d}-{1 + n % 28:02d}T10:00"},
                                  headers=hdr(T["student"][inst], **{"Idempotency-Key": keys[n]}), timeout=5)
                with lock: results[n] = (inst, r.status_code)
                return
            except requests.RequestException: continue
    def restarter(): time.sleep(0.4); start("A")
    th = [threading.Thread(target=worker, args=(n,)) for n in range(40)] + [threading.Thread(target=restarter)]
    [t.start() for t in th]; [t.join() for t in th]
    ok = sum(1 for v in results.values() if v[1] in (200, 201))
    print(f"[E4] 40 booking submissions while A was down and being restarted: {ok}/40 succeeded; served by {sorted({v[0] for v in results.values()})}; rows created: {q('SELECT COUNT(*) FROM bookings WHERE start_ts LIKE ?', '2034-%')[0][0]} (expected 40), distinct keys: {q('SELECT COUNT(DISTINCT idempotency_key) FROM bookings WHERE start_ts LIKE ?', '2034-%')[0][0]}")
    print(f"[E4] after restart: A health={requests.get(base('A') + '/api/health').json()['instance']}, total bookings {q('SELECT COUNT(*) FROM bookings')[0][0]} (was {before} before kill, +40), old session token still valid on restarted A: {requests.get(base('A') + '/api/me', headers=hdr(T['student']['B'])).status_code == 200}")

    # ---- E5: bounded load ----
    CLIENTS, REQS = 30, 20; lat, errs, lock = [], [0], threading.Lock(); t0 = time.time()
    def user(n):
        w = ["student", "student2", "faculty", "faculty2"][n % 4]; inst = "AB"[n % 2]
        for j in range(REQS):
            s = time.time()
            try:
                if j % 5 == 4:
                    d = f"2035-{1 + (n * REQS + j) // 28 % 12:02d}-{1 + (n * REQS + j) % 28:02d}"
                    r = requests.post(base(inst) + "/api/bookings", json={"facility_id": 4, "start": f"{d}T{8 + n % 8:02d}:00", "end": f"{d}T{8 + n % 8:02d}:30"}, headers=hdr(T[w][inst], **{"Idempotency-Key": uuid.uuid4().hex}), timeout=30)
                    bad = r.status_code not in (201, 409)       # 409 = legitimate overlap rejection under contention
                else:
                    r = requests.get(base(inst) + ["/api/dashboard", "/api/facilities", "/api/tickets", "/api/bookings"][j % 4], headers=hdr(T[w][inst]), timeout=30); bad = r.status_code != 200
            except requests.RequestException: bad = True
            with lock:
                lat.append((time.time() - s) * 1000); errs[0] += bad
    th = [threading.Thread(target=user, args=(n,)) for n in range(CLIENTS)]; [t.start() for t in th]; [t.join() for t in th]; dur = time.time() - t0
    lat.sort(); pct = lambda p: lat[min(len(lat) - 1, int(p / 100 * len(lat)))]
    print(f"[E5] bounded load: {CLIENTS} concurrent clients x {REQS} requests = {len(lat)} requests over 2 instances in {dur:.1f}s -> {len(lat) / dur:.0f} req/s; latency ms p50={pct(50):.0f} p95={pct(95):.0f} p99={pct(99):.0f} max={lat[-1]:.0f}; unexpected errors={errs[0]}")

    # ---- final invariants ----
    ov = q("SELECT COUNT(*) FROM bookings a JOIN bookings b ON a.facility_id=b.facility_id AND a.id<b.id AND a.start_ts<b.end_ts AND a.end_ts>b.start_ts "
           "WHERE a.status IN ('requested','approved','confirmed','in_use') AND b.status IN ('requested','approved','confirmed','in_use')")[0][0]
    dupk = q("SELECT COUNT(*) FROM (SELECT user_id,idempotency_key FROM bookings WHERE idempotency_key IS NOT NULL GROUP BY 1,2 HAVING COUNT(*)>1)")[0][0]
    dupt = q("SELECT COUNT(*) FROM (SELECT source_ref FROM tickets WHERE source_ref IS NOT NULL GROUP BY 1 HAVING COUNT(*)>1)")[0][0]
    nb, nt, na = q("SELECT COUNT(*) FROM bookings")[0][0], q("SELECT COUNT(*) FROM tickets")[0][0], q("SELECT COUNT(*) FROM audit_events")[0][0]
    creates = q("SELECT COUNT(*) FROM audit_events WHERE entity='booking' AND action='create'")[0][0]
    print(f"[invariants] bookings={nb}, tickets={nt}, audit rows={na}; overlapping active bookings={ov}; duplicate idempotency keys={dupk}; duplicate ticket source refs={dupt}; booking-create audit rows={creates} (== bookings: {creates == nb})")
    for p in procs.values(): p.kill()
