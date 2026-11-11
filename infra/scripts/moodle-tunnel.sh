#!/usr/bin/env bash
# A real Moodle for the AWS environment, at no cost: the repository's Moodle (./moodle, 5.2) runs
# on this machine and a Cloudflare quick tunnel gives it a public HTTPS address, so that the
# deployed tool and Moodle reach each other (LTI 1.3 launch, and Names and Role Provisioning,
# where the worker on ECS calls Moodle).
#
#   bash infra/scripts/moodle-tunnel.sh        after infra/scripts/deploy.sh; Ctrl-C to stop
#
# It starts Moodle, sets up a course, a teacher, a student and the LTI tool pointing at the
# deployed address (devops/lti-e2e/setup.php), registers this Moodle in the deployed database
# with a one-off ECS task, prints the logins and then waits.
#
# The address is new on every run, and with it the LTI issuer: accounts and courses made through
# an earlier run's Moodle are not found again. Keep it running for as long as the demo lasts.
# Moodle is throwaway like devops/lti-e2e/run.sh: its own network, Postgres and moodledata on
# tmpfs, the source mounted read-only. Nothing of the compose stack is touched, and everything
# is removed on exit. Passwords are made for each run, since the address is public.
#
#   TOOL_URL=https://...   the tool's address, instead of Terraform's output `url`
#   REGISTER=0             do not register in the deployed database (to try the tunnel alone)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TF_DIR="$ROOT/infra/terraform"
tf() { terraform -chdir="$TF_DIR" "$@"; }

NET=moodle-tunnel
DB=moodle-tunnel-db
MOODLE=moodle-tunnel-moodle
TUNNEL=moodle-tunnel-cloudflared
WORK="$(mktemp -d)"

