# Todo

## Phase 0 — Solidify Abstractions
- [x] [1-phase-0-formal-spec](plans/1-phase-0-formal-spec.md) — formal spec as Python types + versioned graph JSON contract

## Phase 1 — Python Orchestration Library
- [ ] 2: Core engine — define and execute graphs with typed nodes, typed edges, while-loops, fan-out/fan-in, checkpointing/resumability → (not yet planned)

## Phase 2 — Visual Editor
- [ ] 3: React Flow-based visual editor — drag/drop authoring, edge wiring, config panels, run controls/status → (not yet planned)

## Phase 3 — Paper-Writing Proof of Concept
- [ ] 4: End-to-end paper-writing workflow running on engine + editor → (not yet planned)

## Phase 4 — Composite Nodes
- [ ] 5: Nest sub-graphs inside nodes, zoom-in/zoom-out → (not yet planned)

## Phase 5 — Advanced Execution Visualization
- [ ] 6: Execution debugging overlays, data-flow inspection, run timeline → (not yet planned)

## Phase 6 — Shareable Blocks / Marketplace
- [ ] 7: Publish and import reusable agent-blocks → (not yet planned)

## Backlog (unphased)
- [ ] Investigate React Flow for graph rendering
- [ ] Survey EvoAgentX for reusable multi-agent patterns
- [ ] Memory system for long chains — short-term vs. long-term memory modeled after human cognition (encoding, consolidation, retrieval). For very long workflows: how nodes recall distant context, how completed sub-graph results are compressed into retrievable memory, how relevance-based recall replaces brute-force context passing. Research: MemGPT, AgentNet's RAG-based adaptive learning, hippocampal indexing analogies.
- [ ] Dynamic model selection — `model_policy` field on operators. Budget-aware selection (read `context.token_budget`, pick model tier accordingly). Learned assignment (track model performance per task type across runs, auto-assign optimal model). Strategies 1-4 (static, fallback, router, cascade) already work via composition; this covers the edge cases that are too verbose to express with existing primitives.
- [ ] `max_concurrency` parameter on For-Each/Map — controls how many parallel instances run simultaneously (rate limits, memory, cost). Not yet in the formal spec.
