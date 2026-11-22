#!/bin/bash
# Horizontal scaling with real models. Paid: every module uses about 280,000 tokens.
#
#   ./run_real.sh claude-fast        # or openai-fast, gemini-fast
#
# Stops by itself when the provider reports a billing or key error, when every item of a run fails, when the model
# has used TOKEN_CAP tokens, or when the machine has under 400 MB of memory available. Rate limits do not stop it: they
# are part of the result. Results land in results/real-<alias>[-<RUN_TAG>].json, and a repeated call skips finished runs.
cd "$(dirname "$0")" || exit 1
alias=${1:?usage: run_real.sh claude-fast|openai-fast|gemini-fast}
# Repeats wait until the 429 fixes are in: repeating a known collapse costs money and adds little.
if [ -n "$RUN_TAG" ] && [ -z "$ALLOW_REPEAT" ]; then echo "repeat $RUN_TAG skipped"; exit 0; fi
export LLM_MODEL=$alias
TOKEN_CAP=${TOKEN_CAP:-30000000}
OUT=real-$alias${RUN_TAG:+-$RUN_TAG}.json
mkdir -p results; chmod 777 results
set -a; source <(grep -E '^(OPENROUTER|TYPESAFE)_API_KEY=' ../.env); set +a

# EXTRA_COMPOSE adds an override, and ONLY limits the run to the labels it names.
dc() { docker compose -f docker-compose.yml -f docker-compose.real.yml ${EXTRA_COMPOSE:+-f "$EXTRA_COMPOSE"} "$@"; }
py() { dc exec -T runner /app/.venv/bin/python drive_generation.py "$@"; }
scale() { # workers backends embedders gateways
  dc up -d --force-recreate --scale worker="$1" --scale backend="$2" --scale embedder="$3" \
    --scale gateway="$4" backend worker embedder gateway proxy 2>&1 | tail -1
  until dc exec -T runner /app/.venv/bin/python -c "import httpx; httpx.get('http://gateway:4000/health/liveliness', timeout=3).raise_for_status(); httpx.get('http://embedder:80/health', timeout=3).raise_for_status(); httpx.get('http://backend:8000/health', timeout=3).raise_for_status()" 2>/dev/null; do sleep 3; done
  sleep 5
}
sample() { while true; do date +%s | tr '\n' ' '; docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' | grep do-an-load | tr '\n' ' '; echo; sleep 15; done >> "results/$OUT.$1.stats"; }
stop_run() { # Stopping the runner ends a driver still inside it, which would otherwise write a false row at its time limit.
  dc stop -t 1 worker runner >/dev/null 2>&1
}
guard() { # Stops the whole run when memory runs out, and leaves queued jobs from spending more tokens.
  while sleep 10; do
    if [ "$(free -m | awk 'NR==2{print $7}')" -lt 400 ]; then
      echo "STOP low memory"; stop_run; kill -TERM $$; return
    fi
  done
}
check() { # Fails when the last row broke a stop condition.
  py check --out "$OUT" --token-cap "$TOKEN_CAP" || { echo "STOP $alias"; exit 3; }
}
finished() {
  if [ -n "$ONLY" ] && [[ " $ONLY " != *" $1 "* ]]; then return 0; fi
  [ -f "results/$OUT" ] && grep -q "\"label\": \"$1\"" "results/$OUT"
}
clear_jobs() { # Before workers start: they would otherwise pick up jobs left by a stopped run and spend tokens on them.
  dc stop worker >/dev/null 2>&1
  dc exec -T db psql -U postgres -d microlearning -qc "DELETE FROM jobs WHERE status IN ('queued','running')"
}
generation() { # label workers modules embedders gateways
  finished "$1" && return
  clear_jobs
  scale "$2" 2 "$4" "$5"
  sample "$1" & P=$!
  py generate --modules "$3" --label "$1" --out "$OUT" --max-seconds 2400 | tail -1 | cut -c1-420
  kill $P 2>/dev/null
  check
}
chat() { # label backends concurrency messages
  finished "$1" && return
  scale 1 "$2" 1 1
  sample "$1" & P=$!
  py chat --concurrency "$3" --messages "$4" --label "$1" --out "$OUT" | tail -1 | cut -c1-420
  kill $P 2>/dev/null
  check
}

guard & G=$!
trap 'kill $G 2>/dev/null; jobs -p | xargs -r kill 2>/dev/null; stop_run' EXIT
dc up -d --wait db 2>&1 | tail -1
clear_jobs
dc up -d --scale worker=1 --scale backend=1 2>&1 | tail -1
until dc exec -T runner /app/.venv/bin/python -c "import httpx; httpx.get('http://gateway:4000/health/liveliness', timeout=3).raise_for_status()" 2>/dev/null; do sleep 3; done
dc exec -T runner /app/.venv/bin/alembic upgrade head 2>&1 | tail -1
py seed --users "${SEED_USERS:-40}" | tail -1

# One worker first: if the model or the key is wrong, this stops after four modules.
generation worker-1x4 1 4 1 1
generation worker-2x4 2 8 1 1
generation worker-4x4 4 16 1 1
generation embedder-2-worker-4x4 4 16 2 1
generation gateway-2-worker-4x4 4 16 1 2
chat chat-api-1 1 25 50
chat chat-api-2 2 50 100
chat chat-api-4 4 100 200
# Last, since it needs the most memory: a low-memory stop here loses no other run.
generation worker-8x4 8 32 1 1
# More modules than worker slots, so a queue forms and the result is a sustained rate. Only when named in ONLY; needs SEED_USERS=96.
[ -n "$ONLY" ] && generation worker-8x4-queue96 8 96 1 1
[ -n "$ONLY" ] && generation worker-4x4-queue32 4 32 1 1
echo "finished $alias"
