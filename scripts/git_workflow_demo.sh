#!/usr/bin/env bash
# Reproduces the Git workflow used for this project in a fresh repository (Q4).
# Usage: GIT_AUTHOR_NAME="Your Name" GIT_AUTHOR_EMAIL=you@example.edu scripts/git_workflow_demo.sh [DEST]
# Run it yourself, then push DEST to GitHub and open the pull request there for the real review record.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"; DEST="${1:-/tmp/campus-ops-repo}"
export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-Student Name}" GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-student@example.edu}"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
BASE="$(mktemp -d)"; python3 "$SRC/experiments/make_baseline.py" "$SRC/backend" "$BASE" >/dev/null
rm -rf "$DEST"; mkdir -p "$DEST"; cd "$DEST"
put() { if [ -d "$1" ]; then mkdir -p "$2"; cp -r "$1/." "$2/"; else mkdir -p "$(dirname "$2")"; cp "$1" "$2"; fi; }
say() { printf '\n\033[1m$ %s\033[0m\n' "$*"; }
git init -q -b main; git config core.autocrlf false

# ---------------- v1.0.0 baseline on main ----------------
cp "$SRC/.gitignore" .
cat > README.md <<'R'
# Smart Campus Operations Platform

Status: v1.0.0 baseline - single-instance assumptions
Run: python backend/seed.py && python backend/dev_server.py
R
git add -A; git commit -q -m "chore: initialise repository, ignore generated files and secrets"
put "$BASE/schema.sql" backend/schema.sql; put "$BASE/triage.py" backend/triage.py; put "$BASE/seed.py" backend/seed.py; put "$SRC/backend/requirements.txt" backend/requirements.txt
git add -A; git commit -q -m "feat(db): baseline schema, synthetic seed data and keyword triage rules"
put "$BASE/app.py" backend/app.py; put "$BASE/serve.py" backend/serve.py; put "$BASE/dev_server.py" backend/dev_server.py
git add -A; git commit -q -m "feat(api): v1 REST API for bookings, tickets, approvals and linked issues"
put "$SRC/frontend" frontend; rm -f frontend/Dockerfile
git add -A; git commit -q -m "feat(ui): single-page interface for catalogue, calendar, queue, approvals"
git tag -a v1.0.0 -m "v1.0.0 baseline: works on one instance; known to fail race scenarios"

# ---------------- branch 1: concurrency hardening ----------------
git switch -q -c feature/concurrency-hardening
put "$SRC/backend/schema.sql" backend/schema.sql
git add -A; git commit -q -m "feat(db): overlap and audit triggers, UNIQUE keys for idempotency"
put "$SRC/backend/app.py" backend/app.py
git add -A; git commit -q -m "fix(api): serialise writes with BEGIN IMMEDIATE, add optimistic versions and Idempotency-Key replay"
put "$SRC/backend/tests" backend/tests; rm -rf backend/tests/__pycache__
git add -A; git commit -q -m "test: 27 unit tests for authorisation, overlap, idempotency, state machines"
put "$SRC/experiments/make_baseline.py" experiments/make_baseline.py; put "$SRC/experiments/race_checks.py" experiments/race_checks.py
put "$SRC/experiments/two_instance_experiment.py" experiments/two_instance_experiment.py; put "$SRC/experiments/ui_journey.py" experiments/ui_journey.py
for f in race_baseline race_revised experiment_output unit_tests_output; do put "$SRC/docs/$f.txt" docs/$f.txt; done
sed -i 's/^Status: .*/Status: v1.1.0-rc1 - concurrency hardened (single host, SQLite WAL)/; s/^Run: .*/Run: python backend\/seed.py \&\& python backend\/dev_server.py/' README.md
git add -A; git commit -q -m "test: race and two-instance experiments with recorded evidence"
put "$SRC/docs/PR-1-concurrency-hardening.md" docs/PR-1-concurrency-hardening.md
git add -A; git commit -q -m "docs: record pull request #1 (author self-review)"

