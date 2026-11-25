# Gateway routing and reproducible experiments

Demo and normal use enable failover by default.
The backend default is `openai-fast` (GPT-6 Luna).
By the rule in `docs/measurements/2026-11-16-four-chat-models.md` (cheapest model within 0.03 of the best chat score) it is the cheapest in every measured task: chat, 131 injection cases, and flashcards with a video script (`docs/measurements/2026-11-16-four-models-rest.md`).
Module generation cost per model is not measured yet.
The formula re-read uses `claude-fast` (Claude Haiku 5.5): on 12 flagged PDF pages it passed the acceptance check on 12/12, as `gemini-flash` did, at $0.0004 a page against $0.0101 and a median 4.9 s against 15.5 s (`docs/measurements/2026-11-16-four-models-rest.md`, step 3).
`devops/gateway/config.yaml` owns provider selection, retries, rate checks and fallback chains.
Provider keys are read only by the gateway from the runtime environment.

## Providers and keys

Since 2026-11-08 one key covers every alias the backend asks for; a second, optional key serves only the fallbacks for when OpenRouter itself fails:

| Key | Aliases | Why this route |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | `gemini-flash`, `claude-fast`, `openai-fast`, `deepseek-flash`, `embed-default`, `transcribe`, formula re-read, voices and pictures | One key and one balance for every model |
| `GEMINI_API_KEY` (optional) | `gemini-flash-direct`, `embed-default-direct` | The same two Gemini models straight from Google AI Studio, so OpenRouter is not the one thing every call depends on |

On 2026-11-25 OpenRouter answered every inference call from a Vietnamese network with `400 Request path cannot be relayed`, whatever the model, upstream pin or IP version, while its free `GET /models` and `GET /credits` still worked.
Every remote alias failed at once, the `gemini-flash` fallback included, because it also went through OpenRouter.
The direct aliases are the answer to that kind of outage.
The project uses a free-tier `GEMINI_API_KEY`, because enabling billing on Google AI Studio needs a prepayment of about 800,000 VND.
The free tier has two costs.
It limits requests per minute and per day, which is acceptable for a route used only while OpenRouter is down; a refused embedding batch is waited out by the backend like any 429.
Google may also use what the free tier receives to improve its products, so during an OpenRouter outage course material reaches a route without the `data_collection: deny` promise of the others.
A key of a billed project removes that cost without any other change.
Left empty, the direct route fails like any provider without a key, one more failed attempt at the end of the chain, so leaving it empty turns the fallback off.

Whisper (`transcribe`) returns segment timestamps through OpenRouter (`verbose_json`), which citations of recordings need.
LiteLLM has no OpenRouter transcription route, so the alias uses the OpenAI protocol with OpenRouter's `api_base`.

Every OpenRouter alias sends `provider: {order: [<one upstream>], allow_fallbacks: false, data_collection: deny}`.
Without it OpenRouter picks an upstream per request (price, latency, uptime), so two runs of one alias could be served by different hosts and quantisations.
With it a failing upstream is an error, which experiment mode needs, and prompts, which carry course material, only reach upstreams that neither store nor train on them.
OpenRouter bills the upstream's token price and takes 5.5% when credit is bought.
Results measured before 2026-11-08 called Anthropic, OpenAI and Groq directly; `devops/loadtest/real_gateway*.yaml` now reach the same models through OpenRouter, pinned to the same upstreams.

## The four chat models

Lecturers choose among exactly four chat models:

| Alias | Model | Upstream |
| --- | --- | --- |
| `gemini-flash` | Gemini 3.8 Flash | Google AI Studio |
| `claude-fast` | Claude Haiku 5.5; formula re-read | Anthropic |
| `openai-fast` | GPT-6 Luna, the default | OpenAI |
| `deepseek-flash` | DeepSeek V4.1 Flash | DeepInfra, endpoint `deepinfra/fp8` |

`deepseek-flash` names the endpoint `deepinfra/fp8` rather than the provider `deepinfra`.
The bare provider slug also matches DeepInfra's priority endpoint, at another price.
All four have chat Ragas and prompt-injection numbers in `docs/measurements/2026-11-16-four-chat-models.md`; module cost per model is not measured yet.
`tests/unit/test_gateway_config.py` pins the offered set.

Outside experiment mode an ordinary failure of the other three falls back to `gemini-flash`, then to `gemini-flash-direct`; a failure of `gemini-flash` goes straight to `gemini-flash-direct`.
LiteLLM does not follow a fallback's own fallbacks, so each chain in the configuration lists both.
A context-window failure falls back to `gemini-flash` only, since the direct route has the same window.
`gemini-flash-direct` is marked `hidden`, so the model picker never offers it; a module it served records `gemini-3.8-flash` as its `served_model`, which is still the Gemini family for the judge and fact-check rules.

Claude Haiku 5.5 and GPT-6 Luna accept only `temperature=1`.
Their deployments drop the `temperature` the pipeline sends (0 at most steps), so they always sample at 1.
Before this, a pinned experiment failed on its first call, and outside experiment mode every temperature-0 step silently fell back to Gemini, which `generation_models` recorded as a Gemini `served_model`.
For the evaluation this means their modules are not deterministic across reruns; the Planner result is still cached per document, so the module topic and Learning Objective stay stable.

