# Media ingestion measurements, 2026-10-26

The private four-file replay reduced extract + chunk + embed wall time by **4.36–6.78×**.
The final Week05 replay after the overlap correction took **19.03 seconds** (3.18× faster
than its 60.48-second control). The historical 231/239-second MP4 ASR delay did **not**
recur, so these results do not attribute that historical delay to the file extension.

## Diagnosis and setup

Control reader: `f75053379f91194fa9373a04545218078e1ba36b` (unchanged media reader).
WSL2, i7-12700H, RTX 3050 Ti Laptop GPU, 4,096 MiB VRAM; TEI 1.9.4.
All services and measurement programs used private `fm-do-an-media-ingest-speedup-*`
`--rm` containers. No live `do-an-*` service, database or volume was used. These are
actual extract/ASR/chunk/embed calls through a private LiteLLM gateway, without database
inserts. Totals below sum those three ingestion steps; they exclude HTTP upload and queue
wait, unlike the historical report's upload-to-done totals.

All four original inputs exceeded 24,000,000 bytes. The old reader therefore already
converted **both MP4 and MP3** to mono 16 kHz MP3 at 32 kbps, then sent one whole-file
request. The initial premise that the video container or higher video bitrate was uploaded
to Groq is false for this corpus:

| File | Original bytes | Old upload bytes | New total upload bytes | Requests before → after |
| --- | ---: | ---: | ---: | ---: |
| Week02.mp4 | 420,662,346 | 18,787,143 | 18,848,184 | 1 → 8 |
| Week03.mp4 | 647,094,101 | 19,096,743 | 19,157,784 | 1 → 8 |
| Week04.mp3 | 87,339,195 | 19,686,413 | 19,756,053 | 1 → 9 |
| Week05.mp3 | 66,812,706 | 18,927,533 | 18,988,504 | 1 → 8 |

New requests are approximately 2.4 MB each: mono, 16 kHz, 32 kbps MP3, 600-second
windows with two seconds of preceding context. The control ASR responses took 19.13,
11.85, 11.84 and 10.85 seconds, rather than the historical 230.98, 239.43, 35.15 and
19.83 seconds. Encoding, bytes and request count cannot explain the old video-only
latency. The historical artifacts contain total client time, without separate upload,
provider queue or inference timings; those components cannot be recovered. Provider or
network variation is a plausible explanation, **not a proven root cause**. The private
replay disabled gateway retries for transparent request/cost accounting; production's
existing router retry policy is another historical timing confounder.

## Four-file control and optimized replay

ASR is wall time from first request to last response, including overlapping work; extract
already includes ASR. Do not add ASR to extract. Transcoding and ASR also overlap in the
optimized path. CPU embeddings are the control; optimized embeddings use GPU.

| File | Extract s before → after | ASR wall s before → after | Embed s CPU → GPU | Three-step total s before → after | Chunks before → after | Transcript similarity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Week02.mp4 | 31.35 → 9.46 | 19.13 → 7.03 | 45.77 → 1.89 | 77.16 → 11.38 | 92 → 91 | 0.9325 |
| Week03.mp4 | 24.42 → 9.89 | 11.85 → 7.40 | 36.75 → 1.90 | 61.21 → 11.82 | 97 → 95 | 0.9290 |
| Week04.mp3 | 25.11 → 11.22 | 11.84 → 8.91 | 30.54 → 1.51 | 55.68 → 12.76 | 73 → 72 | 0.9296 |
| Week05.mp3 | 22.33 → 10.84 | 10.85 → 8.38 | 38.11 → 1.76 | 60.48 → 12.64 | 92 → 92 | 0.9076 |

The four-file table records the initial replay. Boundary inspection then found a duplicate
phrase before a short trailing utterance, and timestamp order when overlap text differed.
Three regression tests now cover those cases. The final code matches multi-word overlap
prefixes inside the preceding shared text and returns units in timestamp order. A full
Week05 replay of that final code gave extract **17.04 s**, ASR wall **14.91 s**, GPU embed
**1.96 s**, total **19.03 s**, **91 chunks**, and similarity **0.9076**. Its eight ASR
responses were saved for deterministic re-merging. No additional four-file provider replay
was made after that merge-only correction, to stay within the requested spend cap.

