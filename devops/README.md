# devops

Everything here is about running the system, not about what it does.
Only the CI workflow stays where GitHub expects it (`.github/`), and a root `docker-compose.yml` that does nothing but include `devops/docker-compose.yml`, so `docker compose up -d` works from the repository root.
That root file keeps the repository root as the project directory, so relative paths, `.env` and the volume names are the same as before the move.

## What is here

- `docker-compose.yml`: the whole stack (database, Moodle, migrate, backend, frontend, LLM gateway, HTTPS proxy).
- `gateway/`: the LLM gateway image and its model routing. Every model is called through a provider's API, none is served locally.
- `moodle/`: the Moodle image.
- `caddy/Caddyfile`: the HTTPS proxy in front of the frontend and the API (`/api`).
- `scripts/rotate_secrets.py`: replaces the database and Moodle admin passwords everywhere they live.
- `.env.example`: every variable the compose file and the backend read, with placeholders.

## Running it

Copy `.env.example` to `.env` at the repository root, then run `python devops/scripts/rotate_secrets.py` once to give the three database roles and the Moodle admin random passwords.
Start the stack with `docker compose up -d`.
The site is then at `https://localhost:8443`.
Caddy signs the local certificate with its own CA, so trust it once with `docker exec do-an-proxy caddy trust`.

## Who holds which credential

- `migrate` (one shot, runs before the backend) holds the database owner's URL.
  It is the only place that does.
- `backend` serves requests as `app_runtime` and runs the worker as `app_worker`.
  It gets its variables one by one, not the whole `.env`.
- The rate-limit counters live in the backend's memory: it runs one process, so they are exact.
  Running several backend processes would need a store they share, which this stack does not have.

## Going public

Set these in `.env`: `SITE_ADDRESS=lms.example.com`, `HSTS_MAX_AGE=31536000`, `CADDY_AUTO_HTTPS=on`, `PROXY_BIND=0.0.0.0`, `PROXY_HTTPS_PORT=443`, `PROXY_HTTP_PORT=80`, `ENVIRONMENT=production`.
In production the backend refuses to start with a weak configuration (`backend/app/config.py`).
The frontend is still served by the Vite dev server behind the proxy.
For a real deployment build it (`npm run build`) and serve `frontend/dist` from Caddy instead.
