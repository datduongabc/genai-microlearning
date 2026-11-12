#!/usr/bin/env bash
# Terraform for one environment of infra/terraform.
#
#   infra/scripts/tf.sh plan                       demo, as before: backend.hcl, terraform.tfvars
#   ENVIRONMENT=staging infra/scripts/tf.sh apply  staging: its own state and its own variables
#
# Every environment other than demo keeps its state under "<environment>/terraform.tfstate" in the
# bucket of backend.hcl and its provider cache in .terraform-<environment>, so a staging command
# can never read or write the demo state. Its sizes come from infra/environments/<environment>.tfvars.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$ROOT/infra/terraform"
ENVIRONMENT="${ENVIRONMENT:-demo}"

if [[ "$ENVIRONMENT" == demo ]]; then
  exec terraform -chdir="$TF_DIR" "$@"
fi

VARS="$ROOT/infra/environments/$ENVIRONMENT.tfvars"
if [[ ! -f "$VARS" ]]; then
  echo "No $VARS: every environment except demo needs its own variables." >&2
  exit 1
fi
export TF_DATA_DIR="$TF_DIR/.terraform-$ENVIRONMENT"
if [[ ! -d "$TF_DATA_DIR" ]]; then
  terraform -chdir="$TF_DIR" init -input=false -backend-config=backend.hcl \
    -backend-config="key=$ENVIRONMENT/terraform.tfstate" >&2
fi

# Only the commands that evaluate variables take a var file; `output` and `state` refuse one.
case "${1:-}" in
  plan | apply | destroy | import | refresh | console)
    command="$1"; shift
    exec terraform -chdir="$TF_DIR" "$command" -var-file="$VARS" "$@" ;;
  *)
    exec terraform -chdir="$TF_DIR" "$@" ;;
esac
