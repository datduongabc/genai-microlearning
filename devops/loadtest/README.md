# Load test

Measures the system without paying for a model. `mock_gateway.py` replaces the LLM gateway, waits around the
latency measured on real models and returns JSON that fits the requested schema. Postgres, Redis, the reranker,
the embedder, the API and the workers are all real. Because the mock has no rate limit, the results do not include
provider quotas.

```bash
DOCUMENTS_DIR=/path/to/docs/documents ./run_matrix.sh
```

The run takes about an hour and writes to `results/`. Remove the stack with `docker compose --profile k6 down`.
Failure tests, which stop each kind of service while it works and check that no job is lost, are
`./run_failures.sh` (about an hour and a quarter; results in `docs/measurements/2026-11-06-failure-tests.md`).

Do not add `-v`: it would also delete the shared `do-an_embedder_models` cache. To start from an empty database, run
`docker volume rm do-an-load_db do-an-load_uploads`.
