"""Smart Campus Facility Booking & Maintenance Platform - backend API (Flask + SQLite/WAL).
All state lives in the database (including login sessions) so any number of API instances can serve any request."""
import contextlib, hashlib, json, os, secrets, sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, g, jsonify, request
from werkzeug.security import check_password_hash
import triage

DB_PATH = os.environ.get("DATABASE_PATH", "/data/campus.db")
INSTANCE_ID = os.environ.get("INSTANCE_ID", "local")
INTEGRATION_MODE = os.environ.get("INTEGRATION_MODE", "simulated")   # disclosed in every integration response
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "8"))
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
ACTIVE_BOOKING = ("requested", "approved", "confirmed", "in_use")
SENSOR_RULES = {   # planning assumptions: sensor_type -> (threshold, narrative used by triage)
    "temperature_c": (30.0, "high temperature, cooling failure, hvac overheating"),
    "humidity_pct":  (75.0, "high humidity, ventilation problem"),
    "power_kw":      (50.0, "power overload, electrical breaker"),
    "water_leak":    (1.0,  "water leak, flood risk, pipe"),
}
TICKET_TRANSITIONS = {
    "new": {"assigned"}, "assigned": {"in_inspection"},
    "in_inspection": {"pending_work", "resolved"}, "pending_work": {"in_inspection", "resolved"},
    "resolved": {"closed", "in_inspection"}, "closed": set(),
}
BOOKING_ACTIONS = {   # action -> (allowed from, to)
    "confirm": ({"approved"}, "confirmed"), "check_in": ({"confirmed"}, "in_use"),
    "complete": ({"in_use"}, "completed"), "cancel": ({"requested", "approved", "confirmed"}, "cancelled"),
}
MANAGERS = ("admin", "facility_manager")

def now_dt(): return datetime.now(timezone.utc)
def now_iso(): return now_dt().strftime(TS_FMT)

class ApiError(Exception):
    def __init__(self, status, code, message, **extra):
        self.status, self.code, self.message, self.extra = status, code, message, extra

def parse_ts(value, field):
    if not isinstance(value, str): raise ApiError(400, "BAD_TIMESTAMP", f"{field} must be an ISO-8601 string")
    v = value.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try: return datetime.strptime(v, fmt).strftime(TS_FMT)
        except ValueError: pass
    raise ApiError(400, "BAD_TIMESTAMP", f"{field} is not a valid ISO-8601 timestamp: {value!r}")

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)   # autocommit; we issue BEGIN explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON"); conn.execute("PRAGMA busy_timeout=15000")
    return conn

def db():
    if "db" not in g: g.db = connect()
    return g.db

@contextlib.contextmanager
def write_tx():
    """BEGIN IMMEDIATE takes the write lock up front: check-then-write sequences become serialised across ALL instances."""
    c = db(); c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK"); raise

def audit(c, actor_id, entity, entity_id, action, from_state=None, to_state=None, detail=None):
    c.execute("INSERT INTO audit_events(ts,actor_id,entity,entity_id,action,from_state,to_state,detail) VALUES(?,?,?,?,?,?,?,?)",
              (now_iso(), actor_id, entity, entity_id, action, from_state, to_state,
               json.dumps(detail) if detail is not None else None))

def audit_denied(user, action, detail):
    """Denials are recorded in their own transaction (called BEFORE any write_tx is opened)."""
    with write_tx() as c:
        audit(c, user["id"] if user else None, "security", None, "denied:" + action, detail=detail)

def rows(cur): return [dict(r) for r in cur.fetchall()]
def one(cur):
    r = cur.fetchone(); return dict(r) if r else None

