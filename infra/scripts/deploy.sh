#!/usr/bin/env bash
# Deploys the current commit to the environment of infra/terraform.
#
#   infra/scripts/deploy.sh            build, migrate, roll out, upload the frontend, smoke test
#   SKIP_BUILD=1 infra/scripts/deploy.sh   reuse images already pushed for this commit
#   IMAGES_ONLY=1 infra/scripts/deploy.sh  push the images and stop (first deploy, see README)
#   ENVIRONMENT=staging infra/scripts/deploy.sh   the same into staging (infra/environments/)
#   PROMOTE_FROM=staging infra/scripts/deploy.sh  copy this commit's images from staging instead of
#                                                  building them, so demo runs the bytes staging checked
#
# Order matters: the migration runs with the NEW image before any service runs it, and the old
# tasks keep serving meanwhile, so a schema change must be expand-then-contract (the old code
# must work on the new schema). A failed migration stops here with nothing rolled out. A rollout
# whose tasks fail their health checks is rolled back by ECS's circuit breaker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# ENVIRONMENT (default demo) is read by tf.sh, which picks the state and the variables.
tf() { "$ROOT/infra/scripts/tf.sh" "$@"; }

if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  echo "The working tree has changes: an image is tagged by commit, so it must be the commit." >&2
  exit 1
fi
# Only a commit on main has been reviewed and passed CI; this runs from a laptop, not from CI.
git -C "$ROOT" fetch --quiet origin main
if ! git -C "$ROOT" merge-base --is-ancestor HEAD origin/main; then
  echo "HEAD is not on origin/main: merge it through a pull request first." >&2
  exit 1
fi
TAG="$(git -C "$ROOT" rev-parse --short=7 HEAD)"
REGION="$(tf output -raw region 2>/dev/null || echo "${AWS_REGION:-ap-southeast-1}")"
# The migration and the smoke test need the database, which stack-power.sh down stops. A state
# from before the db_instance output has no paused stack.
DB="$(tf output -raw db_instance 2>/dev/null || true)"
if [[ -n "$DB" ]]; then
  DB_STATUS="$(aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DB" \
    --query 'DBInstances[0].DBInstanceStatus' --output text)"
  if [[ "$DB_STATUS" != available ]]; then
    echo "RDS $DB is $DB_STATUS, not available: run infra/scripts/stack-power.sh up first." >&2
    exit 1
  fi
fi
ECR_JSON="$(tf output -json ecr)"
repo() { jq -r --arg k "$1" '.[$k]' <<<"$ECR_JSON"; }
SERVICES="$(tf output -json services 2>/dev/null | jq -r '.[]' || true)"

echo "== Deploying $TAG to ${ENVIRONMENT:-demo}"

# --- 0. Promotion: the images staging ran are pushed into this environment's repositories and their
# digests compared, so the bytes that reach demo are the ones checked; a rebuild would install
# whatever Debian published since.
if [[ -n "${PROMOTE_FROM:-}" ]]; then
  if [[ "$PROMOTE_FROM" == "${ENVIRONMENT:-demo}" ]]; then
    echo "PROMOTE_FROM is the environment being deployed." >&2
    exit 1
  fi
  SOURCE_ECR="$(ENVIRONMENT="$PROMOTE_FROM" "$ROOT/infra/scripts/tf.sh" output -json ecr)"
  REGISTRY="$(repo backend | cut -d/ -f1)"
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
  digest() { # repository name, without the registry
    aws ecr describe-images --region "$REGION" --repository-name "$1" --image-ids imageTag="$TAG" \
      --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true
  }
  for name in $(jq -r 'keys[]' <<<"$ECR_JSON"); do
    source_repo="$(jq -r --arg k "$name" '.[$k]' <<<"$SOURCE_ECR" | cut -d/ -f2-)"
    target_repo="$(repo "$name" | cut -d/ -f2-)"
    expected="$(digest "$source_repo")"
    if [[ -z "$expected" || "$expected" == None ]]; then
      echo "No $source_repo:$TAG: deploy $TAG to $PROMOTE_FROM first." >&2
      exit 1
    fi
    if [[ "$(digest "$target_repo")" != "$expected" ]]; then
      # Same registry, so the push mounts the layers from the source repository instead of uploading them.
      docker pull "$REGISTRY/$source_repo:$TAG"
      docker tag "$REGISTRY/$source_repo:$TAG" "$REGISTRY/$target_repo:$TAG"
      docker push "$REGISTRY/$target_repo:$TAG"
    fi
    pushed="$(digest "$target_repo")"
    if [[ "$pushed" != "$expected" ]]; then
      echo "$target_repo:$TAG is $pushed, not $expected as in $PROMOTE_FROM." >&2
      exit 1
    fi
    echo "== $name: $TAG promoted from $PROMOTE_FROM ($pushed)"
  done
  SKIP_BUILD=1
fi

