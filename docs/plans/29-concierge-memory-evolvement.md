# 29: Concierge-First Architecture — Memory Kernel, Workflow Reuse & Self-Evolvement

**Status:** in-progress
**Goal:** Make the concierge the persistent intelligent agent that owns memory, drives iterative workflow building, and learns from every interaction. Workflows become compiled execution artifacts; the concierge is the brain.

## Problem

DAN has two parallel systems that partially overlap:

1. **Chat/Concierge layer** — classifies intent, routes to handlers, manages conversation. Stateless per-message. Memory fragmented across 6 separate stores with different APIs.
2. **Workflow/Engine layer** — builds and executes DAG workflows. Rich execution model (16 node types, typed edges, composites) but no learning, no memory, no reuse from the primary chat path.

These systems are peers when they should be hierarchical:

- **Workflow reuse is not wired to the primary build path.** ChatManager's intent compiler generates from scratch every time. Only the MetaController path (rarely hit) consults ExperienceIndex.
- **Memory is siloed.** ConversationMemory (keyword search, 200-entry cap), UserProfile (direct lookup), ExperienceIndex (semantic, workflow-only), ErrorMemoryIndex (semantic, per-workflow), PrincipleStore (by tags/confidence), MemoryStore (raw key-value). No unified retrieval. Different callers see different subsets.
- **No iterative build loop in chat.** Build mode is single-shot: extract intent → compile → done. No "here's what I built, does this look right?" dialogue. No multi-round test→diagnose→modify cycle.
- **Self-evolvement is one-directional.** Error principles feed the repair system but don't improve future generation. Successful patterns aren't extracted as reusable templates. Preferences are only captured in CLI, not server.
- **Concierge is stateless.** It classifies and forwards. It has no persistent goal, no plan spanning messages, no autonomous ability to trigger builds/runs without user input.

## Architecture

```
User (CLI / WhatsApp / Telegram / Editor)
           │
           ▼
  ┌──────────────────────────────────────────────┐
  │            Concierge (the brain)              │
  │                                               │
  │  ┌─────────────┐  ┌───────────────────────┐  │
  │  │ Memory       │  │ Orchestration         │  │
  │  │ Kernel       │  │                       │  │
  │  │ • retrieve() │  │ • active goals/plans  │  │
  │  │ • store()    │  │ • build sessions      │  │
  │  │ • consolidate│  │ • iterative loop      │  │
  │  │              │  │ • parallel dispatch    │  │
  │  └──────┬───────┘  └───────┬───────────────┘  │
  │         │                  │                   │
  │         ▼                  ▼                   │
  │  ┌─────────────────────────────────────────┐  │
  │  │         Decision + Execution             │  │
  │  │  • memory-informed context assembly      │  │
  │  │  • reuse-first workflow selection        │  │
  │  │  • build / adapt / run / just answer     │  │
  │  │  • post-execution learning extraction    │  │
  │  └─────────────────────────────────────────┘  │
  └──────────────────────┬───────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  Workflow Engine     │
              │  (execution layer)  │
              │  • build graph      │
              │  • run graph        │
              │  • return results   │
              └─────────────────────┘
```

## Design Principles

1. **The concierge is the persistent agent; workflows are ephemeral artifacts.** Intelligence, learning, and memory live in the concierge. Workflows are compiled plans it produces and manages.

2. **Memory is typed, not ranked.** Don't build one big heap scored by a blended formula. Instead: classify memory by type (fact, preference, pattern, failure, principle, workflow asset, episode), route retrieval by task need, rank within type using type-appropriate scoring.

3. **Load by policy, not by formula.** Different tasks need different memory classes. A workflow-build turn needs patterns + preferences + failure principles. A factual question needs facts + episodes. Prompt budget is allocated per class, not globally.

4. **Save candidates, not raw history.** Every interaction produces memory candidates. A lightweight classifier decides: discard, store as episode, or promote to fact/preference/pattern/principle. Raw chat turns are not the main memory unit.

5. **Iterate in conversation.** Workflow building is a dialogue: build → show → refine → test → diagnose → modify → re-test. The concierge drives this loop; the user observes and intervenes.

