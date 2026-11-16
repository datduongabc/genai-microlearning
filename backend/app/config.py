import logging
import secrets
import threading
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)
_signing_key_lock = threading.Lock()

# Một tệp .env duy nhất cho cả container lẫn các lệnh chạy trên máy thật, và đó là
# chính tệp mà docker-compose.yml đọc. Trước đây có thêm backend/.env chép lại 23
# biến giống hệt, và hai bản đã lệch nhau hai lần, lần nào cũng chỉ lộ ra khi một
# trong hai đường chạy hỏng.
#
# Đường dẫn tuyệt đối tính từ chính tệp này chứ không phải từ thư mục đang đứng, để
# chạy alembic hay script từ đâu cũng ra cùng một tệp. Trong container đường dẫn này
# không tồn tại, pydantic bỏ qua tệp thiếu và mọi giá trị đến từ biến môi trường mà
# compose truyền vào.
_ENV_FILE = Path(__file__).resolve().parents[2] / "devops" / ".env"

MIN_PRODUCTION_SECRET_LENGTH = 32
# Passwords that are published in this repository or in every tutorial.
PUBLIC_DEFAULT_PASSWORDS = {
    "", "postgres", "password", "changeme", "app_runtime_dev_password", "app_worker_dev_password",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # "production" makes the service refuse to start on a configuration that would leave
    # authentication weak (see _production_problems), hides the API description and adds
    # the transport security headers. Development stays as convenient as it was.
    environment: Literal["development", "production"] = "development"

    # repr=False on every credential-bearing field: pytest prints the repr of
    # the objects in a failed assertion and tracebacks/logs may include the
    # settings object, so a default repr would spill passwords and keys.
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/microlearning", repr=False
    )
    # Vai trò RIÊNG (không phải postgres siêu người dùng) cho kết nối phục
    # vụ HTTP request thật -- RLS (mục kiến trúc GD2) chỉ có tác dụng với
    # vai trò không phải chủ bảng/siêu người dùng, xem migration
    # 881bca16e9bc. database_url ở trên chỉ còn dành cho migration, test và
    # script: tiến trình chạy thật không cần và trong production không nên có nó.
    app_runtime_database_url: str = Field(
        default="postgresql+psycopg://app_runtime:app_runtime_dev_password@localhost:5432/microlearning",
        repr=False,
    )
    # The background worker (job_queue.py) processes the jobs of every user, so it is
    # exempt from row level security, but it is a role of its own with grants for the
    # tables it works on and nothing else (migration a4d7e2c9b6f1).
    app_worker_database_url: str = Field(
        default="postgresql+psycopg://app_worker:app_worker_dev_password@localhost:5432/microlearning",
        repr=False,
    )

    # LLM calls go through the LLM gateway (LiteLLM Proxy, devops/gateway/config.yaml),
    # the only place that holds the provider API key (OpenRouter, for every remote model) and
    # maps the model aliases below to pinned provider models, so a measured result can be
    # reproduced. `llm_model` is the default alias; a generation can ask for another one
    # (app/llm/gateway.py using_model). Changing provider or model is an alias change here
    # or a mapping change in the gateway config, never a code change.
    llm_gateway_url: str = "http://localhost:4000"
    llm_gateway_key: str = Field(default="", repr=False)
    llm_model: str = "gemini-fast"
    non_teacher_daily_generation_limit: int = Field(default=3, ge=1)
    # Off on a public deployment: the quota above is per account, so open registration would let
    # anyone create accounts to spend the provider credit. Accounts then come from the administrator
    # or from an LMS launch.
    self_registration: bool = True
    # Chat messages a day for an account that cannot author, a student who came through the LMS: each one is a
    # paid model call, and nobody else is accountable for them.
    learner_daily_chat_limit: int = Field(default=30, ge=1)
    # Pictures drawn and videos rendered a day, per account and for every account: each is paid
    # (about $0.04 a picture, $0.03 to $0.11 of voice a render), and neither takes a generation.
    video_redraws_per_day: int = Field(default=20, ge=1)
    video_renders_per_day: int = Field(default=10, ge=1)
    # Pin the selected alias for evaluation; the gateway may still retry that model.
    llm_experiment_mode: bool = False
    # Times one call in a background job waits and asks again after a 429, so a brief limit does not
    # restart the whole job. Requests do not wait.
    llm_rate_limit_retries: int = Field(default=6, ge=0)
    # The gateway alias that serves the embeddings (devops/gateway/config.yaml).
    embedding_model: str = "embed-default"
    # The gateway alias that re-reads, from the rendered page, PDF formulas whose text layer could
    # not be rebuilt (app/ingestion/formula_repair.py). It must accept images; empty turns it off,
    # and those pages then keep the rebuilt text with a warning.
    formula_vision_model: str = "gemini-fast"
    # At most this many pages of one document are sent, so one badly typeset file cannot spend
    # much of the model balance.
    formula_vision_max_pages: int = Field(default=40, ge=0)
    # The reranker service of devops/docker-compose.yml, a cross-encoder in a container of its
    # own so the backend image carries no torch. Not behind the gateway: it holds no provider key.
    reranker_url: str = "http://localhost:8082"
    # "jev" sends the query and the candidate chunks to TypeSafe (app/retrieval/reranker.py); approved
    # 2026-11-08 and the default since. "jina" is the self-hosted service above, nothing sent out.
    reranker_provider: Literal["jina", "jev"] = "jev"
    typesafe_api_key: str = ""
    # Pinned: the `jev-latest` alias moves with each release, so its scores could change with no change here.
    jev_model: str = "jev-1.14.0"
    # Jev calls one process has in flight. One rerank makes a call per candidate. 64 held steady in
    # docs/measurements/2026-11-05-real-llm-and-jev.md, and 128 from one machine was far slower.
    jev_max_concurrent: int = Field(default=64, ge=1)

    # Upload lưu ở đây cho tới khi job document_pipeline xử lý xong (job
    # chạy nền, sau khi request upload gốc đã trả response từ lâu -- khác
    # với trước đây dùng NamedTemporaryFile bị xóa ngay cuối request đồng
    # bộ). Nằm trong bind mount ./backend:/app nên sống sót qua restart
    # container (không phải named volume ephemeral).
    upload_storage_dir: str = "uploads"
    # "local" keeps the file in upload_storage_dir, which only works when the API and the worker
    # share that directory (one machine, or the compose bind mount). "s3" is for separate tasks,
    # as on ECS: the browser puts the file in upload_s3_bucket through a presigned POST and the
    # worker fetches it from there.
    # Credentials come from the default AWS chain, which on ECS is the task role.
    upload_store: Literal["local", "s3"] = "local"
    upload_s3_bucket: str = ""
    upload_s3_prefix: str = "uploads/"
    # A study's reference files stay as long as the study does, so they live outside the prefix the
    # bucket's lifecycle rule expires after a few days.
    study_files_s3_prefix: str = "study-files/"
    # Generated images, voice and videos stay as long as their document, so also outside the expiring prefix.
    media_s3_prefix: str = "media/"
    # The bucket's region, so that presigned POSTs name its regional endpoint. Empty: boto3's default.
    upload_s3_region: str = ""

    # "json" writes one JSON object per log line, for CloudWatch Logs Insights and metric filters.
    log_format: Literal["text", "json"] = "text"
    # How long a stopping worker lets its running jobs finish before it puts them back in the queue.
    # Below ECS's stopTimeout, which on Fargate is at most 120 s.
    worker_shutdown_grace_seconds: float = Field(default=5, ge=0)
    # Queue depth as CloudWatch embedded metric lines, which the worker service scales on. See app/jobs/metrics.py.
    queue_metrics: bool = False

    # False when the jobs run in their own process (python -m app.jobs.worker), so that API
    # and workers scale separately. True keeps a single process working on its own.
    run_job_worker: bool = True
    # Jobs one process runs at once. Read from WORKER_POOL_SIZE.
    worker_pool_size: int = Field(default=4, ge=1)
    # Rerank calls one process has in flight. The service counts each candidate text against its queue
    # and answers 429 when full, so this keeps a bursty pipeline from being refused.
    reranker_max_concurrent: int = Field(default=2, ge=1)
    # Questions of one module generated at once. They do not depend on each other.
    question_concurrency: int = Field(default=5, ge=1)
    # Requests one API process serves at once, and its database connections. FastAPI opens the
    # connection in one worker thread and runs the endpoint in another, so past the pool size every
    # thread can end up waiting for a connection held by a request that is waiting for a thread.
    # Keeping requests at or below the pool size rules that out; the rest queue cheaply.
    api_max_concurrent_requests: int = Field(default=20, ge=1)
    # Chat streams one process holds open. They mostly wait on the network, so they sit outside the
    # limit above. Above this, a new stream gets 503.
    chat_max_streams: int = Field(default=100, ge=1)
    # Streams searching the database at once, below the connection pool so that other requests still get one.
    chat_retrieval_concurrency: int = Field(default=8, ge=1)
    api_queue_timeout_seconds: float = Field(default=30, gt=0)
    # memory:// is exact only for one process; several API processes must share a store.
    rate_limit_storage_uri: str = Field(default="memory://", repr=False)

    # Largest body accepted for anything that is not an upload. The biggest legitimate
    # one is an edited module component, a few tens of kilobytes.
    max_request_body_bytes: int = 1024 * 1024

    # How long finished rows are kept before the periodic clean-up removes them
    # (app/services/maintenance.py). Failed jobs outlive finished ones because
    # the monitoring page is how failures get noticed.
    done_job_retention_days: int = 7
    failed_job_retention_days: int = 30

    # The address people open the application at, as the LMS sees it. The LTI redirect URI is
    # built from it and must match the one registered in the LMS exactly.
    public_url: str = "https://localhost:8443"
    # The tool's own RSA private key, PEM (newlines may be written as \n). With it the tool publishes its
    # public key at /api/lti/jwks and signs the assertion it trades for an access token to an LMS's
    # Names and Role Provisioning service, which keeps course members in step with the LMS course
    # (app/lti/roster.py). Empty turns that off: members then stay until they are removed by hand.
    lti_private_key: str = Field(default="", repr=False)
    # How old a course's member list may be before the worker reads it from the LMS again. A student
    # removed from the LMS course loses the course here within about this long.
    lti_roster_sync_minutes: int = Field(default=60, ge=5)

    moodle_base_url: str = "http://localhost:8080"
    # Moodle's own $CFG->wwwroot (how it's configured, e.g. so a human can
    # open it in a browser on the host machine) -- not necessarily the same
    # host:port moodle_base_url uses to physically reach it from inside a
    # Docker container (host.docker.internal vs localhost). A request whose
    # Host header doesn't match wwwroot gets 303-redirected by Moodle, so
    # moodle_client sends this as the Host header while still connecting via
    # moodle_base_url.
    moodle_wwwroot: str = "http://localhost:8081"
    moodle_token: str = Field(default="", repr=False)

    # No hardcoded default: a known signing secret committed to source lets
    # anyone who reads this repo mint a valid token for any user. If unset,
    # _ensure_secret_key below generates a random per-process value instead
    # (fine for local dev; every issued token becomes invalid on restart).
    secret_key: str = Field(default="", repr=False)
    jwt_algorithm: str = "HS256"
    # Short, because an access token cannot be taken back once issued; the frontend
    # refreshes on a 401, so 15 minutes costs the user nothing. Refresh tokens are stored
    # hashed and are revocable (RefreshToken, app/services/sessions.py).
    access_token_expire_minutes: int = 15
    # A login ends this long after it began, however often it is refreshed: a refresh
    # token passes its own expiry on to its successor.
    refresh_token_expire_days: int = 14

    @model_validator(mode="after")
    def _check_environment(self) -> "Settings":
        if self.upload_store == "s3" and not self.upload_s3_bucket:
            raise ValueError("UPLOAD_STORE=s3 needs UPLOAD_S3_BUCKET")
        if self.environment == "production":
            problems = self._production_problems()
            if problems:
                raise ValueError("Refusing to start with ENVIRONMENT=production: " + "; ".join(problems))
        return self

    def signing_key(self) -> str:
        """The key tokens are signed and checked with.

        Without SECRET_KEY outside production, a random one is made the first time a token
        is signed or checked, not when the configuration is loaded: the migrate service
        loads this configuration only to list the tables, never signs anything, and is
        deliberately not given SECRET_KEY, so warning at load time printed a meaningless
        warning on every migration.
        """
        if self.secret_key:
            return self.secret_key
        with _signing_key_lock:
            # Checked again under the lock: two requests arriving together would otherwise
            # each make a key, and a token signed with one would fail against the other.
            if not self.secret_key:
                logger.warning(
                    "SECRET_KEY not set — generating a random ephemeral secret for this "
                    "process. Set SECRET_KEY in .env so tokens survive a restart."
                )
                self.secret_key = secrets.token_urlsafe(48)
        return self.secret_key

    def _production_problems(self) -> list[str]:
        """Everything that must not be true of a production configuration, all at once so
        that fixing one does not just reveal the next on the following start."""
        problems = []
        if len(self.secret_key) < MIN_PRODUCTION_SECRET_LENGTH:
            # No ephemeral fallback here: with several workers each would sign with its
            # own key, and every restart would sign everybody out.
            problems.append(f"SECRET_KEY is missing or shorter than {MIN_PRODUCTION_SECRET_LENGTH} characters")
        # The owner's URL is only for migrations, tests and scripts. The process that runs in
        # production has no use for it, so it is checked only when it is there.
        owner = make_url(self.database_url) if "database_url" in self.model_fields_set else None
        runtime, worker = make_url(self.app_runtime_database_url), make_url(self.app_worker_database_url)
        urls = [("APP_RUNTIME_DATABASE_URL", runtime), ("APP_WORKER_DATABASE_URL", worker)]
        if owner is not None:
            urls.insert(0, ("DATABASE_URL", owner))
        for name, url in urls:
            if (url.password or "").lower() in PUBLIC_DEFAULT_PASSWORDS:
                problems.append(f"{name} has no password or a well-known default password")
        if owner is not None:
            if owner.username == runtime.username:
                # Row level security does not apply to the role that owns the tables.
                problems.append(
                    "APP_RUNTIME_DATABASE_URL must use a different role from DATABASE_URL, "
                    "because row level security does not apply to the role that owns the tables"
                )
            elif owner.password and owner.password == runtime.password:
                problems.append("the two database roles must not share a password")
        others = [url for _, url in urls if url is not worker]
        if any(worker.username == other.username for other in others):
            problems.append(
                "APP_WORKER_DATABASE_URL must use a role of its own, not the one of APP_RUNTIME_DATABASE_URL "
                "or DATABASE_URL"
            )
        elif worker.password and any(worker.password == other.password for other in others):
            problems.append("APP_WORKER_DATABASE_URL must not share a password with another database role")
        return problems


settings = Settings()
