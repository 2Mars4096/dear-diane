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
  - [x] [4-1-grounded-paper-writing-upgrade](plans/4-1-grounded-paper-writing-upgrade.md) — internet-grounded literature survey, human interview loop, evidence gates, INFORMS-oriented LaTeX/PDF workflow

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
- [x] Auto-layout on load — detect degenerate positions (all nodes at 0,0), apply dagre layout automatically
- [x] Sub-graph editing — removed read-only guards, layer-aware save persists edits back to correct `sub_graphs[key]`
- [x] `.env` loading — server loads `DAN_LLM_API_KEY` via `load_dotenv()` on startup

## Phase 3.75 — Visual Editor Full Editing
> Make the visual editor a complete authoring surface. Currently nodes can be placed and wired, but ports can't be edited, nodes can't be renamed inline, and many editing primitives are missing. This phase closes the gap so users can build workflows entirely from the UI without touching JSON or Python.

- [x] [6-phase-3.75-visual-editor-editing](plans/6-phase-3.75-visual-editor-editing.md) — complete authoring surface (undo/redo, copy/paste, port editing, context menus, inline rename, validation, import/export, input node, command palette, execution UX)
  - [x] [6-1-history-multiselect](plans/6-1-history-multiselect.md) — A. Undo/redo history stack, multi-select (lasso, shift-click, bulk delete/move)
  - [x] [6-2-clipboard-context-menu](plans/6-2-clipboard-context-menu.md) — B. Copy/paste/duplicate, right-click context menu (canvas/node/edge), edge reconnection
  - [x] [6-3-node-port-editing](plans/6-3-node-port-editing.md) — C. Port editor (add/remove/rename/schema), inline node rename, output schema visual builder
  - [x] [6-4-validation](plans/6-4-validation.md) — D. Port-aware connection validation, validation API endpoint, inline error badges + toast summary
  - [x] [6-5-graph-io-input-node](plans/6-5-graph-io-input-node.md) — E. InputNode type, import/export graph JSON, Cmd+K node search, sub-graph creation from selection
  - [x] [6-6-execution-ux](plans/6-6-execution-ux.md) — F. Loop visualization as gate/feedback, streaming run output in logs/preview, human-in-the-loop popup + submit flow
  - [x] [6-7-workflow-as-node](plans/6-7-workflow-as-node.md) — G. Wrap saved workflows as reusable composite nodes from palette/canvas drop
  - [x] [6-8-patch-up](plans/6-8-patch-up.md) — H. Drill-in feedback arrows, workflow-node port cleanup, multi-entry composite run readiness
  - [x] [6-9-multi-tab-workflow-sessions](plans/6-9-multi-tab-workflow-sessions.md) — I. Multi-workflow tabs with per-tab state isolation and reconnect-on-activation run recovery
  - [x] [6-10-gate-loop-condition-redesign](plans/6-10-gate-loop-condition-redesign.md) — J. Gate-style visible loop flow, condition-routing redesign, and collapsible loop internals
  - [x] [6-11-workflow-ux-polish](plans/6-11-workflow-ux-polish.md) — K. Run summary (tokens + time), drill-in auto-layout, multi-tab duplicate graphs
  - [x] [6-12-control-flow-consolidation](plans/6-12-control-flow-consolidation.md) — L. Drop legacy if_else/while_loop; palette shows only If/Else Gate, While Gate, For Each
  - [x] [6-13-multi-dept-visualization](plans/6-13-multi-dept-visualization.md) — M. Orchestrator/department visibility, loop_groups, backend layout for vibe research

## Phase 4 — Core Hardening
> Make existing nodes robust and the platform practically usable. Fill gaps that prevent real workflows from running reliably.

- [x] [7-core-hardening](plans/7-core-hardening.md) — retry/fallback, multi-provider LLM, built-in tools, templates, observability
  - [x] [7-1-runtime-reliability](plans/7-1-runtime-reliability.md) — `RetryPolicy` model on `NodeBase`, ToolExecutor retry/backoff, fallback model, halt semantics, concurrency audit
  - [x] [7-2-multi-provider-llm](plans/7-2-multi-provider-llm.md) — provider registry (OpenAI, Anthropic, Google), per-node model dispatch, key management, cost table
  - [x] [7-3-built-in-tools](plans/7-3-built-in-tools.md) — `dan.tools` package (11 tools: file, web, shell, PDF, utility), auto-registration, ACI quality
  - [x] [7-4-templates-observability](plans/7-4-templates-observability.md) — 5 workflow templates, per-node token/cost display, LogPanel enhancements
  - [ ] [7-5-general-tool-design](plans/7-5-general-tool-design.md) — Generic run_python tool, deprecate plot_backtest/save_grid_csv; agent-generated code

## Phase 5 — Markdown Agent Format
> A third authoring surface alongside the Python builder DSL and the visual editor. One `.md` per agent (frontmatter + natural language), one workflow `.md` to wire them. All three surfaces compile to the same `dan_graph_v1` JSON and coexist — markdown is the most accessible and LLM-generatable format.

