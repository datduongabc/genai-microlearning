import logging
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
    jobs,
    model_selection,
    modules,
    moodle,
    moodle_account,
)
from app.auth.rate_limit import limiter, user_or_address
from app.config import Settings, settings
from app.jobs.queue import start_worker, stop_worker
from app.llm.catalog import GatewayUnavailable
from app.logging_config import configure_app_logging
from app.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware

configure_app_logging()
logger = logging.getLogger("app.rate_limit")
gateway_logger = logging.getLogger("app.gateway")


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
    # Background thread trong chính process backend (không job queue
    # container riêng) -- xử lý document_pipeline/module_generation job nền,
    # xem app/services/job_queue.py.
    start_worker()
    yield
    stop_worker()


def _is_upload(scope) -> bool:
    return scope["method"] == "POST" and scope["path"] == "/documents"


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
    api.add_middleware(SecurityHeadersMiddleware, production=production)

    api.include_router(auth.router)
    api.include_router(admin.router)
    api.include_router(courses.router)
    api.include_router(documents.router)
    api.include_router(modules.router)
    api.include_router(moodle.router)
    api.include_router(moodle_account.router)
    api.include_router(chat.router)
    api.include_router(jobs.router)
    api.include_router(model_selection.router)

    @api.get("/health")
    def health():
        return {"status": "ok"}

    return api


app = create_app()
