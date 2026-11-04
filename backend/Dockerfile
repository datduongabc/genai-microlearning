# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS base

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

RUN pip install --no-cache-dir uv

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
    && chown appuser:appuser /app

# Doi sang nguoi dung thuong TRUOC khi cai dat, khong phai sau: moi thu tao ra
# da thuoc dung chu so huu nen khong can `chown -R` o cuoi, ma lenh do buoc
# Docker chep lai ca thu muc .venv sang mot lop moi chi de doi quyen.
USER appuser

COPY --chown=appuser:appuser pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=10001,gid=10001 \
    uv sync --frozen --no-dev

COPY --chown=appuser:appuser . .

EXPOSE 8000

ENTRYPOINT ["bash", "docker-entrypoint.sh"]

# Dò định kỳ thay vì chờ sự kiện từ hệ điều hành: mã được gắn vào qua bind mount từ ổ đĩa
# Windows, và sự kiện "tệp bị sửa" không đi qua được lớp đó, chỉ tạo và xoá tệp là đi qua.
# Chờ sự kiện thì sửa tệp xong backend vẫn chạy mã cũ. Chỉ dò 116 tệp trong app/ nên rẻ.
ENV WATCHFILES_FORCE_POLLING=true

# --reload-dir app: không giới hạn thì bộ tự nạp lại theo dõi cả /app, gồm .venv với khoảng
# 34 nghìn tệp thư viện so với 116 tệp mã. Nó đã chết vì hết chỗ theo dõi (os error 12) trong
# khi tiến trình phục vụ vẫn trả lời, tức backend âm thầm chạy mã cũ mà không có gì báo.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "app"]
