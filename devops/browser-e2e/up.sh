#!/usr/bin/env bash
# The app in a real browser, through a real Moodle 5.2 LTI 1.3 launch:
#   bash devops/browser-e2e/up.sh     start everything (Moodle's install takes a few minutes)
#   devops/browser-e2e/drive.py    play a teacher or a student, see its docstring
#   bash devops/browser-e2e/down.sh   stop everything
#
# Throwaway: Postgres on tmpfs for both Moodle and the backend, moodledata on tmpfs, no volume, and
# nothing of the compose stack (do-an_db_data, ./moodle/config.php) is touched. Nothing paid is
# called: the LLM gateway is devops/loadtest/mock_gateway.py, every provider key is blank, and the
# gateway and embedder keys are placeholders the mocks do not check. The reranker is left out, so
# chat keeps the hybrid order. About 5.5 GB of RAM, most of it the CPU embedder.
#
# Runtime files (logs, screenshots, browser sessions, Moodle's ids) go to $BE2E_WORK.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
WORK="${BE2E_WORK:-${TMPDIR:-/tmp}/browser-e2e}"
# 58000, the port of devops/lti-e2e/run.sh, could not be bound on the WSL machine this was written on.
TOOL_PORT="${TOOL_PORT:-58010}"
# Moodle's source is not in git (see .gitignore); a worktree points at the main checkout's copy.
MOODLE_SRC="${MOODLE_SRC:-$ROOT/moodle}"
# The compose embedder: the public TEI image, with the model cached in compose's volume (read only).
EMBEDDER_IMAGE="${EMBEDDER_IMAGE:-ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4}"
EMBEDDER_MODELS_VOLUME="${EMBEDDER_MODELS_VOLUME:-do-an_embedder_models}"
DB_PW=e2e-only-password
NET=be2e
mkdir -p "$WORK/logs" "$WORK/uploads"
rm -f "$WORK"/state-*.json "$WORK"/url-*.txt "$WORK/pids"

bash "$HERE/down.sh" >/dev/null 2>&1 || true

cat > "$WORK/config.php" <<PHP
<?php
unset(\$CFG);
global \$CFG;
\$CFG = new stdClass();
\$CFG->dbtype = 'pgsql'; \$CFG->dblibrary = 'native'; \$CFG->dbhost = 'be2e-db';
\$CFG->dbname = 'moodle'; \$CFG->dbuser = 'postgres'; \$CFG->dbpass = '$DB_PW'; \$CFG->prefix = 'mdl_';
\$CFG->dboptions = ['dbpersist' => 0, 'dbport' => '5432', 'dbsocket' => ''];
\$CFG->wwwroot = 'http://localhost:58081'; \$CFG->dataroot = '/var/www/moodledata';
\$CFG->admin = 'admin'; \$CFG->directorypermissions = 0777;
require_once(__DIR__ . '/lib/setup.php');
PHP
chmod 644 "$WORK/config.php"

echo "== Database, embedder"
docker network create "$NET" >/dev/null
docker run -d --rm --name be2e-db --network "$NET" -p 55434:5432 -e POSTGRES_PASSWORD=$DB_PW \
  --tmpfs /var/lib/postgresql/data pgvector/pgvector:pg16 >/dev/null
docker run -d --rm --name be2e-embedder -p 58082:80 --memory 3g -v "$EMBEDDER_MODELS_VOLUME:/data:ro" -e HF_HUB_OFFLINE=1 \
  "$EMBEDDER_IMAGE" --model-id onnx-community/Qwen3-Embedding-0.6B-ONNX --revision c25a394dd583836952667c12f008335071b3f43d \
  --pooling last-token --max-batch-tokens 1024 --max-client-batch-size 128 --api-key mock-not-a-real-key >/dev/null
until docker exec be2e-db pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
sleep 2
docker exec be2e-db psql -U postgres -qc "CREATE DATABASE moodle" -c "CREATE DATABASE microlearning"
docker exec be2e-db psql -U postgres -d microlearning -qc "CREATE EXTENSION vector"

