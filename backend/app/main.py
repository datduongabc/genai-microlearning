import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import (
    admin,
    auth,
    chat,
    courses,
    documents,
    flashcards,
    jobs,
    lti,
    model_selection,
    modules,
    moodle,
    moodle_account,
    studies,
    study_files,
)
from app.auth.rate_limit import limiter, user_or_address
from app.config import Settings, settings
from app.db import database_ready
from app.jobs.queue import start_worker, stop_worker
from app.llm.catalog import GatewayUnavailable
from app.logging_config import configure_app_logging
from app.middleware import (
    BodySizeLimitMiddleware,
    ConcurrencyLimitMiddleware,
    SecurityHeadersMiddleware,
)

configure_app_logging()
logger = logging.getLogger("app.rate_limit")
gateway_logger = logging.getLogger("app.gateway")
readiness_logger = logging.getLogger("app.readiness")


async def _rate_limited(request: Request, exc: RateLimitExceeded):
    # A refused request is either an attack or a client bug, and both are worth a line.
    # %r on the path: it is client text, and a newline in it must not start a fake line.
    logger.warning("rate_limited path=%r key=%s limit=%s", request.url.path, user_or_address(request), exc.detail)
    return _rate_limit_exceeded_handler(request, exc)


async def _gateway_unavailable(request: Request, exc: GatewayUnavailable):
    # Không đưa chi tiết lỗi của httpx ra client: nó chứa địa chỉ nội bộ của gateway.
    gateway_logger.warning("gateway_unavailable path=%r error=%s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Gateway mô hình chưa sẵn sàng, vui lòng thử lại sau ít phút."},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Jobs run here only without a separate worker process (app/jobs/worker.py).
    if settings.run_job_worker:
        start_worker()
    yield
    if settings.run_job_worker:
        stop_worker()


_UPLOAD_CONTENT_PATH = re.compile(r"/documents/[0-9a-fA-F-]{36}/content")


def _is_upload(scope) -> bool:
    """PUT /documents/{id}/content, the file itself: it has its own limit per format."""
    return scope["method"] == "PUT" and _UPLOAD_CONTENT_PATH.fullmatch(scope["path"]) is not None


def create_app(app_settings: Settings = settings) -> FastAPI:
    production = app_settings.environment == "production"
    # The API description lists every route and parameter: a map for an attacker and
    # no use to a user, so only development publishes it.
    api = FastAPI(
        title="AI Microlearning LMS",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    api.state.limiter = limiter
    api.add_exception_handler(RateLimitExceeded, _rate_limited)
    api.add_exception_handler(GatewayUnavailable, _gateway_unavailable)

    # The last one added is the outermost, so the headers also reach the 413 that the
    # size limit answers on its own.
    api.add_middleware(BodySizeLimitMiddleware, max_bytes=app_settings.max_request_body_bytes, exempt=_is_upload)
    api.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=app_settings.api_max_concurrent_requests,
                       queue_timeout=app_settings.api_queue_timeout_seconds, exempt_paths=frozenset({"/health", "/ready"}),
                       max_streams=app_settings.chat_max_streams)
    api.add_middleware(SecurityHeadersMiddleware, production=production)

    api.include_router(auth.router)
    api.include_router(admin.router)
    api.include_router(courses.router)
    api.include_router(documents.router)
    api.include_router(modules.router)
    api.include_router(flashcards.router)
    api.include_router(moodle.router)
    api.include_router(moodle_account.router)
    api.include_router(chat.router)
    api.include_router(jobs.router)
    api.include_router(model_selection.router)
    api.include_router(studies.router)
    api.include_router(study_files.router)
    api.include_router(lti.router)

    @api.get("/health")
    def health():
        """The process answers. For the container health check: failing it restarts the task."""
        return {"status": "ok"}

    @api.get("/ready")
    def ready():
        """The task can serve: for the load balancer, which stops sending it requests while this fails.

        Only the database, which almost every request needs. Valkey has an in-memory fallback, and
        the gateway, the embedder and Jev are shared by every task: one of them down would take
        all tasks out of the load balancer, including for the requests that do not need it."""
        if not database_ready():
            readiness_logger.warning("not_ready reason=database")
            return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
        return {"status": "ok", "database": "ok"}

    return api


app = create_app()
