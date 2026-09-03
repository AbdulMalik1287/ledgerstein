# LedgerStein ships as one container: FastAPI serves both the API and the built
# dashboard, so a deployment is a single service on a single origin. No CORS to
# get wrong, no second URL to keep in sync, and a judge only has one link.

# ---------------------------------------------------------------- dashboard
FROM node:22-alpine AS dashboard

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ------------------------------------------------------------------ runtime
FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY --from=dashboard /build/dist ./frontend/dist

# The batches are not baked in. The generator is deterministic, so the service
# rebuilds them from their seeds on first start -- which also means a cold
# container proves the determinism claim rather than just asserting it.
RUN mkdir -p data/generated

WORKDIR /srv/backend

# Hosts inject the port they want bound. Default 8000 for a plain `docker run`.
ENV PORT=8000
EXPOSE 8000

# Exec form so uvicorn is PID 1 and receives SIGTERM directly -- a host that
# scales the service down then gets a clean shutdown rather than a kill after
# the grace period. uvicorn reads $PORT itself, so no shell expansion is needed.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
