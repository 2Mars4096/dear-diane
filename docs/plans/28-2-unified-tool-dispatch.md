# 28-2: Unified Tool Dispatch

**Parent:** [28-llm-first-chat](28-llm-first-chat.md)
**Status:** completed
**Goal:** Replace classifier→handler dispatch with a single LLM call that has all tools and a multi-turn tool loop.

## Problem

Today: `classify_intent()` picks 1 of 10 handlers → handler calls `ChatManager` in a specific mode → mode filters tools → LLM sees a subset. The classifier is the bottleneck: wrong classification = wrong handler = wrong tools = wrong answer.

Target: every message goes to the LLM with all tools. The LLM decides what to do. Multi-turn: LLM calls tool → gets result → calls another → ... → produces text.

## Tasks

### Core: multi-turn tool loop
- [x] 1. Add multi-turn tool execution to `ChatManager.send_message_with_tools()`
  - [x] 1-1. After the LLM's first `complete()` returns tool calls, execute them, feed results back as tool-result messages, call `complete()` again
  - [x] 1-2. Loop until the LLM returns a text response (no tool calls) or hits the turn cap (default 10, configurable via `max_tool_turns` parameter)
  - [x] 1-3. Stream `ChatToolCallStartEvent` / `ChatToolCallResultEvent` for each tool call so the adapter/editor can show progress
  - [x] 1-4. Respect `cancel_event` at each loop iteration
- [x] 2. Remove mode-based tool filtering
  - [x] 2-1. `_build_messages()` always includes `CAPABILITY_TOOLS_REFERENCE`
  - [x] 2-2. `get_tools(mode)` returns all tools regardless of mode
  - [x] 2-3. `is_available()` and `execute()` skip mode checks
  - [ ] 2-4. Safety filtering moves to Response Actions (28-3) — the LLM can request any tool, but destructive tools get confirmed before execution

### New dispatch path in concierge
- [x] 3. Add `Concierge._llm_first_path()` — the new default
  - [x] 3-1. Assemble context (project, profile, history, surface)
  - [x] 3-2. Build system prompt with context + tool guidelines
  - [x] 3-3. Call `ChatManager.send_message_with_tools()` (with multi-turn loop)
  - [ ] 3-4. Pass through response for Response Actions (28-3)
- [x] 4. Wire the new path into `Concierge.process()`
  - [x] 4-1. Default: `_llm_first_path()` for all messages
  - [ ] 4-2. Optional fast-path: if classifier confidence > 0.95 AND intent is unambiguous (cancel, status), use existing handler directly (skip LLM call for cost)
  - [x] 4-3. Feature flag: `DAN_LLM_FIRST_CHAT=1` (default on) — set to 0 to revert to old classifier path

### Server endpoint simplification
- [x] 5. Simplify `/api/chat/message` in `app.py`
  - [x] 5-1. Skip `detect_chat_mode()` when concierge is active and mode="auto" — use "agent" directly
  - [x] 5-2. Remove the `use_tools` branch — non-concierge fallback always uses `send_message_with_tools`
  - [ ] 5-3. Always route through concierge when available — deferred to 28-5

### Tests
- [x] 6. Add multi-turn tool loop tests
  - [x] 6-1. Test: LLM calls tool on turn 1, returns text on turn 2
  - [x] 6-2. Test: LLM calls 2 different tools across turns, then text
  - [x] 6-3. Test: turn cap enforced (LLM loops stopped at max_tool_turns)
  - [x] 6-4. Test: cancel_event interrupts mid-loop
  - [x] 6-5. Test: text-only response (no tools) works in 1 turn
  - [x] 6-6. Test: tool error status reported correctly
  - [x] 6-7. Test: provider failure on later turn yields ChatErrorEvent
  - [x] 6-8. Test: multiple tool calls in single response (parallel)

## Decisions

- `max_tool_turns` is a method parameter (default 10), not env var — easier to test and override per call
- First-turn provider failures fall back to streaming text; later-turn failures yield ChatErrorEvent
- Mutation tool calls exit the loop (handled as before, not looped)
- Tool result messages use OpenAI format: `{"role": "tool", "tool_call_id": "...", "content": "..."}`
- Existing capability registry tests updated to reflect mode-agnostic behavior

## Files

| File | Action |
|---|---|
| `src/dan/server/chat_manager.py` | Major — multi-turn tool loop, remove mode gating |
| `src/dan/server/concierge/runtime.py` | Major — `_llm_first_path()`, feature flag |
| `src/dan/server/app.py` | Simplify chat_message endpoint |
| `src/dan/server/capability_registry.py` | `get_tools()` / `is_available()` / `execute()` ignore mode |
| `tests/test_server/test_multi_turn_tools.py` | New — 8 tests for multi-turn loop |
| `tests/test_server/test_capability_registry.py` | Updated — mode-agnostic assertions |

## Notes

- The multi-turn tool loop is the hardest part. The LLM API returns tool calls as part of `CompletionResult.tool_calls`. The loop must: build tool-result messages → append to conversation → call `complete()` again.
- OpenAI-compatible APIs expect tool results as `{"role": "tool", "tool_call_id": "...", "content": "..."}` messages.
- The existing `_extract_all_capability_tool_calls()` + execute pattern in `send_message_with_tools()` is the starting point — wrapped in a loop.
- **Long-running tools**: `shell_command` (regressions, builds) can take minutes. Each tool execution should have its own timeout (default 120s, configurable per tool). The WebSocket ping is already disabled (`ping_interval=None`), but the LLM `complete()` timeout should also be generous during tool loops.
- **Parallel tool calls**: multiple tool calls in a single LLM response are executed concurrently via `asyncio.gather()`. Start events are yielded first, then all tools execute in parallel, then result events are yielded in order.
