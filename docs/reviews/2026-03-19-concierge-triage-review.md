# Concierge and Tiered Triage System -- Deep Review

**Reviewer:** Claude Opus 4.6
**Date:** 2026-03-19
**Scope:** Triage, tiered dispatch, tier executors, autonomy model, session management, runtime orchestration, domain learning

---

## Architecture Overview

The concierge system processes user messages through a multi-stage pipeline:

1. **Fast command dispatch** (`runtime.py:545-798`) -- slash commands handled without triage
2. **Fast social classification** (`triage.py:251-273`) -- lexical match on greetings/thanks
3. **Embedding triage** (`triage.py:369-447`) -- cosine similarity against prototype vectors
4. **LLM triage** (`triage.py:1123-1177`) -- structured JSON classification via LLM call
5. **Heuristic fallback** (`triage.py:995-1057`) -- regex-based classification when all else fails
6. **Autonomy resolution** (`autonomy.py:147-201`) -- determines how autonomously the system acts
7. **Context gathering** (`tiered_dispatch.py:197-321`) -- demand-driven context fetch
8. **Tier execution** (`tier_executors.py`) -- Tier 0 (instant), Tier 1 (single-shot), Tier 2 (multi-step)
9. **Post-completion bookkeeping** (`tiered_dispatch.py:517-688`) -- turn recording, memory, telemetry

---

## Findings

### P1 -- Critical Issues

#### P1-1: Duplicate fast-classification in dispatch path

**Files:** `tiered_dispatch.py:496-502`, `triage.py:1132-1139`

`TieredDispatcher._do_triage()` calls `fast_classify_text()` at line 499, and if it matches, it returns the fast result with only `_resolve_context()` called. But the main `triage()` function at line 1132 also calls `fast_classify_text()` as its first step. When `_do_triage()` returns the fast result, it skips post-processing (`_post_process_triage_result`) which normalizes context needs, resolves entities, and enforces furnace/workflow routes. This means a social message arriving through `_do_triage` gets a raw TriageResult without post-processing, while the same message arriving through the full `triage()` function does get post-processed. The difference is small for social turns but represents a code smell -- the two code paths can diverge silently.

#### P1-2: Triage LLM max_tokens=60 is dangerously low

**File:** `runtime.py:409`

The triage system prompt asks the LLM to return a complex JSON object with 13 fields (tier, intent, route, confidence, goal, deliverable, entities, is_resume, resume_task_id, is_social, social_response, context_needs, subtasks, execution_order, rationale). 60 tokens is far too few for this response. A minimal valid response is easily 100+ tokens. This will cause frequent truncation, leading to JSON parse failures and fallback to the heuristic path. The heuristic fallback has only 0.50-0.58 confidence, which degrades routing quality.

#### P1-3: `_ThreadedReflectionLLM.complete()` calls `asyncio.run()` inside a running event loop

**File:** `runtime.py:1402`

`asyncio.run()` cannot be called from within a running event loop (it raises `RuntimeError`). This code is used in `_domain_reflect_async` which runs as a background task on the event loop. The `asyncio.to_thread(reflector.reflect, ...)` at line 1407 does move execution to a thread, but the thread-local state still causes issues depending on Python version. In Python 3.10+, `asyncio.run()` creates a new event loop per call which works from a background thread, but this is fragile and may fail under certain executor configurations.

#### P1-4: Session model `child_execution` literal type mismatch

**File:** `session.py:102`

The Session model declares `child_execution` as `Literal["parallel", "serial"]` but `tiered_dispatch.py:410` assigns `"mixed"` to it. Pydantic will raise a validation error if strict validation is enabled, or silently accept it with lax validation. The "mixed" value is then never checked by any consumer -- `_decompose_and_execute` only checks for `"parallel"` at line 1009 and falls through to serial for everything else. This means "mixed" is functionally identical to "serial", making the triage system's "mixed" execution_order signal meaningless.

### P2 -- Significant Issues

