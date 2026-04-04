# 12-2: Multi-Mode Chat Architecture

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** in-progress
**Goal:** Replace the binary build/mutate mode system with four distinct chat modes (Ask, Agent, Plan, Debug) that match user intent and control tool availability, following Cursor's proven interaction model.

## Mode Definitions

| Mode | Purpose | Tools Available | Mutations? | System Prompt Focus |
|---|---|---|---|---|
| **Ask** | Answer questions about the graph, explain nodes, describe data flow | None (read-only) | No | Graph comprehension, documentation lookup |
| **Agent** | Build and edit workflows conversationally (current build + mutate merged) | `plan_graph_mutations`, `expand_pattern`, future tools | Yes | Intent→mutation planning, pattern library |
| **Plan** | Propose an approach, get user approval before executing | `plan_graph_mutations` (dry-run only) | Only after explicit approval | Step-by-step planning, trade-off analysis |
| **Debug** | Investigate run failures, suggest fixes | `plan_graph_mutations`, `inspect_run_logs` | Yes (fix mutations) | Error analysis, log inspection, fix suggestion |

## Tasks

- [x] 1. Backend mode routing
  - [x] 1-1. Extend `ChatMessageRequest.mode` to accept `"ask" | "agent" | "plan" | "debug"` (deprecate `"build"` / `"mutate"` as aliases for `"agent"`)
  - [x] 1-2. Create prompt templates as constants in `chat_manager.py`: `ASK_PROMPT`, `PLAN_PROMPT`, `DEBUG_PROMPT` (agent reuses existing prompts)
  - [x] 1-3. `chat_manager.py`: select system prompt and tool set based on mode via `_build_messages()`
  - [x] 1-4. Ask mode: disable tool calling entirely; return text-only LLM response
  - [x] 1-5. Plan mode is strictly two-step: (A) natural-language plan proposal first (no tools), (B) after user approval, frontend sends with mode=agent for mutation generation
  - [x] 1-6. Debug mode: inject recent run logs (last failed run for the active workflow) into context automatically via `build_debug_context()`
  - [x] 1-7. Tighten the shared prompt loop and mode-specific behavior hints so Agent validates prior steps, Plan waits for approval, Ask stays read-only, and Debug states a diagnostic hypothesis before fixing
- [x] 2. Frontend mode selector
  - [x] 2-1. Replace current build/mutate toggle with segmented control (Agent | Ask | Plan | Debug) in chat header
  - [ ] 2-2. Mode persists per-thread (store in thread metadata) → deferred to follow-up
  - [x] 2-3. Show mode indicator in chat header (icon + label per mode)
  - [ ] 2-4. Keyboard shortcut to cycle modes (e.g., `Cmd+Shift+M`) → deferred to follow-up
- [x] 3. Agent mode (merge of build + mutate)
  - [x] 3-1. Empty-graph detection auto-enables build-from-intent prompt (same as current behavior)
  - [x] 3-2. Non-empty graph uses graph-aware mutation prompt (same as current behavior)
  - [x] 3-3. Backward compat: existing threads with `mode: "build"` or `mode: "mutate"` map to `"agent"` via `normalize_chat_mode()`
- [x] 4. Ask mode implementation
  - [x] 4-1. System prompt focuses on graph explanation: describe topology, explain node connections, summarize data flow, answer "what does X do?"
  - [x] 4-2. Graph summary is injected (read-only) but no mutation tools are registered
  - [x] 4-3. Mention resolution works (click @node → navigate) but no mutation suggestions
  - [x] 4-4. Frontend: disable diff preview rendering for Ask mode responses (onPreviewMutation=undefined)
- [x] 5. Plan mode implementation
  - [x] 5-1. System prompt instructs LLM to propose a step-by-step approach before generating mutations
  - [x] 5-2. First response is a natural-language plan (no tool calls — tools not passed for plan mode)
  - [x] 5-3. User confirms → frontend sends follow-up with mode=agent for mutation generation
  - [x] 5-4. Frontend: show "Approve Plan" / "Revise" buttons on plan messages (PlanApprovalButtons component)
  - [x] 5-5. Plan approval triggers Agent-mode mutation generation in the same thread
- [x] 6. Debug mode implementation
  - [x] 6-1. Auto-inject context: last run's error events, failed node outputs, stack traces via `build_debug_context()`
  - [x] 6-2. New backend helper: `build_debug_context(runs, workflow_id)` → structured run failure summary
  - [x] 6-3. System prompt focuses on error diagnosis: identify root cause, suggest node edits, offer to fix
  - [ ] 6-4. Debug mutations get a `[debug-fix]` tag in the diff preview for clarity → deferred to follow-up
  - [ ] 6-5. "Fix this" shortcut: one-click from run error → open chat in Debug mode with error pre-filled → deferred to follow-up
