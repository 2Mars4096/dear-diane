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
- [x] [4-phase-3-paper-writing](plans/4-phase-3-paper-writing.md) — end-to-end paper-writing workflow (builder DSL, ForEach, WhileLoop, Tool, Code, mock + live tests, editor integration)

## Phase 3.5 — Frontend Design
- [x] [5-phase-3.5-frontend-design](plans/5-phase-3.5-frontend-design.md) — production-quality workflow builder (LangFlow/Flowise/Coze-inspired)
  - [x] [5-1-multi-layered-graph](plans/5-1-multi-layered-graph.md) — A. CompositeExecutor, `is_blackbox`, canvas drill-in, breadcrumb, animated transitions, port mappings
  - [x] [5-2-live-execution-viz](plans/5-2-live-execution-viz.md) — B. Pulse/glow animations, edge particles, execution path highlighting, timeline/playback, duration badges
  - [x] [5-3-rich-logging](plans/5-3-rich-logging.md) — C. New backend event types, collapsible per-node log sections, icons, click-to-select, filtering
  - [x] [5-4-build-palette](plans/5-4-build-palette.md) — D. Searchable categorized sidebar, agent templates, MCP placeholder, edge type selector, hover previews
  - [x] [5-5-ui-polish](plans/5-5-ui-polish.md) — E. Error handling, loading states, connection validation, toolbar merge, keyboard shortcuts, auto-layout

## MVP — End-to-End Runnable from UI
- [x] Server-side tool registry — `RunManager` accepts `ToolRegistry`, `save_paper` tool registered in `app.py` lifespan
- [x] Run-inputs dialog — `RunInputsDialog` detects `{variable}` placeholders from entry node prompts, shows modal before execution

## Phase 4 — Memory & Context Scoping
> Foundational engine layer for how nodes share, scope, and recall context. Prerequisite for long-running multi-agent workflows where brute-force context passing breaks down. Two pillars: (1) formalized local/global/hierarchical scoping at agent boundaries, and (2) a memory system for compressing and retrieving distant context.

- [ ] 8: Memory & context scoping → (not yet planned)
- [ ] Context scoping across agent boundaries — formalize four scopes (global, local, pass_down, emit_up) with explicit schemas at every agent boundary. Add upward signals (sticky → global, non-sticky → one layer up). Agent boundary contract: accepts, returns, reads_global, writes_global, signals.
- [ ] Memory system for long chains — short-term vs. long-term memory modeled after human cognition (encoding, consolidation, retrieval). For very long workflows: how nodes recall distant context, how completed sub-graph results are compressed into retrievable memory, how relevance-based recall replaces brute-force context passing. Research: MemGPT, AgentNet's RAG-based adaptive learning, hippocampal indexing analogies.

## Phase 5 — Markdown Agent Format
> A third authoring surface alongside the Python builder DSL and the visual editor. One `.md` per agent (frontmatter + natural language), one workflow `.md` to wire them. All three surfaces compile to the same `dan_graph_v1` JSON and coexist — markdown is the most accessible, Python the most programmable, visual the most interactive.

- [ ] 6: Markdown agent format → (not yet planned)
- [ ] Agent file format — YAML frontmatter (`type`, `model`) + `> Accepts` / `> Returns` blockquote for ports + markdown body as prompt/behavior
- [ ] Workflow file format — agent list (markdown links to agent files) + `## Flow` section with arrow notation
- [ ] Flow notation parser — `→` for chaining, `.port` for specific outputs, `| each(agent, parallel: N)`, `| loop(agent, until: cond, max: N)`, `| if(cond, then: A, else: B)`
- [ ] Port type inference — infer types from names and suffixes (`sections[]` → array, plain name → string/text), optional explicit annotation (`> Returns: sections[] (string)`)
- [ ] Auto-wiring — match port names across agents for implicit edge creation; explicit `.port → .port` override when ambiguous
- [ ] Markdown → Graph compiler (`dan.loader`) — parse markdown files, resolve file references, infer wiring, produce `dan_graph_v1` JSON
- [ ] Rewrite `examples/paper_writing.py` as a set of markdown agent files + workflow file (validation)

## Phase 6 — Shareable Blocks / Marketplace
- [ ] 7: Publish and import reusable agent-blocks → (not yet planned)

