#!/bin/bash
# Time to the first word on one API process as messages arrive faster, with the mock gateway and Jev for real.
# Calls Jev for real, about $0.0007 per message. Workers stay stopped.
cd "$(dirname "$0")" || exit 1
mkdir -p results; chmod 777 results
set -a; source <(grep -E '^TYPESAFE_API_KEY=' ../.env); set +a
OUT=${OUT:-chat-stream.json}
dc() { docker compose -f docker-compose.yml -f docker-compose.chat.yml "$@"; }
py() { dc exec -T runner /app/.venv/bin/python drive_generation.py "$@"; }
dc up -d --wait db 2>&1 | tail -1
dc up -d --scale backend="${BACKENDS:-1}" --force-recreate backend gateway embedder runner proxy redis 2>&1 | tail -1
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "STOP stack did not start"; exit 1; }
waited=0
until dc exec -T runner /app/.venv/bin/python -c "import httpx; httpx.get('http://gateway:4000/health/liveliness', timeout=3).raise_for_status(); httpx.get('http://embedder:80/health', timeout=3).raise_for_status(); httpx.get('http://backend:8000/health', timeout=3).raise_for_status()" 2>/dev/null; do
  sleep 3; waited=$((waited + 3))
  [ "$waited" -lt 300 ] || { echo "STOP services not healthy after 300 s"; exit 1; }
done
dc exec -T runner /app/.venv/bin/alembic upgrade head 2>&1 | tail -1
py seed --users 400 | tail -1
# Arrivals per second. Open streams stay well under the limit set above, so this finds the slowest step, not the limit.
for rate in ${RATES:-1 2 4 8 12}; do
  (sleep 25; docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' | grep -E 'load-(backend|db|embedder|gateway|runner)' | tr '\n' ' ' | sed "s/^/$rate /" >> "results/$OUT.stats"; echo >> "results/$OUT.stats") &
  py chat-stream --concurrency 5000 --rate "$rate" --messages $((rate * 45)) --label "rate-$rate" --out "$OUT" | tail -1 | cut -c1-500
  wait
  sleep 3
done
dc stop -t 1 runner >/dev/null 2>&1
echo FINISHED