- [ ] 7. Auto-mode detection *(deferred — stretch goal, not blocking core UX)*
  - [ ] 7-1. Heuristic: questions ("what", "why", "how", "explain") → Ask; error context → Debug; "build", "create", "add" → Agent *(deferred — stretch goal)*
  - [ ] 7-2. Show detected mode as suggestion, user can override *(deferred — stretch goal)*
  - [ ] 7-3. Flag as experimental in UI *(deferred — stretch goal)*

## Decisions

- Prompts stored as constants in `chat_manager.py` alongside existing prompts (not separate files) for co-location with the code that uses them.
- `normalize_chat_mode()` handles backward compat: `"build"` and `"mutate"` both map to `"agent"`.
- Plan mode uses a two-step frontend flow: plan mode sends text-only, approval sends with `mode: "agent"` to enable tool calling. No special backend plan-detection needed.
- Ask and Plan modes route through `send_message` (text-only), while Agent and Debug route through `send_message_with_tools`.
- `openBuildWithAI` now sets `chatMode: "agent"` + increments `chatFocusTrigger` instead of using a separate "build" mode.
- The shared unified prompt now owns the generic self-management loop, while `_build_messages()` injects a short mode-behavior block for Ask / Agent / Plan / Debug so execution constraints stay explicit without duplicating the full system prompt.
- Per-thread mode persistence (2-2) and keyboard shortcuts (2-4) deferred to follow-up.
- Debug diff tag (6-4) and "Fix this" shortcut (6-5) deferred to follow-up.

## Notes

- Current `build`/`mutate` distinction was a mode-of-the-graph (empty vs non-empty), not a mode-of-the-user. The new modes are user-intent-driven.
- Auto-mode detection (task 7) is a stretch goal. Manual mode selection is the MVP.
- Debug mode is the highest-value new mode — it directly addresses the "hard to use with errors" complaint from the initial review.
- Build-from-intent detection is now based on empty graph check (`is_empty_graph`) rather than mode name, making it work seamlessly with agent mode.
- 2026-03-18 follow-up: request-time tool summaries now come from the live chat capability registry instead of the static built-in tool library, Agent/auto prompts explicitly state that `plan_graph_mutations` is the workflow-building interface, and Kimi/OpenAI-compatible tool loops now avoid explicit `tool_choice` forcing (plus retry once with `tool_choice="auto"` if a provider still rejects it). Live smoke after restart confirmed an `auto` chat build prompt entered `plan_graph_mutations` immediately.
- 2026-03-18 follow-up: capability-tool execution now resolves user-facing mode aliases (`build`, `mutate`, and fallback Agent-equivalent paths) onto the canonical Agent registry bucket before advertising tools, extracting tool calls, or dispatching handlers. That keeps explicit build-mode turns aligned with the same capability set the prompt advertises instead of silently dropping Agent-registered tools at execution time.
- 2026-03-18 follow-up: post-discovery workflow-build turns now add the existing required-action retry prompt before the next LLM call whenever `workflow_edit` is still missing but the provider must stay on `tool_choice="auto"`, which reduced live “tool discovery only” stalls and pushed Kimi-backed turns into `plan_graph_mutations` after a short discovery pass instead of another vague continuation turn. `GraphMutator` also now treats `remove_node` on already-missing ids as an idempotent no-op and normalizes guessed source ports onto the sole declared output for single-output nodes (including the recurring `for_each.item -> results` and `code_operator.output -> result` drifts), so the mutation tool spends less time burning auto-retry cycles on harmless graph-cleanup mistakes.
- 2026-03-18 follow-up: workflow-build turns can now express valid control-flow bodies. `GraphMutator` auto-scaffolds empty body sub-graphs when chat adds `for_each` / `composite`-style nodes, exposes a new `replace_body_graph` operation for populating those nested bodies, and the prompt/runtime guidance now tells the model that top-level `for_each` ports are `items` / `results` while `item` belongs inside the body sub-graph entry node. The built-in `fan_out` pattern now expands into that same valid nested shape instead of an impossible top-level body-node layout.
- 2026-04-04 follow-up: `ChatManager.send_message_with_tools()` now resolves an effective execution mode from `required_action_hints` before building prompt/tool payloads, so turns that still require gated tool families (`file_write`, workflow mutation/apply/delete, `start_run`, and similar) are promoted onto a mode that actually exposes those tools instead of only describing them in prompt text. Focused regressions cover both registry-backed `file_write` and the built-in `plan_graph_mutations` workflow-edit path.
