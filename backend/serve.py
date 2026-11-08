"""Local multi-instance runner for experiments (containers use gunicorn instead): python serve.py PORT INSTANCE_ID"""
import os, sys
os.environ["INSTANCE_ID"] = sys.argv[2]
from werkzeug.serving import run_simple
from app import app
run_simple("127.0.0.1", int(sys.argv[1]), app, threaded=True, use_reloader=False)
