import os, sys, tempfile, unittest, sqlite3
os.environ.setdefault("SEED_PASSWORD", "TestOnly-Pass1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as appmod, seed
PW = os.environ["SEED_PASSWORD"]

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        appmod.DB_PATH = seed.DB_PATH = os.path.join(self.tmp.name, "t.db")
        seed.init()
        self.client = appmod.create_app().test_client()
        self.tok = {}
    def tearDown(self): self.tmp.cleanup()
    def h(self, who, **extra):
        if who not in self.tok:
            r = self.client.post("/api/login", json={"email": f"{who}@campus.test", "password": PW})
            self.assertEqual(r.status_code, 200); self.tok[who] = r.get_json()["token"]
        return {"Authorization": "Bearer " + self.tok[who], **extra}
    def post(self, who, url, json=None, **hdr): return self.client.post(url, json=json or {}, headers=self.h(who, **hdr))
    def get(self, who, url): return self.client.get(url, headers=self.h(who))
    def uid(self, email):
        return sqlite3.connect(appmod.DB_PATH).execute("SELECT id FROM users WHERE email=?", (email + "@campus.test",)).fetchone()[0]
    def booking(self, who, fid, s, e, key=None, status=None):
        return self.post(who, "/api/bookings", {"facility_id": fid, "start": s, "end": e, "purpose": "t"}, **({"Idempotency-Key": key} if key else {}))

class TestAuthz(Base):
    def test_unauthenticated(self): self.assertEqual(self.client.get("/api/tickets").status_code, 401)
    def test_bad_login(self): self.assertEqual(self.client.post("/api/login", json={"email": "x@y.z", "password": "no"}).status_code, 401)
    def test_student_cannot_book_restricted(self):
        r = self.booking("student", 1, "2030-01-01T09:00", "2030-01-01T10:00")
        self.assertEqual(r.status_code, 403)
        n = sqlite3.connect(appmod.DB_PATH).execute("SELECT COUNT(*) FROM audit_events WHERE action LIKE 'denied:%'").fetchone()[0]
        self.assertGreaterEqual(n, 1)                    # denial itself is audited
    def test_student_cannot_assign_or_ingest(self):
        self.assertEqual(self.post("student", "/api/tickets/1/assign", {}).status_code, 403)
        self.assertEqual(self.post("student", "/api/sensor-events", {}).status_code, 403)

class TestBooking(Base):
    def test_overlap_rejected_adjacent_allowed(self):
        self.assertEqual(self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00").status_code, 201)
        r = self.booking("student2", 3, "2030-01-01T09:30", "2030-01-01T10:30")
        self.assertEqual(r.status_code, 409); self.assertEqual(r.get_json()["error"]["code"], "OVERLAP")
        self.assertEqual(self.booking("student2", 3, "2030-01-01T10:00", "2030-01-01T11:00").status_code, 201)   # touching ends are fine
    def test_db_trigger_backstop(self):            # bypass the API entirely: the database itself refuses an overlap
        self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00")
        c = sqlite3.connect(appmod.DB_PATH)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO bookings(facility_id,user_id,start_ts,end_ts,status,created_at) VALUES(3,2,'2030-01-01T09:15:00Z','2030-01-01T09:45:00Z','approved','x')")
    def test_cancelled_slot_is_reusable(self):
        b = self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00").get_json()["booking"]
        self.assertEqual(self.post("student", f"/api/bookings/{b['id']}/transition", {"action": "cancel", "expected_version": 1}).status_code, 200)
        self.assertEqual(self.booking("student2", 3, "2030-01-01T09:00", "2030-01-01T10:00").status_code, 201)
    def test_idempotent_retry(self):
        a = self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00", key="k1")
        b = self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00", key="k1")
        self.assertEqual((a.status_code, b.status_code), (201, 200)); self.assertTrue(b.get_json()["replayed"])
        self.assertEqual(a.get_json()["booking"]["id"], b.get_json()["booking"]["id"])
        c = self.booking("student", 3, "2030-01-01T11:00", "2030-01-01T12:00", key="k1")
        self.assertEqual(c.status_code, 422)
        self.assertEqual(sqlite3.connect(appmod.DB_PATH).execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 1)
    def test_lifecycle_and_invalid_transitions(self):
        b = self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00").get_json()["booking"]; i, v = b["id"], b["version"]
        self.assertEqual(self.post("student", f"/api/bookings/{i}/transition", {"action": "complete", "expected_version": v}).status_code, 409)  # approved -> completed invalid
        for act in ("confirm", "check_in", "complete"):
            r = self.post("student", f"/api/bookings/{i}/transition", {"action": act, "expected_version": v}); self.assertEqual(r.status_code, 200); v = r.get_json()["version"]
        self.assertEqual(r.get_json()["status"], "completed")
        self.assertEqual(self.post("student", f"/api/bookings/{i}/transition", {"action": "cancel", "expected_version": v}).status_code, 409)
    def test_other_student_cannot_touch_booking(self):
        b = self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00").get_json()["booking"]
        self.assertEqual(self.post("student2", f"/api/bookings/{b['id']}/transition", {"action": "cancel", "expected_version": 1}).status_code, 403)
    def test_stale_booking_version(self):
        b = self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00").get_json()["booking"]
        self.post("student", f"/api/bookings/{b['id']}/transition", {"action": "confirm", "expected_version": 1})
        r = self.post("student", f"/api/bookings/{b['id']}/transition", {"action": "cancel", "expected_version": 1})
        self.assertEqual(r.get_json()["error"]["code"], "STALE_VERSION")

class TestApprovals(Base):
    def test_restricted_booking_approval_flow(self):
        r = self.booking("faculty", 1, "2030-02-01T09:00", "2030-02-01T11:00"); self.assertEqual(r.get_json()["booking"]["status"], "requested")
        bid = r.get_json()["booking"]["id"]
        # the pending request already holds the slot
        self.assertEqual(self.booking("faculty2", 1, "2030-02-01T10:00", "2030-02-01T12:00").status_code, 409)
        a = self.get("approver", "/api/approvals").get_json(); self.assertEqual(len(a), 1)
        self.assertEqual(self.post("approver", f"/api/approvals/{a[0]['id']}/decision", {"decision": "approve", "justification": "short"}).status_code, 422)
        self.assertEqual(self.post("faculty", f"/api/approvals/{a[0]['id']}/decision", {"decision": "approve", "justification": "approved by me!!"}).status_code, 403)
        ok = self.post("approver", f"/api/approvals/{a[0]['id']}/decision", {"decision": "approve", "justification": "Lab safety briefing completed"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.post("approver", f"/api/approvals/{a[0]['id']}/decision", {"decision": "reject", "justification": "changed my mind later"}).status_code, 409)
        st = sqlite3.connect(appmod.DB_PATH).execute("SELECT status FROM bookings WHERE id=?", (bid,)).fetchone()[0]; self.assertEqual(st, "approved")
    def test_reject_frees_slot(self):
        self.booking("faculty", 1, "2030-02-01T09:00", "2030-02-01T11:00")
        a = self.get("approver", "/api/approvals").get_json()[0]
        self.post("approver", f"/api/approvals/{a['id']}/decision", {"decision": "reject", "justification": "Lab under maintenance"})
        self.assertEqual(self.booking("faculty2", 1, "2030-02-01T09:00", "2030-02-01T11:00").status_code, 201)
    def test_loan_requires_approval(self):
        r = self.post("faculty", "/api/loans", {"item": "Projector", "start": "2030-03-01T09:00", "end": "2030-03-01T12:00"}, **{"Idempotency-Key": "L1"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.post("faculty", "/api/loans", {"item": "Projector", "start": "2030-03-01T09:00", "end": "2030-03-01T12:00"}, **{"Idempotency-Key": "L1"}).status_code, 200)
        self.assertEqual(self.post("student", "/api/loans", {"item": "x", "start": "2030-03-01T09:00", "end": "2030-03-01T12:00"}).status_code, 403)
        a = self.get("approver", "/api/approvals").get_json()[0]
        self.assertEqual(self.post("approver", f"/api/approvals/{a['id']}/decision", {"decision": "approve", "justification": "Teaching need confirmed"}).status_code, 200)

class TestTickets(Base):
    def sensor(self, eid, val=34.2, fid=5, who="manager"):
        return self.post(who, "/api/sensor-events", {"event_id": eid, "facility_id": fid, "sensor_type": "temperature_c", "value": val, "ts": "2030-01-01T09:00:00Z"})
    def test_sensor_creates_one_ticket_even_if_duplicated(self):
        a, b, c = self.sensor("E1"), self.sensor("E1"), self.sensor("E1")
        self.assertEqual((a.status_code, b.status_code, c.status_code), (201, 200, 200)); self.assertTrue(b.get_json()["duplicate"])
        d = sqlite3.connect(appmod.DB_PATH)
        self.assertEqual(d.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 1); self.assertEqual(d.execute("SELECT COUNT(*) FROM sensor_alerts").fetchone()[0], 1)
    def test_normal_reading_no_ticket(self):
        r = self.sensor("E2", val=22.0); self.assertIsNone(r.get_json()["ticket"])
    def test_triage_is_explainable(self):
        self.sensor("E3"); t = self.get("manager", "/api/tickets/1").get_json()["ticket"]
        self.assertEqual(t["suggested_category"], "hvac"); self.assertEqual(t["suggested_priority"], "high"); self.assertIn("keywords", t["triage_explanation"])
    def test_full_journey_sensor_to_closed_with_devops(self):
        self.sensor("E4"); tid, v = 1, 1; tech = self.uid("tech1")
        a = self.post("manager", f"/api/tickets/{tid}/assign", {"assignee_id": tech, "priority": "high", "category": "hvac", "expected_version": v}); self.assertEqual(a.status_code, 200)
        v = a.get_json()["version"]; self.assertEqual(a.get_json()["sla_state"], "on_track")
        self.assertEqual(self.post("tech2", f"/api/tickets/{tid}/transition", {"to": "in_inspection", "expected_version": v}).status_code, 403)   # not the assignee
        r = self.post("tech1", f"/api/tickets/{tid}/transition", {"to": "in_inspection", "expected_version": v}); v = r.get_json()["version"]
        self.assertEqual(self.post("tech1", f"/api/tickets/{tid}/transition", {"to": "resolved", "expected_version": v}).status_code, 422)         # notes required
        d = self.post("tech1", f"/api/tickets/{tid}/devops"); self.assertEqual(d.status_code, 201); self.assertEqual(d.get_json()["integration_mode"], "simulated")
        r = self.post("tech1", f"/api/tickets/{tid}/transition", {"to": "resolved", "expected_version": v, "inspection_notes": "Chiller fan seized", "resolution_notes": "Fan replaced"}); v = r.get_json()["version"]
        self.assertEqual(r.get_json()["status"], "resolved")
        self.assertEqual(self.post("tech1", f"/api/tickets/{tid}/transition", {"to": "closed", "expected_version": v}).status_code, 403)          # technician cannot close
        c = self.post("manager", f"/api/tickets/{tid}/transition", {"to": "closed", "expected_version": v})
        self.assertEqual((c.status_code, c.get_json()["error"]["code"]), (409, "DELIVERY_NOT_VERIFIED"))
        ext = d.get_json()["link"]["external_id"]
        self.assertEqual(self.post("manager", f"/api/tickets/{tid}/devops/verify").status_code, 409)                                              # not released yet
        self.assertEqual(self.post("admin", f"/api/mock-tracker/{ext}/release").status_code, 200)
        self.assertEqual(self.post("manager", f"/api/tickets/{tid}/devops/verify").status_code, 200)
        c = self.post("manager", f"/api/tickets/{tid}/transition", {"to": "closed", "expected_version": v}); self.assertEqual(c.status_code, 200); self.assertEqual(c.get_json()["status"], "closed")
        hist = self.get("manager", f"/api/tickets/{tid}").get_json()["audit"]
        self.assertEqual([h["action"] for h in hist], ["create", "assign", "transition", "transition", "transition"])
    def test_invalid_transition_and_stale_version(self):
        self.sensor("E5"); tech = self.uid("tech1")
        a = self.post("manager", "/api/tickets/1/assign", {"assignee_id": tech, "priority": "high", "category": "hvac", "expected_version": 1}).get_json(); v = a["version"]
        r1 = self.post("tech1", "/api/tickets/1/transition", {"to": "in_inspection", "expected_version": v}); self.assertEqual(r1.status_code, 200)
        r2 = self.post("tech1", "/api/tickets/1/transition", {"to": "pending_work", "expected_version": v, "inspection_notes": "x"})   # second writer used the stale version
        self.assertEqual((r2.status_code, r2.get_json()["error"]["code"]), (409, "STALE_VERSION"))
        r3 = self.post("tech1", "/api/tickets/1/transition", {"to": "closed", "expected_version": r1.get_json()["version"]}); self.assertEqual(r3.status_code, 403)
        r4 = self.post("manager", "/api/tickets/1/transition", {"to": "closed", "expected_version": r1.get_json()["version"]}); self.assertEqual(r4.get_json()["error"]["code"], "INVALID_TRANSITION")
    def test_assign_validates_assignee_and_priority(self):
        self.sensor("E6")
        self.assertEqual(self.post("manager", "/api/tickets/1/assign", {"assignee_id": self.uid("student"), "priority": "high", "category": "hvac", "expected_version": 1}).status_code, 400)
        self.assertEqual(self.post("manager", "/api/tickets/1/assign", {"assignee_id": self.uid("tech1"), "priority": "urgent!", "category": "hvac", "expected_version": 1}).status_code, 400)
    def test_sla_breach_indicator(self):
        self.sensor("E7"); self.post("manager", "/api/tickets/1/assign", {"assignee_id": self.uid("tech1"), "priority": "critical", "category": "hvac", "expected_version": 1})
        c = sqlite3.connect(appmod.DB_PATH); c.execute("UPDATE tickets SET sla_due='2000-01-01T00:00:00Z' WHERE id=1"); c.commit()
        self.assertEqual(self.get("manager", "/api/tickets").get_json()[0]["sla_state"], "breached")
        self.assertEqual(self.get("manager", "/api/dashboard").get_json()["sla"]["breached"], 1)
    def test_technician_sees_only_own_tickets(self):
        self.sensor("E8"); self.post("manager", "/api/tickets/1/assign", {"assignee_id": self.uid("tech1"), "priority": "high", "category": "hvac", "expected_version": 1})
        self.assertEqual(len(self.get("tech1", "/api/tickets").get_json()), 1); self.assertEqual(len(self.get("tech2", "/api/tickets").get_json()), 0)
        self.assertEqual(self.get("tech2", "/api/tickets/1").status_code, 403)

class TestIntegrationRetry(Base):
    def setUp(self):
        super().setUp(); self.post("manager", "/api/sensor-events", {"event_id": "X", "facility_id": 5, "sensor_type": "power_kw", "value": 80, "ts": "2030-01-01T09:00"})
    def issues(self): return sqlite3.connect(appmod.DB_PATH).execute("SELECT COUNT(*) FROM mock_tracker_issues").fetchone()[0]
    def test_timeout_keeps_local_record_and_retry_is_safe(self):
        r = self.post("manager", "/api/tickets/1/devops", {"simulate_failure": "timeout"}); self.assertEqual(r.status_code, 504)
        self.assertEqual(r.get_json()["link"]["status"], "pending_retry"); self.assertEqual(self.issues(), 0)
        ok = self.post("manager", "/api/tickets/1/devops"); self.assertEqual(ok.status_code, 201); self.assertEqual(ok.get_json()["link"]["attempts"], 2)
        again = self.post("manager", "/api/tickets/1/devops"); self.assertEqual(again.status_code, 200); self.assertEqual(self.issues(), 1)
    def test_lost_response_does_not_duplicate_remote_issue(self):
        self.assertEqual(self.post("manager", "/api/tickets/1/devops", {"simulate_failure": "lost_response"}).status_code, 504); self.assertEqual(self.issues(), 1)
        for _ in range(3): self.post("manager", "/api/tickets/1/devops")
        self.assertEqual(self.issues(), 1)
        n = sqlite3.connect(appmod.DB_PATH).execute("SELECT COUNT(*) FROM integration_links").fetchone()[0]; self.assertEqual(n, 1)

class TestAuditAndPersistence(Base):
    def test_audit_is_append_only(self):
        self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00")
        c = sqlite3.connect(appmod.DB_PATH)
        with self.assertRaises(sqlite3.IntegrityError): c.execute("UPDATE audit_events SET action='tampered'")
        with self.assertRaises(sqlite3.IntegrityError): c.execute("DELETE FROM audit_events")
    def test_state_survives_new_app_instance(self):
        self.booking("student", 3, "2030-01-01T09:00", "2030-01-01T10:00")
        client2 = appmod.create_app().test_client()           # simulates a restart: fresh process state, same database
        tok = self.tok["student"]
        r = client2.get("/api/bookings?mine=1", headers={"Authorization": "Bearer " + tok})     # session also survives (stored in DB)
        self.assertEqual(r.status_code, 200); self.assertEqual(len(r.get_json()), 1)
    def test_health_and_instance_header(self):
        r = self.client.get("/api/health"); self.assertEqual(r.status_code, 200); self.assertIn("X-Instance-Id", r.headers)

if __name__ == "__main__": unittest.main(verbosity=2)
