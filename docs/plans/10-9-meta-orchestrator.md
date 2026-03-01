# 10-9: Meta Orchestrator / AI Assistant Mode

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** completed
**Goal:** Meta orchestrator that (A) interprets user needs and self-builds a workflow from intent, and (B) at **runtime**, works with its subgraphs **asynchronously** — like a real manager: receives from different teams in parallel, at any time; sends info to different teams at different times; all teams work simultaneously. Build-time creates the graph; runtime executes it with async bidirectional orchestrator↔subgraph communication.

## Two Modes

| Mode | Scope | Description |
|------|-------|-------------|
| **Build-time** | Create workflow from intent | User describes what they want; system produces runnable graph (Tasks 1–6) |
| **Runtime async orchestrator** | Execution semantics | Async is the primitive (includes concurrency). Orchestrator runs concurrently with subgraphs; receives from any subgraph at any time; sends to any subgraph at any time; all subgraphs run in parallel. More flexible than ForEach-style "block until all" (Task 7) |

## Context

Today (10-1..10-8):
- Chat operates on an **existing** graph (or empty). System prompt already says: "If the workflow is empty and the user asks to create one, use the plan_graph_mutations tool to build it from scratch."
- `GraphMutator.apply` works on any graph dict — `graph.setdefault("nodes", []).append(...)` handles empty graphs. First op is typically `add_node`.
- Pattern library (`chain`, `review_loop`, `fan_out`, `rag_qa`) and validation gate are in place.

The gap: **intent-first UX** — current flow still injects `{graph_summary}` (empty or not) and uses the same mutation prompt. Meta orchestrator provides (build-time):
- A **dedicated build mode** with intent-focused prompt (task decomposition, workflow shape, no graph context initially)
- **Template registry** — map high-level intents ("paper writing", "RAG QA") to pattern combinations or pre-built graphs
- **Clear entry point** — "Build with AI" / "Create from scratch" that opens chat in build mode, then persists result

## Tasks

- [x] 1. **Build-mode system prompt**
  - [x] 1-1. Add `BUILD_FROM_INTENT_PROMPT` (or mode-specific section): focus on task decomposition, stages, node types, data flow — no `{graph_summary}` initially
  - [x] 1-2. Include pattern library reference and template names: "For 'paper writing' use review_loop + chain; for 'RAG QA' use rag_qa pattern"
  - [x] 1-3. Intent → plan: LLM produces mutation plan (same `plan_graph_mutations` tool schema) targeting empty graph
  - [x] 1-4. Handle ambiguity: "Which sections? (default: intro, methods, results, discussion)" or propose defaults
- [x] 2. **Empty-graph bootstrap**
  - [x] 2-1. Ensure `build_graph_summary` handles empty graph (nodes=[], edges=[]) — returns valid `GraphSummary` with `node_count=0`, `revision` from hash of empty structure
  - [x] 2-2. `MutationPlan` base_graph_revision: when building from scratch, inject `base_graph_revision = compute_graph_revision(empty_graph)` before apply — mutator's stale check works; LLM need not set it (backend injects from empty state)
  - [x] 2-3. Validation: `Graph.model_validate` + `validate_graph` on result — 7-8 strict mode recommended for build-from-intent output
- [x] 3. **Template registry (optional, reduces LLM variability)**
  - [x] 3-1. Add `WORKFLOW_TEMPLATES: dict[str, list[dict]]` — keys: "paper_writing", "rag_qa", "chain_3"; values: list of mutation operations (expand_pattern ops)
  - [x] 3-2. Build prompt: "Available templates: paper_writing, rag_qa, chain_3" section added to `BUILD_FROM_INTENT_PROMPT`
  - [x] 3-3. Template + customization: LLM can apply template then add/remove nodes via follow-up mutations
- [x] 4. **API and chat flow**
  - [x] 4-1. Extend `ChatManager.send_message` / `send_message_with_tools` with `mode: Literal["mutate", "build"]` — when `mode="build"`, use `BUILD_FROM_INTENT_PROMPT`; mode="build" forces build prompt even on non-empty graph
  - [x] 4-2. Used mode flag on existing endpoint (not separate endpoint) — `ChatMessageRequest.mode` field on `POST /api/chat/message`
  - [x] 4-3. Response: same `ChatMutationEvent` with graph JSON; client persists to new workflow or replaces current
  - [x] 4-4. Post-build: subsequent messages use `mode="mutate"` with the newly created graph — client-side mode switch
- [x] 5. **Editor UX**
  - [x] 5-1. "Build with AI" / "New from chat" — toolbar or file menu; opens chat panel in build mode, blank canvas
  - [x] 5-2. After LLM returns mutation plan: show diff preview (empty → new graph), confirm, then save
  - [x] 5-3. Optional: "Run this workflow" button after save
- [x] 6. **Tests and docs**
  - [x] 6-1. Integration: intent "create a 3-node chain" → plan → apply → validate; representative intents (paper, RAG, chain)
  - [x] 6-2. Update changelog, architecture, llm-api-guide (build-from-intent flow)
