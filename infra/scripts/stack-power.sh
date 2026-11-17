#!/usr/bin/env bash
# Pauses the environment of infra/terraform while nobody uses it, and starts it again.
#
#   infra/scripts/stack-power.sh down     every ECS service to 0 tasks, scaling suspended, RDS stopped
#   infra/scripts/stack-power.sh up       RDS started, services back at their minimum, /api/ready checked
#   infra/scripts/stack-power.sh status   tasks running/desired per service, and the RDS state
#   ENVIRONMENT=staging infra/scripts/stack-power.sh down   the same for staging (read by tf.sh)
#
# What stays billed while paused: the load balancer, Valkey, the public IPv4 addresses of the load
# balancer, RDS storage and S3. `up` takes about 10 to 15 minutes, most of it RDS starting.
#
# RDS starts itself again after 7 days stopped, and bills from then on: run `down` again within
# the week. `down` on a stack whose services are already at 0 only stops the database.
#
# Terraform while paused:
# - The ECS services ignore desired_count (ecs.tf), and the scalable targets ignore suspended_state
#   (autoscaling.tf), so a `tf.sh apply` neither starts tasks nor resumes scaling.
# - RDS has no running or stopped attribute, so a stopped instance shows no drift, but AWS refuses
#   to modify a stopped instance: an apply that changes the database fails.
# - An apply that changes a scalable target's min or max may start that service's tasks, which
#   then fail against the stopped database.
# So run `up` before any `tf.sh apply`. deploy.sh refuses to run while the database is not available:
# its migration and its smoke test need it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tf() { "$ROOT/infra/scripts/tf.sh" "$@"; }
output() { # name; outputs this script needs that an older state may not have yet
  tf output "$@" 2>/dev/null || {
    echo "The state of ${ENVIRONMENT:-demo} has no output ${*: -1}: run infra/scripts/tf.sh apply once." >&2
    exit 1
  }
}

command="${1:-}"
case "$command" in
  down | up | status) ;;
  *) echo "Usage: $0 down|up|status" >&2; exit 1 ;;
esac

REGION="$(tf output -raw region 2>/dev/null || echo "${AWS_REGION:-ap-southeast-1}")"
CLUSTER="$(output -raw cluster)"
DB="$(output -raw db_instance)"
COUNTS="$(output -json service_counts)"
mapfile -t SERVICES < <(jq -r 'keys[]' <<<"$COUNTS")

db_status() {
  aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DB" \
    --query 'DBInstances[0].DBInstanceStatus' --output text
}
# The minimum of the service's scalable target, or None for a service without one (gateway).
min_capacity() {
  aws application-autoscaling describe-scalable-targets --region "$REGION" --service-namespace ecs \
    --resource-ids "service/$CLUSTER/$1" --query 'ScalableTargets[0].MinCapacity' --output text
}
# Suspends (true) or resumes (false) every kind of scaling; min and max stay as Terraform set them.
suspend_scaling() { # service, true|false
  aws application-autoscaling register-scalable-target --region "$REGION" --service-namespace ecs \
    --scalable-dimension ecs:service:DesiredCount --resource-id "service/$CLUSTER/$1" \
    --suspended-state "DynamicScalingInSuspended=$2,DynamicScalingOutSuspended=$2,ScheduledScalingSuspended=$2"
}
set_count() { # service, count
  aws ecs update-service --region "$REGION" --cluster "$CLUSTER" --service "$1" --desired-count "$2" >/dev/null
}

case "$command" in
  status)
    aws ecs describe-services --region "$REGION" --cluster "$CLUSTER" --services "${SERVICES[@]}" \
      --query 'services[].[serviceName,runningCount,desiredCount]' --output table
    echo "RDS $DB: $(db_status)"
    ;;

  down)
    for svc in "${SERVICES[@]}"; do
      # Suspended first, so no alarm scales the service out again between the two calls.
      if [[ "$(min_capacity "$svc")" != None ]]; then suspend_scaling "$svc" true; fi
      set_count "$svc" 0
      echo "== $svc: 0 tasks"
    done
    # A worker with a job running is protected from being stopped until the job ends, and the job
    # needs the database: stop it only once every task is gone.
    if ! aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" --services "${SERVICES[@]}"; then
      echo "Tasks are still stopping, likely a worker finishing a job. The database is still running:" >&2
      echo "run $0 down again in a few minutes." >&2
      exit 1
    fi
    status="$(db_status)"
    if [[ "$status" == available ]]; then
      aws rds stop-db-instance --region "$REGION" --db-instance-identifier "$DB" >/dev/null
      echo "== RDS $DB: stopping (it starts itself again after 7 days)"
    else
      echo "== RDS $DB: $status, left as it is"
    fi
    ;;

  up)
    status="$(db_status)"
    if [[ "$status" == stopping ]]; then
      # AWS refuses to start an instance until it has stopped.
      echo "RDS $DB is still stopping: run $0 up again once status says stopped." >&2
      exit 1
    elif [[ "$status" == stopped ]]; then
      aws rds start-db-instance --region "$REGION" --db-instance-identifier "$DB" >/dev/null
      echo "== RDS $DB: starting"
    fi
    # Before any task: api fails /ready and worker fails its jobs without the database.
    aws rds wait db-instance-available --region "$REGION" --db-instance-identifier "$DB"
    echo "== RDS $DB: available"
    for svc in "${SERVICES[@]}"; do
      min="$(min_capacity "$svc")"
      if [[ "$min" == None ]]; then
        set_count "$svc" "$(jq -r --arg s "$svc" '.[$s]' <<<"$COUNTS")"
      else
        set_count "$svc" "$min"
        suspend_scaling "$svc" false
      fi
      echo "== $svc: started"
    done
    aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" --services "${SERVICES[@]}"
    URL="$(tf output -raw url)"
    for _ in $(seq 1 10); do
      if curl -fsS "$URL/api/ready" >/dev/null; then
        echo "== Up: $URL"
        exit 0
      fi
      sleep 6
    done
    echo "The services are stable, but $URL/api/ready does not answer 200." >&2
    exit 1
    ;;
esac
