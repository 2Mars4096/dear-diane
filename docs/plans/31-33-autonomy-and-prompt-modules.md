# 31-33: Autonomy Control & Prompt Module Resolver

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Give users an `auto / careful / balanced / aggressive` autonomy preference that controls DAN's **workflow-level operating style** — not just prompt wording, but how aggressively DAN explores context, decomposes work, chains steps, reviews progress, and pushes implementation forward. Autonomy propagates through the session tree so every tier executor applies the same initiative contract to its subtask. The plan also refactors hint-style prompt assembly into a layered `PromptModuleResolver`.

## Context

- `resolve_policy()` in `concierge/policy.py` handles deterministic safety (destructive keywords, high-cost actions, messaging mutations) but has no autonomy-resolution input.
- `Concierge.__init__` accepts `autonomy_level: str | None` in `runtime.py`, but the value is currently stored and not used to drive either `resolve_policy()` or chat prompt construction.
- `AutonomyLevel` exists in `runtime.py`, but there is **no** live autonomy-preference normalization, auto-inference, or change-announcement helper in the current codebase. This plan must add those paths explicitly.
- The tiered concierge model (`TieredDispatcher` → `SingleShotExecutor` / `MultiStepExecutor`) already has session tree propagation (`Session`, `SessionManager.create_child()`), context gathering (`ContextGatherer.gather()`), decomposition (`_should_decompose`, `_decompose_and_execute`), and a multi-turn tool loop in `ChatManager.send_message_with_tools()`. Autonomy must influence decisions at **every one of these layers**, not just the final prompt text.
- **Child sessions are subagents.** They inherit `context`, `task_context`, and a synthetic `TriageResult` from the parent — they do **not** re-triage or re-run context gathering. `_build_child_session()` copies parent context and constructs a lightweight triage with `goal=task_desc`. This means autonomy propagates by inheritance, and context-gathering changes only affect the root dispatch path.
- Prompt assembly in `ChatManager._build_messages()` mixes hint-like guidance, runtime context blocks, and request-specific extras. Only the first category should move into a prompt-module resolver in v1.
- The current hint split is misleading: mode hints and autonomy hints both govern ask/act/confirm posture. They should be merged into one coherent `interaction_policy` layer.
- **Two prompt injection points exist:** `_build_prompt()` in `tier_executors.py` produces per-session context (goal, deliverable, domain, memory) that flows into `prompt_context` → `_build_messages()`. Separately, `_build_messages()` adds behavior hints (mode, surface, research). Autonomy behavior instructions should live in exactly one place — the `interaction_policy` prompt module — and `_build_prompt()` should only carry factual context (goal, autonomy level as metadata), not behavioral prose that duplicates the module.
- `ChatCapabilityRegistry` already owns chat tool schemas and execution. Any `load_prompt_detail` tool should register there.
- Both `send()` and `send_message_with_tools()` call `_build_messages()`, so any prompt-module/JIT design must degrade safely when `tools_available=False`.

## Key code references

| Area | File | Key symbols |
|------|------|-------------|
| Policy gates | `concierge/policy.py` | `resolve_policy`, `ActionPolicy`, `ExecutionPolicy`, `estimate_action_cost`, `_DESTRUCTIVE_KEYWORDS` |
| Project persistence | `concierge/models.py` | `Project` |
| Session tree | `concierge/session.py` | `Session`, `SessionManager`, `create_root`, `create_child`, `SessionTier` |
| Tiered dispatch | `concierge/tiered_dispatch.py` | `TieredDispatcher.dispatch`, `ContextGatherer.gather`, `_do_triage` |
| Tier executors | `concierge/tier_executors.py` | `SingleShotExecutor`, `MultiStepExecutor`, `_should_decompose`, `_decompose_and_execute`, `_build_child_session`, `_extract_chat_params`, `_build_prompt`, `_synthesize` |
| Chat tool loop | `chat_manager.py` | `send_message_with_tools`, `max_tool_turns`, `_build_messages`, `_should_inject_research_prompt_hint` |
| Existing autonomy enum | `concierge/runtime.py` | `AutonomyLevel`, `Concierge.__init__` |
| Command surface | `concierge/command_registry.py` | `CommandDescriptor`, `CommandRegistry` |
| Session-style override precedent | `concierge/progress_ux.py` | `handle_progress_command`, `_user_verbosity_overrides` |
| Hint definitions | `chat/prompts.py` | `_MODE_HINTS`, `_resolve_mode_hints`, `_resolve_surface_hints`, `UNIFIED_SYSTEM_PROMPT` |
| Chat capability registration | `capability_handlers.py` | `register_base_capabilities`, `ChatCapabilityRegistry` |