- [x] 7. **Runtime async orchestrator (execution model)**
  - [x] 7-1. **Orchestrator runs concurrently with subgraphs:** `OrchestratorExecutor` uses `asyncio.create_task` for each team, then runs its own event loop concurrently. Not fire-and-forget gather.
  - [x] 7-2. **Receive from any subgraph, at any time:** Routing callback wraps original `_event_callback` and feeds events to an `asyncio.Queue`. Orchestrator drains queue non-blocking in its loop.
  - [x] 7-3. **Send to any subgraph, at any time:** MVP uses `SharedContextStore` — orchestrator writes to `__orchestrator__{node_id}__received__{team_name}` keys; subgraph nodes read via context edges.
  - [x] 7-4. **All teams work simultaneously:** Teams launched as `asyncio.create_task` — true concurrent execution. Orchestrator does not block them.
  - [x] 7-5. **Event-driven design:** Orchestrator subscribes to all events via routing callback. Logs events in `orchestrator_log` output. Reacts to `NODE_COMPLETED` events from teams.
  - [x] 7-6. **Implementation — event routing:** Existing `layer_path` mechanism in `ExecutionContext.emit_event` already enriches events with subgraph nesting context. Orchestrator matches `layer_path[0]` to team's sub_graph key.
  - [x] 7-7. **Implementation — send mechanism:** Chose option (b): shared context keys. Orchestrator writes to context store keyed by team name. Teams read via context edges. Eventually consistent.
  - [x] 7-8. **Implementation — concurrency model:** `OrchestratorExecutor.execute` spawns teams via `asyncio.create_task`, registers `done_callback` for status tracking, then runs its own poll loop with `asyncio.sleep(0.05)` idle backoff. Completion conditions: `all_done`, `any_done`, `orchestrator_halt`. Timeout support via `timeout_seconds`.

## Sequencing

| Order | Task | Rationale |
|-------|------|-----------|
| 1 | Task 1 (prompt) | Build-mode prompt is the core differentiator |
| 2 | Task 2 (bootstrap) | Empty graph + revision handling must work before end-to-end |
| 3 | Task 4 (API) | Wire mode into ChatManager; can test with curl/httpx |
| 4 | Task 3 (templates) | Optional; improves quality but not blocking |
| 5 | Task 5 (editor) | UX entry point |
| 6 | Task 6 (tests/docs) | Throughout |
| 7 | Task 7 (runtime async) | Depends on 7-9 (parallel subagents); 7-6→7-7→7-8 order: event routing, send mechanism, concurrency model |

## Decisions

- **Mode flag vs. separate endpoint:** Prefer `mode="build"` on existing `send_message` — same tool schema, same apply flow. Separate endpoint only if prompt/schema diverge too much.
- **Orchestrator completion condition (Task 7):** TBD during implementation — options: all subgraphs done, max_iterations, orchestrator emits halt, timeout. Default: all subgraphs done.
- **Templates first:** Start with pattern expansion + optional template registry. Pure LLM-generated graphs from scratch are higher risk; 7-8 validation hardening reduces failure modes.
- **Revision for empty graph:** Use `compute_graph_revision(empty_graph)` — hash of `{"nodes": [], "edges": []}`. Mutator's stale check: if `base_revision != current_revision`, reject. For build mode, `current_revision` is the empty-graph hash.

## Dependencies

- **10-8 (NL mutation hardening):** Pattern library, validation gate, typed schema — all reused. Build mode produces same `MutationPlan` format.
- **7-8 (workflow node API hardening):** Recommend `strict=True` for build-from-intent output to fail fast on malformed plans.
- **7-9 (async parallel subagents):** Task 7 (runtime async orchestrator) depends on 7-9 — parallel subgraphs are the "teams"; orchestrator coordinates them. 7-9 provides async parallel execution (async includes concurrency; more flexible than ForEach). **Work together:** 7-9's event stream + branch_key enables 10-9's "receive at any time"; 10-9's send mechanism + concurrency model extend 7-9 with bidirectional orchestration.

## Primary Files

**Build-time (Tasks 1–6):** `src/dan/server/chat_manager.py`, `src/dan/server/graph_mutator.py`, `src/dan/server/app.py`, `editor/src/components/ChatPanel.tsx`, `editor/src/lib/api.ts`.

**Runtime async (Task 7):** `src/dan/engine/scheduler.py`, `src/dan/engine/executor.py`, `src/dan/engine/events.py`, `src/dan/executors/control_flow.py`, new `OrchestratorNode` / `OrchestratorExecutor` (or equivalent).

## Out of Scope (deferred)

- Multi-turn intent clarification ("Which sections do you want?" → user answers → refine plan) — MVP: single-turn intent → plan → apply. Clarification can be added later.
- Custom template authoring from UI — templates are code-defined initially.

## Notes

- Builds on 10-3 (mutation primitives), 10-8 (pattern macros, validation). Meta orchestrator is the "planning" layer; execution reuses existing mutator.
- **Runtime async orchestrator (Task 7):** Mirrors real-world manager behavior — teams work simultaneously; manager receives updates from any team at any time and can direct any team at any time. Overlaps with "Async loop design" backlog; Task 7 is the concrete design for that pattern.
- Overlaps with "Coding assistant proof-of-concept" backlog — meta orchestrator could produce coding-assistant graphs from "I want a coding assistant" intent.
- Distinction from 10-3: 10-3 mutates an existing graph. 10-9 provides a dedicated build-mode UX and intent-first prompt. Same underlying ops.