- [x] [8-markdown-agent-format](plans/8-markdown-agent-format.md) — `dan.loader`: markdown agent/workflow files → `dan_graph_v1` JSON (third authoring surface)
  - [x] [8-1-format-design-parser](plans/8-1-format-design-parser.md) — A. Agent file format, workflow file format, flow notation parser, port type inference, format versioning
  - [x] [8-2-markdown-graph-compiler](plans/8-2-markdown-graph-compiler.md) — B. Auto-wiring, `dan.loader.compile()` → `dan_graph_v1`, compiler diagnostics + source maps
  - [x] [8-3-validation-parity-advanced](plans/8-3-validation-parity-advanced.md) — C. Paper-writing rewrite, builder parity checklist, coexistence policy, composite agents, linked JSON Schema
  - [x] [8-4-round-trip-decompiler](plans/8-4-round-trip-decompiler.md) — D. Graph → markdown decompiler, visual editor export, round-trip conformance tests

## Phase 6 — Extended Capabilities
> New node types and execution modes following existing patterns. Key missing capabilities for real workflows.

- [x] [9-extended-capabilities](plans/9-extended-capabilities.md) — RAG node, subprocess sandbox, handoff validator
  - [x] [9-1-rag-knowledge-retrieval](plans/9-1-rag-knowledge-retrieval.md) — embedding pipeline (API + local), vector store (FAISS/ChromaDB/memory), `RAGOperator` node, RAGExecutor, Indexer, server CRUD endpoints, editor config panel
  - [x] [9-2-subprocess-sandbox](plans/9-2-subprocess-sandbox.md) — `SandboxRunner`, resource limits, `CodeExecutor` upgrade, shell tool hardening, editor node type
  - [x] [9-3-handoff-validator](plans/9-3-handoff-validator.md) — `ValidatorNode`, rule types (required keys, schema, expression), boundary auto-insert, `wf.validated_composite()` builder helper, editor config panel + context menu action

## Phase 7 — Author & Distribute
> Make DAN easier to author and share: conversational workflow creation, CLI for headless execution, publish workflows as callable APIs, package for distribution.

- [x] [10-chatbox-nl-workflow](plans/10-chatbox-nl-workflow.md) — conversational workflow authoring: chat panel, `@` mentions, NL→graph mutations, diff preview, session history, scoped run-from-chat
  - [x] [10-1-chat-panel-backend](plans/10-1-chat-panel-backend.md) — A. Chat panel React component, backend message endpoint, LLM integration, graph-aware system prompt, streaming
  - [x] [10-2-mention-co-navigation](plans/10-2-mention-co-navigation.md) — B. `@` autocomplete (nodes/workflows/sub-graphs), mention chips, click→canvas navigation, canvas→chat suggestion
  - [x] [10-3-nl-graph-mutation](plans/10-3-nl-graph-mutation.md) — C. Graph operation primitives, LLM function-calling schema, multi-step mutation planning, validation, error recovery
  - [x] [10-4-graph-diff-confirmation](plans/10-4-graph-diff-confirmation.md) — D. Before/after diff computation, visual diff preview, accept/reject/partial-accept, undo integration, session-scoped conversation rollback
  - [x] [10-5-history-execution](plans/10-5-history-execution.md) — E. Per-workflow chat persistence, thread list UI, graph delta tracking, session rollback metadata *(thread UI, auto-restore, session markers done; cascade delete, offline resilience, tests, docs remaining)*
  - [x] [10-6-scoped-run-from-chat](plans/10-6-scoped-run-from-chat.md) — F. Full/node/sub-graph run API, server-authoritative target resolution, chat command handling, run event streaming into thread
  - [x] [10-7-apply-mutation-flow](plans/10-7-apply-mutation-flow.md) — G. Wire GraphDiffPreview, POST apply-mutation endpoint, chat→preview→apply pipeline, session marker

## Phase 7.5 — Author & Distribute (Remaining)
> CLI, publish as API/MCP, shareable blocks, PyPI package, lightweight skills.

- [ ] 10-R: Author & distribute (remaining) → (not yet planned)
- [ ] CLI mode — run workflows in terminal/background. Supervisor-style: start, check progress, inspect logs without blocking. Complements visual editor for headless/CI/server deployments.
- [ ] Publish workflow as API/MCP — build a workflow, publish as a callable MCP server or HTTP endpoint. Turns workflows into consumable services (Coze-style).
- [ ] Shareable blocks — publish and import reusable agent-blocks. Registry/marketplace for community sharing.
- [ ] PyPI package — `pip install dan` with stable public API.
- [ ] Lightweight skills (prompt injection) — skills as prompt-prefix injections scoped by node tag/type. No full hyperedge hooks; just "prepend this text to LLM nodes tagged X."

## Phase 8 — Observe & Recover
> Execution persistence, debugging tools, and iterative refinement capabilities.

