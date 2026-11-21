#!/usr/bin/env bash
# A real Moodle for the AWS environment at a fixed address: the repository's Moodle (./moodle, 5.2)
# runs on this machine and an ngrok agent puts it on the account's free static domain, so that the
# deployed tool and Moodle reach each other (LTI 1.3 launch, Names and Role Provisioning, and
# publishing Book and Quiz, where the api and worker on ECS call Moodle).
#
#   bash infra/scripts/moodle-demo.sh          start, or set up the first time (default: up)
#   bash infra/scripts/moodle-demo.sh down     stop; data, address and logins are kept
#   bash infra/scripts/moodle-demo.sh destroy  remove Moodle and its data for good
#
# The address is the LTI issuer, so it must not change: an issuer that changes loses every account
# and course made through the old one. The containers restart with Docker (--restart
# unless-stopped), so closing the terminal or restarting the machine does not stop Moodle once
# Docker Desktop is running again; the data lives in two Docker volumes.
#
# Needs in devops/.env: NGROK_AUTHTOKEN (https://dashboard.ngrok.com/get-started/your-authtoken)
# and NGROK_DOMAIN, the account's free static domain (https://dashboard.ngrok.com/domains), e.g.
# something.ngrok-free.dev. The passwords and the web service token are made on the first run and
# kept in devops/moodle-demo.env (ignored by git), since the address is public.
#
# The first run on an address also registers this Moodle in the deployed database (a one-off ECS
# task), puts the token in SSM and writes the address into infra/terraform/terraform.tfvars; one
# infra/scripts/deploy.sh then hands it to the api and worker. Later runs do none of that.
#
#   TOOL_URL=https://...   the tool's address, instead of Terraform's output `url`
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TF_DIR="$ROOT/infra/terraform"
tf() { terraform -chdir="$TF_DIR" "$@"; }

NET=moodle-demo
DB=moodle-demo-db
MOODLE=moodle-demo-moodle
AGENT=moodle-demo-ngrok
VOLUMES=(moodle-demo-db moodle-demo-data)
STATE="$ROOT/devops/moodle-demo.env"
# Outside the repository's ignored files too: config.php holds the database password.
CONF_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/microlearning-moodle-demo"

case "${1:-up}" in
  down)
    docker stop "$AGENT" "$MOODLE" "$DB" >/dev/null 2>&1 || true
    echo "Stopped. Data and address are kept: run the script again to start."
    exit 0
    ;;
  destroy)
    read -r -p "Remove Moodle, its courses, users and the saved logins for good? [yes/N] " answer
    [[ "$answer" == yes ]] || exit 1
    docker rm -f "$AGENT" "$MOODLE" "$DB" >/dev/null 2>&1 || true
    docker volume rm "${VOLUMES[@]}" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    rm -rf "$STATE" "$CONF_DIR"
    echo "Removed. The deployed database still lists this Moodle as an LTI platform."
    exit 0
    ;;
  up) ;;
  *) echo "usage: $0 [up|down|destroy]" >&2; exit 2 ;;
esac

