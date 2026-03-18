# 31-33: Autonomy Control & Prompt Module Resolver

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** in-progress
**Goal:** Give users a `careful / balanced / aggressive` autonomy knob that steers both hard policy gates and LLM behavior via prompt injection, and refactor the ad-hoc conditional prompt assembly in `_build_messages` into a composable `PromptModuleResolver`.

## Context

- `resolve_policy()` in `concierge/policy.py` handles deterministic safety (destructive keywords, high-cost actions, messaging mutations) but has no autonomy-level input.
- `Concierge.__init__` accepts `autonomy_level: str | None` but never uses it — the parameter is stored and forgotten.
- `AutonomyLevel` enum exists from 29-2 (`INTERACTIVE / SUPERVISED / AUTONOMOUS`) in the concierge models, plus `_effective_autonomy()` and `_autonomy_override_from_message()`. These drive goal-level behavior but don't touch `resolve_policy` or prompt composition.
- Prompt assembly in `ChatManager._build_messages()` is a linear chain of ad-hoc conditionals: `_resolve_mode_hints()`, `_resolve_surface_hints()`, `_should_inject_research_prompt_hint()`, memory context, MCP block, user context, extra system instructions — each bolted on with its own `if` block.
- The hyperedge JIT pattern (`load_hyperedge` tool + one-line summaries) already solves "detailed prompt on demand" for workflows. Chat doesn't have an equivalent.
- `DomainGenerationProfile.prompt_hints` injects domain-specific text for codegen — another ad-hoc conditional.
- The `{task_hints}` slot in `UNIFIED_SYSTEM_PROMPT` is only used by the research hint today but is the natural expansion point.

## Key code references

| Area | File | Key symbols |
|------|------|-------------|
| Policy gates | `src/dan/server/concierge/policy.py` | `resolve_policy`, `ActionPolicy`, `ExecutionPolicy`, `estimate_action_cost`, `_DESTRUCTIVE_KEYWORDS` |
| Prompt assembly | `src/dan/server/chat_manager.py` | `_build_messages`, `_should_inject_research_prompt_hint` |
| Mode/surface hints | `src/dan/server/chat/prompts.py` | `_MODE_HINTS`, `_resolve_mode_hints`, `_resolve_surface_hints`, `UNIFIED_SYSTEM_PROMPT`, `_RESEARCH_REPORT_PROMPT_HINT` |
| Concierge init | `src/dan/server/concierge/runtime.py` | `Concierge.__init__` (`autonomy_level`), `build_concierge` |
| Existing autonomy | `src/dan/server/concierge/models.py` | `AutonomyLevel`, `_effective_autonomy`, `_autonomy_override_from_message` |
| Hyperedge JIT | `src/dan/engine/hyperedge_runtime.py` | `HyperedgeResolver.apply_pre_prompt`, `HYPEREDGE_JIT_TOOL`, `JIT_SYSTEM_INSTRUCTION` |
| Domain profiles | `src/dan/meta/generation_defaults.py` | `DomainGenerationProfile.prompt_hints` |
| Progress UX | `src/dan/server/concierge/progress_ux.py` | `InteractionRequest`, `advisory_checkpoint`, `required_clarification` |

## Tasks

### Task 0 — AutonomyProfile schema + storage
- [ ] 0-1. Define `AutonomyProfile` in `concierge/policy.py`: `level: Literal["careful", "balanced", "aggressive"]`, `cost_confirm_threshold_override: float | None`, `clarification_bias: Literal["ask", "neutral", "proceed"] | None`
- [ ] 0-2. Add `autonomy_profile` field to `Project` model (default `"balanced"`) — persisted per-project
- [ ] 0-3. Add session-level override: `Concierge` stores `_session_autonomy: AutonomyProfile | None` that can override the project default for the current session
- [ ] 0-4. Map existing `AutonomyLevel` (INTERACTIVE/SUPERVISED/AUTONOMOUS) from 29-2 to new profile levels: INTERACTIVE → careful, SUPERVISED → balanced, AUTONOMOUS → aggressive. Keep backward compat — `DAN_CONCIERGE_AUTONOMY` env var still works.

### Task 1 — Wire autonomy into resolve_policy
- [ ] 1-1. Add `autonomy_profile: AutonomyProfile | None` parameter to `resolve_policy()`
- [ ] 1-2. `careful`: lower `cost_confirm_threshold` to 0.3, promote `ActionPolicy.CONFIRM` for any `PLAN` intent (not just mutations)
- [ ] 1-3. `aggressive`: raise `cost_confirm_threshold` to 5.0, skip `CONFIRM` for messaging-surface mutations (keep `CONFIRM` for `_DESTRUCTIVE_KEYWORDS` — hard safety never relaxes)
- [ ] 1-4. `balanced`: current behavior (no change to thresholds/gates)
- [ ] 1-5. Add `/autonomy [careful|balanced|aggressive]` fast command to switch session level
- [ ] 1-6. Wire: `Concierge.process()` resolves effective profile (session override → project default → env default) and passes to `resolve_policy()`

