"""Ragas faithfulness and answer relevance for the course chat answers of scripts/chat_eval.py.

Runs outside the backend's environment, so Ragas is not a dependency of the application:

    GATEWAY_KEY=... uv run --no-project --python 3.12 --with ragas==0.2.15 --with langchain-openai==0.3.12 \\
        python scripts/chat_eval_ragas.py --answers ../docs/measurements/chat-eval/answers-gemini-fast.jsonl \\
        --judge <alias> --gateway http://localhost:4000 --out ../docs/measurements/chat-eval/ragas-<alias>.jsonl

- Faithfulness: the answer is split into claims and each is checked against the eight contexts the
  model was given; the score is the share supported. ResponseRelevancy: the judge writes three
  questions the answer would fit, and the score is their mean embedding similarity to the question
  asked (0 when the judge finds the answer noncommittal). Embeddings come from the gateway's
  embed-default, the application's own embedder.
- The judge must come from another model family than the model that wrote the answers, as for the
  module rubric (docs/rubric.md); --smoke-test lifts that for a plumbing check whose scores are not
  results.
- Resumed like chat_eval.py; prints the means with 95 % bootstrap intervals, overall and per subject.
"""

import argparse
import asyncio
import json
import math
import os
import random
import statistics
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import Faithfulness, ResponseRelevancy

# Which company trained the model behind a gateway alias or a served model name (devops/gateway/config.yaml).
FAMILIES = {
    "gemini": "google", "claude": "anthropic", "openai": "openai", "gpt": "openai", "deepseek": "deepseek",
    # Served model names of earlier runs, which the answer files keep.
    "llama": "meta", "qwen": "alibaba", "minimax": "minimax",
}


# The gateway's fallbacks would let another model judge when the judge fails (claude-fast falls back to
# gemini-flash, which may be the family that wrote the answers), so they are turned off, and the provider's own model
# name is asked back and recorded for every call, as app/llm/gateway.py does for an experiment.
PINNED = {"disable_fallbacks": True, "metadata": {"_complexity_router_return_raw_model_name": True}}


class ServedModels(BaseCallbackHandler):
    def __init__(self):
        self.names: list[str] = []

    def on_llm_end(self, response, **kwargs):
        name = (response.llm_output or {}).get("model_name")
        self.names.append(name or "?")


def family(name: str) -> str | None:
    name = name.lower().rsplit("/", 1)[-1]
    for prefix in sorted(FAMILIES, key=len, reverse=True):
        if name.startswith(prefix):
            return FAMILIES[prefix]
    return None


def bootstrap(values: list[float], rounds: int = 2000, seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(rounds))
    return means[int(0.025 * rounds)], means[int(0.975 * rounds) - 1]


async def score_all(args) -> None:
    rows = [json.loads(line) for line in args.answers.read_text(encoding="utf-8").splitlines() if line.strip()]
    writers = {family(m) for row in rows for m in row["served_models"]} - {None}
    judge_family = family(args.judge)
    if judge_family is None:
        raise SystemExit(f"unknown family for judge {args.judge!r}; add it to FAMILIES")
    if judge_family in writers and not args.smoke_test:
        raise SystemExit(f"judge {args.judge} is {judge_family}, the family that wrote the answers {sorted(writers)}")

    key = os.environ.get("GATEWAY_KEY", "")
    served = ServedModels()
    llm = LangchainLLMWrapper(ChatOpenAI(model=args.judge, base_url=f"{args.gateway}/v1", api_key=key,
                                         temperature=0, max_retries=6, timeout=120, extra_body=PINNED,
                                         callbacks=[served]))
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(
        model="embed-default", base_url=f"{args.gateway}/v1", api_key=key, check_embedding_ctx_length=False))
    faithfulness = Faithfulness(llm=llm)
    relevance = ResponseRelevancy(llm=llm, embeddings=embeddings)

    done = set()
    if args.out.exists():
        done = {json.loads(line)["chunk_id"] for line in args.out.read_text(encoding="utf-8").splitlines() if line.strip()}
    todo = [row for row in rows if row["chunk_id"] not in done][: args.limit or None]
    with args.out.open("a", encoding="utf-8") as sink:
        for number, row in enumerate(todo, 1):
            sample = SingleTurnSample(user_input=row["question"], response=row["answer"], retrieved_contexts=row["contexts"])
            scores = {"chunk_id": row["chunk_id"], "course": row["course"], "judge": args.judge,
                      "smoke_test": args.smoke_test}
            served.names.clear()
            for name, metric in (("faithfulness", faithfulness), ("answer_relevancy", relevance)):
                try:
                    value = float(await metric.single_turn_ascore(sample))
                    # NaN: the answer made no claim to check, as "the course materials do not cover this".
                    scores[name] = None if math.isnan(value) else value
                    if math.isnan(value):
                        scores[f"{name}_error"] = "no claims"
                except Exception as error:  # kept as missing, not as zero: a judge failure is not a bad answer
                    scores[name] = None
                    scores[f"{name}_error"] = f"{type(error).__name__}: {str(error)[:200]}"
            scores["served_models"] = sorted(set(served.names))
            if any(family(name) in writers for name in served.names) and not args.smoke_test:
                raise SystemExit(f"a call was served by {scores['served_models']}, the answers' family")
            sink.write(json.dumps(scores, ensure_ascii=False) + "\n")
            sink.flush()
            print(f"{number}/{len(todo)} {row['course']}: faithfulness {scores['faithfulness']}, "
                  f"relevancy {scores['answer_relevancy']}")
            await asyncio.sleep(args.pause)


def refused(row: dict) -> bool:
    """ResponseRelevancy is exactly 0 when the judge finds the answer noncommittal: "the course materials do not
    cover this". Faithfulness then scores that sentence as an unsupported claim, so a right refusal would lower
    it; refusals are counted on their own instead and left out of faithfulness."""
    return row.get("answer_relevancy") == 0


def summarize(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    groups = {"all": rows}
    for row in rows:
        groups.setdefault(row["course"], []).append(row)
    print("Faithfulness over the answers that are not refusals; a number after it counts those with no claim.")
    print(f"{'set':24} {'n':>3} {'refused':>7}  {'faithfulness (95% CI)':28} {'answer relevancy (95% CI)':28}")
    for name, group in groups.items():
        cells = []
        for metric in ("faithfulness", "answer_relevancy"):
            scored = [r for r in group if not refused(r)] if metric == "faithfulness" else group
            values = [r[metric] for r in scored if r.get(metric) is not None and not math.isnan(r[metric])]
            if not values:
                cells.append("-")
                continue
            low, high = bootstrap(values)
            missing = len(scored) - len(values)
            cells.append(f"{statistics.fmean(values):.3f} ({low:.3f}-{high:.3f})" + (f" -{missing}" if missing else ""))
        print(f"{name:24} {len(group):>3} {sum(map(refused, group)):>7}  {cells[0]:28} {cells[1]:28}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--judge", required=True, help="gateway alias of the judge")
    parser.add_argument("--gateway", default="http://localhost:4000")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="score at most this many new answers")
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--smoke-test", action="store_true", help="allow a same-family judge; the scores are not results")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    if not args.summary_only:
        asyncio.run(score_all(args))
    summarize(args.out)


if __name__ == "__main__":
    main()
