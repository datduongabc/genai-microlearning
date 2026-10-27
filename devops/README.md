# devops

Everything here is about running the system, not about what it does.
Only the CI workflow stays where GitHub expects it (`.github/`), and a root `docker-compose.yml` that does nothing but include `devops/docker-compose.yml`, keeping plain `docker compose` available as the CPU path from the repository root.
That root file keeps the repository root as the project directory, so the relative paths and the volume names are the same as before the move,
and it points `env_file` at `devops/.env`, the single env file the compose stack and the backend both read.

## What is here

- `docker-compose.yml`: the whole stack (database, Moodle, migrate, backend, frontend, LLM gateway, HTTPS proxy).
- `gateway/`: the LLM gateway image and its model routing. Every model is called through a provider's API, none is served locally.
- `moodle/`: the Moodle image.
- `caddy/Caddyfile`: the HTTPS proxy in front of the frontend and the API (`/api`).
- `scripts/rotate_secrets.py`: replaces the database and Moodle admin passwords everywhere they live.
- `scripts/compose.sh`: the standard Compose launcher, with GPU embedding auto-selection and CPU fallback.
- `.env.example`: every variable the compose file and the backend read, with placeholders.

## Running it

Copy `devops/.env.example` to `devops/.env`, then run `python devops/scripts/rotate_secrets.py` once to give the three database roles and the Moodle admin random passwords.
Start the stack from the repository root with `./devops/scripts/compose.sh up -d`.
This is the standard start path: it selects GPU embedding on supported hardware and
falls back to CPU as described below.
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

## Media ingestion and GPU embedding

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

Use `./devops/scripts/compose.sh up -d` to select GPU embedding automatically on the
measured NVIDIA Ampere 8.6 hardware (including the RTX 3050 Ti). The launcher applies
`devops/docker-compose.gpu.yml` after the CPU base configuration. NVIDIA Container
Toolkit must expose the GPU to Docker. On other hardware it keeps CPU; force that path
with `EMBEDDER_DEVICE=cpu ./devops/scripts/compose.sh up -d`. Ordinary root
`docker compose` also retains the CPU configuration. All Compose arguments, including
`-p`, pass through.

Both paths use Qwen3-Embedding-0.6B, last-token pooling, and the backend's existing 768
normalized dimensions. CPU keeps ONNX revision `c25a394dd583836952667c12f008335071b3f43d`.
The GPU image needs native weights (the ONNX repo has no safetensors), pinned to
`Qwen/Qwen3-Embedding-0.6B` revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.
The GPU override uses TEI `86-1.9.4`, float16, at most 1,024 tokens and four inputs per
model batch. TEI executes model batches serially; its 512-request queue accommodates
the backend's four workers with 100-chunk requests without raising the GPU batch limits.
The reranker stays on its existing CPU runtime.

Verification results and the ASR access limitation are recorded in
`docs/measurements/2026-10-26-media-ingest.md`.