A lecturer whose saved default is no longer offered generates with the system default instead.
Work stored with a dropped alias does the same when it is used again: a retried module, flashcard or video job and a chat edit of an old module run on the system default.
A no-RAG control keeps its pair's alias, so a pair never differs in more than retrieval.

Exact provider model IDs and display names are defined in [`devops/gateway/config.yaml`](../devops/gateway/config.yaml).
No paid API call is required to validate configuration.
Provider availability and credit still require explicit deployment verification.
Embeddings come from Gemini Embedding 001 through `embed-default`, and fall back to the same model through `embed-default-direct`.
Only the same model may serve as an embedding fallback: a vector of another model cannot be compared with the stored chunks.
Transcription, voices and pictures have no fallback; they still depend on OpenRouter alone.

PDF ingestion sends a rendered page to `FORMULA_VISION_MODEL` (default `claude-fast`; the alias must accept images) only when its rebuilt formulas still look broken, at most `FORMULA_VISION_MAX_PAGES` (default 40) pages per document.
Outside experiment mode a failed `claude-fast` call falls back to `gemini-flash`, then `gemini-flash-direct`, like any other call of that alias.
There is no ground truth per page: the measurement counts pages that pass the acceptance check (at least 90% of the text layer's words kept, at most 10% new words), which shows the transcript keeps the text, not that its formulas are right.
An empty `FORMULA_VISION_MODEL` turns the re-read off; such pages keep the rebuilt text and get a `formula_unverified` warning.
See `docs/measurements/2026-10-27-formula-figure-reader.md`.

Judge selection follows the [rubric](rubric.md#1-thiết-kế-nghiên-cứu).
The fact check of flashcards and video scripts uses `claude-fast`, or `openai-fast` for a Claude generator.
Its calls run with fallbacks off, so a judge outage flags the statement as "Chưa kiểm được" instead of letting `gemini-flash` check it.

LiteLLM retries once, does not cool a deployment down after a failure (the backend retries a 429 itself), and applies both ordinary and context-window fallbacks.
To distribute among more deployments, add them under the same `model_name` in this configuration; duplicate entries using the same key and model add no quota.

## Experiments

Set **`LLM_EXPERIMENT_MODE=true`** in the backend environment before generating
experiment modules. Compose forwards it; `devops/.env.example` defaults to false.
Restart/recreate the backend with the project's normal deployment procedure when
changing this environment setting. Run both study arms with the same pinned alias.
Do not change this environment switch while experiment jobs are queued/running.

Pinned requests send LiteLLM's `disable_fallbacks: true` in the OpenAI request
body. They use the original alias. Retries may use the same deployment;
quota, authentication or context-window failures end the request without switching
models. `gateway.experiment_mode()` applies the same switch to one script scope.
`export_study_pair.py` (including new no-RAG generation), `run_judge.py` and
`run_claim_judge.py` enable it automatically. Pin the original RAG generation with
the environment switch too: exporting cannot undo fallbacks in an existing module.

The one-token model probe always disables fallbacks so it tests the selected
alias's own credentials and credit. Authentication errors and insufficient credit
still reject model selection with HTTP 422, including credit/billing 429 errors
such as OpenAI's `insufficient_quota` or `billing_hard_limit_reached`. Plain
rate-limit 429s, including Gemini `RESOURCE_EXHAUSTED`, are treated as usable,
because normal generation, including paid aliases, can rely on router fallbacks
to Gemini. Experiment generation still refuses fallback and can fail on the
same quota-limited model.
Provider refusals and failures, a 400 or a 5xx such as OpenRouter's
"Request path cannot be relayed" or Gemini's 503 "high demand", are treated as
usable for the same reason, since the chain can still reach `gemini-flash-direct`.
An alias the gateway does not know (LiteLLM's "Invalid model name", or a 404),
a missing provider key and a rejected gateway key stay refused.
In experiment mode only plain rate-limit 429s pass, because that run will not fall back.

## Actual model records

Migration `0011_module_generation_models` adds nullable JSONB
`modules.generation_models`. Each completed generation saves every successful chat
response in order as `requested_alias` and `served_model` from the
response. Records saved before 2026-11-17 also carry `routed_alias`, which always equalled `requested_alias`.
Multiple served models are kept: a module can use a backup on one step
and its primary on another. The module preview displays the requested alias and
served models, and `GET /modules/{id}` returns the complete record. Historical
modules remain NULL; their served models cannot be inferred from a mutable alias.

Study files keep each arm's records in `generation_models`, outside the blinded
`packet`. `generator_model` remains the requested alias for compatibility with
older study files. Judge runners check every recorded served model for same-family
bias, falling back to alias lookup only for legacy files without records. Judge
runs remain pinned and keep their existing `judge_model` output field.
An old RAG module and a newly pinned control may have different real models; the
records expose this rather than claiming the chosen aliases prove equivalence.

The deployment still needs valid runtime keys and available provider capacity.
Offline regression coverage is in `backend/tests/unit/test_gateway_config.py`,
`backend/tests/integration/test_llm_gateway.py` and `backend/tests/integration/test_use_retrieval.py`;
it does not establish live provider availability.
For live deployment verification, use a deliberately invalid primary credential
and check a saved module's served model plus refusal to fall back in experiments.

LiteLLM references: [fallbacks](https://docs.litellm.ai/docs/proxy/reliability),
[router/load balancing and cooldowns](https://docs.litellm.ai/docs/routing).