#### P2-1: Embedding triage returns without checking context for anaphora

**File:** `triage.py:369-447`

When `_embedding_triage_result()` succeeds, it returns a `TriageResult` that gets post-processed. However, the embedding classification operates only on the raw text, without awareness of conversation context. A message like "do that again" will be classified by embedding similarity against prototypes without any understanding that "that" refers to a previous workflow build. The post-processing at `_enforce_workflow_edit_route` partially compensates by checking `_infer_fallback_action_hints`, but this relies on regex heuristics rather than the semantic understanding the LLM triage would provide.

#### P2-2: MultiStepExecutor._plan_decomposition is a stub

**File:** `tier_executors.py:1183-1189`

When triage does not provide subtasks and the task context has none, the system falls back to splitting on `" and "`. This is extremely naive -- "research quantum computing and write a report" would produce two subtasks but "investigate the pros and cons" would incorrectly split into "investigate the pros" and "cons". The comment says "LLM call in future" but this is a live code path.

#### P2-3: Synthesis is mechanical concatenation, not coherent

**File:** `tier_executors.py:1428-1443`

For tier-2 tasks, the synthesis simply concatenates child results under headers. There is no LLM-powered synthesis to produce a coherent response. The user sees section headers like "## Search for quantum computing papers" followed by "## Write a summary" rather than a unified answer. Only in the "aggressive" autonomy mode does a remediation child get spawned (lines 1103-1140), and even then it addresses gaps rather than synthesizing.

#### P2-4: Race condition in volatile concierge state

**File:** `runtime.py:1196-1201`

When no memory kernel is available, concierge state is stored in `_volatile_concierge_states` (a plain dict). Concurrent requests to the same surface will read and write this dict without synchronization. Since the concierge is an async system serving multiple requests, two concurrent `_process_inner` calls for the same surface can get stale state or overwrite each other's updates. The `model_copy(deep=True)` prevents mutation aliasing but not read-modify-write races.

#### P2-5: Child sessions inherit parent's route blindly

**File:** `tier_executors.py:1226-1235`

All child sessions inherit the parent's route (including action_hints like `workflow_edit`, `run_control`, etc.). A parent task "research quantum computing and build a workflow for it" decomposed into subtasks would give both the research child and the workflow child the same `workflow_edit` action hint. This could cause the research subtask to erroneously enable mutation tools.

#### P2-6: Context turn truncation is aggressive

**File:** `triage.py:529-532`

Only the last 4 turns are included in triage context, each truncated to 200 characters. For complex multi-step workflows, this means the triage LLM has very limited visibility into what the conversation is actually about. Combined with the 60-token max_tokens limit (P1-2), triage accuracy for deep conversations is likely poor.

#### P2-7: `_finalize_task` marks all intents as "completed"

**File:** `runtime.py:1313-1317`

Since `IntentCategory` only has ASK, AGENT, and PLAN, the else branch is unreachable. Every task that produces content is marked "completed" regardless of whether the task was actually finished. A multi-step research task that was interrupted after one subtask will still be marked "completed" if some content was produced.

### P3 -- Minor Issues

#### P3-1: Cosine similarity is computed in pure Python

**File:** `triage.py:276-288`

The `_cosine_similarity` function iterates element-by-element in Python. For embedding vectors (1536 dimensions for text-embedding-3-small), this is orders of magnitude slower than numpy. Since this runs for every triage call against all prototype vectors, it adds non-trivial latency.

#### P3-2: Global mutable cache for embedding prototypes

**File:** `triage.py:87`

Module-level `_EMBEDDING_PROTOTYPE_CACHE` dict grows unboundedly. No TTL or eviction. Practically fine since there are typically 1-2 configurations, but it is not self-limiting.

#### P3-3: Redundant social detection in fallback

**File:** `triage.py:999-1015`

`_fallback_triage_result` checks `lower in _SOCIAL_TOKENS` again, but `fast_classify_text` already handles this exact case. The fallback social path is only reachable if LLM triage and embedding triage both fail. The responses differ between the two paths ("Got it." vs "How can I help?"), which is confusing.

