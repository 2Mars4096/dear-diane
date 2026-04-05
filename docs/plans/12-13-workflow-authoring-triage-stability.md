# 12-13: Workflow-Authoring Triage Stability

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Make concierge/triage routing deterministic and reliable for conversational workflow-authoring prompts so the build lane is reached consistently instead of path-variably.

## Problem

Realistic workflow-authoring turns — conversational prompts like "Build me a workflow that monitors earnings reports and generates a weekly briefing" — still produce unstable concierge/triage results:

- `parsed=False` from intent extraction when the LLM doesn't emit a tool call
- unparseable triage JSON from the LLM triage stage
- inconsistent intent-compiler activation: the same prompt alternates between `tool_call_present=False, parsed=False` codegen timeout fallback and a successful `intent_compiler` build on clean reruns
- conversational phrasing ("That's actually great. Following the example of RKLB...") can route through `conversation` instead of `workflow_build` because the triage classifier doesn't recognize continuation-style build requests

This is upstream of all graph-generation improvements (44-series) — if the message never reaches the build lane, the structured pipeline never fires.

## Failure Modes (Observed)

1. **LLM triage JSON parse failure** — the triage LLM call returns malformed JSON or a response that doesn't match the expected schema, causing fallback to a default route that may not be `workflow_build`
2. **Embedding triage misclassification** — `_embedding_triage_result()` assigns `intent=ask` and `target=general` for a prompt that should be `intent=agent` / `target=workflow`
3. **Lexical scenario gaps** — `evaluate_lexical_scenarios()` doesn't have patterns for conversational workflow-authoring phrasing ("build me a...", "create a workflow that...", "following the example of...")
4. **Intent extraction tool-call absence** — the LLM doesn't emit a `WorkflowIntent` tool call, so `parse_intent_from_result()` falls back to JSON-in-content parsing which may fail or return `parsed=False`
5. **Continuation-turn misrouting** — follow-up turns that continue a workflow-build conversation ("That's actually great, now add a review step") route as `conversation` instead of `workflow_build` because triage doesn't consider thread context

## Tasks

- [x] 1. Add regression fixtures for known-unstable routing patterns
  - [x] 1-1. Collect 5–10 realistic workflow-authoring prompts that currently misroute (equity research, folder digest, earnings note, continuation turns)
  - [x] 1-2. Add deterministic triage unit tests that assert each prompt reaches `workflow_build` stage in `_determine_stage()`
  - [x] 1-3. Add a test that continuation turns within an active workflow-build thread still route to `workflow_build`
- [x] 2. Harden the lexical/embedding triage layers
  - [x] 2-1. Add lexical scenario patterns for common workflow-authoring phrases ("build me a workflow", "create a workflow that", "design a pipeline", "automate the process of")
  - [x] 2-2. Improve embedding triage's action-hint assignment so `workflow_build` hints are emitted for prompts with clear build intent
  - [x] 2-3. Add thread-context awareness to triage: if the current thread already has a workflow-build turn, bias continuation turns toward `workflow_build` unless explicitly redirected
- [x] 3. Harden LLM triage JSON parsing
  - [x] 3-1. Add structured output / JSON mode for the triage LLM call where the provider supports it
  - [x] 3-2. Add a JSON repair layer for common LLM formatting errors (trailing commas, unquoted keys, markdown fences around JSON)
  - [x] 3-3. Make the fallback route for unparseable triage JSON context-aware: if the thread is in a build conversation, fall back to `workflow_build` instead of `conversation`
- [x] 4. Stabilize intent extraction tool-call reliability
  - [x] 4-1. Audit the intent extraction system prompt and tool schema for ambiguity that causes the LLM to skip the tool call
  - [x] 4-2. Strengthen the JSON-in-content fallback path so `parsed=False` is rarer: try harder to extract a `WorkflowIntent` from the response text before giving up
  - [x] 4-3. Add a bounded retry (max 1) for intent extraction when `tool_call_present=False` and the triage stage is `workflow_build`
- [x] 5. Add metrics and observability
  - [x] 5-1. Record triage route stability metrics: for each prompt, log `route_source` (fast/lexical/embedding/llm), `stage`, `intent`, and whether the route matched the expected outcome
  - [x] 5-2. Add a triage consistency eval: run the same prompt N times and measure route stability (target: >95% same-route on 10 runs)
  - [x] 5-3. Surface triage diagnostic metadata in chat events so build-lane failures can be traced back to routing decisions

