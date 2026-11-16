#!/usr/bin/env bash
# Terraform for one environment of infra/terraform.
#
#   infra/scripts/tf.sh plan                       demo: backend.hcl, terraform.tfvars
#   ENVIRONMENT=staging infra/scripts/tf.sh apply  staging: its own state and its own variables
#
# Every environment other than demo keeps its state under "<environment>/terraform.tfstate" in the
# bucket of backend.hcl and its provider cache in .terraform-<environment>, so a staging command
# can never read or write the demo state. Its sizes come from infra/environments/<environment>.tfvars.
#
# The image tag comes from infra/terraform/image-<environment>.tfvars (git-ignored), which deploy.sh
# writes with the tag it rolls out. A tag kept anywhere else goes stale after the next deploy, and an
# apply with a stale tag rolls every service back to that image.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$ROOT/infra/terraform"
ENVIRONMENT="${ENVIRONMENT:-demo}"
IMAGE="$TF_DIR/image-$ENVIRONMENT.tfvars"

VAR_FILES=()
if [[ "$ENVIRONMENT" != demo ]]; then
  VARS="$ROOT/infra/environments/$ENVIRONMENT.tfvars"
  if [[ ! -f "$VARS" ]]; then
    echo "No $VARS: every environment except demo needs its own variables." >&2
    exit 1
  fi
  VAR_FILES+=("-var-file=$VARS")
  export TF_DATA_DIR="$TF_DIR/.terraform-$ENVIRONMENT"
  if [[ ! -d "$TF_DATA_DIR" ]]; then
    terraform -chdir="$TF_DIR" init -input=false -backend-config=backend.hcl \
      -backend-config="key=$ENVIRONMENT/terraform.tfstate" >&2
  fi
fi

case "${1:-}" in
  plan | apply)
    if [[ ! -f "$IMAGE" ]]; then
      echo "No $IMAGE, so the tag running in $ENVIRONMENT is unknown." >&2
      echo "Run ENVIRONMENT=$ENVIRONMENT infra/scripts/deploy.sh, which writes it, or write the tag that is" >&2
      echo "deployed now by hand: echo 'image_tag = \"<commit>\"' > $IMAGE" >&2
      exit 1
    fi ;;
esac
if [[ -f "$IMAGE" ]]; then VAR_FILES+=("-var-file=$IMAGE"); fi

# Only the commands that evaluate variables take a var file; `output` and `state` refuse one.
case "${1:-}" in
  plan | apply | destroy | import | refresh | console)
    command="$1"; shift
    exec terraform -chdir="$TF_DIR" "$command" ${VAR_FILES[@]+"${VAR_FILES[@]}"} "$@" ;;
  *)
    exec terraform -chdir="$TF_DIR" "$@" ;;
esac