6. **Parallelize the independent, serialize the dependent.** Independent sub-tasks within a single turn (memory retrieval + workflow search) fan out concurrently. Same-goal follow-ups serialize for coherence. No artificial caps on independent work.

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [29-1](29-1-unified-memory-kernel.md) | Unified Memory Kernel | Typed memory store with write/organization/load layers. Replace 6 siloed systems with one concierge-owned kernel. Section-based retrieval with per-type ranking. | ~5 days | None (foundational) |
| [29-2](29-2-concierge-as-orchestrator.md) | Concierge as Orchestrator | Fold MetaController session loop into the concierge. Stateful goals/plans spanning multiple messages. Autonomous build→run→diagnose without waiting for user on every step. | ~4 days | 29-1 (memory informs decisions) |
| [29-3](29-3-iterative-workflow-building.md) | Iterative Workflow Building | Multi-round build/test/diagnose/modify loop in chat. Working-memory for build sessions. User can observe, intervene, or let the concierge iterate autonomously. | ~4 days | 29-2 (concierge drives the loop) |
| [29-4](29-4-experience-driven-reuse.md) | Experience-Driven Reuse | Wire experience retrieval into the primary build path. Reuse-first: check memory before generating. Workflow catalog browsable from chat. Adapter parity. | ~3 days | 29-1, 29-2 |
| [29-5](29-5-concierge-parallelism.md) | Concierge Parallelism | Universal fan-out principle: parallelize all independent sub-tasks (turn prep, tool calls, info gathering, diagnosis, memory extraction, build steps). Resource-based concurrency. Priority queuing. | ~4 days | 29-2 |
| [29-6](29-6-self-evolvement-loop.md) | Self-Evolvement Loop | Passive learning (memory extraction, pattern filing, cross-section reinforcement) AND active adaptation (prompt optimization, per-node model learning, skill evolution, topology suggestions). Both halves needed for genuine self-improvement. | ~7 days | 29-1, 29-3, 29-4 |
| [29-7](29-7-essential-tools.md) | Essential Tools Expansion | Fill critical tool gaps: current_datetime, clipboard, python_eval, send_email, notify, file ops (move/copy/delete), csv_read, spreadsheet_read, git tools (status/diff/log/commit/branch), image_describe, audio_transcribe, compress, translate, diff. Complements 26-7 (inbound media). | ~4 days | None (independent) |
| [29-8](29-8-practical-research-quality-and-audit.md) | Practical Research Quality & Audit | Make the chat system reliable on daily research/report tasks (equity research, deep research, PDF literature summary, literature search + gated-paper handoff) and persist end-to-end chat provenance for debugging and evaluation. | ~4 days | 29-2, 29-4, 28-6 |
| [29-9](29-9-mcp-tool-bridge.md) | MCP Tool Bridge | Consume external MCP servers (Stata, R, databases, custom APIs) as first-class chat tools. Chat-time `/mcp install` command. Auto-connect on startup. Config format matches Cursor/Claude Desktop. | ~4 days | None (independent) |

## Dependencies / Sequencing

```
29-1 (Memory Kernel) ← foundational, start here
  ├→ 29-2 (Concierge Orchestrator) ← needs memory for stateful planning
  │    ├→ 29-3 (Iterative Building) ← needs orchestrator loop
  │    └→ 29-5 (Parallelism) ← needs orchestrator dispatch model
  ├→ 29-4 (Experience Reuse) ← needs memory + orchestrator
  └→ 29-6 (Self-Evolvement) ← needs memory + building + reuse in place

29-7 (Essential Tools) ← independent, can start anytime
29-8 (Practical Research Quality & Audit) ← depends on 29-2 chat orchestration + 29-4 reuse behavior, validates daily-use quality
29-9 (MCP Tool Bridge) ← independent, can start anytime (plugs into capability registry)
```

Critical path: 29-1 → 29-2 → 29-3 → 29-6. Parallelizable: 29-4, 29-5, 29-7, and 29-9 can proceed alongside 29-3. 29-8 starts once the stateful concierge and reuse-first path are good enough to evaluate real daily tasks end-to-end.

## What This Phase Does NOT Do

- Does not remove the OrchestratorNode from the engine. It stays as an intra-run optimization for tight sub-second coordination loops.
- Does not replace the workflow engine. The DAG execution model (16 node types, typed edges, composites) is unchanged. This phase changes who drives it and how.
- Does not add new node types or edge types. Pure concierge/memory/reuse architecture (29-7 adds tools, not nodes).
- Does not require frontend changes. All work is backend (Python). Editor ChatPanel benefits automatically through the same chat API.

## Acceptance

**Memory:**
- Single `memory.retrieve(query, task_type)` call returns ranked mix of facts, preferences, patterns, failures, principles — not 6 separate API calls
- Memory items have type, scope, lifecycle, provenance
- Retrieval policy varies by task type (build vs. answer vs. repair)

**Orchestration:**
- "Build me a data analysis pipeline" → concierge checks memory for similar workflows → reuses/adapts if found → builds from scratch only if nothing matches
- Multi-message goal: "analyze my CSV data" → (concierge builds workflow) → "actually, the delimiter is tab" → (concierge modifies and re-runs) → "great, save this" → (stored as reusable asset)

**Practical daily tasks:**
- "Write me a research report on Rocket Lab (RKLB). Include recent news, financials, sentiment, and a view." → current date is anchored, multiple web queries run, article/report pages are fetched, the final report is structured, and every numeric claim is source-backed
- "Do a deep research report on [topic]." → multiple research angles are searched, promising sources are fetched, synthesis is broad rather than one-shot, and the final answer includes a usable source list
- "Summarize this paper at /path/file.pdf." → `pdf_read` is used before summarization, and the answer is grounded in the actual document content
- "Do a literature review on [topic]." → web search + fetch + local PDF reading are chained together; inaccessible papers trigger an explicit user handoff instead of fabrication