## Autonomy as a Workflow Operating Style

Autonomy is **not** just a prompt personality. It controls how DAN runs the entire task lifecycle.

```
USER MESSAGE
  |
  v
AUTONOMY RESOLUTION (root only)
  preference precedence: turn > session > project > env > auto
  auto inference: risk/reversibility/clarity signals -> careful/balanced/aggressive
  |
  v
TIERED DISPATCH (root only)
  |
  +-- triage: how many subtasks? decompose or direct?
  |     careful:     fewer subtasks, serial, prefer direct
  |     aggressive:  more subtasks, parallel
  |
  +-- context gathering: how much to explore? (root only — children inherit)
  |     careful:     minimal — gather what triage says is needed
  |     aggressive:  broader — also inspect recent changes, related files, test state
  |
  +-- session tree propagation
  |     autonomy_resolution flows as a first-class field on Session
  |     children inherit it (subagent model — no re-triage, no re-gather)
  |     hard safety (resolve_policy) still applies per-action at every level
  |
  v
TIER EXECUTOR (per session — root or child)
  |
  +-- orchestration decisions
  |     MultiStepExecutor._should_decompose():
  |       careful:     higher threshold, skip decomposition
  |       aggressive:  lower threshold, decompose more readily
  |
  +-- tool loop parameters
  |     send_message_with_tools(max_tool_turns=...):
  |       careful:     lower cap, pause earlier
  |       aggressive:  higher cap, more exploration before answering
  |
  +-- prompt behavior (via PromptModuleResolver — single source of behavior instructions)
  |     interaction_policy layer merges mode + autonomy:
  |       careful:     explain plan, ask more, wait for approval
  |       balanced:    current default behavior
  |       aggressive:  minimize questions, state assumptions, keep pushing
  |
  +-- completion standard (tool loop final-answer path)
  |     careful:     stop and report, let user decide next step
  |     aggressive:  self-review: did I finish? should I verify/test?
  |                  is there an obvious next step I should just do?
  |
  v
PARENT-SIDE SYNTHESIS (MultiStepExecutor only, after children complete)
  |
  +-- review child results against original goal
  |     balanced:    concatenate results (current behavior)
  |     aggressive:  check whether combined results achieve the goal;
  |                  if gaps found, spawn a remediation child to address them
  |     careful:     summarize what was done, surface uncertainties for user
  |
  v
RESPONSE
  if effective autonomy changed: briefly state the change once
```

## Tasks

### Task 0 — Preference model, resolution, and persistence
- [x] 0-1. Define `AutonomyPreference` (`auto`, `careful`, `balanced`, `aggressive`) and `AutonomyResolution` carrying `effective_level`, `source` (explicit / inferred / fallback), `reason`, and `announce_change`
- [x] 0-2. Add `autonomy_preference` field to `Project` (default `"auto"`); low-signal `auto` must resolve to `balanced`
- [x] 0-3. Add **per-session** override storage keyed by `external_id` / session scope (patterned after `/progress` overrides); store the user preference, not a singleton effective level
- [x] 0-4. Add normalization helpers that map legacy `AutonomyLevel` / `DAN_CONCIERGE_AUTONOMY` onto the new model
- [x] 0-5. Implement resolution logic: preference precedence, conservative auto-inference, and "did the effective autonomy change?" tracking
- [x] 0-6. Define conservative first-pass inference signals: risky/irreversible/ambiguous/messaging mutation turns bias `careful`; low-risk reversible or clearly directive turns may bias `aggressive`; weak or mixed signals resolve to `balanced`