# ---------------- branch 2: containerisation (independent of branch 1) ----------------
git switch -q main; git switch -q -c feature/containerization
put "$SRC/backend/Dockerfile" backend/Dockerfile; put "$SRC/.dockerignore" .dockerignore
git add -A; git commit -q -m "build(api): Dockerfile with gunicorn and non-root user"
put "$SRC/frontend/Dockerfile" frontend/Dockerfile; put "$SRC/nginx/default.conf" nginx/default.conf
git add -A; git commit -q -m "build(web): nginx image with two-instance upstream and safe retries"
put "$SRC/docker-compose.yml" docker-compose.yml; put "$SRC/.env.example" .env.example
git add -A; git commit -q -m "build: compose stack (migrate, api1, api2, web), secrets via .env"
put "$SRC/scripts/verify_deployment.py" scripts/verify_deployment.py
sed -i 's/^Status: .*/Status: v1.1.0-rc1 - containerised (docker compose, two API instances)/; s/^Run: .*/Run: docker compose up --build/' README.md
git add -A; git commit -q -m "docs: deployment verification script and run instructions"

# ---------------- merge 1 (clean) ----------------
git switch -q main
say "git merge --no-ff feature/concurrency-hardening -m 'Merge pull request #1 from feature/concurrency-hardening'"
git merge --no-ff feature/concurrency-hardening -m "Merge pull request #1 from feature/concurrency-hardening" | tail -3

# ---------------- merge 2 (deliberate conflict in README.md) ----------------
say "git merge --no-ff feature/containerization"
git merge --no-ff feature/containerization -m "Merge pull request #2 from feature/containerization" 2>&1 | tail -4 || true
say "git status --short   # conflict state"; git status --short | head
say "sed -n '1,12p' README.md   # conflict markers"; sed -n '1,12p' README.md
cat > README.md <<'R'
# Smart Campus Operations Platform

Status: v1.1.0 - concurrency hardened and containerised
Run: docker compose up --build   (local development: python backend/seed.py && python backend/dev_server.py)
R
say "git add README.md && git commit --no-edit   # conflict resolved: both statements kept"
git add README.md; git commit -q --no-edit; git log -1 --oneline

# ---------------- recovery from an unsuitable change ----------------
PRE="$(git rev-parse HEAD)"
python3 - <<'P'
import re; s = open("backend/app.py").read()
s = s.replace('c = db(); c.execute("BEGIN IMMEDIATE")', 'c = db()   # perf: skip the explicit transaction'); open("backend/app.py", "w").write(s)
P
git commit -qam "perf: skip explicit write transaction (UNSUITABLE: reintroduces the race)"
BAD="$(git rev-parse --short HEAD)"
say "git revert --no-edit $BAD"; git revert --no-edit HEAD | tail -2
say "git diff --stat $PRE HEAD   # empty output = tree identical to the pre-change state"; git diff --stat "$PRE" HEAD; echo "(no differences)"

# ---------------- release ----------------
put "$SRC/README.md" README.md; put "$SRC/docs" docs; rm -f docs/git_workflow_output.txt; put "$SRC/scripts/git_workflow_demo.sh" scripts/git_workflow_demo.sh
git add -A; git commit -q -m "docs: final README and execution evidence (screenshots, experiment outputs)"
git tag -a v1.1.0 -m "v1.1.0: concurrency hardened, containerised, 27 tests passing"

say "git log --oneline --graph --decorate --all"; git log --oneline --graph --decorate --all
say "git branch -a && git tag -n"; git branch -a; git tag -n
say "git ls-files | head -40   # generated files and .env are not tracked"; git ls-files | grep -v '^docs/shots' | head -40
say "git check-ignore -v .env backend/__pycache__ x.db"; git check-ignore -v .env backend/__pycache__ x.db
say "git shortlog -sn --all"; git shortlog -sn --all
say "tree identical to working project? (diff -r, excluding .git and generated files)"
diff -r --exclude=.git --exclude=__pycache__ --exclude=git_workflow_output.txt --exclude=.env "$SRC" "$DEST" && echo "identical"
