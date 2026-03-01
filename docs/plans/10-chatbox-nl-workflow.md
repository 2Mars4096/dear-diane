# 10: Conversational Workflow Authoring (Chatbox)

**Status:** completed
**Goal:** Add a chat panel to the visual editor where users describe workflows in natural language. The system interprets intent, generates/modifies the graph, and streams results — a fourth interaction surface (alongside drag-and-drop, Python builder, and markdown files) that makes workflow creation as fluid as a conversation.

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [10-1](10-1-chat-panel-backend.md) | Chat Panel & Backend API | Chat UI component, FastAPI message endpoint, LLM integration, graph-aware system prompt, streaming responses | new `editor/src/components/ChatPanel.tsx`, new `src/dan/server/chat_manager.py`, `app.py` |
| [10-2](10-2-mention-co-navigation.md) | `@` Mention & Co-Navigation | `@` trigger detection, autocomplete dropdown (nodes/workflows/sub-graphs), mention chips, click→canvas selection, canvas→chat suggestion | new `editor/src/components/MentionAutocomplete.tsx`, `ChatPanel.tsx`, `useGraphStore.ts` |
| [10-3](10-3-nl-graph-mutation.md) | NL→Graph Mutation Engine | Graph operation primitives, LLM function-calling schema, multi-step mutation planning, validation before apply, error recovery | new `src/dan/server/graph_mutator.py`, `chat_manager.py` |
| [10-4](10-4-graph-diff-confirmation.md) | Graph Diff & Confirmation UX | Before/after graph diff computation, visual diff preview dialog, accept/reject/partial-accept, undo integration, session-scoped rollback | new `editor/src/components/GraphDiffPreview.tsx`, `useGraphStore.ts`, `graphAdapter.ts` |
| [10-5](10-5-history-execution.md) | Chat History & Session Integration | Per-workflow message persistence, history list UI, graph delta tracking, session-scoped rollback metadata | new `src/dan/server/chat_store.py`, `ChatPanel.tsx`, `useGraphStore.ts` |
| [10-6](10-6-scoped-run-from-chat.md) | Scoped Run Execution from Chat | Full/node/sub-graph run API, target resolution, chat command handling, run event streaming into thread | new `src/dan/server/scoped_run.py`, `chat_manager.py`, `run_manager.py`, `app.py` |
| [10-7](10-7-apply-mutation-flow.md) | Apply Mutation Flow | Wire GraphDiffPreview, apply-mutation endpoint, chat→preview→apply pipeline, undo/session marker | `app.py`, `editor/src/lib/api.ts`, `ChatMessage.tsx`, `ChatPanel.tsx` |
| [10-8](10-8-nl-mutation-hardening.md) | NL Mutation Hardening | Validate-before-save, port-aware edges, entry/exit recompute, typed tool schema, pattern macros, mutation CI | `graph_mutator.py`, `chat_manager.py`, `llm-api-guide.md` |
| [10-9](10-9-meta-orchestrator.md) | Meta Orchestrator | Zero-to-workflow from natural language intent; self-builds workflow from scratch | `chat_manager.py`, `graph_mutator.py`, ChatPanel |
| [10-10](10-10-domain-nl-authoring.md) | Domain NL Authoring | `data_ingest` pattern, rich INFORMS paper-writing template, lightweight skill injection, `BUILD_FROM_INTENT_PROMPT` quality, multi-turn clarification | `graph_mutator.py`, `chat_manager.py`, `llm-api-guide.md` |

## Dependencies / Sequencing

Build order is mostly linear — each sub-plan extends the previous:

1. **10-1** first: foundational chat panel + backend. Nothing else works without this.
2. **10-2** second: `@` mentions enrich the chat experience and provide structured node references that 10-3 depends on.
3. **10-3** third: the core intelligence layer. Converts NL to graph mutations. Needs the chat panel (10-1) and mention resolution (10-2) to provide context.
4. **10-4** fourth: safety layer. Graph diff preview before applying mutations from 10-3. Can be developed partly in parallel with 10-3.
5. **10-5** fifth: persistence and session metadata. Gives chat durable threads and mutation provenance.
6. **10-6** sixth: scoped execution and run streaming from chat. Split into its own backend-heavy plan for detail and testability.

```
10-1 (panel + API)
  └──> 10-2 (@ mentions)
         └──> 10-3 (NL→graph mutation)
                └──> 10-4 (diff + confirmation)
                       └──> 10-5 (history + session)
                              └──> 10-6 (scoped run + streaming)
```

10-6 backend API work can begin after 10-1, but full chat-thread integration depends on 10-5.

## Shared Decisions

- **Chat is a panel, not a modal.** The chat panel lives alongside the canvas as a resizable pane (like the existing log panel). It's always accessible, not a popup. Users can see the graph and chat simultaneously.
- **Server-authoritative graph context.** The backend loads graph state from `workflow_id` and serializes `GraphSummary` server-side. Client-supplied graph context is not trusted for mutation planning.
- **Mutations are operations, not full graph replacement.** The LLM produces a sequence of atomic graph operations (add_node, remove_node, edit_node, add_edge, remove_edge, edit_edge_type, rename_node, set_prompt, etc.). This enables granular diff, partial accept, and undo — and avoids the LLM hallucinating unrelated graph changes.
- **Mutation apply is optimistic-concurrency-safe.** Every mutation plan carries a base graph revision/hash. If graph state changes before apply, the plan is rejected as stale and re-planning is required.
- **`@` mentions resolve to structured context.** `@SectionWriter` doesn't just paste a name into the prompt — it injects the full node definition (type, ports, prompt, connections) so the LLM can reason about it precisely.
- **Chat uses the same LLM provider infrastructure.** Chat messages route through `ProviderRegistry` using a configurable `DAN_CHAT_MODEL` (default: `claude-sonnet-4-6`). No separate LLM integration.
- **WebSocket for streaming.** Chat responses stream token-by-token over the existing WebSocket infrastructure (new event types, same connection pattern as run events).
- **Graph mutations go through the undo stack.** Every chat-generated mutation batch is pushed as a single undo snapshot. `Ctrl+Z` reverts the entire chat action, not individual operations.
- **Chat history is per-workflow.** Each workflow tab has its own conversation thread. Switching tabs switches the chat context. History persists to disk alongside graph JSON.
- **Rollback is session-scoped.** "Revert to here" is supported within the active editor session/tab history. Cross-reload rollback pointers are intentionally out of scope.
- **No drag-and-drop replacement.** Chat augments the visual editor — it doesn't replace it. Users can freely mix chat commands with manual edits. The graph is the source of truth; chat is an input method.

## Notes

- The existing `HumanInTheLoopNode` is for mid-execution human input (approve/reject during a run). The chatbox is for design-time interaction (building/editing the graph before or between runs). These are orthogonal features.
- Cursor's `@` file mention system is the UX reference. The autocomplete should feel equally fast and fluid.
- The graph-aware system prompt is the key differentiator. Generic chatbots can't reason about graph topology. DAN's chat knows the exact structure and can make precise edits.
- Future: chat can serve as a conversational interface for running workflows (Phase 10-6), blurring the line between design-time and run-time interaction.
- Future: multi-user chat (collaborative editing with shared chat thread) is out of scope for this phase. Single-user local is the target.