# --- 1. Images. Immutable tags: pushing a tag that exists fails, which is what a rebuild of the
# same commit should do; set SKIP_BUILD=1 to reuse it.
if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
  REGISTRY="$(repo backend | cut -d/ -f1)"
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
  build() { # name, context
    # No provenance attestation: with one, buildx pushes an image index, and ECR's scan on push
    # skips an index, so no image was ever scanned.
    docker build --platform linux/amd64 --provenance=false -t "$(repo "$1"):$TAG" "$2"
    docker push "$(repo "$1"):$TAG"
  }
  build backend "$ROOT/backend"
  build gateway "$ROOT/devops/gateway"
fi
if [[ "${IMAGES_ONLY:-0}" == 1 ]]; then
  echo "== Images pushed for $TAG"
  exit 0
fi
CLUSTER="$(tf output -raw cluster)"

# --- 2. Migration, as a one-off task of the new image. The task definition Terraform keeps is
# copied with the image swapped, so nothing is rolled out before the schema is ready.
MIGRATE="$(tf output -json migrate)"
FAMILY="$(jq -r .task_definition <<<"$MIGRATE")"
NEW_DEF="$(aws ecs describe-task-definition --region "$REGION" --task-definition "$FAMILY" --query taskDefinition --output json |
  jq --arg image "$(repo backend):$TAG" '
    .containerDefinitions[0].image = $image
    | {family, taskRoleArn, executionRoleArn, networkMode, containerDefinitions, requiresCompatibilities, cpu, memory, runtimePlatform}
    | with_entries(select(.value != null))')"
MIGRATE_ARN="$(aws ecs register-task-definition --region "$REGION" --cli-input-json "$NEW_DEF" \
  --query taskDefinition.taskDefinitionArn --output text)"
NETWORK="awsvpcConfiguration={subnets=[$(jq -r '.subnets | join(",")' <<<"$MIGRATE")],securityGroups=[$(jq -r .security_group <<<"$MIGRATE")],assignPublicIp=ENABLED}"

echo "== Migrating with $MIGRATE_ARN"
TASK="$(aws ecs run-task --region "$REGION" --cluster "$CLUSTER" --launch-type FARGATE \
  --task-definition "$MIGRATE_ARN" --network-configuration "$NETWORK" --query 'tasks[0].taskArn' --output text)"
aws ecs wait tasks-stopped --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK"
EXIT="$(aws ecs describe-tasks --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK" \
  --query 'tasks[0].containers[0].exitCode' --output text)"
if [[ "$EXIT" != 0 ]]; then
  echo "Migration failed (exit $EXIT); nothing was rolled out. Logs: /ecs/$CLUSTER/migrate" >&2
  exit 1
fi

# --- 3. Roll the services out to the new image.
echo "== Rolling out"
# The apply stores the tag in the state (output image_tag), where tf.sh reads it, so a later infra
# apply keeps this image instead of rolling back. The plan is shown and waits for "yes": it applies
# every other difference too, and a forced replacement of the database would otherwise pass unseen.
if ! IMAGE_TAG="$TAG" tf apply; then
  echo "Not rolled out: the apply was declined or failed." >&2
  exit 1
fi
# On the first deploy the services start before this migration has made the database roles, so
# api fails /ready and the circuit breaker may fail that deployment. With nothing to roll back to,
# it stays failed, and the apply above changes nothing because the tag is the same. Start it again
# now that the schema is ready.
for svc in $SERVICES; do
  STATE="$(aws ecs describe-services --region "$REGION" --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].deployments[?status==`PRIMARY`].rolloutState | [0]' --output text)"
  if [[ "$STATE" == FAILED ]]; then
    echo "== $svc: last deployment failed, starting it again"
    aws ecs update-service --region "$REGION" --cluster "$CLUSTER" --service "$svc" --force-new-deployment >/dev/null
  fi
done
# shellcheck disable=SC2086 # one argument per service
aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" --services $SERVICES

# --- 4. Frontend. Hashed assets are cached for a year; index.html never, so a deploy shows at once.
echo "== Frontend"
BUCKET="$(tf output -raw frontend_bucket)"
(cd "$ROOT/frontend" && npm ci && npm run build)
# No --delete for assets: a tab opened before this deploy still loads its old lazy chunks by name.
# shortcut: old hashed files pile up in the bucket, add an S3 lifecycle rule if that ever matters.
aws s3 sync "$ROOT/frontend/dist/assets" "s3://$BUCKET/assets" --cache-control "public,max-age=31536000,immutable"
aws s3 sync "$ROOT/frontend/dist" "s3://$BUCKET" --delete --exclude "assets/*" --cache-control "no-cache"
aws cloudfront create-invalidation --distribution-id "$(tf output -raw cloudfront_distribution_id)" --paths "/index.html" >/dev/null

# --- 5. Smoke test through the public address: CloudFront, the load balancer, the api, RDS.
URL="$(tf output -raw url)"
for _ in $(seq 1 10); do
  if curl -fsS "$URL/api/ready" >/dev/null; then
    echo "== Deployed $TAG: $URL"
    exit 0
  fi
  sleep 6
done
echo "Deployed, but $URL/api/ready does not answer 200." >&2
exit 1
