FROM python:3.14-slim AS model
WORKDIR /model
COPY prepare_model.py /prepare_model.py
RUN python /prepare_model.py

FROM ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4
COPY --from=model /model /model
CMD ["--model-id", "/model", "--max-client-batch-size", "64", "--max-batch-tokens", "2048"]