Similarity is `difflib.SequenceMatcher(autojunk=False)` on case-folded, whitespace-split
words against the fresh whole-file control transcript. Against the archived original
Week02/Week03 transcripts, the initial optimized similarities were 0.9324/0.9288; fresh
controls matched those archives at 0.9997 each. The archives lack Week04/Week05 full
transcripts, so fresh control transcripts were saved for those files. This metric measures
agreement with another ASR result, **not recognition accuracy or WER against human text**.
Manual inspection found changed terminology and distant student questions in both paths;
no human transcript was available. Exact word matching removes repeated overlap text;
paraphrased overlap remains a known limit and would need word-level alignment.

## Embedding compatibility and GPU memory

CPU retains ONNX revision `c25a394dd583836952667c12f008335071b3f43d`.
GPU uses the native package of the same Qwen3-Embedding-0.6B model, revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`: the ONNX repository has no native
safetensors. Pooling remains last-token; both paths return the existing 768 normalized
components. On six real chunks from the two archived lectures plus English/Vietnamese
samples, minimum CPU/GPU cosine was **0.99999835** (required ≥0.999).

On identical saved transcripts: Week02's 92 chunks took **43.55 s CPU / 1.54 s GPU**;
Week03's 97 chunks took **48.90 s CPU / 1.57 s GPU** (28.2× / 31.2×).
Four concurrent requests of 100 chunks all returned 200, in 5.79–5.91 seconds each.
Sampling VRAM every 200 ms recorded a peak of **1,315 MiB**, leaving **2,781 MiB** free.
TEI processes model batches serially, limited to 1,024 tokens and four inputs per batch;
512 queued inputs accommodate the four backend workers. The current reranker remains on
CPU. VieNeu TTS was not loaded; its future coexistence is **not** claimed as validated.

`devops/scripts/compose.sh` automatically selects the measured Ampere 8.6 GPU override;
missing/other GPUs retain CPU. `EMBEDDER_DEVICE=cpu` forces CPU. The actual Compose
consumer validated both resolved configurations without starting the application stack.

## Cost and validation

Groq's [published duration price](https://console.groq.com/docs/speech-to-text) is unchanged:
Whisper Large V3 Turbo at $0.04/hour. Normalization changes bytes, not audio duration.
Overlap plus encoder padding increased the four-file replay's estimated billed duration
by about **0.32%**. Four whole-file controls, four optimized files, and the final Week05
replay reserved **$0.478396** in total, below the **$0.50** cap. Every request reserved its
estimated cost before upload; all 45 successful requests returned without retry. Two
initial account-spend-block rejections returned 400 and no transcript before the owner
restored access; they are recorded separately and are not successful duration usage.

The reader retries only failed windows (three attempts, retry-after/backoff, document
retry deadline), and the shared process-wide pool bounds preparation and ASR together.
Exhausted windows raise a timestamped terminal error shown by the existing failed-job
monitor; successful partial text is never returned. Unit tests exercise concurrency,
normalization, absolute offsets, padding, duplicate text, retry isolation, failure and
preflight rejection. Automated validation results are appended below after the final run.

Raw proof is outside the repository under `/mnt/d/firstmate/data/do-an-media-ingest-speedup/`:
`diagnosis.json`, `replay.json`, `replay-final-week05.json`, `groq-budget.json`,
`embed-measure.json`, `gpu-stress.json`, `vram.csv`, transcript JSON, test logs and the
measurement programs. These paths are local evidence, not public CI dependencies.

Final automated validation: **1,191 passed, 1 live-provider test deselected**, in 143.85 s
on the private database; **744 existing warnings** (the earlier full run had the same
warning count). Ruff over the entire backend passed; `git diff --check` passed.
Private CPU/GPU/gateway/database containers and the temporary provider-key env file
were removed after measurements and tests. No additional paid ASR calls are needed
for review: automated tests and one short smoke check cover the changed flow using
the recorded live evidence above.
