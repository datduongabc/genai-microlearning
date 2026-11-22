#!/bin/bash
# Failure tests: stop each kind of service while it is working and check that nothing is lost and that the
# system recovers on its own. The local stand-in for S6 on AWS. Same stack as the other load tests, so no paid model:
# the LLM is mock_gateway.py, everything else is real. About an hour and a quarter. Results in results/.
#
#   ./run_failures.sh                  every scenario
#   ./run_failures.sh worker-sigterm   only the ones named
#
# Each generation scenario queues 8 modules on 2 workers x 4 threads, makes the fault 60 s in, undoes it when the
# scenario says so, and waits for every job. It passes when all 8 jobs are done and exactly one module exists per
# document. job_attempts counts the times a job went back to the queue and counted it as an attempt.
cd "$(dirname "$0")" || exit 1
mkdir -p results; chmod 777 results
export MOCK_LLM_MEDIAN_SECONDS=13.3
OUT=failures.json
run() { docker compose exec -T runner /app/.venv/bin/python drive_generation.py "$@"; }
sql() { docker compose exec -T db psql -U postgres -d microlearning -tAc "$1"; }
stack() { docker compose up -d --scale worker=2 --scale backend=2 2>&1 | tail -1; }
wanted() { [ -z "$SELECTED" ] || [[ " $SELECTED " == *" $1 "* ]]; }
SELECTED="$*"

docker compose build gateway 2>&1 | tail -1
stack
docker compose exec -T runner /app/.venv/bin/alembic upgrade head 2>&1 | tail -1
run seed --users 16 | tail -1

# label, seconds until the fault, the fault, seconds until it is undone, how to undo it (empty: never)
generation() { local label=$1 at=$2 fault=$3 after=$4 recover=$5
  wanted "$label" || return 0
  stack; sleep 20; local since; since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  sql "DELETE FROM jobs WHERE status IN ('queued','running')" >/dev/null
  run generate --modules 8 --label "$label" --out $OUT > "results/failure-$label.log" 2>&1 & local G=$!
  sleep "$at"; echo "$(date +%T) $label: $fault"; eval "$fault"
  if [ -n "$recover" ]; then sleep "$after"; echo "$(date +%T) $label: $recover"; eval "$recover"; fi
  wait $G
  docker compose logs --no-color --since "$since" worker 2>&1 | grep -E 'job_queue|ERROR|Traceback' > "results/failure-$label.worker.log"
  python3 - "$label" <<'PY'
import json, sys
row = [r for r in json.load(open("results/failures.json")) if r["label"] == sys.argv[1]][-1]
ok = row["done"] == row["modules"] and row["modules_created"] == row["modules"] == row["documents_with_modules"]
print(f"{'PASS' if ok else 'FAIL'} {row['label']}: done {row['done']}/{row['modules']}, modules {row['modules_created']}, "
      f"attempts {row['job_attempts']}, wall {row['wall_seconds']} s, errors {row['errors']}")
PY
}

W1=do-an-load-worker-1
generation baseline 0 true 0 ""
# A deploy or a scale-in: the worker puts its jobs back without counting an attempt.
generation worker-sigterm 60 "docker stop -t 120 $W1 >/dev/null" 5 "docker start $W1 >/dev/null"
# A crash: nothing puts the jobs back, the sweeper does once their heartbeat is 15 minutes old.
generation worker-sigkill 60 "docker kill -s KILL $W1 >/dev/null" 5 "docker start $W1 >/dev/null"
generation gateway-60s 60 "docker compose stop gateway >/dev/null 2>&1" 60 "docker compose start gateway >/dev/null 2>&1"
generation embedder-60s 60 "docker compose stop embedder >/dev/null 2>&1" 60 "docker compose start embedder >/dev/null 2>&1"
generation database-restart 60 "docker compose restart db >/dev/null 2>&1" 0 ""
generation api-sigkill 60 "docker kill -s KILL do-an-load-backend-1 >/dev/null" 5 "docker start do-an-load-backend-1 >/dev/null"

# Chat while a dependency is down: Redis should not matter (rate limits fall back to each process), a missing
# embedder cannot be worked around. A failing Jev keeps the hybrid order (tests/unit/test_jev_reranker.py).
chat_down() { local label=$1 service=$2
  wanted "$label" || return 0
  stack; sleep 20
  # A fresh api: it caches query embeddings, and the questions repeat between scenarios.
  docker compose restart backend >/dev/null 2>&1; sleep 15
  docker compose stop "$service" >/dev/null 2>&1
  run chat --concurrency 2 --messages 6 --label "$label" --out failures-chat.json | tail -1
  docker compose start "$service" >/dev/null 2>&1
  python3 - "$label" <<'PY'
import json, sys
row = [r for r in json.load(open("results/failures-chat.json")) if r["label"] == sys.argv[1]][-1]
print(f"{row['label']}: status codes {row['status_codes']}, latency {row['latency_seconds']}")
PY
}
chat_down chat-redis-down redis
chat_down chat-embedder-down embedder
echo finished
