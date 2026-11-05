#!/bin/bash
# The whole load test: API capacity at 1, 2 and 4 containers, then generation at growing worker counts.
# Results land in results/. Takes about an hour and calls no paid model. See README.md.
cd "$(dirname "$0")" || exit 1
mkdir -p results; chmod 777 results
export MOCK_LLM_MEDIAN_SECONDS=13.3
sample() { while true; do date +%s | tr '\n' ' '; docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' | grep do-an-load | tr '\n' ';'; echo; sleep 10; done >> "results/stats-$1.txt"; }
run() { docker compose exec -T runner /app/.venv/bin/python drive_generation.py "$@"; }

docker compose build gateway 2>&1 | tail -1
docker compose up -d --scale worker=1 --scale backend=1 --scale reranker=1 2>&1 | tail -1
docker compose exec -T runner /app/.venv/bin/alembic upgrade head 2>&1 | tail -1
run seed --users 40 | tail -1

for n in 1 2 4; do
  docker compose up -d --scale worker=1 --scale backend=$n 2>&1 | tail -1; sleep 12
  sample api-$n & P=$!
  docker compose --profile k6 run --rm -e VUS=200 -e DURATION=60s k6 run --quiet --summary-export /results/api-$n.json /scripts/api_read.js 2>&1 | tail -25 > results/api-$n.log
  kill $P; echo "api $n done"
done

generation() { label=$1; workers=$2; modules=$3; rerankers=$4
  docker compose up -d --force-recreate --scale worker=$workers --scale backend=2 --scale reranker=$rerankers backend worker reranker 2>&1 | tail -1; sleep 25
  docker compose exec -T db psql -U postgres -d microlearning -qc "DELETE FROM jobs WHERE status IN ('queued','running')"
  run seed --users 40 | tail -1
  sample $label & P=$!
  run generate --modules $modules --label "$label" | tail -1 | cut -c1-300
  kill $P
}
generation workers-1x4 1 4 1
generation workers-2x4 2 8 1
generation workers-4x4 4 16 1
generation workers-8x4 8 32 1
generation workers-8x4-reranker-2 8 32 2
echo finished