get_env() { grep -E "^$1=" "$ROOT/devops/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true; }
NGROK_AUTHTOKEN="$(get_env NGROK_AUTHTOKEN)"
NGROK_DOMAIN="$(get_env NGROK_DOMAIN)"
NGROK_DOMAIN="${NGROK_DOMAIN#https://}"
if [[ -z "$NGROK_AUTHTOKEN" || -z "$NGROK_DOMAIN" ]]; then
  echo "Set NGROK_AUTHTOKEN and NGROK_DOMAIN in devops/.env (see the top of this script)." >&2
  exit 1
fi
MOODLE_URL="https://$NGROK_DOMAIN"

TOOL_URL="${TOOL_URL:-$(tf output -raw url 2>/dev/null || true)}"
if [[ "$TOOL_URL" != https://* ]]; then
  echo "No deployed address: apply and deploy first (infra/README.md), or set TOOL_URL." >&2
  exit 1
fi

# The quick-tunnel Moodle this replaces, if one is still running.
docker rm -f moodle-tunnel-cloudflared moodle-tunnel-moodle moodle-tunnel-db >/dev/null 2>&1 || true

secret() { openssl rand -hex "$1"; }
if [[ ! -f "$STATE" ]]; then
  umask 077
  # Moodle's password policy wants upper and lower case, a digit and a symbol.
  cat > "$STATE" <<EOF
DB_PW=$(secret 16)
ADMIN_PW=Aa1!$(secret 10)
USER_PW=Aa1!$(secret 6)
WS_TOKEN=$(secret 16)
REGISTERED_URL=
EOF
fi
# shellcheck source=/dev/null
source "$STATE"

mkdir -p "$CONF_DIR"
cat > "$CONF_DIR/config.php" <<PHP
<?php
unset(\$CFG);
global \$CFG;
\$CFG = new stdClass();
\$CFG->dbtype = 'pgsql'; \$CFG->dblibrary = 'native'; \$CFG->dbhost = '$DB';
\$CFG->dbname = 'moodle'; \$CFG->dbuser = 'postgres'; \$CFG->dbpass = '$DB_PW'; \$CFG->prefix = 'mdl_';
\$CFG->dboptions = ['dbpersist' => 0, 'dbport' => '5432', 'dbsocket' => ''];
\$CFG->wwwroot = '$MOODLE_URL'; \$CFG->dataroot = '/var/www/moodledata';
// TLS ends at ngrok, which hands Apache plain HTTP.
\$CFG->sslproxy = true;
\$CFG->admin = 'admin'; \$CFG->directorypermissions = 0777;
require_once(__DIR__ . '/lib/setup.php');
PHP
chmod 644 "$CONF_DIR/config.php"

exists() { docker container inspect "$1" >/dev/null 2>&1; }
# Runs a container the first time; later only starts it, so its settings and data stay as they were.
up() {
  local name="$1"; shift
  if exists "$name"; then docker start "$name" >/dev/null; else docker run -d --name "$name" --restart unless-stopped "$@" >/dev/null; fi
}

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

echo "== Database"
up "$DB" --network "$NET" -e POSTGRES_PASSWORD="$DB_PW" -v moodle-demo-db:/var/lib/postgresql/data pgvector/pgvector:pg16
until docker exec "$DB" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
sleep 2
docker exec "$DB" psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'moodle'" | grep -q 1 ||
  docker exec "$DB" psql -U postgres -qc "CREATE DATABASE moodle"

echo "== ngrok ($MOODLE_URL)"
# Free accounts show a one-time warning page to browsers; the calls from AWS are not browsers.
up "$AGENT" --network "$NET" -e NGROK_AUTHTOKEN="$NGROK_AUTHTOKEN" ngrok/ngrok:3.39.13-debian \
  http --log stdout --url "$MOODLE_URL" "http://$MOODLE:80"

echo "== Moodle (the first install takes a few minutes)"
docker build -q -t do-an-moodle -f "$ROOT/devops/moodle/Dockerfile" "$ROOT" >/dev/null
up "$MOODLE" --network "$NET" \
  -v "$ROOT/moodle:/var/www/html:ro" -v "$CONF_DIR/config.php:/var/www/html/config.php:ro" \
  -v "$ROOT/moodle-plugin/local_lms_bridge:/var/www/html/public/local/lms_bridge:ro" \
  -v "$ROOT/devops/lti-e2e:/e2e:ro" -v moodle-demo-data:/var/www/moodledata \
  -e MOODLE_WWWROOT="$MOODLE_URL" -e MOODLE_DB_HOST="$DB" -e MOODLE_DB_PORT=5432 -e MOODLE_DB_NAME=moodle \
  -e MOODLE_DB_USER=postgres -e MOODLE_DB_PASSWORD="$DB_PW" -e MOODLE_SITE_NAME="Microlearning demo" \
  -e MOODLE_ADMIN_USER=admin -e MOODLE_ADMIN_PASSWORD="$ADMIN_PW" -e MOODLE_ADMIN_EMAIL=admin@example.com \
  -e MOODLE_TOKEN="$WS_TOKEN" do-an-moodle
READY=0
for _ in $(seq 1 90); do
  if [[ "$(docker inspect -f '{{.State.Status}}' "$MOODLE")" != running ]]; then
    docker logs "$MOODLE" 2>&1 | tail -20 >&2
    exit 1
  fi
  # Through ngrok, so this also checks the agent and the domain.
  if curl -fs -o /dev/null --max-time 10 -H "ngrok-skip-browser-warning: 1" "$MOODLE_URL/login/index.php"; then
    READY=1
    break
  fi
  sleep 10
done
if [[ "$READY" != 1 ]]; then
  docker logs "$AGENT" 2>&1 | tail -10 >&2
  echo "Moodle does not answer at $MOODLE_URL." >&2
  exit 1
fi

# Finds what an earlier run made, so it is the same course, users and LTI tool every time.
# No TOOL_PUBLIC_KEY: the tool is public, so Moodle fetches its key set from /api/lti/jwks itself.
docker exec -u www-data -e TOOL_URL="$TOOL_URL" -e E2E_USER_PASSWORD="$USER_PW" "$MOODLE" php /e2e/setup.php 2>/dev/null |
  grep '^{' > "$CONF_DIR/moodle.json"
j() { jq -r ".$1" "$CONF_DIR/moodle.json"; }

if [[ "$REGISTERED_URL" != "$MOODLE_URL" ]]; then
  echo "== First run on this address: registering it with the deployment"
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
  # The token the backend publishes with; the api and worker read it when they start.
  aws ssm put-parameter --region "$REGION" --overwrite --type SecureString \
    --name "/$CLUSTER/moodle-token" --value "$WS_TOKEN" >/dev/null
  # Where the backend reaches Moodle, for every later apply and deploy of the demo.
  TFVARS="$TF_DIR/terraform.tfvars"
  touch "$TFVARS"
  sed -i '/^moodle_base_url\s*=/d; /^moodle_wwwroot\s*=/d' "$TFVARS"
  printf 'moodle_base_url = "%s"\nmoodle_wwwroot  = "%s"\n' "$MOODLE_URL" "$MOODLE_URL" >> "$TFVARS"
  sed -i "s#^REGISTERED_URL=.*#REGISTERED_URL=$MOODLE_URL#" "$STATE"
  NEXT="Run infra/scripts/deploy.sh once, so the api and worker get this address and the token."
else
  NEXT="Already registered with the deployment: nothing else to do."
fi

cat <<EOF

== Ready
Moodle:   $MOODLE_URL
Tool:     $TOOL_URL
Course:   $MOODLE_URL/course/view.php?id=$(j course_id)   (activity "Microlearning")
Logins:   admin, e2eteacher, e2estudent; passwords in devops/moodle-demo.env (ADMIN_PW, USER_PW)

$NEXT
Open the course as the teacher first, then as the student. It keeps running after this script ends.
EOF
