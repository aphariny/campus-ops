// Smart Campus Operations - single-page UI. UI visibility is a convenience only: the API enforces every permission.
const $ = (s, r = document) => r.querySelector(s);
const state = { token: sessionStorage.getItem("token"), user: JSON.parse(sessionStorage.getItem("user") || "null"), tab: "dashboard", facilities: [] };
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pill = v => `<span class="pill ${esc(v)}">${esc(v).replace(/_/g, " ")}</span>`;
const fmt = t => t ? t.replace("T", " ").replace(":00Z", "") : "";
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());

async function api(path, { method = "GET", body, key } = {}) {
  const h = { "Content-Type": "application/json" };
  if (state.token) h.Authorization = "Bearer " + state.token;
  if (key) h["Idempotency-Key"] = key;
  const r = await fetch("/api" + path, { method, headers: h, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  state.instance = r.headers.get("X-Instance-Id");
  if (!r.ok) { const e = new Error(data.error?.message || r.statusText); e.code = data.error?.code; e.status = r.status; e.data = data; throw e; }
  return data;
}
function banner(msg, kind = "ok") { $("#banner").innerHTML = msg ? `<div class="msg ${kind}">${esc(msg)}</div>` : ""; }
async function act(fn, okMsg) { try { banner(""); await fn(); if (okMsg && !$("#banner .msg")) banner(okMsg, "ok"); } catch (e) { banner(`${e.code || e.status}: ${e.message}`, "err"); } await render(true); }

const TABS = [["dashboard", "Dashboard", null], ["facilities", "Facilities & Calendar", null], ["tickets", "Maintenance", null],
  ["approvals", "Approvals & Loans", null], ["sensors", "Sensors", ["admin", "facility_manager"]]];

function shell() {
  $("#nav").innerHTML = TABS.filter(t => !t[2] || t[2].includes(state.user?.role)).map(t => `<button class="${state.tab === t[0] ? "on" : ""}" data-tab="${t[0]}">${t[1]}</button>`).join("");
  $("#who").innerHTML = state.user ? `${esc(state.user.name)} (${esc(state.user.role)}) · API instance: <b>${esc(state.instance || "-")}</b> <button class="btn alt" id="out">Sign out</button>` : "";
  document.querySelectorAll("nav button").forEach(b => b.onclick = () => { state.tab = b.dataset.tab; banner(""); render(); });
  const o = $("#out"); if (o) o.onclick = () => { sessionStorage.clear(); state.token = null; state.user = null; render(); };
}

async function render(keepBanner) {
  if (!keepBanner) banner("");
  if (!state.token) { shell(); return loginView(); }
  try { state.facilities = await api("/facilities"); await views[state.tab](); } catch (e) { banner(e.message, "err"); }
  shell();
}

function loginView() {
  $("#view").innerHTML = `<div class="card login"><h2>Sign in</h2><div class="row"><label>Email<input id="em" value="student@campus.test"></label></div>
  <div class="row"><label>Password<input id="pw" type="password"></label></div><button class="btn" id="go">Sign in</button>
  <p class="small">Synthetic demo accounts: admin, manager, tech1, tech2, approver, faculty, faculty2, student, student2 @campus.test</p></div>`;
  $("#go").onclick = async () => { try { const r = await api("/login", { method: "POST", body: { email: $("#em").value, password: $("#pw").value } });
    state.token = r.token; state.user = r.user; sessionStorage.setItem("token", r.token); sessionStorage.setItem("user", JSON.stringify(r.user)); state.tab = "dashboard"; render(); } catch (e) { banner(e.message, "err"); } };
}

const views = {
  async dashboard() {
    const d = await api("/dashboard"); const tk = d.tickets_by_status, bk = d.bookings_by_status;
    const kpi = (n, l) => `<div class="card kpi"><b>${n}</b><span>${l}</span></div>`;
    $("#view").innerHTML = `<div class="grid">${kpi(Object.values(bk).reduce((a, b) => a + b, 0), "bookings")}${kpi(Object.values(tk).reduce((a, b) => a + b, 0), "tickets")}
      ${kpi(d.open_critical, "open critical tickets")}${kpi(d.pending_approvals, "pending approvals")}${kpi(d.utilisation_next_7d_pct + "%", "utilisation next 7 days")}</div>
      <div class="grid"><div class="card"><h3>SLA indicators</h3>${Object.entries(d.sla).map(([k, v]) => `<div>${pill(k)} ${v}</div>`).join("")}</div>
      <div class="card"><h3>Tickets by status</h3>${Object.entries(tk).map(([k, v]) => `<div>${pill(k)} ${v}</div>`).join("") || "<span class=small>none</span>"}</div>
      <div class="card"><h3>Bookings by status</h3>${Object.entries(bk).map(([k, v]) => `<div>${pill(k)} ${v}</div>`).join("") || "<span class=small>none</span>"}</div></div>
      <p class="small">${esc(d.note)}</p>`;
  },
  async facilities() {
    const today = new Date().toISOString().slice(0, 10), sel = state.selFac || state.facilities[0].id, day = state.day || today;
    const bks = await api(`/bookings?facility_id=${sel}&date=${day}`);
    $("#view").innerHTML = `<div class="card"><h3>Facility catalogue</h3><table><tr><th>Code</th><th>Name</th><th>Campus</th><th>Type</th><th>Capacity</th><th>Access</th></tr>
      ${state.facilities.map(f => `<tr><td>${esc(f.code)}</td><td>${esc(f.name)}</td><td>${esc(f.campus)}</td><td>${esc(f.kind)}</td><td>${f.capacity}</td><td>${f.restricted ? "restricted (approval)" : "open"}</td></tr>`).join("")}</table></div>
      <div class="card"><h3>Booking calendar &amp; new booking</h3><div class="row">
      <label>Facility<select id="fac">${state.facilities.map(f => `<option value="${f.id}" ${f.id == sel ? "selected" : ""}>${esc(f.name)}${f.restricted ? " 🔒" : ""}</option>`).join("")}</select></label>
      <label>Date<input type="date" id="day" value="${day}"></label>
      <label>From<input type="time" id="t1" value="09:00"></label><label>To<input type="time" id="t2" value="10:00"></label>
      <label>Purpose<input id="pur" value="Class session"></label><button class="btn" id="book">Submit booking</button></div>
      <table><tr><th>Slot (UTC)</th><th>Booked by</th><th>Status</th><th>Actions</th></tr>${bks.map(b => `<tr><td>${fmt(b.start_ts)} → ${fmt(b.end_ts).slice(11)}</td><td>${esc(b.user_name)}</td><td>${pill(b.status)}</td>
      <td>${["confirm", "check_in", "complete", "cancel"].map(a => `<button class="btn alt" data-b="${b.id}" data-v="${b.version}" data-a="${a}">${a.replace("_", " ")}</button>`).join(" ")}</td></tr>`).join("") || "<tr><td colspan=4 class=small>no bookings that day</td></tr>"}</table></div>`;
    $("#fac").onchange = e => { state.selFac = +e.target.value; render(); }; $("#day").onchange = e => { state.day = e.target.value; render(); };
    $("#book").onclick = () => act(async () => { const r = await api("/bookings", { method: "POST", key: uuid(), body: { facility_id: +$("#fac").value, start: `${$("#day").value}T${$("#t1").value}`, end: `${$("#day").value}T${$("#t2").value}`, purpose: $("#pur").value } });
      if (r.booking.status === "requested") banner("Restricted facility: booking is awaiting approval", "ok"); }, "Booking submitted");
    document.querySelectorAll("[data-b]").forEach(b => b.onclick = () => act(() => api(`/bookings/${b.dataset.b}/transition`, { method: "POST", body: { action: b.dataset.a, expected_version: +b.dataset.v } }), "Booking updated"));
  },
  async tickets() {
    const role = state.user.role, mgr = ["admin", "facility_manager"].includes(role), list = await api("/tickets");
    const techs = mgr ? await api("/users?role=technician") : [];
    let detail = "";
    if (state.openTicket) { const d = await api("/tickets/" + state.openTicket); detail = ticketDetail(d, mgr, techs); }
    $("#view").innerHTML = `<div class="card"><h3>Maintenance queue</h3><div class="row"><label>Report a problem – facility<select id="nf">${state.facilities.map(f => `<option value="${f.id}">${esc(f.name)}</option>`).join("")}</select></label>
      <label>Title<input id="nt" placeholder="e.g. Projector not working"></label><button class="btn" id="nb">Create ticket</button></div>
      <table><tr><th>#</th><th>Facility</th><th>Title</th><th>Priority</th><th>Status</th><th>SLA</th><th>Assignee</th><th></th></tr>
      ${list.map(t => `<tr><td>${t.id}</td><td>${esc(t.facility_name)}</td><td>${esc(t.title)}</td><td>${t.priority ? pill(t.priority) : '<span class=small>suggest: ' + esc(t.suggested_priority) + "</span>"}</td><td>${pill(t.status)}</td><td>${pill(t.sla_state)}</td><td>${esc(t.assignee_name || "")}</td><td><button class="btn alt" data-t="${t.id}">open</button></td></tr>`).join("") || "<tr><td colspan=8 class=small>no tickets</td></tr>"}</table></div>${detail}`;
    $("#nb").onclick = () => act(() => api("/tickets", { method: "POST", key: uuid(), body: { facility_id: +$("#nf").value, title: $("#nt").value } }), "Ticket created");
    document.querySelectorAll("[data-t]").forEach(b => b.onclick = () => { state.openTicket = +b.dataset.t; render(); });
    wireTicket();
  },
  async approvals() {
    const role = state.user.role, canDecide = ["admin", "approver"].includes(role);
    const ap = canDecide ? await api("/approvals") : [], loans = await api("/loans");
    $("#view").innerHTML = `${canDecide ? `<div class="card"><h3>Pending approvals</h3><table><tr><th>#</th><th>Type</th><th>Requested by</th><th>Subject</th><th>Decision (justification required)</th></tr>
      ${ap.map(a => `<tr><td>${a.id}</td><td>${esc(a.subject_type)}</td><td>${esc(a.requester_name)}</td><td>${esc(a.summary)}</td><td><div class="row"><input id="j${a.id}" placeholder="Justification (min 10 chars)" size="34">
      <button class="btn" data-ap="${a.id}" data-d="approve">Approve</button><button class="btn bad" data-ap="${a.id}" data-d="reject">Reject</button></div></td></tr>`).join("") || "<tr><td colspan=5 class=small>nothing pending</td></tr>"}</table></div>` : ""}
      <div class="card"><h3>Equipment loans</h3><div class="row"><label>Item<input id="li" value="Portable projector"></label><label>Date<input id="ld" type="date" value="${new Date(Date.now() + 864e5).toISOString().slice(0, 10)}"></label>
      <button class="btn" id="lb">Request loan</button></div><p class="small">Faculty, managers and administrators may request loans; every loan needs an approver.</p>
      <table><tr><th>#</th><th>Item</th><th>Window</th><th>Status</th></tr>${loans.map(l => `<tr><td>${l.id}</td><td>${esc(l.item)}</td><td>${fmt(l.start_ts)} → ${fmt(l.end_ts).slice(11)}</td><td>${pill(l.status)}</td></tr>`).join("") || "<tr><td colspan=4 class=small>none</td></tr>"}</table></div>`;
    $("#lb").onclick = () => act(() => api("/loans", { method: "POST", key: uuid(), body: { item: $("#li").value, start: $("#ld").value + "T09:00", end: $("#ld").value + "T17:00" } }), "Loan requested");
    document.querySelectorAll("[data-ap]").forEach(b => b.onclick = () => act(() => api(`/approvals/${b.dataset.ap}/decision`, { method: "POST", body: { decision: b.dataset.d, justification: $("#j" + b.dataset.ap).value } }), "Decision recorded"));
  },
  async sensors() {
    $("#view").innerHTML = `<div class="card"><h3>Controlled sensor-event simulator</h3><p class="small">Synthetic feed: a reading above its planning threshold creates exactly one maintenance ticket, even if the event is delivered repeatedly.</p>
      <div class="row"><label>Event id<input id="se" value="EVT-${Date.now() % 100000}"></label><label>Facility<select id="sf">${state.facilities.map(f => `<option value="${f.id}">${esc(f.name)}</option>`).join("")}</select></label>
      <label>Sensor<select id="ss"><option value="temperature_c">temperature_c (&gt;30)</option><option value="power_kw">power_kw (&gt;50)</option><option value="humidity_pct">humidity_pct (&gt;75)</option><option value="water_leak">water_leak (≥1)</option></select></label>
      <label>Value<input id="sv" type="number" value="34.2" step="0.1"></label><button class="btn" id="sb">Send event</button><button class="btn alt" id="sb2">Send same event again</button></div><div id="sres" class="small"></div></div>`;
    const send = () => act(async () => { const r = await api("/sensor-events", { method: "POST", body: { event_id: $("#se").value, facility_id: +$("#sf").value, sensor_type: $("#ss").value, value: +$("#sv").value, ts: new Date().toISOString().slice(0, 19) } });
      banner(r.duplicate ? `Duplicate event ignored – existing ticket #${r.ticket?.id ?? "none"} reused` : r.ticket ? `Threshold breached → ticket #${r.ticket.id} created` : "Reading within limits – no ticket", "ok"); });
    $("#sb").onclick = send; $("#sb2").onclick = send;
  },
};

function ticketDetail(d, mgr, techs) {
  const t = d.ticket, role = state.user.role, v = t.version, L = d.integration;
  const next = { assigned: ["in_inspection"], in_inspection: ["pending_work", "resolved"], pending_work: ["in_inspection", "resolved"], resolved: mgr ? ["closed", "in_inspection"] : [] }[t.status] || [];
  return `<div class="card" id="td" data-id="${t.id}" data-v="${v}" data-ext="${esc(L?.external_id || "")}"><h3>Ticket #${t.id}: ${esc(t.title)}</h3>
    <div class="flow">${["new", "assigned", "in_inspection", "pending_work", "resolved", "closed"].map(s => `<span class="pill ${s === t.status ? "confirmed" : ""}">${s.replace("_", " ")}</span>`).join(" → ")}</div>
    <p>Status ${pill(t.status)} · SLA ${pill(t.sla_state)} · due ${fmt(t.sla_due) || "–"} · version ${v}</p>
    <p><b>Triage suggestion</b> (deterministic keyword rules, not machine learning): ${esc(t.suggested_category)} / ${esc(t.suggested_priority)}<br><span class="small">${esc(t.triage_explanation)}</span></p>
    ${mgr && ["new", "assigned"].includes(t.status) ? `<div class="row"><label>Technician<select id="as">${techs.map(u => `<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select></label>
      <label>Priority (manager decides)<select id="ap">${["critical", "high", "medium", "low"].map(p => `<option ${p === t.suggested_priority ? "selected" : ""}>${p}</option>`).join("")}</select></label>
      <label>Category<input id="ac" value="${esc(t.category || t.suggested_category)}"></label><button class="btn" id="asg">Confirm &amp; assign</button></div>` : ""}
    ${next.length ? `<div class="row"><label>Inspection notes<textarea id="in" rows="2">${esc(t.inspection_notes || "")}</textarea></label><label>Resolution notes<textarea id="rn" rows="2">${esc(t.resolution_notes || "")}</textarea></label>
      ${next.map(n => `<button class="btn alt" data-to="${n}">→ ${n.replace("_", " ")}</button>`).join("")}</div>` : ""}
    <h4>Linked developer issue</h4>${L ? `<p>${esc(L.external_id || "(pending)")} ${pill(L.status)} · attempts ${L.attempts} · <b>integration mode: ${esc(L.mode)}</b> (local stand-in tracker, not live)</p>` : '<p class="small">none</p>'}
    ${(mgr || role === "technician") ? `<div class="row"><button class="btn alt" id="dv">Create / retry linked issue</button><button class="btn alt" id="dvf">…simulate timeout</button>${role === "admin" ? `<button class="btn alt" id="dvr">Mark released (admin)</button>` : ""}${mgr ? `<button class="btn alt" id="dvv">Verify delivery</button>` : ""}</div>` : ""}
    <h4>Audit history</h4><table><tr><th>Time</th><th>Actor</th><th>Action</th><th>From → To</th></tr>${d.audit.map(a => `<tr><td>${fmt(a.ts)}</td><td>${a.actor_id ?? ""}</td><td>${esc(a.action)}</td><td>${esc(a.from_state || "")} → ${esc(a.to_state || "")}</td></tr>`).join("")}</table></div>`;
}
function wireTicket() {
  const td = $("#td"); if (!td) return; const id = td.dataset.id, v = +td.dataset.v;
  const asg = $("#asg"); if (asg) asg.onclick = () => act(() => api(`/tickets/${id}/assign`, { method: "POST", body: { assignee_id: +$("#as").value, priority: $("#ap").value, category: $("#ac").value, expected_version: v } }), "Ticket assigned");
  td.querySelectorAll("[data-to]").forEach(b => b.onclick = () => act(() => api(`/tickets/${id}/transition`, { method: "POST", body: { to: b.dataset.to, expected_version: v, inspection_notes: $("#in").value, resolution_notes: $("#rn").value } }), "Ticket updated"));
  const call = (path, body, m) => () => act(() => api(`/tickets/${id}${path}`, { method: "POST", body }), m);
  if ($("#dv")) $("#dv").onclick = call("/devops", {}, "Linked issue created (or already existed)");
  if ($("#dvf")) $("#dvf").onclick = call("/devops", { simulate_failure: "timeout" }, "Timeout simulated");
  if ($("#dvr")) $("#dvr").onclick = () => act(() => api(`/mock-tracker/${td.dataset.ext}/release`, { method: "POST" }), "Tracker issue marked released");
  if ($("#dvv")) $("#dvv").onclick = call("/devops/verify", {}, "Delivery verified");
}
render();
