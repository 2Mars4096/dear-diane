# 14: Phase 9A — Memory & Cross-Run State

**Status:** not-started
**Goal:** Add durable memory primitives that persist across runs, enforce explicit context boundaries across nested agents, and support long-horizon recall without ballooning prompt context.

## Motivation

Phase 9A is the first Deep Systems cluster because later capabilities depend on stable memory semantics:
- `SharedContextStore`, `LocalStateManager`, and `ArtifactStore` are all in-memory and run-scoped — checkpointed per run but never loaded across runs. There is no `session_id` concept anywhere.
- Agent boundaries are conceptually documented (`architecture.md` §Context Scoping), and partial schema fields exist (`read_set`, `write_set`, `external_input_schema`, `ContextProjection`), but **none are enforced at runtime** — `ContextProjection` is defined in `models/context.py` but unused; `_run_subgraph` passes the same `SharedContextStore` instance with no isolation.
- Long-chain workflows need explicit short-term/long-term memory mechanics to preserve signal while avoiding context overload. Existing compaction (`CompactionRule` in `models/context.py`, `compact_history()` in `chat_manager.py`) is chat-only and not available to the engine.

This phase turns those into buildable contracts before behavior modifiers (9B) and new execution primitives (9C) expand complexity.

## Existing Infrastructure (baseline)

| Component | Location | What exists | Gap |
|---|---|---|---|
| `SharedContextStore` | `engine/context_runtime.py` | In-memory KV store; `snapshot()`/`restore()` for checkpoints; key validation against `graph.shared_context` | Run-scoped only; no cross-run persistence; no scope isolation for nested sub-graphs |
| `LocalStateManager` | `engine/context_runtime.py` | Node-scoped working memory; checkpointed per run | No cross-run persistence; scope is flat (no global/local distinction) |
| `ArtifactStore` | `engine/context_runtime.py` | In-memory; comment: "persistent backend can be swapped later" | No persistence layer |
| `ContextProjection` | `models/context.py` | Model defined with `include`/`exclude`/`rename`/`transform` fields; `CompositeNode.projections` field | **Never used** by any executor — entirely dead code |
| `CompactionRule` | `models/context.py` | Defines `sliding_window`, `summarize`, `diff` strategies | **Never used** — no executor activates compaction |
| `read_set` / `write_set` | `models/nodes.py` | Declared on `NodeBase`; validated for `ContextEdge` targets in `validation/graph.py` | Validation only — no runtime access control |
| `ChatStore` | `server/chat_store.py` | Durable thread persistence; chat checkpoints | Not tied to engine state; no `session_id` |
| `RunManager` / `RunRecord` | `server/run_manager.py` | In-memory run tracking; no disk persistence | Runs lost on restart; no `session_id` |
| `compact_history()` | `server/chat_manager.py` | 4-phase sliding window for chat context | Chat-only; not available to engine execution |
| Engine checkpoints | `engine/checkpoint.py` | `FileSystemCheckpointStore` persists per topological level | Run-scoped; `list_runs()` unused; no session model |

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [14-1](14-1-session-conversation-memory.md) | Session / Conversation Memory | Persist run-linked conversation + key-value memory across multiple `Engine.run()` invocations | `engine/context_runtime.py`, `engine/scheduler.py`, `engine/checkpoint.py`, `server/run_manager.py`, `server/chat_store.py`, `server/app.py` |
| [14-2](14-2-context-scoping-boundaries.md) | Context Scoping Across Agent Boundaries | Activate `ContextProjection`, enforce `global`/`local`/`pass_down`/`emit_up`, add signal routing | `models/context.py`, `models/control_flow.py`, `engine/context_runtime.py`, `executors/control_flow.py`, `validation/graph.py` |
| [14-3](14-3-long-chain-memory-system.md) | Memory System for Long Chains | Activate `CompactionRule`, add short-term/long-term memory pipeline with retrieval and consolidation | `models/context.py`, `engine/context_runtime.py`, `rag/`, `server/chat_manager.py` |

## Dependencies / Sequencing

1. **Phase 8 (13-1) first or in parallel**: 13-1 builds `RunStore` and `EventLog` for run persistence. 14-1 session memory should coordinate storage layout with `RunStore` and reuse run provenance contracts. If 13-1 is not complete, 14-1 must define its own lightweight persistence that can later integrate.
2. **14-1 second**: establishes persistent identity and storage surfaces (`session_id`, memory namespaces).
3. **14-2 third**: codifies what can cross boundaries and how signals propagate upward. Schema design can start during 14-1 storage work, but runtime enforcement should wait for stable persisted memory contracts.
4. **14-3 fourth**: builds retrieval and consolidation on top of persisted memory + boundary contracts from 14-1/14-2.

## Success Criteria

- Runs can resume with relevant session memory available without manual context replay.
- Agent boundaries enforce explicit input/output/signal schemas and prevent accidental context leakage.
- Long workflows can recall distant context through retrieval and summaries, not only raw message replay.
- Memory policy defaults are executable, tested, and documented (not prose-only).
- Existing dead-code models (`ContextProjection`, `CompactionRule`) are either activated or explicitly superseded.

## Decisions

- **Start with local filesystem persistence** and clear interfaces; delay distributed/multi-tenant storage to later phases.
- **Schema-first boundaries**: boundary contracts are typed and validated before execution.
- **Explicit memory writes**: promote append-only/auditable memory mutations over implicit side effects.
- **Compaction by default**: long-term memory favors summarized/indexed artifacts over full raw transcripts.
- **Activate before replacing**: existing models (`ContextProjection`, `CompactionRule`, `read_set`/`write_set`) should be activated and extended, not duplicated.

## Notes

- This phase is the core enabler for self-evolving orchestrator Tier 1 behavior (backlog item: persistent error memory → reflection → prompt injection). Tier 1 works with session memory (14-1) + existing RAG; Tier 2 benefits from 13-1 run history; Tier 3 needs 9B hyperedges.
- Existing architecture notes in `docs/architecture.md` are the conceptual baseline; 14-* plans convert them into implementation tasks.
- Phase 8 (13-*) run persistence and Phase 9A session memory are complementary: 13-* persists execution artifacts for observability; 14-* persists reusable state for continuity.
