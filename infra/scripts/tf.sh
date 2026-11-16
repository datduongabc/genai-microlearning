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
# The image tag is the one the environment's state holds (output image_tag), or IMAGE_TAG, which
# deploy.sh sets for its rollout. A tag kept anywhere else, such as a file in one checkout, goes stale
# after a deploy from another, and an apply with a stale tag rolls every service back to that image.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$ROOT/infra/terraform"
ENVIRONMENT="${ENVIRONMENT:-demo}"

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

# Only the commands that evaluate variables take a var file; `output` and `state` refuse one.
case "${1:-}" in
  plan | apply | destroy | import | refresh | console)
    command="$1"; shift
    # A state from before the image_tag output, or a new stack, has none.
    tag="${IMAGE_TAG:-$(terraform -chdir="$TF_DIR" output -raw image_tag || true)}"
    if [[ -n "$tag" ]]; then
      VAR_FILES+=(-var "image_tag=$tag")
    elif [[ "$command" == plan || "$command" == apply ]]; then
      echo "The state of $ENVIRONMENT holds no image tag, so the tag running there is unknown." >&2
      echo "Run ENVIRONMENT=$ENVIRONMENT infra/scripts/deploy.sh, or set IMAGE_TAG to the tag deployed now" >&2
      echo "for this one command: IMAGE_TAG=<commit> infra/scripts/tf.sh $command" >&2
      exit 1
    fi
    exec terraform -chdir="$TF_DIR" "$command" ${VAR_FILES[@]+"${VAR_FILES[@]}"} "$@" ;;
  *)
    exec terraform -chdir="$TF_DIR" "$@" ;;
esac