- [ ] 11: Observe & recover → (not yet planned)
- [ ] Run history / comparison — persist run artifacts (events, outputs, tokens, latency) to disk. History list in editor. Side-by-side comparison for iterative prompt tuning.
- [ ] Action audit log — persistent, queryable log of every action (tool calls, LLM outputs, decisions). Debugging + compliance + post-run analysis.
- [ ] Checkpoints as portals — tweak downstream subgraph and re-run from checkpoint without restarting. Extends existing checkpoint/resume for partial re-runs.
- [ ] Variable inspector in config panel — show available upstream data at each node. Leverage typed edge schemas.
- [ ] Node test cases / annotations — pin expected input/output pairs per node for isolated testing. Right-click → "Add test case."

## Phase 9 — Application Layer
> Higher-level coordination patterns and external integrations.

- [ ] 12: Application layer → (not yet planned)
- [ ] Agent teams — group-chat style multi-agent coordination with "@" routing, handoffs, and conversational context.
- [ ] Messaging/comm integrations — email, Slack, Discord, Telegram, WhatsApp adapters. Trigger workflows from external messages.
- [ ] User system — login, auth, per-user data isolation. Graph store, runs, checkpoints scoped to user. Multi-user/team/cloud deployments.

## Phase 10 — Deep Systems
> Architectural additions for advanced use cases. Build when real workflows demand them.

- [ ] 13: Deep systems → (not yet planned)
- [ ] Context scoping across agent boundaries — formalize four scopes (global, local, pass_down, emit_up) with explicit schemas at every agent boundary. Upward signals (sticky → global, non-sticky → one layer up). Agent boundary contract: accepts, returns, reads_global, writes_global, signals.
- [ ] Memory system for long chains — short-term vs. long-term memory (encoding, consolidation, retrieval). How nodes recall distant context, how completed sub-graph results compress into retrievable memory. Research: MemGPT, AgentNet, hippocampal indexing.
- [ ] Memory policy defaults (agreed)
  - [ ] DAN-native memory first; external context-db adapters later
  - [ ] Canonical layers: `L0=TOC/index`, `L1=abstract/overview`, `L2=detailed payload + artifact refs`
  - [ ] Lifespan: memory at all scope levels plus cross-run persistence on checkpoint/resume
  - [ ] Message semantics: source emits, target receives
  - [ ] Rule model: deterministic global rules + node-specific rules
  - [ ] Retrieval: contingent + rule-based, balanced determinism/recall
  - [ ] Retrieval budget: retrieve `20` → rerank `8` → inject `4`; cap ~`35%` of prompt budget
  - [ ] Consolidation triggers: at checkpoint + context pressure (soft `85-90%`, hard `95%`)
  - [ ] Sticky-write approval required; timeout escalates to parent
  - [ ] Parallel conflict: hybrid aggregator (generic default + per-key reducers)
  - [ ] Memory hygiene: forbid chain-of-thought persistence; store only project-useful memory
  - [ ] Global schema: `id`, `scope`, `type`, `summary`, `payload_ref`, `tags`, `confidence`, `provenance`, `created_at`, `ttl`, `approval_status`
  - [ ] TTL defaults: `profile=365d`, `preferences=180d`, `entities=365d`, `events=90d`, `cases=365d`, `patterns=730d`
  - [ ] Future tuning: treat all defaults as starting points; tune via telemetry, retrieval quality, cost/latency
- [ ] Hyperedges — engine runtime — skills/rules as hyperedges attaching to multiple nodes. Types: skill, guardrail, style, override. Attachment by node ID, type, tags, subgraph. Precedence: policy > rule > skill. Hooks: pre_prompt, tool_call, post_output, validation.
- [ ] Hyperedges — markdown syntax — reference skill/rule `.md` files in workflow with attachment scope. Depends on engine runtime.
- [ ] HumanNode generalization — promote to first-class node type. Chat UI as renderer. Adjustable autonomy via topology. Background mode = zero HumanNodes.
- [ ] Dynamic model selection — `model_policy` field. Budget-aware selection, learned assignment. Strategies beyond static/fallback/router/cascade.
- [ ] Voting / ensemble primitive — same-model voting + cross-model ensemble. `Vote` node or builder sugar `wf.vote()`.
- [ ] Session / conversation memory — lightweight persistence for conversation history + key-value state across multiple `Engine.run()` invocations.
- [ ] Loop as context manager — loops manage what context feeds back, not just control flow. Feedback selectors filter what flows from body back to condition.

## Backlog (unphased)
- [x] Investigate React Flow for graph rendering — adopted in Phase 2, `@xyflow/react` v12
- [ ] Survey EvoAgentX for reusable multi-agent patterns
- [ ] Coding assistant proof-of-concept — build Cursor-like agent mode as a DAN graph (~15 node types, ReAct while-loop + tool operators). Validate Ask/Agent/Debug/Plan modes as graph templates.
- [ ] science-cursor rebuild — extract scholar engines as DAN agents. Build PaperOrchestrator as a DAN network. VS Code extension as thin rendering client.
- [ ] Copy selection to new workflow — lasso/shift-click, paste into new tab or blank template. Extract subgraph as standalone reusable workflow.
- Vibe research example (`examples/vibe_research_md/`) — multi-dept workflow, run_multi_dept.py. Design: WORKFLOW.md.
