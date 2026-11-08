#!/usr/bin/env bash
# LTI 1.3 launch against a real Moodle (the repository's ./moodle, 5.2), end to end, and Names and
# Role Provisioning: a student taken out of the Moodle course is taken out of the course here.
#   bash devops/lti-e2e/run.sh
#
# Throwaway and isolated: its own network, a Postgres on tmpfs for both Moodle and the backend,
# Moodle's source mounted read-only with a config.php of its own, moodledata on tmpfs. No volume
# is created, and nothing of the compose stack (do-an_db_data, ./moodle/config.php) is touched.
# Everything is removed on exit. Needs about 1 GB of RAM and a few minutes for Moodle's install.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
WORK="$(mktemp -d)"
DB_PW=e2e-only-password
NET=lti-e2e
API_PID=

cleanup() {
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  docker rm -f lti-e2e-moodle lti-e2e-db >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

cat > "$WORK/config.php" <<PHP
<?php
unset(\$CFG);
global \$CFG;
\$CFG = new stdClass();
\$CFG->dbtype = 'pgsql'; \$CFG->dblibrary = 'native'; \$CFG->dbhost = 'lti-e2e-db';
\$CFG->dbname = 'moodle'; \$CFG->dbuser = 'postgres'; \$CFG->dbpass = '$DB_PW'; \$CFG->prefix = 'mdl_';
\$CFG->dboptions = ['dbpersist' => 0, 'dbport' => '5432', 'dbsocket' => ''];
\$CFG->wwwroot = 'http://localhost:58081'; \$CFG->dataroot = '/var/www/moodledata';
\$CFG->admin = 'admin'; \$CFG->directorypermissions = 0777;
require_once(__DIR__ . '/lib/setup.php');
PHP
chmod 644 "$WORK/config.php"
# The tool's key for this run only: the backend signs with it, Moodle checks with its public half.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$WORK/tool.pem" 2>/dev/null
openssl pkey -in "$WORK/tool.pem" -pubout -out "$WORK/tool.pub"

echo "== Database"
docker network create "$NET" >/dev/null
docker run -d --rm --name lti-e2e-db --network "$NET" -p 55434:5432 -e POSTGRES_PASSWORD=$DB_PW \
  --tmpfs /var/lib/postgresql/data pgvector/pgvector:pg16 >/dev/null
until docker exec lti-e2e-db pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
sleep 2
docker exec lti-e2e-db psql -U postgres -qc "CREATE DATABASE moodle" -c "CREATE DATABASE microlearning"
docker exec lti-e2e-db psql -U postgres -d microlearning -qc "CREATE EXTENSION vector"

echo "== Moodle (installing, a few minutes)"
docker build -q -t do-an-moodle -f "$ROOT/devops/moodle/Dockerfile" "$ROOT" >/dev/null
docker run -d --name lti-e2e-moodle --network "$NET" -p 58081:80 \
  -v "$ROOT/moodle:/var/www/html:ro" -v "$WORK/config.php:/var/www/html/config.php:ro" \
  -v "$ROOT/moodle-plugin/local_lms_bridge:/var/www/html/public/local/lms_bridge:ro" -v "$HERE:/e2e:ro" \
  --tmpfs /var/www/moodledata:uid=33,gid=33,mode=0777 \
  -e MOODLE_WWWROOT=http://localhost:58081 -e MOODLE_DB_HOST=lti-e2e-db -e MOODLE_DB_PORT=5432 -e MOODLE_DB_NAME=moodle \
  -e MOODLE_DB_USER=postgres -e MOODLE_DB_PASSWORD=$DB_PW -e MOODLE_SITE_NAME="LTI e2e" -e MOODLE_ADMIN_USER=admin \
  -e 'MOODLE_ADMIN_PASSWORD=E2e-admin-pass-1!' -e MOODLE_ADMIN_EMAIL=admin@example.com \
  -e MOODLE_TOKEN=e2etoken0000000000000000000000ab do-an-moodle >/dev/null
for _ in $(seq 1 90); do
  if [[ "$(docker inspect -f '{{.State.Status}}' lti-e2e-moodle)" != running ]]; then
    docker logs lti-e2e-moodle 2>&1 | tail -20; exit 1
  fi
  curl -fs -o /dev/null localhost:58081/login/index.php && break
  sleep 10
done
docker exec -u www-data -e TOOL_URL=http://localhost:58000 -e TOOL_PUBLIC_KEY="$(cat "$WORK/tool.pub")" lti-e2e-moodle php /e2e/setup.php 2>/dev/null | grep '^{' > "$WORK/moodle.json"
cat "$WORK/moodle.json"

echo "== Backend"
export DATABASE_URL=postgresql+psycopg://postgres:$DB_PW@localhost:55434/microlearning
export APP_RUNTIME_DATABASE_URL=postgresql+psycopg://app_runtime:e2e-runtime-pw@localhost:55434/microlearning
export APP_WORKER_DATABASE_URL=postgresql+psycopg://app_worker:e2e-worker-pw@localhost:55434/microlearning
export SECRET_KEY=e2e-secret-key-not-for-production-000000 PUBLIC_URL=http://localhost:58000 RUN_JOB_WORKER=false
LTI_PRIVATE_KEY="$(cat "$WORK/tool.pem")"
export LTI_PRIVATE_KEY
# Blank, so that devops/.env's real keys are never used by this run.
export GEMINI_API_KEY='' OPENROUTER_API_KEY='' GROQ_API_KEY='' TYPESAFE_API_KEY='' LLM_GATEWAY_KEY='' EMBEDDER_API_KEY='' MOODLE_TOKEN=''
cd "$ROOT/backend"
uv run alembic upgrade head >/dev/null
j() { jq -r ".$1" "$WORK/moodle.json"; }
PYTHONPATH=. uv run python scripts/register_lti_platform.py --issuer "$(j issuer)" --client-id "$(j client_id)" \
  --auth-login-url "$(j auth_login_url)" --jwks-url "$(j jwks_url)" --token-url "$(j token_url)" \
  --deployment "$(j deployment_id)" --links-moodle
PYTHONPATH=".:$HERE" uv run uvicorn asgi:app --port 58000 >"$WORK/api.log" 2>&1 &
API_PID=$!
until curl -fs -o /dev/null localhost:58000/api/ready; do sleep 1; done

browse() { uv run python "$HERE/browser.py" "$WORK/moodle.json" "$1"; }
echo "== Student before the teacher opened the tool: refused"
if browse e2estudent; then
  echo "a student got in before any teacher had opened the tool" >&2; exit 1
fi
echo "== Teacher launch"
TEACHER="$(browse e2eteacher | tee >(cat >&2) | tail -1)"
echo "== Teacher again: same account and course"
browse e2eteacher | tail -1
echo "== Student launch: a member of the teacher's course, chat only"
STUDENT="$(browse e2estudent | tee >(cat >&2) | tail -1)"
jq -e --argjson t "$TEACHER" '.user.can_author == false and .course_role == "member" and .course_id == $t.course_id and .courses == []
  and .chat_latest == 200 and .documents == 404' <<<"$STUDENT" >/dev/null || { echo "student check failed" >&2; exit 1; }
jq -e '.user.can_author == true and .course_role == "owner" and .chat_latest == 200 and .documents == 200' <<<"$TEACHER" >/dev/null \
  || { echo "teacher check failed" >&2; exit 1; }
echo "== Stored"
docker exec lti-e2e-db psql -U postgres -d microlearning -tAc \
  "select 'users ' || count(*) || ', moodle_user_id ' || string_agg(moodle_user_id::text, ',') from users" -c \
  "select 'courses ' || count(*) || ', moodle_course_id ' || string_agg(moodle_course_id::text, ',') from courses" -c \
  "select 'course_members ' || count(*) from course_members"
echo "== Expected moodle ids: teacher $(j teacher_id), course $(j course_id)"

stored_url="$(docker exec lti-e2e-db psql -U postgres -d microlearning -tAc "select memberships_url from lti_contexts")"
[[ "$stored_url" == "$(j issuer)/mod/lti/services.php/"* ]] || { echo "no member list address stored: $stored_url" >&2; exit 1; }
# One roster check, as if it ran $1 days from now, so that every list is due.
sync() {
  PYTHONPATH=. uv run python -c 'import json, sys; from datetime import UTC, datetime, timedelta
from app.lti.roster import sync_rosters
print(json.dumps(sync_rosters(datetime.now(UTC) + timedelta(days=int(sys.argv[1])))))' "$1"
}
members() { docker exec lti-e2e-db psql -U postgres -d microlearning -tAc "select count(*) from course_members"; }

echo "== Roster check while the student is enrolled: nobody removed"
SYNC="$(sync 1)"; echo "$SYNC"
jq -e '.courses == 1 and .removed == 0 and .skipped == 0' <<<"$SYNC" >/dev/null && [[ "$(members)" == 1 ]] \
  || { echo "an enrolled student was removed, or the list was not read" >&2; exit 1; }

echo "== Student taken out of the Moodle course: out of the course here at the next roster check"
docker exec -u www-data lti-e2e-moodle php /e2e/unenrol.php e2estudent
SYNC="$(sync 2)"; echo "$SYNC"
jq -e '.removed == 1 and .skipped == 0' <<<"$SYNC" >/dev/null && [[ "$(members)" == 0 ]] \
  || { echo "the roster check did not remove the student" >&2; exit 1; }
echo "== Teacher still owns the course after the check"
browse e2eteacher | tail -1 | jq -e '.course_role == "owner" and .documents == 200' >/dev/null || { echo "teacher lost the course" >&2; exit 1; }
echo "== All checks passed"