def create_app():
    app = Flask(__name__)

    @app.teardown_appcontext
    def close_db(_):
        conn = g.pop("db", None)
        if conn is not None: conn.close()

    @app.errorhandler(ApiError)
    def handle_api_error(e):
        body = {"error": {"code": e.code, "message": e.message, **e.extra}}
        return jsonify(body), e.status

    @app.errorhandler(404)
    def nf(_): return jsonify({"error": {"code": "NOT_FOUND", "message": "route not found"}}), 404

    @app.after_request
    def tag_instance(resp):
        resp.headers["X-Instance-Id"] = INSTANCE_ID
        return resp

    def current_user():
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "): return None
        th = hashlib.sha256(auth[7:].encode()).hexdigest()
        r = one(db().execute("SELECT u.id,u.name,u.email,u.role,u.department,s.expires_at FROM sessions s "
                             "JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (th,)))
        if not r or r["expires_at"] < now_iso(): return None
        return r

    def auth(*roles):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                user = current_user()
                if not user: raise ApiError(401, "UNAUTHENTICATED", "valid bearer token required")
                if roles and user["role"] not in roles:
                    audit_denied(user, request.endpoint, {"role": user["role"], "needs": list(roles)})
                    raise ApiError(403, "FORBIDDEN", f"role '{user['role']}' may not perform this operation")
                g.user = user
                return fn(*a, **kw)
            return wrapper
        return deco

    def body():
        d = request.get_json(silent=True)
        if not isinstance(d, dict): raise ApiError(400, "BAD_JSON", "JSON object body required")
        return d

    def need(d, *fields):
        for f in fields:
            if d.get(f) in (None, ""): raise ApiError(400, "MISSING_FIELD", f"'{f}' is required", field=f)

    def expected_version(d):
        v = d.get("expected_version")
        if not isinstance(v, int): raise ApiError(400, "MISSING_FIELD", "integer 'expected_version' is required", field="expected_version")
        return v

    def sla_state(t, policies):
        if t["status"] in ("resolved", "closed"):
            if not t["sla_due"] or not t["resolved_at"]: return "met"
            return "met" if t["resolved_at"] <= t["sla_due"] else "breached"
        if not t["sla_due"]: return "not_started"
        due = datetime.strptime(t["sla_due"], TS_FMT).replace(tzinfo=timezone.utc)
        remaining = (due - now_dt()).total_seconds() / 3600
        if remaining < 0: return "breached"
        total = policies.get(t["priority"], 24)
        return "at_risk" if remaining < 0.25 * total else "on_track"

    def policies(c): return {r["priority"]: r["resolve_hours"] for r in c.execute("SELECT priority,resolve_hours FROM sla_policies")}

    def ticket_view(c, t):
        t = dict(t); t["sla_state"] = sla_state(t, policies(c)); return t

    # ---------------- auth ----------------
    @app.get("/api/health")
    def health():
        db().execute("SELECT 1")
        return jsonify({"status": "ok", "instance": INSTANCE_ID, "integration_mode": INTEGRATION_MODE})

    @app.post("/api/login")
    def login():
        d = body(); need(d, "email", "password")
        u = one(db().execute("SELECT * FROM users WHERE email=?", (d["email"].strip().lower(),)))
        if not u or not check_password_hash(u["password_hash"], d["password"]):
            raise ApiError(401, "BAD_CREDENTIALS", "invalid email or password")
        token = secrets.token_urlsafe(32)
        with write_tx() as c:
            c.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), u["id"],
                      (now_dt() + timedelta(hours=SESSION_TTL_HOURS)).strftime(TS_FMT)))
            audit(c, u["id"], "user", u["id"], "login")
        return jsonify({"token": token, "user": {k: u[k] for k in ("id", "name", "email", "role", "department")}})

    @app.get("/api/me")
    @auth()
    def me(): return jsonify(g.user)

    @app.get("/api/users")
    @auth(*MANAGERS)
    def users():
        role = request.args.get("role")
        q, a = "SELECT id,name,email,role,department FROM users", ()
        if role: q, a = q + " WHERE role=?", (role,)
        return jsonify(rows(db().execute(q, a)))

    # ---------------- facilities & bookings ----------------
    @app.get("/api/facilities")
    @auth()
    def facilities(): return jsonify(rows(db().execute("SELECT * FROM facilities ORDER BY campus,name")))

    @app.get("/api/bookings")
    @auth()
    def list_bookings():
        q = ("SELECT b.id,b.facility_id,b.user_id,b.start_ts,b.end_ts,b.purpose,b.status,b.version,b.created_at,f.name facility_name,u.name user_name FROM bookings b JOIN facilities f ON f.id=b.facility_id "
             "JOIN users u ON u.id=b.user_id WHERE 1=1")
        a = []
        if request.args.get("facility_id"): q += " AND b.facility_id=?"; a.append(request.args["facility_id"])
        if request.args.get("date"):
            day = parse_ts(request.args["date"] + "T00:00", "date")
            nxt = (datetime.strptime(day, TS_FMT) + timedelta(days=1)).strftime(TS_FMT)
            q += " AND b.start_ts < ? AND b.end_ts > ?"; a += [nxt, day]
        if request.args.get("mine") == "1": q += " AND b.user_id=?"; a.append(g.user["id"])
        return jsonify(rows(db().execute(q + " ORDER BY b.start_ts", a)))

    @app.post("/api/bookings")
    @auth()
    def create_booking():
        d = body(); need(d, "facility_id", "start", "end")
        start, end = parse_ts(d["start"], "start"), parse_ts(d["end"], "end")
        if end <= start: raise ApiError(400, "BAD_RANGE", "end must be after start")
        if (datetime.strptime(end, TS_FMT) - datetime.strptime(start, TS_FMT)) > timedelta(hours=8):
            raise ApiError(400, "TOO_LONG", "a booking may not exceed 8 hours (planning assumption)")
        user = g.user
        fac = one(db().execute("SELECT * FROM facilities WHERE id=?", (d["facility_id"],)))
        if not fac: raise ApiError(404, "NO_FACILITY", "facility not found")
        if fac["restricted"] and user["role"] == "student":
            audit_denied(user, "create_booking", {"facility_id": fac["id"], "reason": "students may not book restricted facilities"})
            raise ApiError(403, "FORBIDDEN", "students may not book restricted facilities")
        key = request.headers.get("Idempotency-Key")
        rhash = hashlib.sha256(f"{fac['id']}|{start}|{end}|{d.get('purpose','')}".encode()).hexdigest()
        with write_tx() as c:
            if key:
                prev = one(c.execute("SELECT * FROM bookings WHERE user_id=? AND idempotency_key=?", (user["id"], key)))
                if prev:
                    if prev["request_hash"] != rhash:
                        raise ApiError(422, "IDEMPOTENCY_KEY_REUSED", "same Idempotency-Key used with a different request")
                    return jsonify({"booking": prev, "replayed": True}), 200
            clash = one(c.execute("SELECT id,start_ts,end_ts,status FROM bookings WHERE facility_id=? AND status IN "
                                  "('requested','approved','confirmed','in_use') AND start_ts<? AND end_ts>?", (fac["id"], end, start)))
            if clash: raise ApiError(409, "OVERLAP", "facility already reserved for an overlapping slot", conflicting_booking=clash)
            status = "requested" if fac["restricted"] else "approved"
            try:
                cur = c.execute("INSERT INTO bookings(facility_id,user_id,start_ts,end_ts,purpose,status,idempotency_key,request_hash,created_at)"
                                " VALUES(?,?,?,?,?,?,?,?,?)", (fac["id"], user["id"], start, end, d.get("purpose"), status, key, rhash, now_iso()))
            except sqlite3.IntegrityError as e:       # trigger backstop
                if "OVERLAP" in str(e): raise ApiError(409, "OVERLAP", "facility already reserved (database constraint)")
                raise
            bid = cur.lastrowid
            if fac["restricted"]:
                c.execute("INSERT INTO approvals(subject_type,subject_id,requested_by,status,created_at) VALUES('booking',?,?,'pending',?)",
                          (bid, user["id"], now_iso()))
            audit(c, user["id"], "booking", bid, "create", None, status,
                  {"facility_id": fac["id"], "start": start, "end": end, "approval_required": bool(fac["restricted"])})
            return jsonify({"booking": one(c.execute("SELECT * FROM bookings WHERE id=?", (bid,))), "replayed": False}), 201

    @app.post("/api/bookings/<int:bid>/transition")
    @auth()
    def booking_transition(bid):
        d = body(); need(d, "action"); ev = expected_version(d)
        if d["action"] not in BOOKING_ACTIONS: raise ApiError(400, "BAD_ACTION", f"action must be one of {sorted(BOOKING_ACTIONS)}")
        allowed_from, to = BOOKING_ACTIONS[d["action"]]
        with write_tx() as c:
            b = one(c.execute("SELECT * FROM bookings WHERE id=?", (bid,)))
            if not b: raise ApiError(404, "NOT_FOUND", "booking not found")
            if g.user["id"] != b["user_id"] and g.user["role"] not in MANAGERS:
                raise ApiError(403, "FORBIDDEN", "only the booking owner or a manager may change this booking")
            if b["version"] != ev:
                raise ApiError(409, "STALE_VERSION", "booking changed since you read it", current_version=b["version"], current_status=b["status"])
            if b["status"] not in allowed_from:
                raise ApiError(409, "INVALID_TRANSITION", f"cannot {d['action']} a booking that is '{b['status']}'", current_status=b["status"])
            c.execute("UPDATE bookings SET status=?,version=version+1 WHERE id=? AND version=?", (to, bid, ev))
            if to == "cancelled":
                c.execute("UPDATE approvals SET status='withdrawn',decided_at=? WHERE subject_type='booking' AND subject_id=? AND status='pending'", (now_iso(), bid))
            audit(c, g.user["id"], "booking", bid, d["action"], b["status"], to)
            return jsonify(one(c.execute("SELECT * FROM bookings WHERE id=?", (bid,))))

    # ---------------- sensor events & tickets ----------------
    def create_ticket(c, fac_id, source_type, source_ref, title, description, created_by, sensor_alert_id=None, booking_id=None):
        s = triage.suggest(title, description)
        cur = c.execute("INSERT INTO tickets(facility_id,source_type,source_ref,booking_id,sensor_alert_id,title,description,"
                        "suggested_category,suggested_priority,triage_explanation,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,'new',?,?)",
                        (fac_id, source_type, source_ref, booking_id, sensor_alert_id, title, description,
                         s["category"], s["priority"], s["explanation"], created_by, now_iso()))
        audit(c, created_by, "ticket", cur.lastrowid, "create", None, "new", {"source": source_ref, "triage": s})
        return cur.lastrowid

    @app.post("/api/sensor-events")
    @auth(*MANAGERS)
    def sensor_event():
        d = body(); need(d, "event_id", "facility_id", "sensor_type", "value", "ts")
        if d["sensor_type"] not in SENSOR_RULES: raise ApiError(400, "BAD_SENSOR", f"sensor_type must be one of {sorted(SENSOR_RULES)}")
        if not isinstance(d["value"], (int, float)): raise ApiError(400, "BAD_VALUE", "value must be numeric")
        ts = parse_ts(d["ts"], "ts")
        with write_tx() as c:
            fac = one(c.execute("SELECT * FROM facilities WHERE id=?", (d["facility_id"],)))
            if not fac: raise ApiError(404, "NO_FACILITY", "facility not found")
            prev = one(c.execute("SELECT * FROM sensor_alerts WHERE event_id=?", (d["event_id"],)))
            if prev:   # duplicate delivery: no new alert, no new ticket
                t = one(c.execute("SELECT id,status FROM tickets WHERE sensor_alert_id=?", (prev["id"],)))
                return jsonify({"alert": prev, "ticket": t, "duplicate": True}), 200
            threshold, narrative = SENSOR_RULES[d["sensor_type"]]
            breached = d["value"] >= threshold if d["sensor_type"] == "water_leak" else d["value"] > threshold
            cur = c.execute("INSERT INTO sensor_alerts(event_id,facility_id,sensor_type,value,threshold,breached,event_ts,received_at) VALUES(?,?,?,?,?,?,?,?)",
                            (d["event_id"], fac["id"], d["sensor_type"], d["value"], threshold, int(breached), ts, now_iso()))
            aid, tid = cur.lastrowid, None
            audit(c, g.user["id"], "sensor_alert", aid, "ingest", None, "breached" if breached else "normal", {"event_id": d["event_id"]})
            if breached:
                title = f"{d['sensor_type']} reading {d['value']} exceeds {threshold} at {fac['name']}"
                tid = create_ticket(c, fac["id"], "sensor", "sensor:" + d["event_id"], title, narrative, g.user["id"], sensor_alert_id=aid)
            return jsonify({"alert": one(c.execute("SELECT * FROM sensor_alerts WHERE id=?", (aid,))),
                            "ticket": {"id": tid, "status": "new"} if tid else None, "duplicate": False}), 201

    @app.post("/api/tickets")
    @auth()
    def manual_ticket():
        d = body(); need(d, "facility_id", "title")
        key = request.headers.get("Idempotency-Key")
        with write_tx() as c:
            if not one(c.execute("SELECT id FROM facilities WHERE id=?", (d["facility_id"],))): raise ApiError(404, "NO_FACILITY", "facility not found")
            ref = f"manual:{g.user['id']}:{key}" if key else None
            if ref:
                prev = one(c.execute("SELECT * FROM tickets WHERE source_ref=?", (ref,)))
                if prev: return jsonify({"ticket": prev, "replayed": True}), 200
            tid = create_ticket(c, d["facility_id"], "manual", ref, d["title"], d.get("description", ""), g.user["id"], booking_id=d.get("booking_id"))
            return jsonify({"ticket": one(c.execute("SELECT * FROM tickets WHERE id=?", (tid,))), "replayed": False}), 201

    @app.get("/api/tickets")
    @auth()
    def list_tickets():
        c = db(); q, a = "SELECT t.*,f.name facility_name,u.name assignee_name FROM tickets t JOIN facilities f ON f.id=t.facility_id LEFT JOIN users u ON u.id=t.assignee_id WHERE 1=1", []
        if request.args.get("status"): q += " AND t.status=?"; a.append(request.args["status"])
        if g.user["role"] == "technician": q += " AND t.assignee_id=?"; a.append(g.user["id"])
        if g.user["role"] in ("student", "faculty"): q += " AND t.created_by=?"; a.append(g.user["id"])
        pol = policies(c)
        out = rows(c.execute(q + " ORDER BY t.id DESC", a))
        for t in out: t["sla_state"] = sla_state(t, pol)
        return jsonify(out)

    @app.get("/api/tickets/<int:tid>")
    @auth()
    def get_ticket(tid):
        c = db(); t = one(c.execute("SELECT * FROM tickets WHERE id=?", (tid,)))
        if not t: raise ApiError(404, "NOT_FOUND", "ticket not found")
        u = g.user
        if (u["role"] == "technician" and t["assignee_id"] != u["id"]) or (u["role"] in ("student", "faculty") and t["created_by"] != u["id"]):
            raise ApiError(403, "FORBIDDEN", "not your ticket")
        link = one(c.execute("SELECT * FROM integration_links WHERE ticket_id=?", (tid,)))
        hist = rows(c.execute("SELECT * FROM audit_events WHERE entity='ticket' AND entity_id=? ORDER BY id", (tid,)))
        return jsonify({"ticket": ticket_view(c, t), "integration": link, "audit": hist})

    @app.post("/api/tickets/<int:tid>/assign")
    @auth(*MANAGERS)
    def assign_ticket(tid):
        d = body(); need(d, "assignee_id", "priority", "category"); ev = expected_version(d)
        if d["priority"] not in ("critical", "high", "medium", "low"): raise ApiError(400, "BAD_PRIORITY", "priority must be critical|high|medium|low")
        with write_tx() as c:
            t = one(c.execute("SELECT * FROM tickets WHERE id=?", (tid,)))
            if not t: raise ApiError(404, "NOT_FOUND", "ticket not found")
            if t["version"] != ev: raise ApiError(409, "STALE_VERSION", "ticket changed since you read it", current_version=t["version"], current_status=t["status"])
            if t["status"] not in ("new", "assigned"): raise ApiError(409, "INVALID_TRANSITION", f"cannot assign a ticket that is '{t['status']}'")
            tech = one(c.execute("SELECT id,role FROM users WHERE id=?", (d["assignee_id"],)))
            if not tech or tech["role"] != "technician": raise ApiError(400, "BAD_ASSIGNEE", "assignee must be a technician")
            hrs = c.execute("SELECT resolve_hours FROM sla_policies WHERE priority=?", (d["priority"],)).fetchone()[0]
            due = (now_dt() + timedelta(hours=hrs)).strftime(TS_FMT)
            c.execute("UPDATE tickets SET status='assigned',assignee_id=?,priority=?,category=?,sla_due=?,version=version+1 WHERE id=? AND version=?",
                      (tech["id"], d["priority"], d["category"], due, tid, ev))
            overridden = (d["priority"], d["category"]) != (t["suggested_priority"], t["suggested_category"])
            audit(c, g.user["id"], "ticket", tid, "assign", t["status"], "assigned",
                  {"assignee_id": tech["id"], "priority": d["priority"], "category": d["category"], "sla_due": due, "overrode_suggestion": overridden})
            return jsonify(ticket_view(c, one(c.execute("SELECT * FROM tickets WHERE id=?", (tid,)))))

    @app.post("/api/tickets/<int:tid>/transition")
    @auth()
    def ticket_transition(tid):
        d = body(); need(d, "to"); ev = expected_version(d); to = d["to"]
        if to not in TICKET_TRANSITIONS: raise ApiError(400, "BAD_STATE", "unknown target state")
        with write_tx() as c:
            t = one(c.execute("SELECT * FROM tickets WHERE id=?", (tid,)))
            if not t: raise ApiError(404, "NOT_FOUND", "ticket not found")
            u = g.user
            is_mgr = u["role"] in MANAGERS
            if to == "closed" and not is_mgr: raise ApiError(403, "FORBIDDEN", "only a facility manager or administrator may close a ticket")
            if not is_mgr and not (u["role"] == "technician" and t["assignee_id"] == u["id"]):
                raise ApiError(403, "FORBIDDEN", "only the assigned technician or a manager may update this ticket")
            if t["version"] != ev: raise ApiError(409, "STALE_VERSION", "ticket changed since you read it", current_version=t["version"], current_status=t["status"])
            if to not in TICKET_TRANSITIONS[t["status"]]:
                raise ApiError(409, "INVALID_TRANSITION", f"'{t['status']}' -> '{to}' is not allowed", allowed=sorted(TICKET_TRANSITIONS[t["status"]]))
            insp = (d.get("inspection_notes") or t["inspection_notes"] or "").strip()
            res = (d.get("resolution_notes") or t["resolution_notes"] or "").strip()
            if to in ("pending_work", "resolved") and not insp: raise ApiError(422, "NOTES_REQUIRED", "inspection notes are required", field="inspection_notes")
            if to == "resolved" and not res: raise ApiError(422, "NOTES_REQUIRED", "resolution notes are required", field="resolution_notes")
            resolved_at = now_iso() if to == "resolved" else t["resolved_at"]
            if to == "closed":
                link = one(c.execute("SELECT * FROM integration_links WHERE ticket_id=?", (tid,)))
                if link and link["status"] != "delivered":
                    raise ApiError(409, "DELIVERY_NOT_VERIFIED", "linked developer issue is not verified as delivered", link_status=link["status"])
            c.execute("UPDATE tickets SET status=?,inspection_notes=?,resolution_notes=?,resolved_at=?,closed_at=?,version=version+1 WHERE id=? AND version=?",
                      (to, insp or None, res or None, resolved_at, now_iso() if to == "closed" else None, tid, ev))
            audit(c, u["id"], "ticket", tid, "transition", t["status"], to, {"inspection_notes": bool(insp), "resolution_notes": bool(res)})
            return jsonify(ticket_view(c, one(c.execute("SELECT * FROM tickets WHERE id=?", (tid,)))))

    # ---------------- approvals & loans ----------------
    @app.get("/api/approvals")
    @auth("admin", "approver")
    def list_approvals():
        st = request.args.get("status", "pending")
        return jsonify(rows(db().execute(
            "SELECT a.*,u.name requester_name, CASE a.subject_type WHEN 'booking' THEN (SELECT f.name||' '||b.start_ts||' - '||b.end_ts FROM bookings b JOIN facilities f ON f.id=b.facility_id WHERE b.id=a.subject_id) "
            "ELSE (SELECT l.item||' x'||l.quantity FROM equipment_loans l WHERE l.id=a.subject_id) END summary "
            "FROM approvals a JOIN users u ON u.id=a.requested_by WHERE a.status=? ORDER BY a.id", (st,))))

    @app.post("/api/approvals/<int:aid>/decision")
    @auth("admin", "approver")
    def decide(aid):
        d = body(); need(d, "decision")
        if d["decision"] not in ("approve", "reject"): raise ApiError(400, "BAD_DECISION", "decision must be approve|reject")
        just = (d.get("justification") or "").strip()
        if len(just) < 10: raise ApiError(422, "JUSTIFICATION_REQUIRED", "a justification of at least 10 characters is required (auditable decision)")
        with write_tx() as c:
            a = one(c.execute("SELECT * FROM approvals WHERE id=?", (aid,)))
            if not a: raise ApiError(404, "NOT_FOUND", "approval not found")
            if a["requested_by"] == g.user["id"]: raise ApiError(403, "SELF_APPROVAL", "you may not decide your own request")
            if a["status"] != "pending": raise ApiError(409, "ALREADY_DECIDED", f"approval is already '{a['status']}'", current_status=a["status"])
            ok = d["decision"] == "approve"
            c.execute("UPDATE approvals SET status=?,decided_by=?,justification=?,decided_at=? WHERE id=?", ("approved" if ok else "rejected", g.user["id"], just, now_iso(), aid))
            tbl, new = ("bookings", "approved" if ok else "cancelled") if a["subject_type"] == "booking" else ("equipment_loans", "approved" if ok else "rejected")
            old = c.execute(f"SELECT status FROM {tbl} WHERE id=?", (a["subject_id"],)).fetchone()[0]
            if old != "requested": raise ApiError(409, "INVALID_TRANSITION", f"subject is '{old}', not 'requested'")
            c.execute(f"UPDATE {tbl} SET status=?,version=version+1 WHERE id=?", (new, a["subject_id"]))
            audit(c, g.user["id"], "approval", aid, d["decision"], "pending", "approved" if ok else "rejected", {"subject": a["subject_type"], "subject_id": a["subject_id"], "justification": just})
            audit(c, g.user["id"], a["subject_type"], a["subject_id"], "approval_" + d["decision"], old, new, {"approval_id": aid})
            return jsonify(one(c.execute("SELECT * FROM approvals WHERE id=?", (aid,))))

    @app.post("/api/loans")
    @auth("faculty", "admin", "facility_manager")
    def create_loan():
        d = body(); need(d, "item", "start", "end")
        s, e = parse_ts(d["start"], "start"), parse_ts(d["end"], "end")
        if e <= s: raise ApiError(400, "BAD_RANGE", "end must be after start")
        key = request.headers.get("Idempotency-Key")
        with write_tx() as c:
            if key:
                prev = one(c.execute("SELECT * FROM equipment_loans WHERE user_id=? AND idempotency_key=?", (g.user["id"], key)))
                if prev: return jsonify({"loan": prev, "replayed": True}), 200
            cur = c.execute("INSERT INTO equipment_loans(item,quantity,user_id,start_ts,end_ts,status,idempotency_key,created_at) VALUES(?,?,?,?,?,'requested',?,?)",
                            (d["item"], int(d.get("quantity", 1)), g.user["id"], s, e, key, now_iso()))
            c.execute("INSERT INTO approvals(subject_type,subject_id,requested_by,status,created_at) VALUES('loan',?,?,'pending',?)", (cur.lastrowid, g.user["id"], now_iso()))
            audit(c, g.user["id"], "loan", cur.lastrowid, "create", None, "requested", {"item": d["item"]})
            return jsonify({"loan": one(c.execute("SELECT * FROM equipment_loans WHERE id=?", (cur.lastrowid,))), "replayed": False}), 201

    @app.get("/api/loans")
    @auth()
    def list_loans():
        q, a = "SELECT * FROM equipment_loans", ()
        if g.user["role"] not in MANAGERS + ("approver",): q, a = q + " WHERE user_id=?", (g.user["id"],)
        return jsonify(rows(db().execute(q + " ORDER BY id DESC", a)))

    # ---------------- DevOps integration (simulated tracker, disclosed) ----------------
    def integration_response(link, **extra):
        return {"link": link, "integration_mode": INTEGRATION_MODE,
                "disclosure": "Integration mode is SIMULATED: a local stand-in tracker, not a live GitHub/Jira service.", **extra}

    @app.post("/api/tickets/<int:tid>/devops")
    @auth(*MANAGERS, "technician")
    def devops_create(tid):
        d = request.get_json(silent=True) or {}
        sim = d.get("simulate_failure")          # None | 'timeout' | 'lost_response' (test hook for the simulated tracker)
        idem = f"ticket-{tid}-issue"
        with write_tx() as c:
            t = one(c.execute("SELECT * FROM tickets WHERE id=?", (tid,)))
            if not t: raise ApiError(404, "NOT_FOUND", "ticket not found")
            if g.user["role"] == "technician" and t["assignee_id"] != g.user["id"]: raise ApiError(403, "FORBIDDEN", "not your ticket")
            link = one(c.execute("SELECT * FROM integration_links WHERE idempotency_key=?", (idem,)))
            if link and link["status"] in ("linked", "delivered"):
                return jsonify(integration_response(link, replayed=True)), 200
            if not link:
                c.execute("INSERT INTO integration_links(ticket_id,system,mode,status,idempotency_key,attempts,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?)",
                          (tid, "issue-tracker", INTEGRATION_MODE, "pending_retry", idem, now_iso(), now_iso()))
            c.execute("UPDATE integration_links SET attempts=attempts+1,updated_at=? WHERE idempotency_key=?", (now_iso(), idem))
            if sim == "timeout":
                c.execute("UPDATE integration_links SET last_error='timeout (tracker unreachable)' WHERE idempotency_key=?", (idem,))
                audit(c, g.user["id"], "integration", tid, "create_failed", None, "pending_retry", {"error": "timeout"})
                link = one(c.execute("SELECT * FROM integration_links WHERE idempotency_key=?", (idem,)))
                c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")   # keep the local record even though we answer with an error
                return jsonify(integration_response(link, retry="safe: repeat the same request", error={"code": "INTEGRATION_TIMEOUT", "message": "issue tracker did not respond; local record kept, retrying is safe"})), 504
            # call the (idempotent) tracker: INSERT OR IGNORE on idem_key means a retry never creates a second issue
            c.execute("INSERT OR IGNORE INTO mock_tracker_issues(idem_key,title) VALUES(?,?)", (idem, t["title"]))
            ext = 1000 + c.execute("SELECT id FROM mock_tracker_issues WHERE idem_key=?", (idem,)).fetchone()[0]
            if sim == "lost_response":   # tracker did create the issue but we never saw the reply
                c.execute("UPDATE integration_links SET last_error='response lost after tracker created issue' WHERE idempotency_key=?", (idem,))
                audit(c, g.user["id"], "integration", tid, "create_response_lost", None, "pending_retry", {})
                link = one(c.execute("SELECT * FROM integration_links WHERE idempotency_key=?", (idem,)))
                c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
                return jsonify(integration_response(link, retry="safe: repeat the same request", error={"code": "INTEGRATION_TIMEOUT", "message": "issue tracker did not respond; local record kept, retrying is safe"})), 504
            c.execute("UPDATE integration_links SET status='linked',external_id=?,last_error=NULL WHERE idempotency_key=?", (f"DEV-{ext}", idem))
            audit(c, g.user["id"], "integration", tid, "linked", "pending_retry", "linked", {"external_id": f"DEV-{ext}"})
            link = one(c.execute("SELECT * FROM integration_links WHERE idempotency_key=?", (idem,)))
            return jsonify(integration_response(link, replayed=False)), 201

    @app.get("/api/tickets/<int:tid>/devops")
    @auth()
    def devops_get(tid):
        link = one(db().execute("SELECT * FROM integration_links WHERE ticket_id=?", (tid,)))
        if not link: raise ApiError(404, "NO_LINK", "no developer issue linked to this ticket")
        return jsonify(integration_response(link))

    @app.post("/api/mock-tracker/<ext>/release")
    @auth("admin")
    def mock_release(ext):
        n = int(ext.replace("DEV-", "")) - 1000
        with write_tx() as c:
            if c.execute("UPDATE mock_tracker_issues SET state='released' WHERE id=?", (n,)).rowcount == 0: raise ApiError(404, "NOT_FOUND", "issue not found")
            audit(c, g.user["id"], "integration", n, "tracker_release", "open", "released", {"external_id": ext})
        return jsonify({"external_id": ext, "state": "released", "integration_mode": INTEGRATION_MODE})

    @app.post("/api/tickets/<int:tid>/devops/verify")
    @auth(*MANAGERS)
    def devops_verify(tid):
        with write_tx() as c:
            link = one(c.execute("SELECT * FROM integration_links WHERE ticket_id=?", (tid,)))
            if not link or link["status"] == "pending_retry": raise ApiError(409, "NOT_LINKED", "no confirmed link to verify")
            st = c.execute("SELECT state FROM mock_tracker_issues WHERE idem_key=?", (link["idempotency_key"],)).fetchone()[0]
            if st != "released": raise ApiError(409, "NOT_DELIVERED", "tracker issue is not released yet", tracker_state=st)
            c.execute("UPDATE integration_links SET status='delivered',updated_at=? WHERE id=?", (now_iso(), link["id"]))
            audit(c, g.user["id"], "integration", tid, "delivery_verified", link["status"], "delivered", {})
            return jsonify(integration_response(one(c.execute("SELECT * FROM integration_links WHERE id=?", (link["id"],)))))

    # ---------------- dashboard & audit ----------------
    @app.get("/api/dashboard")
    @auth()
    def dashboard():
        c = db(); pol = policies(c)
        tix = rows(c.execute("SELECT status,priority,sla_due,resolved_at FROM tickets"))
        sla = {"on_track": 0, "at_risk": 0, "breached": 0, "met": 0, "not_started": 0}
        for t in tix: sla[sla_state(t, pol)] += 1
        horizon = (now_dt() + timedelta(days=7)).strftime(TS_FMT)
        booked = c.execute("SELECT COALESCE(SUM((julianday(end_ts)-julianday(start_ts))*24),0) FROM bookings WHERE status IN ('approved','confirmed','in_use') AND start_ts>=? AND start_ts<?", (now_iso(), horizon)).fetchone()[0]
        n_fac = c.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]
        return jsonify({
            "bookings_by_status": {r["status"]: r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM bookings GROUP BY status")},
            "tickets_by_status": {r["status"]: r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM tickets GROUP BY status")},
            "open_critical": sum(1 for t in tix if t["priority"] == "critical" and t["status"] not in ("resolved", "closed")),
            "sla": sla,
            "pending_approvals": c.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0],
            "utilisation_next_7d_pct": round(100 * booked / (n_fac * 7 * 12), 1) if n_fac else 0,
            "note": "Illustrative figures computed from synthetic data (12 bookable hours/day assumed); not production measurements.",
        })

    @app.get("/api/audit")
    @auth(*MANAGERS)
    def audit_list():
        q, a = "SELECT * FROM audit_events WHERE 1=1", []
        for f in ("entity", "entity_id"):
            if request.args.get(f): q += f" AND {f}=?"; a.append(request.args[f])
        return jsonify(rows(db().execute(q + " ORDER BY id DESC LIMIT 200", a)))

    return app

app = create_app()
