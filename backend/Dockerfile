# syntax=docker/dockerfile:1

# Comments are provided throughout this file to help you get started.
# If you need more help, visit the Dockerfile reference guide at
# https://docs.docker.com/go/dockerfile-reference/

# Want to help us make this template better? Share your feedback here: https://forms.gle/ybq9Krt8jtBL3iCk7

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim as base

# Prevents Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1

# Keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Nguoi dung thuong de chay ung dung, khong phai root.
# Co thu muc nha that chu khong phai /nonexistent: uv ghi cache vao do, va
# khong ghi duoc thi moi lenh `uv run` deu that bai ngay khi container khoi dong.
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/home/appuser" \
    --shell "/bin/bash" \
    --uid "${UID}" \
    appuser

# Tesseract doc tai lieu quet, ffmpeg tach tieng khoi video truoc khi phien am.
# Phai cai khi con la root, truoc khi chuyen sang nguoi dung thuong o duoi.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-vie ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Tai san mo hinh rerank vao anh, de lan chay dau tien khong phai cho tai ve.
# Khong con nap all-MiniLM-L6-v2: tu khi nhung qua gateway thi khong ai dung no nua.
RUN uv run python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-base')"

COPY . .

# uv can ghi vao .venv va vao cache khi chay, nen quyen phai thuoc ve nguoi dung
# se chay ung dung chu khong phai root.
RUN chown -R appuser:appuser /app /home/appuser

# Chuyen sang nguoi dung thuong sau khi da cai dat xong moi thu.
USER appuser

EXPOSE 8000

ENTRYPOINT ["bash", "docker-entrypoint.sh"]

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
