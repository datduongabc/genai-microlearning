# Embedder task size on AWS, 2 vs 4 vCPU (2026-11-17)

The same two lectures and three questions were run against the demo stack (`microlearning-demo`, ap-southeast-1) at two embedder sizes:

- **2 vCPU / 4 GB:** image c301a68, PR #120.
- **4 vCPU / 8 GB:** image bc4ec90, PR #121.

Each run used a fresh course and a throwaway account. Both were deleted afterwards.

| Measure | 2 vCPU, 4 GB | 4 vCPU, 8 GB |
| --- | --- | --- |
| Ingest, `calculus 1/1. Giới hạn dãy số.pdf` (1.2 MB) | 734 s, task replaced twice | 74 s |
| Ingest, `operating-system/Chapter 5_CPU Scheduling.pdf` (3.2 MB) | 82 s | 55 s |
| Chat, time to first token | 5.9, 3.1, 3.5 s | 3.9, 2.3, 7.0 s |
| Answers with a citation | 3 of 3 | 3 of 3 |

**What happened at 2 vCPU:**
- The embedder sat at 98% CPU (CloudWatch maximum) and failed its health checks.
- ECS replaced it at 14:14 and 14:18 UTC, both times in the middle of the embedding: "replaced 1 tasks due to an unhealthy status".
- Memory peaked at 57% of 4 GB, so the size is bound by CPU, not memory.

**How the times are measured:**
- Ingest is the time from `POST /documents/{id}/complete` until the status is `embedded`, polled every 3 s.
- Chat goes through `POST /courses/{id}/chat/stream` with the default model (`openai-fast`).

Chat latency depends mostly on the language model. Three questions per size are too few to compare it.

The `chunks` field in the JSON files is the first page of the chunk list, not the chunk total.

## Files

- `aws_measure.py` is the client. It needs `EMAIL`, `PASSWORD` and `OUT` in the environment.
- `2vcpu.json` and `4vcpu.json` are its outputs.