TOOL_URL="${TOOL_URL:-$(tf output -raw url 2>/dev/null || true)}"
if [[ "$TOOL_URL" != https://* ]]; then
  echo "No deployed address: apply and deploy first (infra/README.md), or set TOOL_URL." >&2
  exit 1
fi

remove_stack() {
  docker rm -f "$TUNNEL" "$MOODLE" "$DB" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
cleanup() {
  remove_stack
  rm -rf "$WORK"
}
trap cleanup EXIT
# A signal ends the script through the EXIT trap, so the clean-up runs once and to the end.
trap 'exit 130' INT TERM
# What a run that was killed outright left behind.
remove_stack

secret() { openssl rand -hex "$1"; }
DB_PW="$(secret 16)"
# Moodle's password policy wants upper and lower case, a digit and a symbol.
ADMIN_PW="Aa1!$(secret 10)"
USER_PW="Aa1!$(secret 6)"

echo "== Tunnel"
docker network create "$NET" >/dev/null
# The tunnel first, since Moodle is installed with the address it gives. No Cloudflare account:
# a quick tunnel is anonymous and its address lasts as long as this container.
docker run -d --name "$TUNNEL" --network "$NET" cloudflare/cloudflared:2026.10.7 \
  tunnel --no-autoupdate --url "http://$MOODLE:80" >/dev/null
MOODLE_URL=
for _ in $(seq 1 30); do
  MOODLE_URL="$(docker logs "$TUNNEL" 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1 || true)"
  [[ -n "$MOODLE_URL" ]] && break
  sleep 2
done
if [[ -z "$MOODLE_URL" ]]; then
  docker logs "$TUNNEL" 2>&1 | tail -20 >&2
  echo "The tunnel gave no address." >&2
  exit 1
fi
echo "$MOODLE_URL"

cat > "$WORK/config.php" <<PHP
<?php
unset(\$CFG);
global \$CFG;
\$CFG = new stdClass();
\$CFG->dbtype = 'pgsql'; \$CFG->dblibrary = 'native'; \$CFG->dbhost = '$DB';
\$CFG->dbname = 'moodle'; \$CFG->dbuser = 'postgres'; \$CFG->dbpass = '$DB_PW'; \$CFG->prefix = 'mdl_';
\$CFG->dboptions = ['dbpersist' => 0, 'dbport' => '5432', 'dbsocket' => ''];
\$CFG->wwwroot = '$MOODLE_URL'; \$CFG->dataroot = '/var/www/moodledata';
// TLS ends at Cloudflare and the tunnel hands Apache plain HTTP.
\$CFG->sslproxy = true;
\$CFG->admin = 'admin'; \$CFG->directorypermissions = 0777;
require_once(__DIR__ . '/lib/setup.php');
PHP
chmod 644 "$WORK/config.php"

echo "== Database"
docker run -d --name "$DB" --network "$NET" -e POSTGRES_PASSWORD="$DB_PW" \
  --tmpfs /var/lib/postgresql/data pgvector/pgvector:pg16 >/dev/null
until docker exec "$DB" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
sleep 2
docker exec "$DB" psql -U postgres -qc "CREATE DATABASE moodle"

echo "== Moodle (installing, a few minutes)"
docker build -q -t do-an-moodle -f "$ROOT/devops/moodle/Dockerfile" "$ROOT" >/dev/null
docker run -d --name "$MOODLE" --network "$NET" \
  -v "$ROOT/moodle:/var/www/html:ro" -v "$WORK/config.php:/var/www/html/config.php:ro" \
  -v "$ROOT/moodle-plugin/local_lms_bridge:/var/www/html/public/local/lms_bridge:ro" \
  -v "$ROOT/devops/lti-e2e:/e2e:ro" \
  --tmpfs /var/www/moodledata:uid=33,gid=33,mode=0777 \
  -e MOODLE_WWWROOT="$MOODLE_URL" -e MOODLE_DB_HOST="$DB" -e MOODLE_DB_PORT=5432 -e MOODLE_DB_NAME=moodle \
  -e MOODLE_DB_USER=postgres -e MOODLE_DB_PASSWORD="$DB_PW" -e MOODLE_SITE_NAME="Microlearning demo" \
  -e MOODLE_ADMIN_USER=admin -e MOODLE_ADMIN_PASSWORD="$ADMIN_PW" -e MOODLE_ADMIN_EMAIL=admin@example.com \
  -e MOODLE_TOKEN="$(secret 16)" do-an-moodle >/dev/null
READY=0
for _ in $(seq 1 90); do
  if [[ "$(docker inspect -f '{{.State.Status}}' "$MOODLE")" != running ]]; then
    docker logs "$MOODLE" 2>&1 | tail -20 >&2
    exit 1
  fi
  # Through the tunnel, so this also waits for the new address to resolve.
  if curl -fs -o /dev/null --max-time 10 "$MOODLE_URL/login/index.php"; then
    READY=1
    break
  fi
  sleep 10
done
if [[ "$READY" != 1 ]]; then
  echo "Moodle does not answer at $MOODLE_URL." >&2
  exit 1
fi

# No TOOL_PUBLIC_KEY: the tool is public, so Moodle fetches its key set from /api/lti/jwks itself.
docker exec -u www-data -e TOOL_URL="$TOOL_URL" -e E2E_USER_PASSWORD="$USER_PW" "$MOODLE" php /e2e/setup.php 2>/dev/null |
  grep '^{' > "$WORK/moodle.json"
j() { jq -r ".$1" "$WORK/moodle.json"; }

if [[ "${REGISTER:-1}" == 1 ]]; then
  echo "== Registering this Moodle in the deployed database"
  REGION="$(tf output -raw region)"
  CLUSTER="$(tf output -raw cluster)"
  MIGRATE="$(tf output -json migrate)"
  NETWORK="awsvpcConfiguration={subnets=[$(jq -r '.subnets | join(",")' <<<"$MIGRATE")],securityGroups=[$(jq -r .security_group <<<"$MIGRATE")],assignPublicIp=ENABLED}"
  # The migrate task holds the owner's database URL, which the script needs (docs/integration/lti.md).
  OVERRIDES="$(jq -n --arg issuer "$(j issuer)" --arg client "$(j client_id)" --arg auth "$(j auth_login_url)" \
    --arg jwks "$(j jwks_url)" --arg token "$(j token_url)" --arg deployment "$(j deployment_id)" '
    {containerOverrides: [{name: "migrate", environment: [{name: "PYTHONPATH", value: "/app"}], command: [
      "/app/.venv/bin/python", "scripts/register_lti_platform.py", "--issuer", $issuer, "--client-id", $client,
      "--auth-login-url", $auth, "--jwks-url", $jwks, "--token-url", $token, "--deployment", $deployment,
      "--links-moodle"]}]}')"
  TASK="$(aws ecs run-task --region "$REGION" --cluster "$CLUSTER" --launch-type FARGATE \
    --task-definition "$(jq -r .task_definition <<<"$MIGRATE")" --network-configuration "$NETWORK" \
    --overrides "$OVERRIDES" --query 'tasks[0].taskArn' --output text)"
  aws ecs wait tasks-stopped --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK"
  EXIT="$(aws ecs describe-tasks --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK" \
    --query 'tasks[0].containers[0].exitCode' --output text)"
  if [[ "$EXIT" != 0 ]]; then
    echo "Registration failed (exit $EXIT). Logs: /ecs/$CLUSTER/migrate" >&2
    exit 1
  fi
fi

cat <<EOF

== Ready
Moodle:   $MOODLE_URL
Tool:     $TOOL_URL
Course:   $MOODLE_URL/course/view.php?id=$(j course_id)   (activity "Microlearning")
Admin:    admin / $ADMIN_PW
Teacher:  e2eteacher / $USER_PW
Student:  e2estudent / $USER_PW

Open the course as the teacher first, then as the student. Ctrl-C stops Moodle and the tunnel.
EOF
# Ends when the tunnel does, so a dead tunnel is not left looking alive.
# In the background: bash runs a trap only between commands, and this one lasts for hours.
docker wait "$TUNNEL" >/dev/null &
wait $!
echo "The tunnel stopped." >&2
exit 1
