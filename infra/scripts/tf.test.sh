#!/usr/bin/env bash
# Checks tf.sh's image tag handling against a fake terraform: no AWS, no state.
#   bash infra/scripts/tf.test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_SH="${TF_SH:-$ROOT/infra/scripts/tf.sh}"
FAKE="$(mktemp -d)"
VARS="$ROOT/infra/environments/tftest.tfvars"
IMAGE="$ROOT/infra/terraform/image-tftest.tfvars"
DATA="$ROOT/infra/terraform/.terraform-tftest" # present, so tf.sh does not run init
trap 'rm -rf "$FAKE" "$DATA" "$VARS" "$IMAGE"' EXIT
printf '#!/bin/sh\necho "$@"\n' >"$FAKE/terraform" && chmod +x "$FAKE/terraform"
export PATH="$FAKE:$PATH" ENVIRONMENT=tftest
touch "$VARS" && mkdir -p "$DATA"
fail() { echo "FAIL: $*" >&2; exit 1; }

# Without the tag file plan and apply stop, and say how to get one.
for command in plan apply; do
  out="$("$TF_SH" "$command" 2>&1)" && fail "$command ran without $IMAGE: $out"
  grep -q "deploy.sh" <<<"$out" || fail "the message does not point to deploy.sh: $out"
done

# With it, the tag file comes after the environment's variables; output takes no var file.
echo 'image_tag = "abc1234"' >"$IMAGE"
out="$("$TF_SH" apply -target=x)"
[[ "$out" == *"apply -var-file=$VARS -var-file=$IMAGE -target=x" ]] || fail "apply arguments: $out"
out="$("$TF_SH" output -raw url)"
[[ "$out" == *" output -raw url" && "$out" != *var-file* ]] || fail "output arguments: $out"

echo "tf.sh: ok"
