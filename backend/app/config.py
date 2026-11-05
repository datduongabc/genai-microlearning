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
    # the only place that holds the provider API keys (Gemini/OpenAI/Anthropic) and
    # maps the model aliases below to pinned provider models, so a measured result can be
    # reproduced. `llm_model` is the default alias; a generation can ask for another one
    # (app/llm/gateway.py using_model). Changing provider or model is an alias change here
    # or a mapping change in the gateway config, never a code change.
    llm_gateway_url: str = "http://localhost:4000"
    llm_gateway_key: str = Field(default="", repr=False)
    llm_model: str = "gemini-fast"
    non_teacher_daily_generation_limit: int = Field(default=3, ge=1)
    # Pin the selected alias for evaluation; the gateway may still retry that model.
    llm_experiment_mode: bool = False
    # The gateway alias that serves the embeddings (devops/gateway/config.yaml).
    embedding_model: str = "embed-default"
    # The gateway alias that re-reads, from the rendered page, PDF formulas whose text layer could
    # not be rebuilt (app/ingestion/formula_repair.py). It must accept images; empty turns it off,
    # and those pages then keep the rebuilt text with a warning.
    formula_vision_model: str = "gemini-fast"
    # At most this many pages of one document are sent, so one badly typeset file cannot spend a
    # day of the free quota.
    formula_vision_max_pages: int = Field(default=40, ge=0)
    # The reranker service of devops/docker-compose.yml, a cross-encoder in a container of its
    # own so the backend image carries no torch. Not behind the gateway: it holds no provider key.
    reranker_url: str = "http://localhost:8082"
    # "jina" is the service above. "jev" sends the query and the candidate chunks to TypeSafe; see app/retrieval/reranker.py.
    reranker_provider: Literal["jina", "jev"] = "jina"
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
