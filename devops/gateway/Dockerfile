FROM python:3.12-slim

ARG LITELLM_VERSION=1.100.1

RUN pip install --no-cache-dir uv
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=60 uv pip install --system "litellm[proxy]==${LITELLM_VERSION}"

WORKDIR /app

EXPOSE 4000

ENTRYPOINT ["litellm", "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000"]