echo "== Moodle (installing, a few minutes)"
docker build -q -t do-an-moodle -f "$ROOT/devops/moodle/Dockerfile" "$ROOT" >/dev/null
docker run -d --rm --name be2e-moodle --network "$NET" -p 58081:80 \
  -v "$MOODLE_SRC:/var/www/html:ro" -v "$WORK/config.php:/var/www/html/config.php:ro" \
  -v "$ROOT/moodle-plugin/local_lms_bridge:/var/www/html/public/local/lms_bridge:ro" -v "$ROOT/devops/lti-e2e:/e2e:ro" \
  --tmpfs /var/www/moodledata:uid=33,gid=33,mode=0777 \
  -e MOODLE_WWWROOT=http://localhost:58081 -e MOODLE_DB_HOST=be2e-db -e MOODLE_DB_PORT=5432 -e MOODLE_DB_NAME=moodle \
  -e MOODLE_DB_USER=postgres -e MOODLE_DB_PASSWORD=$DB_PW -e MOODLE_SITE_NAME="Browser e2e" -e MOODLE_ADMIN_USER=admin \
  -e 'MOODLE_ADMIN_PASSWORD=E2e-admin-pass-1!' -e MOODLE_ADMIN_EMAIL=admin@example.com \
  -e MOODLE_TOKEN=e2etoken0000000000000000000000ab do-an-moodle >/dev/null
for _ in $(seq 1 90); do
  if [[ "$(docker inspect -f '{{.State.Status}}' be2e-moodle 2>/dev/null)" != running ]]; then
    echo "Moodle stopped" >&2; exit 1
  fi
  curl -fs -o /dev/null localhost:58081/login/index.php && break
  sleep 10
done
docker exec -u www-data -e TOOL_URL="http://localhost:$TOOL_PORT" be2e-moodle php /e2e/setup.php 2>/dev/null \
  | grep '^{' > "$WORK/moodle.json"
cat "$WORK/moodle.json"

echo "== Backend, mock gateway, frontend"
export DATABASE_URL=postgresql+psycopg://postgres:$DB_PW@localhost:55434/microlearning
export APP_RUNTIME_DATABASE_URL=postgresql+psycopg://app_runtime:e2e-runtime-pw@localhost:55434/microlearning
export APP_WORKER_DATABASE_URL=postgresql+psycopg://app_worker:e2e-worker-pw@localhost:55434/microlearning
export SECRET_KEY=e2e-secret-key-not-for-production-000000 PUBLIC_URL="http://localhost:$TOOL_PORT" RUN_JOB_WORKER=true
export UPLOAD_STORAGE_DIR="$WORK/uploads"
export LLM_GATEWAY_URL=http://localhost:58004 EMBEDDER_URL=http://localhost:58082 FORMULA_VISION_MODEL=''
export RERANKER_PROVIDER=jina RERANKER_URL=http://localhost:58099
# Blank, so that devops/.env's real keys are never used by this run; placeholders where the mocks want one.
export OPENROUTER_API_KEY='' TYPESAFE_API_KEY='' MOODLE_TOKEN=''
export LLM_GATEWAY_KEY=mock-not-a-real-key EMBEDDER_API_KEY=mock-not-a-real-key
export MOCK_LLM_MEDIAN_SECONDS=1 MOCK_COMPLETION_TOKENS=200
cd "$ROOT/backend"
uv run alembic upgrade head >/dev/null
j() { jq -r ".$1" "$WORK/moodle.json"; }
PYTHONPATH=. uv run python scripts/register_lti_platform.py --issuer "$(j issuer)" --client-id "$(j client_id)" \
  --auth-login-url "$(j auth_login_url)" --jwks-url "$(j jwks_url)" --deployment "$(j deployment_id)" --links-moodle
nohup uv run uvicorn --app-dir ../devops/loadtest mock_gateway:app --port 58004 >"$WORK/logs/gateway.log" 2>&1 &
echo $! >>"$WORK/pids"
nohup uv run uvicorn app.main:app --port 58001 >"$WORK/logs/api.log" 2>&1 &
echo $! >>"$WORK/pids"
cd "$ROOT/frontend"
BACKEND_URL=http://localhost:58001 nohup node node_modules/vite/bin/vite.js --port "$TOOL_PORT" --strictPort \
  >"$WORK/logs/vite.log" 2>&1 &
echo $! >>"$WORK/pids"
for _ in $(seq 1 60); do
  curl -fs -o /dev/null "localhost:$TOOL_PORT/api/ready" && curl -fs -o /dev/null localhost:58082/health && break
  sleep 2
done
# On the WSL machine this was written on, a process started from a detached shell has at times been
# put on a random port instead of the one it asked for, without any error. Say so rather than wait.
if ! curl -fs -o /dev/null "localhost:$TOOL_PORT/api/ready"; then
  echo "the tool does not answer on $TOOL_PORT; vite says:" >&2
  grep -m1 Local "$WORK/logs/vite.log" >&2 || tail -n 20 "$WORK/logs/vite.log" >&2
  echo "start vite again from a terminal, or pick another TOOL_PORT" >&2
  exit 1
fi
echo "== Ready: Moodle http://localhost:58081 (e2eteacher / e2estudent, E2e-user-pass-1!), tool http://localhost:$TOOL_PORT"
echo "   runtime files in $WORK"
