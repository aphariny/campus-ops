"""Drives the real UI in headless Chromium through the end-to-end journey and saves screenshots (Playwright)."""
import os, sys
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
from playwright.sync_api import sync_playwright
URL, PW, OUT = "http://127.0.0.1:5200", os.environ["SEED_PASSWORD"], os.path.join(os.path.dirname(__file__), "..", "docs", "shots")
def shot(pg, name): pg.wait_for_timeout(350); pg.screenshot(path=f"{OUT}/{name}.png", full_page=True); print("shot", name)
def login(pg, who):
    pg.goto(URL); pg.evaluate("sessionStorage.clear()"); pg.goto(URL); pg.fill("#em", f"{who}@campus.test"); pg.fill("#pw", PW); pg.click("#go"); pg.wait_for_selector("#out")
def tab(pg, label): pg.click(f"nav button:has-text('{label}')"); pg.wait_for_timeout(400)
def banner(pg):
    pg.wait_for_selector(".msg"); pg.wait_for_timeout(700); return pg.inner_text(".msg")
def opent(pg): pg.click("[data-t]"); pg.wait_for_selector("#td"); pg.wait_for_timeout(300)
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1180, "height": 800})
    pg.goto(URL); shot(pg, "01_login")
    # 1. sensor event -> ticket (manager), duplicate delivery ignored
    login(pg, "manager"); tab(pg, "Sensors"); pg.select_option("#sf", label="Computer Lab 1"); pg.fill("#sv", "34.2"); pg.click("#sb"); print("1:", banner(pg)); shot(pg, "02_sensor_ticket_created")
    tab(pg, "Sensors"); pg.fill("#se", pg.input_value("#se")); 
    # resend identical event: reuse same event id (field re-renders with a new id, so set it back)
    import re; eid = re.search(r"EVT-\d+", pg.content()).group(0)
    # the first event id was overwritten by re-render; query it from the API through the page
    ev = pg.evaluate("""async () => (await (await fetch('/api/audit?entity=sensor_alert', {headers:{Authorization:'Bearer '+sessionStorage.getItem('token')}})).json())[0].detail""")
    first = re.search(r"EVT-\d+", ev).group(0); pg.fill("#se", first); pg.select_option("#sf", label="Computer Lab 1"); pg.fill("#sv", "34.2"); pg.click("#sb2"); print("1b:", banner(pg)); shot(pg, "03_duplicate_event_ignored")
    # 2. manager assigns (confirms priority)
    tab(pg, "Maintenance"); opent(pg); pg.wait_for_selector("#asg"); pg.select_option("#as", label="Tara Tech"); shot(pg, "04_ticket_triage_assign"); pg.click("#asg"); print("2:", banner(pg))
    # 3. technician inspects, links developer issue, resolves
    login(pg, "tech1"); tab(pg, "Maintenance"); opent(pg); pg.click("[data-to='in_inspection']"); print("3a:", banner(pg))
    pg.click("#dvf"); print("3b:", banner(pg)); shot(pg, "05_integration_timeout_pending_retry")
    pg.click("#dv"); print("3c:", banner(pg))
    pg.fill("#in", "Chiller fan seized; coolant normal"); pg.click("[data-to='resolved']"); print("3d (no resolution notes):", banner(pg)); shot(pg, "06_rejected_missing_resolution_notes")
    pg.fill("#in", "Chiller fan seized; coolant normal"); pg.fill("#rn", "Fan replaced; temperature back to 22C"); pg.click("[data-to='resolved']"); print("3e:", banner(pg))
    # 4. closure is blocked until delivery verified
    login(pg, "manager"); tab(pg, "Maintenance"); opent(pg); pg.click("[data-to='closed']"); print("4a:", banner(pg)); shot(pg, "07_close_blocked_until_delivery_verified")
    login(pg, "admin"); tab(pg, "Maintenance"); opent(pg); pg.click("#dvr"); print("4b:", banner(pg))
    login(pg, "manager"); tab(pg, "Maintenance"); opent(pg); pg.click("#dvv"); print("4c:", banner(pg)); pg.click("[data-to='closed']"); print("4d:", banner(pg))
    shot(pg, "08_ticket_closed_audit_history")
    # 5. restricted booking + approval; overlap rejection; student denied
    login(pg, "faculty"); tab(pg, "Facilities"); pg.select_option("#fac", label="Chemistry Lab 1 🔒"); pg.fill("#t1", "10:00"); pg.fill("#t2", "12:00"); pg.click("#book"); print("5a:", banner(pg))
    login(pg, "faculty2"); tab(pg, "Facilities"); pg.select_option("#fac", label="Chemistry Lab 1 🔒"); pg.fill("#t1", "11:00"); pg.fill("#t2", "13:00"); pg.click("#book"); print("5b:", banner(pg)); shot(pg, "09_overlap_rejected")
    login(pg, "student"); tab(pg, "Facilities"); pg.select_option("#fac", label="Chemistry Lab 1 🔒"); pg.fill("#t1", "15:00"); pg.fill("#t2", "16:00"); pg.click("#book"); print("5c:", banner(pg)); shot(pg, "10_student_denied_restricted")
    pg.select_option("#fac", label="Seminar Room 1"); pg.fill("#t1", "09:00"); pg.fill("#t2", "10:00"); pg.click("#book"); print("5d:", banner(pg)); shot(pg, "11_calendar_with_booking")
    login(pg, "faculty"); tab(pg, "Approvals"); pg.fill("#lb", "") if False else None; pg.click("#lb"); print("5e (loan):", banner(pg))
    login(pg, "approver"); tab(pg, "Approvals"); pg.fill("[id^=j]", "short"); pg.click("[data-d='approve']"); print("5f:", banner(pg))
    pg.fill("[id^=j]", "Lab safety briefing completed"); pg.click("[data-d='approve']"); print("5g:", banner(pg)); shot(pg, "12_approvals")
    login(pg, "manager"); tab(pg, "Dashboard"); shot(pg, "13_dashboard")
    b.close()
