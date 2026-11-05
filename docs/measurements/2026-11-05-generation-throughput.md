# Generation throughput after the fixes, 2026-11-05

The earlier run in `2026-11-05-load-test-baseline.md` stopped scaling at 8 workers and failed 11 of 32 modules. This
run repeats it with the same machine, load and mock LLM after three changes. Raw results are in
`docs/measurements/load-test/generation-after.json` and `rerank-candidates.json`.

## What changed

- **A busy reranker no longer restarts the job.** The call retries on 429, 502, 503, 504 and connection errors, with
  backoff, and each process sends at most `RERANKER_MAX_CONCURRENT` requests at once (two by default). Before this, one
  failure sent the whole job back to the queue, which repeated LLM calls that had already succeeded.
- **Questions run in parallel inside a module.** `QUESTION_CONCURRENCY` questions at a time (five by default). The
  questions stage took 75% of a module's time on a real model.
- **Question retrieval reranks 15 candidates instead of 30.** Content keeps 30, see below.

## Result

Each module made 23 mock LLM calls and 8 retrievals, with the LLM median at about 13.3 s. All jobs were queued at the
same moment. Each row has one reranker container with a four-CPU quota.

| Workers x threads | Modules | Failed before | Failed after | Modules/hour before | Modules/hour after | Module mean before | Module mean after | Rerank mean after | Rerank p95 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 x 4 | 4 | 0 | 0 | 38.4 | 72.6 | 357 s | 183 s | 4.1 s | 6.1 s |
| 2 x 4 | 8 | 0 | 0 | 67.0 | 118.3 | 393 s | 222 s | 10.9 s | 21.9 s |
| 4 x 4 | 16 | 0 | 0 | 114.8 | 155.8 | 461 s | 321 s | 27.6 s | 45.5 s |
| 8 x 4 | 32 | 11 | 0 | 79.9 | 199.7 | 500 s | 494 s | 62.4 s | 93.0 s |

No job in the last three rows went back to the queue. Throughput now keeps rising at 8 workers instead of falling, and
it is 2.5 times the earlier 8 x 4 figure. Scaling is still far from linear: eight times the workers gives 2.75 times the
throughput.

## The bottleneck that remains

The reranker. At 8 x 4 it averaged 332% CPU and reached its 400% quota, and a rerank call took 62 s on average against
4 s at 1 x 4. That time includes waiting for a slot and for retries. The growth in module time comes mostly from this
wait, because LLM latency stayed near 14 s, and embedding, vector search and BM25 stayed under 0.15 s at every load.

Whether a second reranker now helps has not been measured. The earlier test with two showed no gain, but restarted jobs
hid its effect. To use more reranker containers, `RERANKER_MAX_CONCURRENT` has to rise with them, since it is a limit
per worker process.

## Rerank candidates

Measured on the same 250 questions as the retrieval baseline, one request at a time.

| Candidates | Recall@5 | Time per rerank |
| ---: | ---: | ---: |
| 30 | 0.984 | 2.48 s |
| 20 | 0.984 | 1.56 s |
| 15 | 0.988 | 1.16 s |
| 10 | 0.988 | 0.81 s |

Below 30 candidates, recall did not drop on this set, but its questions are in the same language as the documents and
were written from the gold chunk. The 2026-10-14 report saw gold chunks at ranks 14 and 16 on a cross-language query, so 15 is
close to the edge. Content stays at 30 because cross-language retrieval has not been measured.

## Against the 30,000-user target

The capacity targets are 150 modules per hour at 3,000 concurrent users and 1,200 at 6,000. These rest on assumed
generation rates of 5% and 20% of users per hour, not on measured behaviour.

- 150 modules per hour is reached here at 4 x 4, on the mock.
- 1,200 is not reached, and this run does not show how to reach it.
- Provider quota is not covered. At 278,000 tokens per module, 1,200 modules per hour is about 5.6 million tokens per
  minute, which the mock does not limit.

## Limits

- One shared machine: load generator, database and services together. At 8 x 4, available memory dropped to about 970 MB
  of 7.57 GiB and swap in use reached 1.6 GB, so there is little room for more containers here.
- The mock LLM never fails or limits requests. The only real-model figure is 369 s per module, measured sequentially
  before these changes.
- The parallel question change has not been run on a real model yet, so provider rate limits may reduce its gain.
- Every run is a single burst and was not repeated, so small differences between rows are not meaningful.
