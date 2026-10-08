# Gateway routing and reproducible experiments

Demo and normal use enable failover by default. The backend default remains
`gemini-fast` (Gemini 3.5 Flash Lite). `devops/gateway/config.yaml` owns provider
selection, retries, cooldowns, rate checks and fallback chains. Provider keys are
read only by the gateway from the runtime environment.

Large-safe aliases (`gemini-fast`, `gemini-flash`, OpenAI and Claude aliases) fall
back to the other free Gemini models. Paid providers are never automatic backups;
select their aliases explicitly to use them. No fallbacks apply to embeddings or
transcription. The two Gemini aliases back up each other, with LiteLLM's bounded
fallback attempts preventing an endless cycle. If neither free model can serve a
large request, generation fails rather than sending it to Groq or a paid model.

Planner, Learning Objective, relevance checks and question checks can use the
internal `gemini-fast-small` / `gemini-flash-small` profiles. The backend selects
these profiles only for Gemini requests with at most 6,000 estimated tokens,
including the response schema and a 2,048-token output budget. Their backups are
the other Gemini small profile, then `groq-pro`. Larger calls keep the ordinary
alias. Content, including no-RAG Content and its self-check, never opts into the
small profile. Slides, Example and question generation also keep the ordinary
alias. `groq-fast` is removed.

Groq has an 8,000 TPM budget, so Content's 20–66k-token requests cannot fit. Its
router deployment declares `tpm: 8000`, a conservative 3,952-token input ceiling
and a 2,048-token output ceiling; LiteLLM pre-call checks and provider 429s enforce
capacity. A request that exceeds its input window can fall back to Gemini. The
small profiles reserve room below the TPM ceiling; this is not a per-user quota.
Account-wide traffic outside this gateway still consumes the provider's quota.

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
still reject model selection with HTTP 422. A probe 429 is treated as usable,
because normal generation can rely on router fallbacks. Experiment generation
still refuses fallback and can fail on the same quota-limited model.

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
Tests exercise the router's configuration and pinned/fallback behavior without
provider calls; private live proof uses a deliberately invalid primary credential
and verifies a saved module's served model plus refusal to fall back in experiments.

LiteLLM references: [fallbacks](https://docs.litellm.ai/docs/proxy/reliability),
[router/load balancing and cooldowns](https://docs.litellm.ai/docs/routing).
