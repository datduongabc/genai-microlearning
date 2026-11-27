# Rubric v3 judged by two model families, 2026-11-26 and 2026-11-27

Two sets of RAG / no-RAG module pairs were scored on rubric v3 (`docs/rubric.md`) by two judges from different families: `claude-fast` (Claude Haiku 5.5) and `openai-fast` (GPT-6 Luna), both through OpenRouter.
No human rater has scored these packets yet, so this is judge-only evidence; human-versus-judge kappa is still open.

## Sets

| Set | Generator | Pairs | Packets | Judge rows | Analysis |
| --- | --- | ---: | --- | --- | --- |
| Gemini (from 2026-11-07) | `gemini-fast` | 10 | `module-study/packets/` | `judge-v3.jsonl` | `analysis-v3.json` |
| DeepSeek | `deepseek-flash` | 9 | `module-study/packets-deepseek/` | `judge-v3-deepseek.jsonl` | `analysis-v3-deepseek.json` |
| Both | | 19 | | both files | `analysis-v3-combined.json` |

The DeepSeek set was generated on a temporary copy of the dev database with the same five-subject documents and chunk counts as the Gemini set.
Its tenth pair (scientific socialism, Chapter 2) was skipped because the OpenRouter balance ran low.
Cost: judging the Gemini set $0.17; generating the DeepSeek set $1.48 and judging it $0.19.

## Commands

From `backend/`:

```bash
PYTHONPATH=. uv run python scripts/run_judge.py ../docs/measurements/module-study/packets-deepseek \
  --judges claude-fast,openai-fast --out ../docs/measurements/module-study/judge-v3-deepseek.jsonl
PYTHONPATH=. uv run python scripts/analyze_study.py \
  ../docs/measurements/module-study/judge-v3.jsonl ../docs/measurements/module-study/judge-v3-deepseek.jsonl \
  --out ../docs/measurements/module-study/analysis-v3-combined.json
```

## Results on the 19 pairs

Wilcoxon signed-rank on the pair differences (RAG minus no-RAG), Holm-corrected over the nine criteria:

| Criterion | Judge | No RAG | RAG | Holm p |
| --- | --- | ---: | ---: | ---: |
| No invented facts (`hallucination_score`) | Claude | 2.84 | 3.95 | 0.007 |
| No invented facts (`hallucination_score`) | GPT | 2.21 | 3.37 | 0.018 |
| Completeness | Claude | 3.63 | 4.37 | 0.040 |
| Completeness | GPT | 4.26 | 4.79 | 0.121 |

No other criterion differed after correction, for either judge.
On each set alone (10 and 9 pairs) RAG scores higher on no invented facts for both judges, but nothing survives Holm, so the combined set is the one to quote.

Agreement between the two judges per criterion (38 modules): Cohen's kappa 0.12 to 0.64, Gwet AC2 0.76 to 0.93.
The gap comes from scores bunching at 4 and 5, where kappa is known to fall while raw agreement stays high; both figures are in `agreement` of the combined analysis.
