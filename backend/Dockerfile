FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend ./frontend

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# No HEALTHCHECK: under plain `docker compose` (no Swarm/k8s, no watcher like
# autoheal) an unhealthy status doesn't trigger a restart by itself - only
# `restart: unless-stopped` reacting to the process actually dying does that,
# regardless of this check. So it would be purely cosmetic status in `docker
# ps`, at the cost of spawning a Python interpreter every interval - not
# worth it on constrained hardware for a non-critical home dashboard.

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