### Task 1 — Session tree propagation and policy hooks
- [x] 1-1. Add `autonomy_resolution: AutonomyResolution | None` as a first-class field on `Session`
- [x] 1-2. Set `autonomy_resolution` on root sessions during `TieredDispatcher.dispatch()` after triage resolves
- [x] 1-3. Propagate `autonomy_resolution` to child sessions in `SessionManager.create_child()` — children inherit the parent's resolved autonomy (subagent model: no re-triage, no re-resolution)
- [x] 1-4. Add `autonomy_resolution` parameter to `resolve_policy()`; `careful` lowers thresholds, `aggressive` raises them, hard safety invariants (destructive keywords, publish) never relax
- [x] 1-5. Thread `AutonomyResolution` into the response path so when the effective level changes, the assistant briefly states that change in the visible reply once
- [x] 1-6. Add `/autonomy [auto|careful|balanced|aggressive] [--project]` through `command_registry.py`; plain `/autonomy` is session-scoped, `--project` persists
- [x] 1-7. Include `autonomy_resolution` in session telemetry (`session_complete` and `tiered_dispatch_complete` events) so "why did DAN become aggressive here?" is answerable from logs

### Task 2 — Per-tier orchestration behavior
- [x] 2-1. **Context gathering intensity (root only):** extend `ContextGatherer.gather()` to accept `autonomy_resolution`; `aggressive` adds broader context signals (recent task turns, related file inspection, test/diff state); `careful` gathers only what triage explicitly requested. Child sessions inherit parent context and do not re-gather.
- [x] 2-2. **Decomposition threshold:** make `MultiStepExecutor._should_decompose()` autonomy-aware; `aggressive` lowers the bar to decompose, `careful` raises it
- [x] 2-3. **Tool loop budget:** thread autonomy into `_extract_chat_params()` and `send_message_with_tools()` so `max_tool_turns` is higher for `aggressive` (more exploration, more chaining) and lower for `careful`
- [x] 2-4. **Completion standard:** for `aggressive`, inject a self-review prompt into the tool loop's final-answer path: "before stopping, check: did I fully address the goal? should I verify/test? is there an obvious next step I should do now?" For `careful`, prompt the model to summarize what was done and surface any uncertainties
- [x] 2-5. **Parent-side synthesis review:** upgrade `MultiStepExecutor._synthesize()` so the parent checks whether child results collectively achieve the original goal. For `aggressive`, hard gaps stay deterministic, but regex/keyword-style edge signals use a text-only LLM fallback before the parent decides whether to spawn a bounded remediation child (max 1, inherits autonomy). For `careful`, the synthesis summarizes what was done and surfaces uncertainties for user review. For `balanced`, preserve current concatenation behavior.
- [x] 2-6. **Per-session factual context:** update `_build_prompt()` in `tier_executors.py` to include the effective autonomy level as factual metadata (e.g. `"Autonomy: aggressive"`) so the LLM knows its operating posture, but do **not** put behavioral prose here — behavioral instructions belong exclusively in the `interaction_policy` prompt module to avoid duplication

### Task 3 — PromptModuleResolver (layered hint sections)
- [x] 3-1. Define `PromptContext` dataclass: `mode`, `surface`, `model`, `user_message`, `workflow_id`, `autonomy_resolution`, `tools_available`, project metadata, precomputed hint flags
- [x] 3-2. Define async-friendly `PromptModule` / `ResolvedPromptModule` types in `chat/prompts.py` with a stable layer/family declaration (`interaction_policy`, `surface_presentation`, `task_specializer`)
- [x] 3-3. Implement `PromptModuleResolver` with registration, stable layer ordering, priority ordering within a layer, and JIT detail lookup
- [x] 3-4. Refactor `_build_messages()` so the resolver owns only hint-like sections and collapses them into a single `{module_hints}` prompt block
- [x] 3-5. Keep `user_context_block`, `mcp_block`, `memory_context`, workflow/context summaries, and `extra_system_instructions` outside the resolver in v1
- [x] 3-6. Thread resolved autonomy/project info from `_extract_chat_params()` into both `send()` and `send_message_with_tools()`

