# syntax=docker/dockerfile:1

# Stages: `runtime` holds what every way of running the app needs, `dev` is what compose runs
# (hot reload over a bind mount), and `prod`, the last and so the default, is what ECS and the
# load test run: the installed dependencies, the code and nothing that only builds or tests it.
ARG PYTHON_VERSION=3.14
ARG UV_VERSION=0.12.23

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS runtime

# Prevents Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1

# Keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Tesseract doc tai lieu quet, ffmpeg tach tieng khoi video truoc khi phien am.
# Cai khi con la root, truoc khi doi sang nguoi dung thuong.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-vie ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# Hinh trong slide duoc OCR song song, moi anh mot tien trinh Tesseract: de moi tien trinh
# tu chia them luong thi chung tranh nhau CPU va cham hon chay tuan tu.
ENV OMP_THREAD_LIMIT=1

# Nguoi dung thuong de chay ung dung, khong phai root. Co thu muc nha that chu
# khong phai /nonexistent: uv ghi cache vao do, va khong ghi duoc thi moi lenh
# `uv run` deu that bai ngay khi container khoi dong.
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/home/appuser" \
    --shell "/bin/bash" \
    --uid "${UID}" \
    appuser \
    && chown appuser:appuser /app \
    # Owned by appuser in the image, so that a new volume mounted here (compose's `uploads`) is
    # created with this owner: the source bind mount of compose belongs to the host user instead.
    && install -d -o appuser -g appuser /app/uploads

EXPOSE 8000

ENTRYPOINT ["bash", "docker-entrypoint.sh"]

# --- The dependencies for prod, installed by uv in a stage of their own so that uv stays out.
FROM python:${PYTHON_VERSION}-slim AS deps
COPY --from=uv /uv /usr/local/bin/uv
# Compiled now, since the image that runs cannot write them (its files belong to root).
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- Compose: the code comes in through a bind mount and `uv run` keeps .venv in sync.
FROM runtime AS dev
COPY --from=uv /uv /usr/local/bin/uv

# Doi sang nguoi dung thuong TRUOC khi cai dat, khong phai sau: moi thu tao ra
# da thuoc dung chu so huu nen khong can `chown -R` o cuoi, ma lenh do buoc
# Docker chep lai ca thu muc .venv sang mot lop moi chi de doi quyen.
USER appuser

COPY --chown=appuser:appuser pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=10001,gid=10001 \
    uv sync --frozen --no-dev

COPY --chown=appuser:appuser . .

# Dò định kỳ thay vì chờ sự kiện từ hệ điều hành: mã được gắn vào qua bind mount từ ổ đĩa
# Windows, và sự kiện "tệp bị sửa" không đi qua được lớp đó, chỉ tạo và xoá tệp là đi qua.
# Chờ sự kiện thì sửa tệp xong backend vẫn chạy mã cũ. Chỉ dò 116 tệp trong app/ nên rẻ.
ENV WATCHFILES_FORCE_POLLING=true

# --reload-dir app: không giới hạn thì bộ tự nạp lại theo dõi cả /app, gồm .venv với khoảng
# 34 nghìn tệp thư viện so với 116 tệp mã. Nó đã chết vì hết chỗ theo dõi (os error 12) trong
# khi tiến trình phục vụ vẫn trả lời, tức backend âm thầm chạy mã cũ mà không có gì báo.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "app"]

# --- ECS and the load test. Code and libraries belong to root and are read-only to the process.
FROM runtime AS prod
COPY --from=deps /app/.venv /app/.venv
COPY alembic.ini docker-entrypoint.sh ./
COPY alembic ./alembic
COPY app ./app
# Only the scripts an operator runs against a deployment (docs/integration/lti.md, infra/README.md);
# the research ones (judges, benchmarks, exports) run from a checkout.
COPY scripts/create_admin.py scripts/register_lti_platform.py scripts/reembed_chunks.py ./scripts/
ENV PATH=/app/.venv/bin:$PATH
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
