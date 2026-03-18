# 31-33: Autonomy Control & Prompt Module Resolver

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** in-progress
**Goal:** Give users a `careful / balanced / aggressive` autonomy knob that mainly steers LLM behavior via prompt injection, modestly adjusts selected policy thresholds, and refactors the current hint-style prompt assembly in `_build_messages()` into a composable `PromptModuleResolver` without destabilizing runtime context injection.

## Context

- `resolve_policy()` in `concierge/policy.py` handles deterministic safety (destructive keywords, high-cost actions, messaging mutations) but has no autonomy-profile input.
- `Concierge.__init__` accepts `autonomy_level: str | None` in `runtime.py`, but the value is currently stored and not used to drive either `resolve_policy()` or chat prompt construction.
- `AutonomyLevel` exists in `runtime.py`, but there is **no** live `_effective_autonomy()` or `_autonomy_override_from_message()` helper in the current codebase. This plan must add those normalization/override paths explicitly instead of assuming they already exist.
- Prompt assembly in `ChatManager._build_messages()` mixes together three different concerns:
  1. hint-like guidance (`_resolve_mode_hints()`, `_resolve_surface_hints()`, research hint injection),
  2. runtime context blocks (memory, MCP, user context, workflow/context text),
  3. request-specific extras (`extra_system_instructions`).
  Only the first category should move into a prompt-module resolver in v1.
- The hyperedge JIT pattern (`load_hyperedge` tool + one-line summaries) already solves "detailed prompt on demand" for workflow execution. Chat can reuse the pattern, but via the chat capability/tool system rather than a ChatManager-only special case.
- `ChatCapabilityRegistry` already owns chat tool schemas and execution. Any `load_prompt_detail` tool should register there so it works like the rest of the chat tool surface.
- `DomainGenerationProfile.prompt_hints` shows DAN already has one other prompt-hint injection path outside chat. This plan should stay narrow and clean up chat first rather than trying to unify all prompt systems in one slice.
- Both `send()` and `send_message_with_tools()` call `_build_messages()`, so any prompt-module/JIT design must degrade safely when `tools_available=False`.

## Key code references

| Area | File | Key symbols |
|------|------|-------------|
| Policy gates | `src/dan/server/concierge/policy.py` | `resolve_policy`, `ActionPolicy`, `ExecutionPolicy`, `estimate_action_cost`, `_DESTRUCTIVE_KEYWORDS`, `_MESSAGING_SURFACES` |
| Project persistence | `src/dan/server/concierge/models.py` | `Project` |
| Project storage | `src/dan/server/concierge/project_store.py` | `ProjectStore.create_project`, `save_project`, `get_project` |
| Existing autonomy enum | `src/dan/server/concierge/runtime.py` | `AutonomyLevel`, `Concierge.__init__`, `_dispatch_registry_fast_command` |
| Command surface | `src/dan/server/concierge/command_registry.py` | `CommandDescriptor`, `CommandRegistry`, `_populate_default_commands` |
| Session-style override precedent | `src/dan/server/concierge/progress_ux.py` | `handle_progress_command`, `_user_verbosity_overrides` |
| Chat prompt assembly | `src/dan/server/chat_manager.py` | `_build_messages`, `send()`, `send_message_with_tools()`, `_should_inject_research_prompt_hint` |
| Hint definitions | `src/dan/server/chat/prompts.py` | `_MODE_HINTS`, `_resolve_mode_hints`, `_resolve_surface_hints`, `UNIFIED_SYSTEM_PROMPT`, `_RESEARCH_REPORT_PROMPT_HINT` |
| Chat tool plumbing | `src/dan/server/capability_registry.py` | `ChatCapabilityRegistry`, `CapabilityContext`, `build_tool_schema` |
| Chat capability registration | `src/dan/server/capability_handlers.py` | `register_base_capabilities` |
| Concierge-to-chat param bridge | `src/dan/server/concierge/tier_executors.py` | `_extract_chat_params` |
| Hyperedge JIT reference pattern | `src/dan/engine/hyperedge_runtime.py` | `HYPEREDGE_JIT_TOOL`, `JIT_SYSTEM_INSTRUCTION` |
| Domain hint precedent | `src/dan/meta/generation_defaults.py` | `DomainGenerationProfile.prompt_hints` |

## Tasks

### Task 0 — Autonomy profile model and state boundaries
- [ ] 0-1. Define `AutonomyProfile` in `concierge/policy.py`: `level: Literal["careful", "balanced", "aggressive"]`, optional `cost_confirm_threshold_override`, and an LLM-steering field such as `clarification_bias`
- [ ] 0-2. Add `autonomy_profile` field to `Project` (default `"balanced"`) so the preference can persist per project without breaking older saved project JSON
- [ ] 0-3. Add **per-session** override storage keyed by `external_id` / session scope (patterned after `/progress` overrides); do **not** use a single `_session_autonomy` scalar on the singleton `Concierge`
- [ ] 0-4. Add explicit normalization helpers that map legacy `AutonomyLevel` / `DAN_CONCIERGE_AUTONOMY` (`interactive/supervised/autonomous`) onto `careful/balanced/aggressive`

### Task 1 — Wire autonomy into policy and command flow
- [ ] 1-1. Add `autonomy_profile: AutonomyProfile | None` parameter to `resolve_policy()`
- [ ] 1-2. `careful`: lower `cost_confirm_threshold`, bias toward `CONFIRM` for `PLAN`/mutation-style turns, and rely on prompt guidance for extra clarification behavior
- [ ] 1-3. `aggressive`: raise `cost_confirm_threshold` and skip the messaging-surface mutation confirm gate, but keep destructive/publish-style confirmations intact
- [ ] 1-4. `balanced`: preserve current live behavior
- [ ] 1-5. Add `/autonomy [careful|balanced|aggressive] [--project]` through `command_registry.py` rather than an ad-hoc fast-command branch; plain `/autonomy` is session-scoped, `--project` persists to the resolved project
- [ ] 1-6. Resolve effective profile with clear precedence: explicit turn override > session override > project default > env default > balanced

