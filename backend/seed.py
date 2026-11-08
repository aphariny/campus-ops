"""Idempotent database initialisation + synthetic reference data. Run once per deployment (compose 'migrate' service)."""
import os, sqlite3
from werkzeug.security import generate_password_hash
DB_PATH = os.environ.get("DATABASE_PATH", "/data/campus.db")
PASSWORD = os.environ.get("SEED_PASSWORD")          # demo password comes from environment, never from source control
if not PASSWORD: raise SystemExit("SEED_PASSWORD is not set (copy .env.example to .env)")

USERS = [("Asha Admin","admin@campus.test","admin","IT"),("Farid Manager","manager@campus.test","facility_manager","Estates"),
         ("Tara Tech","tech1@campus.test","technician","Estates"),("Tom Tech","tech2@campus.test","technician","Estates"),
         ("Ana Approver","approver@campus.test","approver","Science"),("Fiona Faculty","faculty@campus.test","faculty","Science"),
         ("Felix Faculty","faculty2@campus.test","faculty","Engineering"),("Sam Student","student@campus.test","student","Science"),
         ("Sia Student","student2@campus.test","student","Engineering")]
FACILITIES = [("N-LAB1","Chemistry Lab 1","laboratory","North","Science Block",30,1),("N-LAB2","Physics Lab 2","laboratory","North","Science Block",30,1),
              ("N-HALL","Main Lecture Hall","lecture_hall","North","Admin Block",300,0),("N-ROOM1","Seminar Room 1","classroom","North","Admin Block",40,0),
              ("S-LAB1","Computer Lab 1","laboratory","South","Tech Block",60,0),("S-ROOM2","Meeting Room 2","meeting_room","South","Tech Block",12,0),
              ("E-AUD","Auditorium","auditorium","East","Arts Block",500,1),("W-GYM","Sports Hall","sports","West","Sports Complex",120,0)]
SLA = [("critical",1,4),("high",4,24),("medium",8,72),("low",24,168)]

def init():
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(open(os.path.join(here, "schema.sql")).read())
    for n, e, r, d in USERS:
        c.execute("INSERT OR IGNORE INTO users(name,email,role,department,password_hash) VALUES(?,?,?,?,?)", (n, e, r, d, generate_password_hash(PASSWORD)))
    for f in FACILITIES:
        c.execute("INSERT OR IGNORE INTO facilities(code,name,kind,campus,building,capacity,restricted) VALUES(?,?,?,?,?,?,?)", f)
    for s in SLA:
        c.execute("INSERT OR IGNORE INTO sla_policies VALUES(?,?,?)", s)
    c.commit(); c.close()
    print(f"database ready at {DB_PATH}")

if __name__ == "__main__": init()
