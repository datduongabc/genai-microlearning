#!/usr/bin/env bash
# Stops what up.sh started. Runtime files in $BE2E_WORK are kept (screenshots, logs).
WORK="${BE2E_WORK:-${TMPDIR:-/tmp}/browser-e2e}"
if [[ -f "$WORK/pids" ]]; then
  while read -r pid; do
    pkill -TERM -P "$pid" 2>/dev/null
    kill "$pid" 2>/dev/null
  done <"$WORK/pids"
  rm -f "$WORK/pids"
fi
docker rm -f be2e-moodle be2e-db be2e-embedder >/dev/null 2>&1
docker network rm be2e >/dev/null 2>&1
true
