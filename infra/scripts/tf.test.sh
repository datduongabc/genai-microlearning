#!/usr/bin/env bash
# Checks tf.sh's image tag handling against a fake terraform: no AWS, no state.
#   bash infra/scripts/tf.test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_SH="${TF_SH:-$ROOT/infra/scripts/tf.sh}"
FAKE="$(mktemp -d)"
VARS="$ROOT/infra/environments/tftest.tfvars"
DATA="$ROOT/infra/terraform/.terraform-tftest" # present, so tf.sh does not run init
trap 'rm -rf "$FAKE" "$DATA" "$VARS"' EXIT
# The fake answers `output -raw image_tag` with $STATE_TAG, as a state without that output answers
# nothing, and echoes every other command line.
cat >"$FAKE/terraform" <<'FAKE'
#!/bin/sh
case "$*" in
  *"output -raw image_tag") printf '%s' "${STATE_TAG:-}" ;;
  *) echo "$@" ;;
esac
FAKE
chmod +x "$FAKE/terraform"
export PATH="$FAKE:$PATH" ENVIRONMENT=tftest
unset IMAGE_TAG STATE_TAG
touch "$VARS" && mkdir -p "$DATA"
fail() { echo "FAIL: $*" >&2; exit 1; }

# With no tag in the state and no IMAGE_TAG, plan and apply stop and say how to get one.
for command in plan apply; do
  out="$("$TF_SH" "$command" 2>&1)" && fail "$command ran without a tag: $out"
  grep -q "deploy.sh" <<<"$out" && grep -q "IMAGE_TAG" <<<"$out" || fail "the message does not say what to do: $out"
done

# The tag the state holds comes after the environment's variables; output takes no variables.
export STATE_TAG=abc1234
out="$("$TF_SH" apply -target=x)"
[[ "$out" == *"apply -var-file=$VARS -var image_tag=abc1234 -target=x" ]] || fail "apply arguments: $out"
out="$("$TF_SH" destroy)"
[[ "$out" == *"destroy -var-file=$VARS -var image_tag=abc1234" ]] || fail "destroy arguments: $out"
out="$("$TF_SH" output -raw url)"
[[ "$out" == *" output -raw url" && "$out" != *var-file* && "$out" != *image_tag* ]] || fail "output arguments: $out"

# IMAGE_TAG, as deploy.sh sets it for its rollout, wins over the state.
out="$(IMAGE_TAG=def5678 "$TF_SH" plan)"
[[ "$out" == *"plan -var-file=$VARS -var image_tag=def5678" ]] || fail "IMAGE_TAG: $out"

echo "tf.sh: ok"
