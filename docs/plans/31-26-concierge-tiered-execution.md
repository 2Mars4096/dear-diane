# 31-26: Concierge Tiered Execution

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Route concierge LLM stages through a shared tier map so cheap models handle lightweight turns, stronger models handle planning/building, and the user's `DAN_TIER_MAP` actually applies consistently across concierge + workflow execution.

## Context

The concierge pipeline has a small number of real LLM call sites:
- intent classification via `_resolve_classifier_model()` / `_classify_llm_complete()`
- workflow intent extraction via `_intent_llm_complete()` → `extract_workflow_intent()`
- solver planning via `GoalResolver._llm_resolve()`
- chat responses via `ChatManager.send_message()` and `ChatManager.send_message_with_tools()`

Only a subset of concierge handlers actually reach those LLM paths. `StatusHandler`, `PublishHandler`, `WorkflowQueryHandler`, and most `RunHandler` paths are deterministic capability calls, so assigning them tiers would not save tokens.

Additionally, `DAN_TIER_MAP` currently gets parsed in `app.py` / `chat_factory.py`, but concierge classifier resolution does not reuse that override path. Numeric keys (`"1"`, `"2"`, `"3"`) also do not match the workflow engine's canonical tier names (`micro`, `routine`, `reasoning`, `critical`), so the user's current `.env` map is not applied consistently. This plan fixes that at the normalization layer, not ad hoc inside concierge.

## Stage Assignment

| LLM stage | Tier | Rationale |
|---|---|---|
| Intent classification | `micro` | Fast structured routing; cheapest acceptable model |
| Workflow intent extraction (`extract_workflow_intent`) | `reasoning` | Decompose a user goal into a structured workflow intent |
| Goal resolver / solver (`GoalResolver._llm_resolve`) | `reasoning` | Planning, decomposition, reuse-vs-build decisions |
| File review (`FileHandler._review_document`) | `routine` | Summarization/review over already-extracted document text |
| Direct-task chat fallback | `routine` | Moderate synthesis when deterministic web lookup is insufficient |
| Experience fallback | `routine` | Explain "no saved workflows" + suggest next steps |
| Conversation (`ask` / normal chat) | `routine` | Everyday synthesis and tool use |
| Conversation (`plan` / `debug`) | `reasoning` | Explicit higher-cognition modes should not be down-tiered |
| Workflow build / mutate | `reasoning` | Highest-risk user-facing authoring path; avoid quality regression |

## Out Of Scope

- Tier-based validation changes. Validation remains as-is for this rollout.
- Automatic scoring for concierge stages. Use explicit stage mapping, not `TierPolicy`.
- Legacy `MetaGoalHandler` / `MetaController` path. It is a separate orchestration stack and should be handled in a follow-up once concierge-first paths are stable.
- Deterministic handlers with no LLM call (`StatusHandler`, `PublishHandler`, `WorkflowQueryHandler`, most `RunHandler` paths).

## Tasks

- [ ] 1. Add shared tier-map normalization
  - [ ] 1-1. Introduce a single helper in `src/dan/providers/tier_defaults.py` (or a closely related module) to normalize user tier maps before they reach either workflow `TierPolicy` or concierge.
  - [ ] 1-2. Support numeric 3-tier shorthand from `.env`: `"1"` → `micro`, `"2"` → `routine`, `"3"` → `reasoning` **and** `critical` unless the user explicitly provides a `critical` mapping.
  - [ ] 1-3. Preserve canonical named keys as the internal representation (`micro`, `routine`, `reasoning`, `critical`). Numeric keys are input shorthand only.
  - [ ] 1-4. Update `app.py`, `chat_factory.py`, and concierge classifier/model resolution to reuse the same normalization helper instead of each parsing `DAN_TIER_MAP` independently.
  - [ ] 1-5. Add tests for named maps, numeric shorthand, mixed maps, and the 3-tier → 4-tier expansion used by workflow `TierPolicy`.

- [ ] 2. Add concierge tier resolver
  - [ ] 2-1. Create `src/dan/server/concierge/tiering.py` (name flexible) with canonical internal stage names and a `STAGE_TIER_MAP` that maps stages to named tiers, not numeric strings.
  - [ ] 2-2. Add `ConciergeTierResolver` with `resolve_model(stage: str) -> str` and `resolve_tier(stage: str) -> str`, using the normalized tier map plus `DAN_CHAT_MODEL` / `DAN_LLM_MODEL` as fallback.
  - [ ] 2-3. Construct the resolver in `make_concierge()` and inject it into both `Concierge` and `GoalResolver` (or inject a smaller model-resolver callback). Do not bury all wiring inside `Concierge.__init__()`, because `GoalResolver` is built before `Concierge`.
  - [ ] 2-4. Add unit tests for stage→tier→model mapping, fallback behavior, and classifier parity with the shared normalized map.