## Backlog (unphased)
- [x] Investigate React Flow for graph rendering — adopted in Phase 2, `@xyflow/react` v12
- [ ] Built-in retry/backoff for ToolOperator — mirror LLMExecutor's transient-error retry (rate limits, timeouts, network). Currently ToolExecutor catches exceptions and immediately returns FAILED.
- [ ] Per-node retry policy / fallback model — `retry_policy` field (max_retries, backoff, fallback_model, on_failure) exists in architecture docs but is not implemented in runtime. Add to EngineConfig/executor dispatch.
- [ ] Standardized handoff validator between major stages — lightweight validation node at agent boundaries that checks required output keys, schema conformance, and non-empty values before passing data downstream. Prevents silent data loss propagation.
- [ ] Survey EvoAgentX for reusable multi-agent patterns
- [ ] Dynamic model selection — `model_policy` field on operators. Budget-aware selection (read `context.token_budget`, pick model tier accordingly). Learned assignment (track model performance per task type across runs, auto-assign optimal model). Strategies 1-4 (static, fallback, router, cascade) already work via composition; this covers the edge cases that are too verbose to express with existing primitives.
- [ ] `max_concurrency` parameter on For-Each/Map — controls how many parallel instances run simultaneously (rate limits, memory, cost). Not yet in the formal spec.
- [ ] Hyperedges — engine runtime (skills/rules) — model skills and rules as hyperedges that attach to multiple nodes simultaneously. Types: skill (knowledge), guardrail (constraint), style (consistency), override (interception). Attachment by node ID, node type, tags, or subgraph. Precedence: policy > rule > skill. Execution hooks: pre_prompt, tool_call, post_output, validation.
- [ ] HumanNode generalization — promote Human-in-the-Loop from a control-flow primitive to a first-class node type. Chat UI becomes a renderer for active HumanNodes. Adjustable autonomy via graph topology (place/remove HumanNodes). Background mode = zero HumanNodes.
- [ ] Coding assistant proof-of-concept — build Cursor-like agent mode as a DAN graph (~15 node types, ReAct while-loop + tool operators). Validate that Ask/Agent/Debug/Plan modes are expressible as four graph templates sharing a context layer.
- [ ] science-cursor rebuild — extract scholar engines (Literature, Execution, Writing) as DAN agents. Build PaperOrchestrator as a DAN network. VS Code extension becomes a thin rendering client.
- [ ] Composite agents in markdown — `type: composite` agent files with an internal `## Flow` section, enabling nested zoom-in behavior from a single `.md` file.
- [ ] Hyperedges — markdown authoring syntax — reference skill/rule `.md` files in workflow with attachment scope (`→ all llm agents`, `→ section_writer, reviewer`). Same frontmatter-plus-prose format. Depends on hyperedge engine runtime above.
- [ ] Markdown round-trip from visual editor — export a graph edited in the visual editor back to markdown agent files + workflow file (inverse of `dan.loader`).
- [ ] Linked JSON Schema files — for rare complex structured outputs, allow `> Returns: (schema: schemas/outline.json)` to reference an external schema file instead of inline inference.
- [ ] Markdown/Python coexistence policy — define whether `dan.builder` and `dan.loader` are peers (both compile to `dan_graph_v1`) or whether one is canonical IR. Establish deprecation criteria if one surface subsumes the other.
- [ ] `dan.loader` ↔ `dan.builder` parity checklist — ensure markdown loader covers all node types, edge types, and control-flow features supported by the Python builder. Track gaps explicitly.
- [ ] Compiler diagnostics + source maps — `dan.loader` parser errors should reference `file.md:line` for actionable feedback. Source map from graph nodes back to markdown origin for debugging.
- [ ] Markdown round-trip conformance tests — `markdown → graph → markdown` produces stable output (idempotent diff). Similar to builder/decompiler round-trip golden tests.
- [ ] Markdown format versioning — `format_version` field in workflow frontmatter for forward compatibility when the notation evolves.
- [ ] Voting / ensemble primitive — same-model voting (run node N times with different temperatures, aggregate by majority/threshold) and cross-model ensemble (Claude + GPT + Gemini on same task, best-of or consensus). Either a dedicated `Vote` node or builder sugar like `wf.vote("review", n=3, threshold=2)`. Useful for guardrails, evaluation, and high-stakes decisions. Ref: Anthropic "Building Effective Agents" parallelization pattern.
- [ ] Tool definition quality (ACI) — invest in tool descriptions for ToolOperator: include example usage, edge cases, input format requirements. Enforce absolute paths over relative in file-manipulating tools (models fail after `cd`). "Poka-yoke" tool arguments to prevent misuse. Treat agent-computer interface design with the same rigor as human-computer interface design. Ref: Anthropic ACI appendix.
- [ ] Latency / cost observability — surface per-node token counts, wall-clock latency, and estimated cost in the execution viz and LogPanel. Helps users make informed topology and model-selection decisions. Connects to dynamic model selection backlog item.
