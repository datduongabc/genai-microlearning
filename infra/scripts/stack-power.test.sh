#!/usr/bin/env bash
# Checks the order of stack-power.sh's AWS calls against a fake terraform, aws and curl: no AWS.
#   bash infra/scripts/stack-power.test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/infra/scripts/stack-power.sh"
FAKE="$(mktemp -d)"
trap 'rm -rf "$FAKE"' EXIT
export LOG="$FAKE/calls" ENVIRONMENT=demo
unset IMAGE_TAG

# The outputs of a stack with api and worker scalable and gateway not; NO_OUTPUTS is a state from
# before stack-power.sh existed.
cat >"$FAKE/terraform" <<'FAKE'
#!/bin/sh
case "$*" in
  *"output -raw region") echo ap-southeast-1 ;;
  *"output -raw url") echo https://d.example ;;
  *"output -raw cluster") echo c ;;
  *"output -raw db_instance") [ -z "$NO_OUTPUTS" ] && echo db || exit 1 ;;
  *"output -json service_counts") [ -z "$NO_OUTPUTS" ] && echo '{"api":1,"gateway":2,"worker":1}' || exit 1 ;;
  *) echo "unexpected terraform $*" >&2; exit 1 ;;
esac
FAKE
# Logs every call; the gateway has no scalable target, the others a minimum of 3.
cat >"$FAKE/aws" <<'FAKE'
#!/bin/sh
echo "aws $*" >>"$LOG"
case "$*" in
  *describe-scalable-targets*gateway*) echo None ;;
  *describe-scalable-targets*) echo 3 ;;
  *describe-db-instances*) echo "$DB_STATUS" ;;
  *"wait services-stable"*) exit "${STABLE_EXIT:-0}" ;;
esac
FAKE
cat >"$FAKE/curl" <<'FAKE'
#!/bin/sh
echo "curl $*" >>"$LOG"
FAKE
chmod +x "$FAKE/terraform" "$FAKE/aws" "$FAKE/curl"
export PATH="$FAKE:$PATH"
fail() { echo "FAIL: $*" >&2; cat "$LOG" >&2; exit 1; }
calls() { grep -E "$1" "$LOG" | grep -vE 'describe-(scalable-targets|db-instances)' | sed -E 's/ --region ap-southeast-1//' || true; }
run() { : >"$LOG"; "$SCRIPT" "$@"; }

# down: scaling suspended before each count goes to 0, the database stopped only after the tasks are gone.
DB_STATUS=available run down >/dev/null
expected="aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount --resource-id service/c/api --suspended-state DynamicScalingInSuspended=true,DynamicScalingOutSuspended=true,ScheduledScalingSuspended=true
aws ecs update-service --cluster c --service api --desired-count 0
aws ecs update-service --cluster c --service gateway --desired-count 0
aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount --resource-id service/c/worker --suspended-state DynamicScalingInSuspended=true,DynamicScalingOutSuspended=true,ScheduledScalingSuspended=true
aws ecs update-service --cluster c --service worker --desired-count 0
aws ecs wait services-stable --cluster c --services api gateway worker
aws rds stop-db-instance --db-instance-identifier db"
[[ "$(calls .)" == "$expected" ]] || fail "down: $(calls .)"

# down while a worker still finishes a job: the database keeps running.
DB_STATUS=available STABLE_EXIT=255 run down >/dev/null 2>&1 && fail "down passed with tasks still running"
[[ -z "$(calls stop-db-instance)" ]] || fail "the database was stopped under a running job"

# up: the database available before any task, each scalable service back at its minimum and its
# scaling resumed, the gateway at its own count, then the services stable and /api/ready checked.
DB_STATUS=stopped run up >/dev/null
expected="aws rds start-db-instance --db-instance-identifier db
aws rds wait db-instance-available --db-instance-identifier db
aws ecs update-service --cluster c --service api --desired-count 3
aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount --resource-id service/c/api --suspended-state DynamicScalingInSuspended=false,DynamicScalingOutSuspended=false,ScheduledScalingSuspended=false
aws ecs update-service --cluster c --service gateway --desired-count 2
aws ecs update-service --cluster c --service worker --desired-count 3
aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount --resource-id service/c/worker --suspended-state DynamicScalingInSuspended=false,DynamicScalingOutSuspended=false,ScheduledScalingSuspended=false
aws ecs wait services-stable --cluster c --services api gateway worker
curl -fsS https://d.example/api/ready"
[[ "$(calls .)" == "$expected" ]] || fail "up: $(calls .)"

# up on a database that is still stopping: nothing started.
DB_STATUS=stopping run up >/dev/null 2>&1 && fail "up passed on a stopping database"
[[ -z "$(calls 'start-db-instance|update-service')" ]] || fail "up started something on a stopping database"

# A state without the new outputs says what to do, before any AWS call.
out="$(NO_OUTPUTS=1 run down 2>&1)" && fail "down ran without the outputs"
grep -q "tf.sh apply" <<<"$out" || fail "the message does not say what to do: $out"
[[ ! -s "$LOG" ]] || fail "AWS was called without the outputs"

run reboot >/dev/null 2>&1 && fail "an unknown command passed"

echo "stack-power.sh: ok"
