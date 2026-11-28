"""Stands in for the LLM gateway in load tests, so that no provider is called or paid for.

Each chat call waits a random time around the latency measured on real models
(docs/measurements/2026-11-04-five-subject-baseline.md), then returns JSON that fits the schema the
caller asked for. The content is meaningless: this measures the system, not generation quality.
It has no rate limit, so results show the capacity of this system, not of a provider account.
Embedding calls go on to the real embedder."""

import asyncio
import itertools
import json
import os
import random

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

MEDIAN_SECONDS = float(os.environ.get("MOCK_LLM_MEDIAN_SECONDS", "13.3"))
SIGMA = float(os.environ.get("MOCK_LLM_SIGMA", "0.24"))
COMPLETION_TOKENS = int(os.environ.get("MOCK_COMPLETION_TOKENS", "2700"))
STREAM_FIRST_SECONDS = float(os.environ.get("MOCK_STREAM_FIRST_SECONDS", "1.5"))
STREAM_SECONDS = float(os.environ.get("MOCK_STREAM_SECONDS", "6"))
STREAM_PIECES = int(os.environ.get("MOCK_STREAM_PIECES", "60"))
EMBEDDER_URL = os.environ.get("EMBEDDER_URL", "http://embedder:80")
EMBEDDER_KEY = os.environ.get("EMBEDDER_API_KEY", "")

app = FastAPI()
_counter = itertools.count(1)
_client = httpx.AsyncClient(timeout=120)


def fake_value(schema: dict, root: dict, name: str = "value"):
    """A value that satisfies a JSON schema, with text unique enough to pass duplicate checks."""
    if "$ref" in schema:
        return fake_value(root["$defs"][schema["$ref"].rsplit("/", 1)[-1]], root, name)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return fake_value(options[0], root, name)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type", "string")
    if kind == "object":
        return {key: fake_value(sub, root, key) for key, sub in schema.get("properties", {}).items()}
    if kind == "array":
        size = max(schema.get("minItems", 1), min(schema.get("maxItems", 1), 3))
        return [fake_value(schema.get("items", {}), root, name) for _ in range(size)]
    if kind == "integer":
        return schema.get("minimum", 0)
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return True
    return f"Nội dung giả lập {name} số {next(_counter)} [S1]"


@app.get("/model/info")
@app.get("/v1/model/info")
def model_info():
    return {"data": []}


@app.get("/health/liveliness")
def health():
    return "ok"


async def _stream_reply():
    """A reply in timed pieces, for measuring how many open streams one API process holds. Not a model's latency."""
    await asyncio.sleep(STREAM_FIRST_SECONDS)
    for number in range(STREAM_PIECES):
        piece = {"id": "mock", "object": "chat.completion.chunk", "created": 0, "model": "mock-model",
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": f"phần {number} "}, "finish_reason": None}]}
        yield f"data: {json.dumps(piece, ensure_ascii=False)}\n\n"
        await asyncio.sleep(STREAM_SECONDS / STREAM_PIECES)
    yield "data: [DONE]\n\n"


@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    if body.get("stream"):
        return StreamingResponse(_stream_reply(), media_type="text/event-stream")
    await asyncio.sleep(random.lognormvariate(0, SIGMA) * MEDIAN_SECONDS)
    response_format = body.get("response_format") or {}
    schema = (response_format.get("json_schema") or {}).get("schema")
    content = json.dumps(fake_value(schema, schema), ensure_ascii=False) if schema else "Nội dung giả lập [S1]"
    prompt_tokens = len(json.dumps(body.get("messages", []), ensure_ascii=False)) // 4
    return {
        "id": f"mock-{next(_counter)}", "object": "chat.completion", "created": 0, "model": "mock-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": COMPLETION_TOKENS,
                  "total_tokens": prompt_tokens + COMPLETION_TOKENS},
    }


@app.post("/embeddings")
@app.post("/v1/embeddings")
async def embeddings(request: Request):
    # An embedder started without --api-key takes no header, and "Bearer " alone is refused by httpx.
    headers = {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {EMBEDDER_KEY}"} if EMBEDDER_KEY else {})
    response = await _client.post(f"{EMBEDDER_URL}/v1/embeddings", content=await request.body(), headers=headers)
    return response.json()


JEV_SECONDS = float(os.environ.get("MOCK_JEV_SECONDS", "0.3"))


@app.post("/v1/systemone")
async def jev(request: Request):
    """Stands in for TypeSafe's Jev when run as its own service (the `jev` service), so that it can be stopped
    alone. Scores sit above the chat's threshold: a run measures the system, not ranking quality."""
    await request.body()
    await asyncio.sleep(JEV_SECONDS)
    return {"answers": {"relevant": {"noul": round(random.uniform(0.3, 1.0), 3)}}}
