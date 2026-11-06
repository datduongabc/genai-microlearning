# The embedder for ECS, with the model inside: a new task starts serving without first fetching
# 2.4 GB from Hugging Face, and cannot fail to start because the Hub is slow or down.
# Compose keeps downloading into its cache volume, as its GPU override needs other weights.
FROM python:3.14-slim AS model
COPY prepare_model.py /prepare_model.py
RUN python /prepare_model.py

FROM ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4
COPY --from=model /model /model
# The same arguments as the embedder of devops/docker-compose.yml, see the reasons there. The
# API key comes from the API_KEY environment variable, never the image.
CMD ["--model-id", "/model", "--served-model-name", "Qwen3-Embedding-0.6B", "--pooling", "last-token", \
     "--max-batch-tokens", "1024", "--max-client-batch-size", "128"]