**Auditability:**
- Every research-oriented chat turn can be reconstructed from durable audit records: user request, assembled prompt/messages, tool calls/results, cited URLs/files, final answer, and linked run/memory IDs
- Audit records preserve enough raw detail for debugging, with secret redaction rather than best-effort summaries only

**Self-evolvement:**
- After 10 workflow builds, common patterns are automatically extracted as reusable templates
- User preference corrections ("I prefer Claude for writing") persist and influence all future builds
- Workflow failures produce principles that improve the next generation attempt

**Responsiveness:**
- If a user query takes more than 5 seconds before the first response, the concierge emits a short reassuring progress note explaining what it is currently doing — not generic filler
- Very long turns (deep research, multi-fetch) emit periodic progress updates (~15 s intervals)

**Parallelism:**
- Independent sub-tasks (memory search + workflow lookup) execute concurrently within a single turn
- A new independent project starts immediately, never waits for unrelated work

## Files (primary)

| File | Action |
|---|---|
| `src/dan/engine/memory_kernel.py` | New — unified typed memory store |
| `src/dan/server/concierge/runtime.py` | Major — stateful orchestration loop, memory integration |
| `src/dan/server/concierge/build_session.py` | New — iterative build state machine |
| `src/dan/server/concierge/dispatcher.py` | Major — resource-based parallelism, priority queuing |
| `src/dan/server/concierge/memory_bridge.py` | Major — rewire to unified memory kernel |
| `src/dan/server/chat_manager.py` | Moderate — memory-informed context assembly, reuse-first build |
| `src/dan/server/chat_factory.py` | Moderate — wire memory kernel into chat services |
| `src/dan/engine/experience.py` | Moderate — adapt to feed into unified memory |
| `src/dan/engine/error_memory.py` | Moderate — adapt to feed into unified memory |
| `src/dan/engine/user_profile.py` | Moderate — adapt to feed into unified memory |
| `src/dan/engine/conversation_memory.py` | Deprecate — replaced by memory kernel |

## Decisions

- (to be filled during execution)

## Notes

- Follow-up patch (2026-03-09): `ChatManager._build_messages()` now injects memory-kernel context from `user_message` in both the LLM-first and legacy prompt paths, fixing the prompt-assembly crash uncovered by the broader suite. Related tests were refreshed for the current architecture: unified LLM-first prompt assertions, mode-agnostic publish/share capability availability, `create_session_for_goal()` in the legacy meta-goal handler test, and CLI local-mode integration coverage that does not depend on a running dev server.
- Review patch (2026-03-09): the Phase 29 goal-orchestrator path now only activates when both `memory_kernel` and `meta_controller` are wired, build sessions persist across turns instead of being recreated, workflow-asset memories store goal descriptions plus reuse counters, and `pdf_read` vision mode is available through the chat capability layer with explicit truncation metadata.
- Practical daily-task quality is now an explicit Phase 29 acceptance target. Plan [29-8](29-8-practical-research-quality-and-audit.md) turns the four real-world research/report flows from [28-6](28-6-real-world-test-scenarios.md) into hard acceptance criteria and adds full chat-level provenance/audit.
- Phase 18 (LLM-First Chat) simplifies the routing stack. Phase 19 assumes that cleanup is done and builds on the simplified path. If Phase 18 is incomplete, Phase 19 works anyway — the memory kernel and orchestration loop sit above whatever routing exists.
- The existing `ExperienceStore`, `ErrorMemoryIndex`, `PrincipleStore`, and `UserProfile` are good building blocks. The unified memory kernel wraps/adapts them rather than rewriting from scratch.
- The `ConcurrentDispatcher` is a good foundation for 29-5. The changes are about removing artificial caps and adding priority, not a rewrite.
- Fast-command path (2026-03-09): commands (`/save`, `/build-*`, `/memory-*`, `/mcp`, preference confirmations) now skip the parallel prep phase (memory retrieval + context resolution + speculative reuse search) and respond instantly without any LLM involvement. Dispatcher bypass list also extended.
- 2026-03-09 final reconciliation: All 8 sub-plans reviewed against code. 29-1 → completed (all kernel, adapters, policies, consolidation shipped). 29-2 → completed (goal orchestration, MetaController bridge, autonomy levels). 29-3 → completed (build session state machine, diagnosis, structural review). 29-4 → completed (reuse/adapt/generate, catalog tools, adapter parity, feedback loop). 29-5 → completed (fan-out utility, resource tracking, priority queuing, unified queue). 29-6 → in-progress (§1-5 LLM extraction and §2-6 reflection principle extraction now shipped; prompt optimization §7, skill evolution §9, and integration tests §12 remain). 29-7 → completed (32 tools shipped). 29-8 → completed (audit model, research hardening, scenario regression).