### Task 2 — PromptModuleResolver
- [ ] 2-1. Define `PromptModule` dataclass in `chat/prompts.py`: `id: str`, `condition: Callable[[PromptContext], bool]`, `content: Callable[[PromptContext], str]`, `priority: int` (lower = earlier in prompt), `jit: bool` (if True, inject summary + load tool instead of full content when large)
- [ ] 2-2. Define `PromptContext` dataclass: `mode`, `surface`, `model`, `user_message`, `autonomy_profile`, `workflow_id`, `project`, `has_memory_context`, etc. — everything `_build_messages` currently passes around as loose parameters
- [ ] 2-3. Implement `PromptModuleResolver` class with `register(module)`, `async resolve(context) -> list[ResolvedModule]` (returns content strings sorted by priority)
- [ ] 2-4. `_build_messages` creates a `PromptContext`, calls `resolver.resolve(ctx)`, and joins the returned content blocks into the system prompt — replacing the current inline conditionals

### Task 3 — Autonomy prompt modules
- [ ] 3-1. Register `autonomy_hints` module — injects level-specific behavioral instructions:
  - `careful`: "Before any modification, explain your plan and wait for approval. Ask clarifying questions when ambiguous. Confirm before proceeding with file writes, workflow edits, or multi-step operations."
  - `balanced`: (empty — current default behavior, no extra injection needed)
  - `aggressive`: "Minimize questions. Proceed with the most reasonable interpretation unless genuinely blocked. Only ask for confirmation on destructive or irreversible actions. Prefer action over deliberation."
- [ ] 3-2. Register `mode_hints` module — migrate existing `_resolve_mode_hints()` content
- [ ] 3-3. Register `surface_hints` module — migrate existing `_resolve_surface_hints()` content
- [ ] 3-4. Register `research_hints` module — migrate `_RESEARCH_REPORT_PROMPT_HINT` with its detection logic (`_should_inject_research_prompt_hint`)

### Task 4 — Task-specific detail loading (JIT for chat)
- [ ] 4-1. Add `load_prompt_detail` tool schema (analogous to `load_hyperedge`) for chat: LLM can call it to fetch full content of a registered JIT prompt module
- [ ] 4-2. Mark `research_hints` as `jit=True` — when the research hint is needed, inject a one-line summary ("Detailed research/report writing instructions available. Call `load_prompt_detail('research')` when starting a research task.") and make the tool available
- [ ] 4-3. Add `exploration_hints` JIT module — detailed context-exploration instructions (currently scattered or absent), available on demand
- [ ] 4-4. Handle `load_prompt_detail` tool calls in `ChatManager.send_message_with_tools()` — return the full module content as a tool result

### Task 5 — Tests
- [ ] 5-1. Unit tests for `AutonomyProfile` + `resolve_policy` integration (careful/balanced/aggressive affect thresholds, destructive keywords always confirmed)
- [ ] 5-2. Unit tests for `PromptModuleResolver` (registration, priority ordering, condition filtering, JIT summary vs full content)
- [ ] 5-3. Integration test: `/autonomy aggressive` switches session level, next prompt uses aggressive hints
- [ ] 5-4. Integration test: `load_prompt_detail` tool call returns correct module content
- [ ] 5-5. Regression: existing prompt assembly produces equivalent output after module migration

## Decisions

- Autonomy is **primarily prompt-driven** for LLM steering. `resolve_policy` only adjusts `cost_confirm_threshold` and the messaging-mutation gate — hard safety (`_DESTRUCTIVE_KEYWORDS`) never relaxes regardless of autonomy level.
- The `PromptModuleResolver` is a thin registry, not a plugin system. Modules are registered in code at import time. No dynamic loading, no config files.
- JIT loading reuses the hyperedge pattern: one-line summary in the system prompt + a tool the LLM can call. This keeps the base prompt short while making detailed instructions available on demand.
- `balanced` is the default and is designed to produce identical behavior to the current system — zero behavioral regression on deploy.
- The existing `AutonomyLevel` enum from 29-2 is mapped, not replaced. `DAN_CONCIERGE_AUTONOMY` env var keeps working.

## Notes

- The `{task_hints}` slot in `UNIFIED_SYSTEM_PROMPT` becomes `{module_hints}` to hold all resolved module content (autonomy + research + future modules).
- `_build_messages` cleanup: after migration, the function body should be ~40% shorter — the conditionals move into registered module conditions.
- Future modules (code review instructions, debugging protocol, exploration guide) can be added as new `PromptModule` registrations without touching `_build_messages`.
- Autonomy overrides from natural language ("just do it", "be careful") via `_autonomy_override_from_message()` should set the session-level `AutonomyProfile`, bridging 29-2's detection with the new prompt injection.

## Estimate

~3 days
