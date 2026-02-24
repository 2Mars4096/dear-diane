# Todo

## Phase 0 — Solidify Abstractions
- [x] [1-phase-0-formal-spec](plans/1-phase-0-formal-spec.md) — formal spec as Python types + versioned graph JSON contract

## Phase 1 — Python Orchestration Library
- [x] [2-phase-1-orchestration-engine](plans/2-phase-1-orchestration-engine.md) — async execution engine with typed nodes, while-loops, fan-out/fan-in, checkpointing/resumability

## Phase 1.5 — Workflow Builder API
- [x] [1-5-builder-api](plans/1-5-builder-api.md) — fluent Python DSL (`dan.builder`) with f-string magic, `>>` chaining, context-manager sub-graphs, compiler, decompiler, full round-trip

## Phase 2 — Visual Editor
- [x] [3-phase-2-visual-editor](plans/3-phase-2-visual-editor.md) — full-stack visual editor (FastAPI + React Flow) with live streaming execution

## Phase 3 — Paper-Writing Proof of Concept
- [ ] 4: End-to-end paper-writing workflow running on engine + editor → (not yet planned)

## Phase 4 — Composite Nodes
- [ ] 5: Nest sub-graphs inside nodes, zoom-in/zoom-out → (not yet planned)

## Phase 5 — Advanced Execution Visualization
- [ ] 6: Execution debugging overlays, data-flow inspection, run timeline → (not yet planned)

## Phase 6 — Shareable Blocks / Marketplace
- [ ] 7: Publish and import reusable agent-blocks → (not yet planned)

## Backlog (unphased)
- [x] Investigate React Flow for graph rendering — adopted in Phase 2, `@xyflow/react` v12
- [ ] Survey EvoAgentX for reusable multi-agent patterns
- [ ] Memory system for long chains — short-term vs. long-term memory modeled after human cognition (encoding, consolidation, retrieval). For very long workflows: how nodes recall distant context, how completed sub-graph results are compressed into retrievable memory, how relevance-based recall replaces brute-force context passing. Research: MemGPT, AgentNet's RAG-based adaptive learning, hippocampal indexing analogies.
- [ ] Dynamic model selection — `model_policy` field on operators. Budget-aware selection (read `context.token_budget`, pick model tier accordingly). Learned assignment (track model performance per task type across runs, auto-assign optimal model). Strategies 1-4 (static, fallback, router, cascade) already work via composition; this covers the edge cases that are too verbose to express with existing primitives.
- [ ] `max_concurrency` parameter on For-Each/Map — controls how many parallel instances run simultaneously (rate limits, memory, cost). Not yet in the formal spec.
- [ ] Hyperedges (skills/rules) — model skills and rules as hyperedges that attach to multiple nodes simultaneously. Types: skill (knowledge), guardrail (constraint), style (consistency), override (interception). Attachment by node ID, node type, tags, or subgraph. Precedence: policy > rule > skill. Execution hooks: pre_prompt, tool_call, post_output, validation.
- [ ] HumanNode generalization — promote Human-in-the-Loop from a control-flow primitive to a first-class node type. Chat UI becomes a renderer for active HumanNodes. Adjustable autonomy via graph topology (place/remove HumanNodes). Background mode = zero HumanNodes.
- [ ] Context scoping across agent boundaries — formalize four scopes (global, local, pass_down, emit_up) with explicit schemas at every agent boundary. Add upward signals (sticky → global, non-sticky → one layer up). Agent boundary contract: accepts, returns, reads_global, writes_global, signals.
- [ ] Coding assistant proof-of-concept — build Cursor-like agent mode as a DAN graph (~15 node types, ReAct while-loop + tool operators). Validate that Ask/Agent/Debug/Plan modes are expressible as four graph templates sharing a context layer.
- [ ] science-cursor rebuild — extract scholar engines (Literature, Execution, Writing) as DAN agents. Build PaperOrchestrator as a DAN network. VS Code extension becomes a thin rendering client.