#### P3-4: Autonomy `infer_autonomy_level` has no surface-specific tuning for CLI

**File:** `autonomy.py:113-144`

The function checks `_MESSAGING_SURFACES` for careful mode but has no special handling for CLI vs web surfaces. CLI users likely expect more aggressive autonomy by default, while web/API users might prefer balanced.

#### P3-5: SessionManager is not thread-safe

**File:** `session.py:121-126`

Plain dicts with no locks. In the async context this is fine for single-threaded asyncio, but `ContextGatherer.gather()` uses `asyncio.to_thread()` which could theoretically access the session manager from a thread pool.

#### P3-6: Triage system prompt is built at import time

**File:** `triage.py:454-512`

Makes it impossible to dynamically adjust the triage prompt based on runtime configuration without reloading the module.

#### P3-7: `prune_completed` uses fixed 300-second age

**File:** `session.py:286`, `tiered_dispatch.py:686`

Completed sessions are pruned after 5 minutes. For long-running Tier 2 tasks that spawn many children, the parent session could be pruned before a late-completing child reports back.

---

## Strengths

### S1: Well-layered fallback chain
The triage pipeline has four layers (fast lexical, embedding, LLM, heuristic) with graceful degradation. Each layer is independently toggleable. The embedding layer being optional via `DAN_TRIAGE_EMBEDDING_ENABLED` is a good design choice.

### S2: Robust JSON parsing
The `_parse_triage_response` function (triage.py:1060-1115) handles many edge cases: string-encoded tiers, missing fields, invalid enum values, markdown-fenced JSON. Defensive parsing prevents most LLM formatting variance from causing failures.

### S3: Session tree with budget enforcement
Well-designed budget limits: `max_depth` (4), `max_children` (8), `max_total_sessions` (32), all configurable via environment variables. The `can_spawn_child` check prevents runaway recursion. State transitions are validated against `_VALID_TRANSITIONS`.

### S4: Autonomy model is well-structured
The four-level autonomy model (careful/balanced/aggressive/auto) with explicit priority chain (turn > session > project > env > inferred) is clean. The `resolve_autonomy` function correctly separates preference resolution from inference, and the `announce_change` flag prevents noisy notifications.

### S5: Context gathering is demand-driven
`ContextGatherer.gather()` only fetches what triage says is needed (memory, file reads, domain expertise) rather than speculatively loading everything. Aggressive mode adds extra context (git status, related files, recent turns) only when the autonomy level warrants it.

### S6: Domain learning pipeline is comprehensive
The domain system spans detection, reflection, template management, consolidation, validation, auto-discovery, and keyword expansion. The tiered feature gating via `is_feature_enabled()` means the system can progressively enable more autonomous learning behavior.

### S7: Cancel propagation is thorough
Cancel event checking appears at every decision point in the tier executors. The `cancel_tree` function cascades cancellation through all children. The `_mark_session_cancelled` function handles partial results.

### S8: Workflow-edit route enforcement is context-aware
The `_enforce_workflow_edit_route` function (triage.py:800-854) and `_has_recent_workflow_activity` guard (triage.py:936-953) prevent generic retry language from incorrectly routing to workflow mutation unless recent conversation actually involved workflow editing.

---

## Summary

The concierge and tiered triage system is architecturally sound with good separation of concerns, robust fallback chains, and thoughtful budget enforcement. The most impactful issue is **P1-2** (max_tokens=60 for triage LLM) which likely causes the majority of triage calls to fall back to heuristics, undermining the entire classification pipeline. **P1-4** (child_execution literal mismatch) is a latent type safety issue. **P2-2** (stub decomposition) and **P2-3** (concatenation-only synthesis) are the biggest user-facing quality gaps for Tier 2 tasks. The autonomy model is well-calibrated in structure but under-tested against real-world edge cases like mixed-intent messages.
