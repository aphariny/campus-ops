# Build context is the repository root:  docker compose build
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home app && mkdir /data && chown app:app /data
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app.py backend/seed.py backend/triage.py backend/schema.sql ./
USER app
EXPOSE 5000
# 2 worker processes x 4 threads per container; all state lives in the shared database volume
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--access-logfile", "-", "app:app"]
