#!/bin/sh
# Choose the measured Ampere 8.6 runtime; unsupported/missing GPUs use CPU.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
embedder_device=${EMBEDDER_DEVICE:-auto}
if [ "$embedder_device" = auto ]; then
    capability=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n 1 || true)
    if [ "$capability" = 8.6 ]; then embedder_device=gpu; else embedder_device=cpu; fi
fi
case "$embedder_device" in
    gpu) set -- -f "$repo_dir/devops/docker-compose.yml" -f "$repo_dir/devops/docker-compose.gpu.yml" "$@" ;;
    cpu) set -- -f "$repo_dir/devops/docker-compose.yml" "$@" ;;
    *) printf '%s\n' 'EMBEDDER_DEVICE must be auto, cpu or gpu' >&2; exit 2 ;;
esac
exec docker compose --project-directory "$repo_dir" --env-file "$repo_dir/devops/.env" "$@"
