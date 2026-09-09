# Install into a target dir in a throwaway stage so pip/wheel/setuptools
# (and any build artifacts) never end up in the final image - nothing at
# runtime needs pip itself.
FROM python:3.12-slim AS builder
WORKDIR /install
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --target=/install -r requirements.txt

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local/lib/python3.12/site-packages
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

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
