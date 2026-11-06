# Failure tests on one machine, 2026-11-06

The local stand-in for S6 on AWS: stop each kind of service while it is working, then check that no job is lost, no
module is made twice, and the system recovers without anyone acting. Run with `devops/loadtest/run_failures.sh` on
the load test stack: the LLM is `mock_gateway.py` (median 13.3 s per call), and Postgres, Redis, the embedder, the
reranker, two api and two worker containers (4 threads each) are real. No paid model was called. Raw results are in
`docs/measurements/load-test/failures-generation.json` and `failures-chat.json`.

## Generation

Each row queues 8 modules at once, makes the fault 60 s in, and waits for every job. Passing means 8 jobs done and
exactly one module per document. Attempts count the times a job went back to the queue as a failed attempt; the
queue gives up on a job at 5.

| Scenario | Fault | Done | Modules | Attempts (most for one job) | Wall | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| baseline | none | 8/8 | 8 | 0 (0) | 240 s | pass |
| worker-sigterm | `docker stop` one worker, as a deploy or scale-in does | 8/8 | 8 | 0 (0) | 258 s | pass |
| worker-sigkill | `kill -9` one worker, restarted 5 s later | 8/8 | 8 | 4 (1) | 1,090 s | pass, slow |
| gateway-60s | LLM gateway stopped for 60 s | 8/8 | 8 | 22 (3) | 342 s | pass |
| reranker-60s | reranker stopped for 60 s | 8/8 | 8 | 6 (1) | 342 s | pass |
| embedder-60s | embedder stopped for 60 s | 8/8 | 8 | 8 (2) | 321 s | pass |
| database-restart | `docker compose restart db` | 8/8 | 8 | 2 (1) | 255 s | pass |
| api-sigkill | `kill -9` one api container | 8/8 | 8 | 0 (0) | 238 s | pass |

What the logs show:

- **SIGTERM** works as designed: the stopping worker logged `stopping with 4 job(s) unfinished, put back in the
  queue`, and the other worker took them at once without counting an attempt.
- **SIGKILL** loses no job, but recovery takes the sweeper's threshold: the four jobs the dead worker held stayed
  `running` from its last heartbeat at 13:50:14 until the sweeper requeued them at 14:05:22, 14 minutes after the kill.
- **Gateway, reranker, embedder, database:** each failed step was retried with backoff and finished once the service
  was back. A 60 s gateway outage already took one job to 3 of its 5 attempts.
- **The api** is not in the generation path: killing one changed nothing.

## Chat

Six chat messages, two at a time, through `POST /courses/{id}/chat` while one dependency is stopped. The api is
restarted first, because it caches query embeddings and the questions repeat.

| Dependency stopped | Before the fix | After the fix |
| --- | --- | --- |
| Redis | 6 × 200 | 6 × 200 (rate limits fall back to each process) |
| reranker | **6 × 500** | 6 × 200, hybrid order (`rerank failed, chat keeps the hybrid order`) |
| embedder | 2 × 500 (4 × 200 came from the embedding cache) | 6 × 200, keyword search (`chat searches by keyword only`) |

**The bug.** `POST /chat` searched with `retrieve()`, the generation path, whose reranker call retries five times and
then raises. Only the streaming endpoint had the chat fallback. Both now use `search_for_chat` and `rerank_for_chat`
(`app/retrieval/search.py`): a failed reranker keeps the hybrid order after one retry, and a failed query embedding
leaves the BM25 candidates. Generation keeps failing the step instead, so the queue retries it with full retrieval.

## What these runs show and do not show

- One run of each, on one machine, with a mock model. The point is correctness under failure, not timing.
- Not covered: a Valkey or Postgres outage longer than a restart, a network partition, a full disk, a stopped Moodle
  during publication, and anything on AWS (S6 there, with ECS replacing tasks).

## Changes made after these runs

Both rerun with the same script; the other scenarios were not rerun.

| Scenario | Before | After |
| --- | --- | --- |
| worker-sigkill | requeued 14 min 11 s after the kill, wall 1,090 s | requeued 4 min 10 s after the kill, wall 505 s |
| gateway-60s | 22 attempts, at most 3 of 5 for one job, wall 342 s | 13 attempts, at most 2 of 5 for one job, wall 369 s |

1. **A crashed worker's jobs come back sooner.** `STUCK_THRESHOLD_SECONDS` is 5 minutes instead of 15: five missed
   beats at one a minute, so a live job is still never requeued. A failed beat is now retried after 5 s instead of
   60, so a worker whose database connection was cut refreshes its locks as soon as the database is back, before the
   sweeper, which waited for it too, can take them.
2. **An outage costs fewer attempts.** A failure that comes from a service we depend on being unavailable (a
   connection error, a 502/503/504, litellm's connection and server errors, a dropped database connection) is retried
   after 30, 60, 120 and 240 s instead of 5, 10, 20 and 40, which rides out about seven minutes instead of a little over
   one. The attempt limit stays, so a service that never comes back still fails the job. The wall time of the gateway
   scenario grew by 27 s: a job waits longer before its first retry after a short outage.
