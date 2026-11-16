# Gateway routing and reproducible experiments

Demo and normal use enable failover by default.
The backend default is `deepseek-flash` (DeepSeek V4.1 Flash), chosen in `docs/measurements/2026-11-16-four-chat-models.md`: chat quality within 0.03 of the best and near the lowest measured cost.
The formula re-read stays on `gemini-flash`, which reads images best.
`devops/gateway/config.yaml` owns provider selection, retries, rate checks and fallback chains.
Provider keys are read only by the gateway from the runtime environment.

## Providers and keys

Since 2026-11-08 one key covers every remote alias:

| Key | Aliases | Why this route |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | `gemini-flash`, `claude-fast`, `openai-fast`, `deepseek-flash`, `embed-openai`, `transcribe`, formula re-read | One key and one balance for every model |
| `EMBEDDER_API_KEY` | `embed-default` | The local embedder container |

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
| `gemini-flash` | Gemini 3.8 Flash; reads images, formula re-read | Google AI Studio |
| `claude-fast` | Claude Haiku 5.5 | Anthropic |
| `openai-fast` | GPT-6 Luna | OpenAI |
| `deepseek-flash` | DeepSeek V4.1 Flash, the default | DeepInfra, endpoint `deepinfra/fp8` |

`deepseek-flash` names the endpoint `deepinfra/fp8` rather than the provider `deepinfra`.
The bare provider slug also matches DeepInfra's priority endpoint, at another price.
All four have chat Ragas and prompt-injection numbers in `docs/measurements/2026-11-16-four-chat-models.md`; module cost per model is not measured yet.
`tests/unit/test_gateway_config.py` pins the offered set.

`gemini-flash` has no fallback; it is the fallback of the other three.
Outside experiment mode their ordinary and context-window failures fall back to `gemini-flash`.

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
Embeddings remain local Qwen3-Embedding-0.6B through `embed-default`.
No fallbacks apply to embeddings or transcription.

PDF ingestion sends a rendered page to `FORMULA_VISION_MODEL` (default `gemini-flash`, which must accept images) only when its rebuilt formulas still look broken, at most `FORMULA_VISION_MAX_PAGES` (default 40) pages per document.
An empty `FORMULA_VISION_MODEL` turns the re-read off; such pages keep the rebuilt text and get a `formula_unverified` warning.
See `docs/measurements/2026-10-27-formula-figure-reader.md`.

Judge selection follows the [rubric](rubric.md#1-thiết-kế-nghiên-cứu).
The fact check of flashcards and video scripts uses `claude-fast`, or `openai-fast` for a Claude generator.

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

## Actual model records

Migration `0011_module_generation_models` adds nullable JSONB
`modules.generation_models`. Each completed generation saves every successful chat
response in order as `requested_alias`, `routed_alias`, and `served_model` from the
response. Multiple served models are kept: a module can use a backup on one step
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
