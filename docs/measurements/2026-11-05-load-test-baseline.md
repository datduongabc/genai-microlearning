# Load test baseline, 2026-11-05

Measured before any generation optimisation. The method is in `devops/loadtest/README.md`, and the raw results are
in `docs/measurements/load-test/`.

## Conditions

- WSL2, i7-12700H (20 threads), RTX 3050 Ti Laptop, Docker with 7.57 GiB of RAM. The load generator, the database and
  every service share this one machine.
- The LLM is a mock with a median latency of 13.3 s per call and no rate limit. Everything else is real.
- Each reranker container has a four-CPU quota.

## A stall found by this test

With 100 concurrent requests, the API dropped from about 320 to 14 requests per second and nearly every request timed
out. It stayed stalled afterwards. FastAPI opens a database connection in one worker thread and runs the endpoint in
another, so every thread could end up waiting for a connection held by a request that was waiting for a thread.

`ConcurrencyLimitMiddleware` now keeps requests served at once at or below the database pool size. After the fix, the
same endpoint served 328 requests per second at 100 concurrent requests with no errors.

## API reads

200 virtual users with no think time, three requests per iteration, 60 seconds. After the fix.

| API containers | Requests per second | Median | p95 | Failures |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 199 | 2.9 s | 3.4 s | 0% |
| 2 | 372 | 1.4 s | 1.7 s | 0% |
| 4 | 583 | 0.8 s | 1.2 s | 0% |

Four containers give 2.9 times the throughput of one. An API container used about one CPU core at its peak.

## Module generation

Every job in a run is queued at the same moment, so retrieval requests arrive in bursts. Sustained traffic spread over
time could behave better, and this test does not show by how much. Each module made 22 to 23 LLM calls and 8 retrievals.

| Workers x threads | Reranker containers | Modules | Failed | Modules per hour | Module mean | Rerank mean | Rerank p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 x 4 | 1 | 4 | 0 | 38.4 | 357 s | 4.2 s | 10.1 s |
| 2 x 4 | 1 | 8 | 0 | 67.0 | 393 s | 8.6 s | 22.1 s |
| 4 x 4 | 1 | 16 | 0 | 114.8 | 461 s | 18.8 s | 43.0 s |
| 8 x 4 | 1 | 32 | 11 | 79.9 | 500 s | 25.1 s | 47.0 s |
| 8 x 4 | 2 | 32 | 9 | 72.9 | 524 s | 27.2 s | 57.7 s |

Module means count successful modules only. LLM latency stayed flat near 14 s, and the embedder, vector search and BM25
stayed fast at every load, with BM25 under 3 ms. The reranker is the shared bottleneck: it ran at its four-CPU quota.

At 32 concurrent jobs the reranker answered 429 or the 60-second call timed out. Each failure sent the whole job back to
the queue to start again, which repeats LLM calls that had already succeeded. Throughput fell instead of levelling off.
A second reranker did not fix this. This run does not explain why, because attempts wasted on restarts hide its effect.

## Limits

- Results come from one shared machine, so they show where the system breaks, not what production would reach.
- The mock has no provider quota or token limit. About 278,000 tokens per module were measured on a real model.
- The rate of generation per user, used for capacity estimates, is still an assumption.