### Task 4 — Initial prompt modules
- [x] 4-1. Register `interaction_policy` as the **single source** of behavioral autonomy instructions, merging current `_MODE_HINTS` with autonomy-specific posture:
  - `careful`: explain plan first, ask more readily when ambiguous, wait for approval before modifications
  - `balanced`: match current default behavior
  - `aggressive`: minimize user-facing questions, state assumptions briefly, push forward, self-review before stopping, explore context proactively
- [x] 4-2. Register `surface_presentation` by migrating `_resolve_surface_hints()`, including Telegram bot-name formatting
- [x] 4-3. Register `research_specializer` using the current detector path, preserving inline guidance quality
- [x] 4-4. Keep the module boundary open for future task specializers (`exploration`, `code_review`, `debug_protocol`)

### Task 5 — Chat-side JIT prompt detail loading
- [x] 5-1. Add `load_prompt_detail` as a normal chat capability (schema + handler + registry registration)
- [x] 5-2. Expose `load_prompt_detail` only when at least one resolved module has extra detail available **and** `tools_available=True`
- [x] 5-3. Split research guidance into compact inline core plus optional expanded detail
- [x] 5-4. Add `exploration_specializer` as the first genuinely JIT-heavy task-specializer
- [x] 5-5. When tools are unavailable, never emit instructions telling the model to call `load_prompt_detail`

### Task 6 — Tests
- [x] 6-1. Unit tests for `AutonomyPreference`, `AutonomyResolution`, and `resolve_policy()` integration across explicit and `auto` modes, including hard-safety invariants
- [x] 6-2. Unit tests proving session overrides do not leak across `external_id` / surface scopes
- [x] 6-3. Integration tests for `/autonomy` command registration/dispatch, including `auto`, session override, and `--project` persistence
- [x] 6-4. Unit tests for auto-inference transitions and "announce change once" behavior
- [x] 6-5. **Session tree propagation tests:** root session sets `autonomy_resolution`, children inherit it (subagent model), hard safety still fires per-action regardless of autonomy level
- [x] 6-6. **Orchestration behavior tests:** `_should_decompose()` threshold changes with autonomy, `max_tool_turns` adjusts, context gathering broadens with `aggressive` at root level only
- [x] 6-7. **Synthesis review tests:** `aggressive` synthesis detects gaps and spawns at most one remediation child; `careful` synthesis surfaces uncertainties; `balanced` preserves current concatenation behavior
- [x] 6-8. Unit tests for `PromptModuleResolver` ordering, async resolution, layer composition, JIT exposure
- [x] 6-9. Regression test that `tools_available=False` prompt builds never suggest `load_prompt_detail`
- [x] 6-10. Capability test that `load_prompt_detail` returns expected module body and fails cleanly on unknown IDs
- [x] 6-11. Balanced-mode regression that migrated interaction/surface/research hints remain materially equivalent to current output when `auto` resolves to `balanced`
- [x] 6-12. Telemetry test that `autonomy_resolution` appears in `session_complete` event metadata

## Decisions