## Likely Files

Existing (modify or extend):
- `src/dan/server/concierge/triage.py` — `triage()`, `fast_classify_text()`, `_embedding_triage_result()`, `evaluate_lexical_scenarios()`, LLM triage JSON parsing
- `src/dan/server/concierge/triage_scenarios.py` — lexical scenario patterns
- `src/dan/server/concierge/tier_executors.py` — `_determine_stage()` mapping triage results to concierge stages
- `src/dan/server/concierge/intent_catalog.py` — intent classification patterns
- `src/dan/server/chat_manager.py` — `_parse_intent_from_result()`, `_generate_workflow_from_intent()`
- `src/dan/server/agent_runtime/workflow_generation_helpers.py` — `parse_intent_from_result()`
- `src/dan/server/chat/helpers.py` — `detect_chat_mode()`
- `tests/test_concierge/test_tiered_dispatch.py`
- `tests/test_concierge/test_triage.py` (if it exists, or create)
- `tests/eval/workflow_contract_comparison_prompts.json` — existing prompt fixtures

## Decisions

- Triage stability is a prerequisite for structured workflow generation (44-series) — a great build pipeline is useless if messages don't reach it.
- Lexical and embedding layers should handle the common cases deterministically; the LLM triage layer should only fire for genuinely ambiguous prompts.
- Thread-context awareness should bias routing but not override explicit user intent.
- Intent extraction retries should be bounded (`DAN_INTENT_EXTRACTION_MAX_RETRIES`, default `1`) and only fire when triage confidently identified a build turn — not hardcoded.
- Metrics should measure route stability per-prompt, not just aggregate success rates.
- **No scattered inline keyword lists or hardcoded classification thresholds.** Large lexical corpora and triage-pattern tables should live in a versioned config module or JSON asset, not be duplicated across helpers or forced through env vars. Scalar confidence thresholds and retry caps should remain `DAN_*` env-var configurable.

## Notes

- Landed across `src/dan/server/concierge/triage.py`, `src/dan/server/concierge/triage_scenarios.py`, `src/dan/server/concierge/tier_executors.py`, `src/dan/server/concierge/runtime/__init__.py`, `src/dan/meta/intent_extraction.py`, `src/dan/server/agent_runtime/workflow_generation_helpers.py`, and `src/dan/server/agent_runtime/workflow_generation.py`.
- Focused regressions now cover explicit workflow-authoring phrases, continuation-turn routing, repeated-trial route stability, trailing-comma triage JSON repair, JSON-object response mode with legacy fallback, looser `WorkflowIntent` parsing, and bounded build-lane intent-extraction retry.
- 2026-04-04 follow-up: output-persistence phrasing now has deterministic write routing too. Lexical/fallback triage recognizes prompts like `save the outline to disk`, `persist documentation`, and `export the summary to a file` as `write_file` turns instead of leaving those phrases to LLM guesswork.
- 2026-04-04 follow-up: workflow-understanding prompts like `what does this workflow do` / `what is this workflow about` now route as `workflow_query`, and the follow-up heuristics no longer reinterpret those turns as workflow edits just because the thread already has workflow activity.
- Concierge telemetry/session metadata now carry `route_source`, `concierge_stage`, `session_tier`, `scenario_id`, and `scenario_confidence`, so build-lane failures can be traced back to the routing decision that produced them.
- The two backlog items this plan consolidates: "Stabilize concierge/triage performance for workflow-authoring turns" and "Stabilize intent extraction / intent-compiler variability for the equity workflow prompt."
- The 44-5 (routing/rollout) plan assumes triage reliably delivers build-intent prompts to the generation pipeline. This plan is the upstream prerequisite.
- The existing `fast_classify_text()` → `evaluate_lexical_scenarios()` → `_embedding_triage_result()` → LLM triage cascade is the right architecture — the problem is coverage gaps in the first three layers, not the cascade design itself.
- The equity-research prompt is the motivating case, but the fix should cover the general class of conversational workflow-authoring prompts.
