# Smart Campus Operations Platform

Status: v1.1.0 - concurrency hardened and containerised
Run: `docker compose up --build` (see below) or `python backend/dev_server.py` for local development

Containerised full-stack platform for facility booking, sensor-alert intake, explainable maintenance triage,
technician assignment, inspection, approvals, linked developer issues and audited closure.

| Layer | Technology |
|---|---|
| Frontend | Single-page app (HTML + vanilla JavaScript), served by nginx |
| Backend | Python 3.12, Flask 3.1, gunicorn (two instances behind nginx) |
| Database | SQLite in WAL mode on a shared named volume (single host) |
| Delivery | Git (feature branches, tags), Docker, Docker Compose |

## Quick start (clean machine: Git + Docker Compose v2 only)

```bash
git clone <your-repo-url> campus-ops && cd campus-ops
git checkout v1.1.0                 # deploy the identified release
cp .env.example .env                # then edit .env and choose SEED_PASSWORD (never committed)
docker compose up --build -d        # migrate -> api1, api2 -> web
open http://localhost:8080          # demo users: admin|manager|tech1|tech2|approver|faculty|faculty2|student|student2 @campus.test
docker compose ps                   # all services healthy
docker compose down                 # stop (data kept);  docker compose down -v  also deletes the data volume
```

Verify the deployment (stdlib only): `SEED_PASSWORD=... python scripts/verify_deployment.py create`,
then `docker compose restart api1 api2`, then `... verify_deployment.py check`.

## Local development and tests

```bash
cd backend && pip install -r requirements.txt
export SEED_PASSWORD=dev-only-password DATABASE_PATH=/tmp/campus.db
python seed.py && python dev_server.py 5000       # http://127.0.0.1:5000
SEED_PASSWORD=x python -m unittest discover -s tests -v     # 27 tests
python experiments/two_instance_experiment.py                # two real API processes: races, restart, bounded load
```

## Planning assumptions (not production measurements)
Peak 60 users (300 after expansion); booking <= 8 h; SLA resolve hours critical 4 / high 24 / medium 72 / low 168;
sensor thresholds temperature > 30 C, humidity > 75 %, power > 50 kW, water leak >= 1. Triage uses deterministic keyword
rules (not machine learning). The developer-issue integration is a **simulated** local tracker and is labelled as such in the API and UI.