- [ ] 3. Refactor ChatManager for request-scoped model override
  - [ ] 3-1. Add optional `model_override: str | None = None` to both `ChatManager.send_message()` and `ChatManager.send_message_with_tools()`.
  - [ ] 3-2. Compute `effective_model` once per request and thread it through provider resolution, streaming/tool paths, retry/replan branches, cost estimation, context-window calculation, and audit persistence. Avoid partial replacement of only a few `provider.complete()` calls.
  - [ ] 3-3. Update internal helpers that currently hardcode `self._chat_model` (`_resolve_provider`, tool-loop fallbacks, JSON fallback streaming, message-building helpers where model matters) to accept/use `effective_model`.
  - [ ] 3-4. Tests: `send_message()` override, `send_message_with_tools()` override, and at least one fallback/retry branch to ensure the override survives the whole request lifecycle.

- [ ] 4. Wire tier resolver into concierge stages
  - [ ] 4-1. Replace `_resolve_classifier_model()` internals so classifier resolution uses the same normalized tier map as the workflow engine and returns the `micro` model from that map.
  - [ ] 4-2. Inline execution / workflow intent extraction: pass `model=tier_resolver.resolve_model("intent_extraction")` to `extract_workflow_intent(...)` via `_intent_llm_complete()`.
  - [ ] 4-3. `GoalResolver._llm_resolve()`: use injected resolver/callback so solver requests the `reasoning` model explicitly instead of `model=""`.
  - [ ] 4-4. `FileHandler._review_document()`: pass `model_override=tier_resolver.resolve_model("file_review")` to `send_message()`.
  - [ ] 4-5. `DirectTaskHandler._fallback_to_chat()` and `ExperienceHandler` fallback: pass `model_override=tier_resolver.resolve_model(...)` to `send_message_with_tools()`.
  - [ ] 4-6. `ConversationHandler.handle()`: use mode-aware stage mapping (`conversation` vs `plan/debug`) when choosing `model_override`.
  - [ ] 4-7. `WorkflowBuildHandler.handle()`: pass `model_override=tier_resolver.resolve_model("workflow_build")`.
  - [ ] 4-8. Keep deterministic handlers unchanged unless they already fall through to one of the LLM-backed handlers above.

- [ ] 5. Observability
  - [ ] 5-1. Add DEBUG logging whenever concierge resolves a stage model (`stage`, `tier`, `model`, `fallback_used`).
  - [ ] 5-2. Thread `concierge_stage`, `concierge_tier`, and `concierge_model` into `audit_metadata` and `TelemetryEvent.metadata` for chat turns / classifier / solver events.
  - [ ] 5-3. If `/analytics` must aggregate by tier or stage (not just model), add an explicit telemetry follow-up task to make those fields groupable rather than assuming metadata is queryable today.

- [ ] 6. Docs
  - [ ] 6-1. Update `.env.example` and `docs/cli.md` to document canonical named keys plus numeric shorthand, including the `3` → `reasoning`/`critical` expansion.
  - [ ] 6-2. Update `docs/architecture.md` to describe concierge stage-based tier routing and its separation from workflow `TierPolicy` scoring.
  - [ ] 6-3. Update `docs/llm-api-guide.md` if concierge tiered execution becomes part of the supported runtime contract.

- [ ] 7. Verification
  - [ ] 7-1. Add focused tests proving the classifier uses the user's micro-tier override.
  - [ ] 7-2. Add solver tests proving the reasoning-tier model is requested when the LLM path is used.
  - [ ] 7-3. Add handler/runtime tests for file review, conversation, direct-task fallback, and workflow build model overrides.
  - [ ] 7-4. Manual smoke test with a numeric `DAN_TIER_MAP` in `.env` to confirm concierge + workflow engine now agree on resolved models.

## Decisions

- Explicit stage-based mapping, not automatic scoring. Concierge has a fixed set of stage types, so deterministic routing is simpler and easier to tune than reusing `TierPolicy` heuristics.
- Canonical internal tier names stay `micro` / `routine` / `reasoning` / `critical`. Numeric `"1"` / `"2"` / `"3"` is just user shorthand at the env/config boundary.
- Because the user currently has 3 tiers, numeric `"3"` expands to both `reasoning` and `critical` unless overridden explicitly.
- Concierge tiering should be independent of `DAN_ENABLE_TIER_POLICY`; it shares the tier map, but not the workflow-engine scoring mechanism.
- Validation stays unchanged. This plan only changes model selection for concierge LLM stages.

## Notes

- The classifier already has a dedicated micro-tier path, but it currently does not consume the user's normalized `DAN_TIER_MAP`; this plan fixes that rather than replacing the classifier flow.
- `_intent_llm_complete()` already accepts a `model` parameter, and `extract_workflow_intent()` also accepts `model`, so intent extraction can be tiered with minimal API churn.
- `GoalResolver` currently receives only `llm=getattr(capability_context, "llm_provider", None)` in `make_concierge()`. Model resolution therefore needs explicit injection; it cannot be inferred magically inside `_llm_resolve()`.
- `ChatManager` has no `send_message_streaming()` API. The two public request paths are `send_message()` and `send_message_with_tools()`.
