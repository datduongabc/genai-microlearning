# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14

# LiteLLM is installed into a virtual environment here and only that is copied on: uv and its
# cache stay out of the image that runs.
FROM python:${PYTHON_VERSION}-slim AS deps
ARG LITELLM_VERSION=1.100.1
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_HTTP_TIMEOUT=60
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install "litellm[proxy]==${LITELLM_VERSION}"

FROM python:${PYTHON_VERSION}-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
# The Debian security fixes published since the base image was built (ECR's scan on push).
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY --from=deps /opt/venv /opt/venv
RUN adduser --disabled-password --gecos "" --home /home/gateway --uid 10001 gateway

WORKDIR /app
# In the image for ECS, which has no bind mount. Compose still mounts the file over it, so an
# edit there needs no rebuild.
COPY config.yaml /app/config.yaml
USER gateway

EXPOSE 4000

ENTRYPOINT ["litellm", "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000"]
