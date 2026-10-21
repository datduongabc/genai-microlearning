# Gateway routing and reproducible experiments

Demo and normal use enable failover by default. The backend default remains
`gemini-fast` (Gemini 3.5 Flash Lite). `devops/gateway/config.yaml` owns provider
selection, retries, cooldowns, rate checks and fallback chains. Provider keys are
read only by the gateway from the runtime environment.

The two free Gemini aliases back up each other, with LiteLLM's bounded fallback
attempts preventing an endless cycle. If neither free model can serve a large
request, generation fails. Paid generation aliases are selected explicitly and are
never fallback targets. Outside experiment mode their ordinary and context-window
failures fall back to the free `gemini-fast`, then `gemini-flash`:

| Alias | Evaluation use |
| --- | --- |
| `claude-fast` | Leading cheap generation model |
| `openai-fast` | Alternative cheap generation model |
| `claude-pro` | Strong model for a small subset |
| `openai-pro` | Alternative strong model |

Exact provider model IDs and display names are defined in
[`devops/gateway/config.yaml`](../devops/gateway/config.yaml).
No paid API call is required to validate configuration. Provider availability and
credit still require explicit deployment verification. Embeddings remain local
Qwen3-Embedding-0.6B through `embed-default`. No fallbacks apply to embeddings or transcription.

`groq-pro` is a paid lecturer opt-in model, excluded from the judge pool.
Judge selection, including for modules generated with `groq-pro`, follows the
[rubric](rubric.md#1-thiết-kế-nghiên-cứu).
Its fallback targets are free Gemini models; it is never an automatic backup.

Planner, Learning Objective, relevance checks and question checks can use the
internal `gemini-fast-small` / `gemini-flash-small` profiles. The backend selects
these profiles only for Gemini requests with at most 6,000 estimated tokens,
including the response schema and a 2,048-token output budget. Their only backup is
the other Gemini small profile. Larger calls keep the ordinary
alias. Content, including no-RAG Content and its self-check, never opts into the
small profile. Slides, Example and question generation also keep the ordinary
alias. The historical `groq-fast` alias is unavailable in the current configuration.

Groq's `groq-pro` deployment now uses the paid Developer plan, declared as
`tpm: 250000`, `max_input_tokens: 128000` and `max_output_tokens: 2048`. The
[model's context is 131,072 tokens](https://console.groq.com/docs/models), so
this leaves room for the bounded output and response schema. A whole study
packet of roughly 9.6–12.6k tokens fits in one call; the former 3,952-token cap
was a gateway setting for the 8,000 TPM free plan, not the model's context.
Billing must be enabled; account-specific limits and traffic outside this
gateway can still cause 429s. LiteLLM pre-call checks and provider limits enforce
capacity. Oversized normal calls can fall back to Gemini; study judges pin
each chosen alias with fallbacks disabled and keep incomplete ratings resumable without truncation.
The deployment requests low reasoning effort so the output allowance can
include the JSON answer.

LiteLLM retries once, cools failed deployments for 60 seconds, and applies both
ordinary and context-window fallbacks. `routing_strategy: simple-shuffle` spreads
requests among deployments sharing an alias. The optional `gemini-balanced`
alias has two distinct free Gemini deployments for actual load balancing. The
default single-model aliases favor their primary and use backups only on failure.
To distribute among more deployments, add them under the same `model_name` in
this configuration; duplicate entries using the same key and model add no quota.

## Experiments

Set **`LLM_EXPERIMENT_MODE=true`** in the backend environment before generating
experiment modules. Compose forwards it; `devops/.env.example` defaults to false.
Restart/recreate the backend with the project's normal deployment procedure when
changing this environment setting. Run both study arms with the same pinned alias.
Do not change this environment switch while experiment jobs are queued/running.

Pinned requests send LiteLLM's `disable_fallbacks: true` in the OpenAI request
body. They use the original alias, bypass small profiles and refuse
`gemini-balanced` and internal small aliases. Retries may use the same deployment;
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
to free Gemini. Experiment generation still refuses fallback and can fail on the
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
