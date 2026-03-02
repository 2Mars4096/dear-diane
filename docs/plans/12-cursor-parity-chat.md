# 12: Phase 7.2 — Cursor-Parity Chat Experience

**Status:** completed (core)
**Goal:** Evolve the chatbox from a graph-editing assistant into a full-featured conversational development surface with Cursor-level UX quality — multi-mode interaction, rich context mentions, inline tool display, robust conversation lifecycle, and measurable quality.

## Motivation

Phase 7 (plan 10) delivered a functional chat panel with NL→graph mutations, `@` mentions (nodes/workflows/subgraphs), diff preview, scoped run commands, and build-from-intent. However, a detailed audit against Cursor's chat surface revealed significant gaps in **modes**, **context richness**, **tool transparency**, **conversation management**, and **reliability**. These gaps compound into a chatbox that feels fragile and limited compared to a modern AI-powered editor.

### What exists today (Phase 7 deliverables)
- Two chat modes: `build` (empty graph → intent-first) and `mutate` (edit existing graph)
- `@` mentions for nodes, workflows, sub-graphs with autocomplete
- LLM function-calling for `MutationPlan` generation
- `GraphDiffPreview` with accept/reject
- Per-workflow thread persistence, thread list UI
- `/run`, `/run-node`, `/run-subgraph` chat commands
- WebSocket streaming (token-by-token)
- Auto-retry on failed mutation dry-runs
- Optimistic concurrency via graph revision hashing

### What Cursor has that we don't
| Capability | Cursor | DAN Chat (today) |
|---|---|---|
| **Chat modes** | Agent, Ask, Plan, Debug | build, mutate (both mutation-focused) |
| **@mentions** | @Files, @Code, @Docs, @Web, @Past Chats, @Git | @node, @workflow, @subgraph only |
| **Context mgmt** | Auto-summarization, context budget, token counting | Unbounded history (risks context overflow) |
| **Tool display** | Inline tool calls with expand/collapse, inputs/outputs | Mutation plan only, no inline tool trace |
| **Approval model** | Approve/reject per tool call, sandbox mode | All-or-nothing mutation accept/reject |
| **Stop/interrupt** | Stop generation mid-stream | No stop button |
| **Message queue** | Send follow-ups while generating | Must wait for completion |
| **Checkpoints** | Conversation checkpoints, restore points | Session-scoped undo only |
| **Export/share** | Export as markdown, share links | None |
| **Search** | Search across threads | None |
| **Terminal integration** | Inline terminal output, command execution | `/run` returns status badge, no inline output |
| **Error investigation** | Debug mode analyzes errors, suggests fixes | Manual only |

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [12-1](12-1-chat-reliability-polish.md) | Chat Reliability & Polish | Fix trust gaps: revision concurrency, history compaction, dead CTAs, env var cleanup, integration tests | `chat_manager.py`, `ChatPanel.tsx`, `ChatMessage.tsx`, `api.ts`, `app.py` |
| [12-2](12-2-multi-mode-chat.md) | Multi-Mode Chat | Ask / Agent / Plan / Debug modes with mode-specific prompts and tool availability | `chat_manager.py`, `ChatPanel.tsx`, `useGraphStore.ts`, new prompt files |
| [12-3](12-3-rich-context-mentions.md) | Rich Context & Mentions | @Files, @Code, @Docs, @Web, @Past Chats + server-side resolution + context budget | `MentionAutocomplete.tsx`, `chat_manager.py`, new resolvers |
| [12-4](12-4-tool-display-execution.md) | Tool Display & Execution | Inline tool call rendering, approval gates, sandbox execution, terminal output in chat | `ChatMessage.tsx`, `ChatPanel.tsx`, `chat_manager.py`, `app.py` |
| [12-5](12-5-conversation-lifecycle.md) | Conversation Lifecycle | Stop generation, message queue, checkpoints, export, search, thread branching | `ChatPanel.tsx`, `chat_store.py`, `app.py`, new endpoints |
| [12-6](12-6-chat-quality-harness.md) | Chat Quality Harness | Eval framework, regression tests, provider compatibility, latency benchmarks | new `tests/chat/`, `chat_manager.py`, CI config |

## Dependencies / Sequencing

```
12-1 (reliability — must come first)
  ├──> 12-2 (modes — depends on stable chat infrastructure)
  │      └──> 12-4 (tool display — Agent/Debug modes need tool rendering)
  ├──> 12-3 (context — can parallel with 12-2)
  │      └──> 12-4 (tool display — uses context for tool calls)
  └──> 12-5 (conversation lifecycle — depends on stable chat)
12-6 (quality harness — can start after 12-1, runs alongside everything)
```

12-1 is the foundation — fixes must land before new features. 12-2 and 12-3 can proceed in parallel after 12-1. 12-4 depends on both modes and context. 12-5 is mostly independent UI/backend work. 12-6 is cross-cutting and should start early to measure progress.

## Shared Decisions

- **Modes are prompt + tool-availability switches, not separate endpoints.** The same `/api/chat/message` endpoint handles all modes. Mode determines system prompt template and which tools the LLM can call.
- **Context budget is server-enforced.** The backend counts tokens and truncates/summarizes to fit within model limits. Frontend shows token usage but doesn't make truncation decisions.
- **Mentions resolve server-side.** Today mentions parse client-side only. This phase adds server-side resolution so the LLM receives structured context objects (file contents, code snippets, docs, past chat excerpts).
- **Tool calls are first-class chat events.** New WebSocket event types (`chat_tool_call_start`, `chat_tool_call_result`) render inline in the chat stream, not just as mutation plans.
- **Backward compatible.** Existing `build`/`mutate` behavior maps to Agent mode. No breaking changes to existing chat threads or API contracts.
- **Incremental delivery.** Each sub-plan delivers independently usable improvements. The phase doesn't need to complete fully to add value.

## Notes

- This phase does not aim for 1:1 Cursor feature parity — Cursor is a general code editor agent; DAN is a workflow builder. The goal is *UX parity* in the chat interaction model while keeping the domain focus on graph authoring.
- Multi-user chat is still out of scope. Single-user local remains the target.
- The existing `HumanInTheLoopNode` (run-time approval) is orthogonal to chat modes (design-time interaction). They may converge in future phases.