- Autonomy is a **workflow-level operating style**, not just a prompt personality. It controls context-gathering depth, decomposition strategy, step chaining, tool budget, completion standard, and prompt behavior.
- Autonomy propagates through the session tree as a first-class field on `Session`. Every tier head — root or child — applies the same initiative contract to its subtask.
- **Children are subagents.** They inherit context and autonomy from their parent. They do not re-triage or re-run context gathering. This matches the existing `_build_child_session()` design and keeps the child path lightweight.
- **Review and remediation are parent-side.** After children complete, the parent's `_synthesize()` step checks results against the original goal. If `aggressive` finds gaps, it spawns at most one remediation child — not a separate review child or recursive self-review tree. This avoids runaway self-review loops while still giving the parent the ability to push for completion.
- Aggressive parent-side review now uses an explicit deterministic completion check: planned-subtask coverage, open follow-up signals in child output, and coarse goal/deliverable term coverage replace the earlier "multiple children implies review" heuristic. Regex/keyword-style edge signals do not directly force remediation; they trigger a text-only LLM fallback review first.
- **Behavioral instructions live in one place.** The `interaction_policy` prompt module is the single source of autonomy behavior prose. `_build_prompt()` carries only factual metadata (autonomy level, goal, domain) — never behavioral instructions that duplicate the module.
- The default stored preference is `auto`. Conservative inference falls back to `balanced` when signals are weak, preserving current behavior.
- User-set explicit autonomy always beats inferred autonomy.
- Hard safety never relaxes: `resolve_policy()` applies destructive/publish/irreversible confirmation gates per-action at every session tree level, regardless of autonomy.
- `aggressive` means "more initiative, more self-driven orchestration, more forward motion" — not "more reckless." It asks the user fewer blocking questions but asks the system/workspace more questions (inspect files, check recent changes, review against goal, verify before stopping).
- Session-scoped concierge autonomy state is keyed by `surface + external_id` (with legacy external-id fallback on load) so `/autonomy` preferences and last-effective tracking do not leak across surfaces that happen to reuse the same user/thread identifier.
- The `PromptModuleResolver` rollout is intentionally narrow: it owns hint-like prompt sections only, not memory/MCP/tool inventories/workflow summaries.
- Prompt modules are layered: `interaction_policy`, `surface_presentation`, `task_specializer`. Mode + autonomy are merged into one `interaction_policy` layer so they cannot contradict each other.
- When effective autonomy changes, the assistant says so briefly once.
- Legacy `AutonomyLevel` / `DAN_CONCIERGE_AUTONOMY` inputs are mapped, not broken.
- Internal text-only review calls now reuse the same resolved workflow/project/autonomy context extraction as the tool-loop path, so prompt modules and memory scoping stay aligned across both `send()` and `send_message_with_tools()`.
- `research_specializer` now follows the intended JIT pattern: a compact inline behavior block stays in the main prompt, while the step-by-step research workflow lives in `prompt:research_specializer:full`.
- `exploration_specializer` is now the first additional task-specializer beyond research, using the same pattern: heuristic-first routing, tiny LLM fallback on ambiguous triggers, compact inline guidance, and a heavier `load_prompt_detail` expansion path.
- Aggressive root-only context gathering now broadens beyond triage-explicit reads with bounded extra signals: recent task turns, a compact task snapshot, related file snippets from surface/task context, and a tiny git-status repo snapshot when a workspace root is available.

## Notes

- `UNIFIED_SYSTEM_PROMPT` should collapse `{surface_hints}`, `{mode_hints}`, and `{task_hints}` into a single `{module_hints}` section once the resolver owns all three layers.
- `_build_messages()` is called by both `send()` and `send_message_with_tools()`, so every resolver/JIT change must be validated on both code paths.
- `_build_prompt()` in `tier_executors.py` is the per-session factual context (goal, deliverable, domain, memory, autonomy level as metadata). `_build_messages()` in `chat_manager.py` adds behavioral hints via the `PromptModuleResolver`. This separation prevents duplication.
- If `load_prompt_detail` needs resolver access at capability-execution time, prefer exposing lookup through `CapabilityContext` / `ctx.chat_manager`.
- Prefer runtime-provided `AutonomyResolution` metadata over asking the model to infer its own autonomy state from scattered prompt text.
- The "max 1 remediation child" rule in synthesis review is enforced by a simple flag, not by the generic session budget. This prevents `aggressive` synthesis from exhausting the child budget on repeated remediation attempts.
- Current implementation intentionally leaves richer aggressive root-context gathering (related files, test/diff inspection), compact-vs-expanded research prompt splitting, an `exploration_specializer`, and the remaining session-isolation/telemetry/balanced-equivalence regressions as follow-up hardening items.
- Plan scope is complete. Future prompt specializers (`code_review`, `debug_protocol`, etc.) can build on the same resolver/JIT pattern established here.
- Internal rule for this area: when regex/keyword heuristics are used as a completion or routing fast path, prefer an LLM fallback for ambiguous edge cases rather than letting the regex be the final authority.
- Future heavy modules can follow after this lands: debugging protocol, code-review checklist, exploration playbook, domain-specific prompt packs.

## Estimate

~7 days