### Task 2 — PromptModuleResolver (hint sections only)
- [ ] 2-1. Define `PromptContext` dataclass carrying the values relevant to hint modules: `mode`, `surface`, `model`, `user_message`, `workflow_id`, `autonomy_profile`, `tools_available`, optional project metadata, and any precomputed hint flags needed by async detectors
- [ ] 2-2. Define async-friendly `PromptModule` / `ResolvedPromptModule` types in `chat/prompts.py` so modules can do async work (for example the current research-hint classifier path) instead of forcing purely synchronous callables
- [ ] 2-3. Implement `PromptModuleResolver` with registration, priority ordering, and JIT detail lookup (`resolve(ctx)`, `list_active_jit_details(ctx)`, `get_detail(id)`)
- [ ] 2-4. Refactor `_build_messages()` so the resolver owns only hint-like sections (surface/mode/autonomy/research/future behavior hints)
- [ ] 2-5. Keep `user_context_block`, `mcp_block`, `memory_context`, workflow/context summaries, and `extra_system_instructions` outside the resolver in v1
- [ ] 2-6. Thread resolved autonomy/project info from `tier_executors._extract_chat_params()` into both `send()` and `send_message_with_tools()` so the resolver sees the same state on tool and non-tool paths

### Task 3 — Initial prompt modules
- [ ] 3-1. Register `autonomy_hints` module with level-specific behavior:
  - `careful`: explain plan first, ask more readily when ambiguous, and wait for approval before modifications
  - `balanced`: match current default behavior with little or no extra text
  - `aggressive`: minimize questions, proceed on reasonable assumptions, and reserve confirmation for genuinely risky actions
- [ ] 3-2. Register `mode_hints` module by migrating the current `_MODE_HINTS` content with minimal wording churn
- [ ] 3-3. Register `surface_hints` module by migrating `_resolve_surface_hints()` behavior, including Telegram bot-name-specific formatting
- [ ] 3-4. Register `research_hints` module using the current detector path (`_classify_research_prompt_signal()` / `_should_inject_research_prompt_hint()`) but preserve the current inline guidance quality for first rollout

### Task 4 — Chat-side JIT prompt detail loading
- [ ] 4-1. Add `load_prompt_detail` as a **normal chat capability** (schema + handler + registry registration), not as a ChatManager-only tool-call special case
- [ ] 4-2. Expose `load_prompt_detail` only when at least one resolved module has extra detail available **and** `tools_available=True`
- [ ] 4-3. Split research guidance into a compact inline core plus optional expanded detail; do **not** replace the current research hint with a summary-only prompt in v1
- [ ] 4-4. Add `exploration_hints` as the first genuinely JIT-heavy chat module for better context-gathering instructions on demand
- [ ] 4-5. When tools are unavailable, never emit instructions telling the model to call `load_prompt_detail`; inline a safe fallback or omit the JIT invitation entirely

### Task 5 — Tests
- [ ] 5-1. Unit tests for `AutonomyProfile` + `resolve_policy()` integration across careful/balanced/aggressive, including hard-safety invariants for destructive keywords
- [ ] 5-2. Unit tests proving session overrides do not leak across `external_id` / surface scopes
- [ ] 5-3. Integration tests for `/autonomy` command registration/dispatch, including session override and `--project` persistence behavior
- [ ] 5-4. Unit tests for `PromptModuleResolver` ordering, async resolution, JIT exposure, and detail lookup
- [ ] 5-5. Regression test that text-only / `tools_available=False` prompt builds never suggest unavailable `load_prompt_detail`
- [ ] 5-6. Capability test that `load_prompt_detail` returns the expected module body and fails cleanly on unknown IDs
- [ ] 5-7. Balanced-mode regression that migrated surface/mode/research hints remain materially equivalent to current output

## Decisions

- Autonomy is **primarily prompt-driven**. `resolve_policy()` only adjusts a small set of deterministic thresholds/gates; it does not become a broad behavior engine.
- Hard safety never relaxes: destructive keywords and similarly irreversible actions still require confirmation regardless of autonomy level.
- The first `PromptModuleResolver` rollout is intentionally narrow: it owns hint-like prompt sections, not memory context, MCP/tool inventories, user profile blocks, or workflow summaries.
- JIT detail loading uses the existing chat capability architecture (`ChatCapabilityRegistry`) so it composes with current tool availability, caching, and provider behavior.
- `balanced` is the default and should preserve current behavior as closely as possible; new behavior should be opt-in via session/project/env settings.
- Legacy `AutonomyLevel` / `DAN_CONCIERGE_AUTONOMY` inputs are mapped, not broken.

## Notes

- `UNIFIED_SYSTEM_PROMPT` should collapse the current `{surface_hints}`, `{mode_hints}`, and `{task_hints}` placeholders into a single `{module_hints}` section once the resolver owns all three.
- `_build_messages()` is called by both `send()` and `send_message_with_tools()`, so every resolver/JIT change must be validated on both code paths.
- If `load_prompt_detail` needs resolver access at capability-execution time, prefer exposing lookup through `CapabilityContext` / `ctx.chat_manager` rather than duplicating prompt-module state in two places.
- Future heavy modules can follow after this lands cleanly: debugging protocol, code-review checklist, exploration playbook, domain-specific prompt packs.

## Estimate

~4 days
