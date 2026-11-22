# Load test

Measures the system without paying for a model. `mock_gateway.py` replaces the LLM gateway, waits around the
latency measured on real models and returns JSON that fits the requested schema. Postgres, Redis, the embedder,
the API and the workers are all real; reranking uses Jev when `TYPESAFE_API_KEY` is set (`docker-compose.jev.yml`)
and keeps the hybrid order otherwise. Because the mock has no rate limit, the results do not include provider quotas.
The scaling matrix with the self-hosted Jina reranker (`run_matrix.sh`, removed with Jina) is recorded in
`docs/measurements/2026-11-05-generation-throughput.md`.

Remove the stack with `docker compose --profile k6 down`.
Failure tests, which stop each kind of service while it works and check that no job is lost, are
`./run_failures.sh` (about an hour and a quarter; results in `docs/measurements/2026-11-06-failure-tests.md`).

Do not add `-v`: it would also delete the shared `do-an_embedder_models` cache. To start from an empty database, run
`docker volume rm do-an-load_db do-an-load_uploads`.
