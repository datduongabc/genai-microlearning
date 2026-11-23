# devops

Everything here is about running the system, not about what it does.
Only the CI workflow stays where GitHub expects it (`.github/`), and a root `docker-compose.yml` that does nothing but include `devops/docker-compose.yml`, keeping plain `docker compose` available as the CPU path from the repository root.
That root file keeps the repository root as the project directory, so the relative paths and the volume names are the same as before the move,
and it points `env_file` at `devops/.env`, the single env file the compose stack and the backend both read.

## What is here

- `docker-compose.yml`: the whole stack (database, Moodle, migrate, backend, frontend, LLM gateway, HTTPS proxy).
- `docker-compose.scale.yml`: several API and worker containers on one host.
- `gateway/`: the LLM gateway image and its model routing. Chat, transcription and embedding models are called through a provider's API, and reranking through Jev's; no model runs in the stack.
- `moodle/`: the Moodle image.
- `caddy/Caddyfile`: the HTTPS proxy in front of the frontend and the API (`/api`).
- `scripts/rotate_secrets.py`: replaces the database and Moodle admin passwords everywhere they live.
- `scripts/compose.sh`: the standard Compose launcher (the compose file and `devops/.env` from any directory).
- `.env.example`: every variable the compose file and the backend read, with placeholders.

## Running it

Copy `devops/.env.example` to `devops/.env`, then run `python devops/scripts/rotate_secrets.py` once to give the three database roles and the Moodle admin random passwords.
Start the stack from the repository root with `./devops/scripts/compose.sh up -d`.
The site is then at `https://localhost:8443`.
Caddy signs the local certificate with its own CA, so trust it once with `docker exec do-an-proxy caddy trust`.

## Who holds which credential

- `migrate` (one shot, runs before the backend) holds the database owner's URL.
  It is the only place that does.
- `backend` serves requests as `app_runtime` and runs the worker as `app_worker`.
  It gets its variables one by one, not the whole `.env`.
- `worker` runs the queued jobs with the same two roles and the same variables as `backend`.
- `redis` holds the rate-limit counters that the API containers share, and nothing else. If it is
  down, each container keeps limiting on its own until it returns.

## Scaling on one host

The API and the workers are separate containers, and neither keeps state another one needs: sessions
are JWTs plus refresh tokens in Postgres, jobs and their status are in Postgres, and rate-limit
counters are in Redis. Each BM25 index in memory is only a cache, checked against the chunks in
Postgres on every search.

```bash
./devops/scripts/compose.sh -f devops/docker-compose.scale.yml up -d --scale backend=2 --scale worker=2
```

Each worker container runs `WORKER_POOL_SIZE` jobs at once (four by default), with `QUESTION_CONCURRENCY` questions at
a time inside a module; Jev calls per process are bounded by `JEV_MAX_CONCURRENT`. With `UPLOAD_STORE=local` (the compose default) the browser sends the file to the API,
which keeps it in the shared bind mount, so running on several hosts needs `UPLOAD_STORE=s3`: the browser then posts
the file straight to the bucket with a presigned POST, and needs the bucket's CORS to allow the site's origin.

## Going public

Set these in `.env`: `SITE_ADDRESS=lms.example.com`, `HSTS_MAX_AGE=31536000`, `CADDY_AUTO_HTTPS=on`, `PROXY_BIND=0.0.0.0`, `PROXY_HTTPS_PORT=443`, `PROXY_HTTP_PORT=80`, `ENVIRONMENT=production`.
In production the backend refuses to start with a weak configuration (`backend/app/config.py`).
The frontend is still served by the Vite dev server behind the proxy.
For a real deployment build it (`npm run build`) and serve `frontend/dist` from Caddy instead.

## Media ingestion

At most four Whisper uploads run at once across all document jobs in the backend
process. Audio and video use the same mono 16 kHz, 32 kbps MP3
preparation, in ten-minute windows with two seconds of context overlap. Only failed
windows retry (at most three attempts, honoring `retry-after`). Exhausted network or
server errors return the job to the durable queue for a later retry; rate limits, the
retry deadline and other errors fail the document with the window's timestamp in the
existing job monitor. Successful windows
are merged in source order, removing matching overlap text and retaining absolute
segment locators. Normalization preserves duration-based pricing; the overlap adds
about 0.3% audio duration for an 80-minute lecture.

Verification results and the ASR access limitation are recorded in
`docs/measurements/2026-10-26-media-ingest.md`.
