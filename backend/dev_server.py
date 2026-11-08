"""Developer convenience: serve API + static frontend from one process (containers use nginx for this)."""
import os, sys
from flask import send_from_directory
from app import app
FRONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
@app.get("/")
def index(): return send_from_directory(FRONT, "index.html")
@app.get("/<path:f>")
def static_files(f): return send_from_directory(FRONT, f)
if __name__ == "__main__":
    from werkzeug.serving import run_simple
    run_simple("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 5000, app, threaded=True, use_reloader=False)
