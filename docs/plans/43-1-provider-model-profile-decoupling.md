# 43-1: Provider / Model Profile Decoupling

**Parent:** [43-paper-review-benchmark-hardening](43-paper-review-benchmark-hardening.md)
**Status:** completed
**Goal:** Make model choices in `examples/paper_writing.py` explicit per-stage rather than implicitly inherited from the engine default, so benchmark runs declare which model profile they intend to use.

## Problem

The paper-writing workflow's LLM nodes (`wf.llm(...)`) do not specify models — they inherit `EngineConfig.llm_default_model` (currently `"claude-sonnet-4-6"` via env fallback). This is implicit rather than explicit:

- a benchmark run does not declare per-stage model intent (e.g., "use a strong model for review merging, a fast model for aspect surveys")
- changing the global default changes all stages silently
- the `search_web` tool function hardcodes `"perplexity/sonar-pro-search"` as a direct API call outside the engine's model resolution

Note: the original plan described "multiple hardcoded provider-specific model strings" in the workflow definition — that was **incorrect**. The graph nodes themselves carry no model strings; the coupling is at the engine-config level, not the workflow level.

### Existing model resolution path

- `LLMOperator.model: str` — nodes can carry an explicit model, but the paper-writing workflow does not set it
- `EngineConfig.llm_default_model` — global fallback, set from `DAN_LLM_MODEL` or `"claude-sonnet-4-6"`
- `ProviderRegistry` — resolves model strings to providers via prefix patterns + override map
- `ModelGateway` — wraps the registry; no alias or profile layer
- `TierPolicy` / `model_policy` — optional selection strategies on nodes, not used in this workflow
- No named "model profile" catalog exists in the codebase

## Tasks

- [ ] 1. Add per-stage model intent to the workflow graph nodes (set `model` on key `LLMOperator` stages, or introduce a profile map passed to `build_paper_workflow()`)
- [ ] 2. Externalize the `search_web` tool function's model string (`perplexity/sonar-pro-search`) into configuration
- [ ] 3. Define a minimal profile/config contract for paper-writing stages (e.g., `{"survey": "claude-sonnet-4-6", "review": "claude-sonnet-4-6", "web_search": "perplexity/sonar-pro-search"}`)
- [ ] 4. Ensure the selected profile is recorded in run artifacts and logs
- [ ] 5. Add tests that prove the workflow can switch profiles without code edits

## Likely Files

- `examples/paper_writing.py`
- `src/dan/server/runtime_config.py`
- `src/dan/providers/registry.py`
- `src/dan/models/nodes.py` (where `LLMOperator.model` is defined)
- `tests/test_loader/test_paper_writing_parity.py`

## Notes

- The goal is not to remove provider diversity; it is to make provider choice deliberate and observable.
- The workflow should still support strong-model stages, but those choices need to come from config or a profile map.
- If a profile cannot be resolved, the failure should be explicit and early.
- Consider whether `model_policy` or `task_tier` on `LLMOperator` is the right vehicle, or whether a simpler profile dict passed to `build_paper_workflow()` is sufficient for this workflow.

## Completion Notes (2026-03-26)

- `build_paper_workflow()` now accepts `model_profile`, `default_model`, and `node_model_overrides`.
- Per-stage LLM nodes use explicit resolved models instead of inheriting silently from the engine default.
- Web fallback search now accepts a configurable `web_search_model`.
- Saved artifact summaries record `model_profile` and `model_selection`.
