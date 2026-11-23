#!/bin/sh
# The standard Compose launcher: the repository's compose file and devops/.env, from any directory.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
exec docker compose -f "$repo_dir/devops/docker-compose.yml" --project-directory "$repo_dir" --env-file "$repo_dir/devops/.env" "$@"
