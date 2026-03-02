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
  - [x] [6-14-gate-test-suite](plans/6-14-gate-test-suite.md) — N. Workflow seam hardening first (code result contract, strict edge validation, dead-edge warnings) + expanded gate regression suite

## Phase 4 — Core Hardening
> Make existing nodes robust and the platform practically usable. Fill gaps that prevent real workflows from running reliably.

- [ ] [7-core-hardening](plans/7-core-hardening.md) — retry/fallback, multi-provider LLM, built-in tools, templates, observability, node state simplification
  - [x] [7-1-runtime-reliability](plans/7-1-runtime-reliability.md) — `RetryPolicy` model on `NodeBase`, ToolExecutor retry/backoff, fallback model, halt semantics, concurrency audit
  - [x] [7-2-multi-provider-llm](plans/7-2-multi-provider-llm.md) — provider registry (OpenAI, Anthropic, Google), per-node model dispatch, key management, cost table
  - [x] [7-3-built-in-tools](plans/7-3-built-in-tools.md) — `dan.tools` package (11 tools: file, web, shell, PDF, utility), auto-registration, ACI quality
  - [x] [7-4-templates-observability](plans/7-4-templates-observability.md) — 5 workflow templates, per-node token/cost display, LogPanel enhancements
  - [ ] [7-5-general-tool-design](plans/7-5-general-tool-design.md) — Generic run_python tool, deprecate plot_backtest/save_grid_csv; agent-generated code
  - [ ] [7-6-node-state-simplification](plans/7-6-node-state-simplification.md) — Loop-scoped state bag, code node port defaults, struct (spread) edges; eliminate state-threading boilerplate
  - [ ] [7-7-editor-navigation-layout-hardening](plans/7-7-editor-navigation-layout-hardening.md) — Nested drill-in/out/save fix (depth-3 cap), deterministic port ordering (logic+rules), edge routing optimization
  - [ ] [7-8-workflow-node-api-hardening](plans/7-8-workflow-node-api-hardening.md) — Loader/validation/builder/mutator correctness; round-trip, gate defaults (Task 3 done), strict mode, mutator warn
  - [x] [7-9-async-parallel-subagents](plans/7-9-async-parallel-subagents.md) — Run subagents concurrently; orchestration layer fans out to multiple subagents in parallel with proper fan-in semantics (Task 3 checkpoint/resume and 5-3 visualization deferred)

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
  - [x] [10-2-mention-co-navigation](plans/10-2-mention-co-navigation.md) — B. `@` autocomplete (nodes/workflows/sub-graphs), mention chips, click→canvas navigation, canvas→chat suggestion *(core UI done; canvas→chat, backend resolution, tests deferred to 12-3/12-6)*
  - [x] [10-3-nl-graph-mutation](plans/10-3-nl-graph-mutation.md) — C. Graph operation primitives, LLM function-calling schema, multi-step mutation planning, validation, error recovery
  - [x] [10-4-graph-diff-confirmation](plans/10-4-graph-diff-confirmation.md) — D. Before/after diff computation, visual diff preview, accept/reject/partial-accept, undo integration, session-scoped conversation rollback
  - [x] [10-5-history-execution](plans/10-5-history-execution.md) — E. Per-workflow chat persistence, thread list UI, graph delta tracking, session rollback metadata *(core persistence, thread UI, auto-restore, session markers done; delta tracking UI, cascade delete, offline resilience, tests, docs deferred to 12-*)*
  - [x] [10-6-scoped-run-from-chat](plans/10-6-scoped-run-from-chat.md) — F. Full/node/sub-graph run API, server-authoritative target resolution, chat command handling, run event streaming into thread *(core API, scoped builder, command parser, event mapper done; output mapping, NL intent, rate guard, tests, docs deferred to 12-*)*
  - [x] [10-7-apply-mutation-flow](plans/10-7-apply-mutation-flow.md) — G. Wire GraphDiffPreview, POST apply-mutation endpoint, chat→preview→apply pipeline, session marker
  - [x] [10-8-nl-mutation-hardening](plans/10-8-nl-mutation-hardening.md) — Validate-before-save gate, port-aware edges, entry/exit recompute, typed tool schema, prompt enrichment, auto-retry, pattern macros, stale-plan/idempotency hardening, mutation CI, acceptance metrics rollout
  - [x] [10-9-meta-orchestrator](plans/10-9-meta-orchestrator.md) — Meta orchestrator interprets user needs and self-builds a workflow from natural language intent; zero-to-workflow from scratch
  - [x] [10-10-domain-nl-authoring](plans/10-10-domain-nl-authoring.md) — RAG-ready `data_ingest` (with indexing), path-aware `data_analysis` branch, sub-graph-safe template generation, strict tool-port hardening, INFORMS template + `apply_skill`, build-mode clarification, artifact-level acceptance gates

## Phase 7.1 — Structure Review (Intermediate)
> Audit and patch only — no new features. Review file organization and object/data structure design for maintainability and correctness before scaling.

- [x] [11-structure-review](plans/11-structure-review.md) — files + objects audit; review-and-patch phase (implemented via parallel subagents)
  - [x] [11-1-object-audit](plans/11-1-object-audit.md) — Node types, graph schema, edges, execution state, API contracts, TS parity
  - [x] [11-2-file-audit](plans/11-2-file-audit.md) — Directory layout, module boundaries, imports, separation of concerns
  - [x] [11-3-cross-cutting-audit](plans/11-3-cross-cutting-audit.md) — Serialization paths, naming, validation contracts, schema evolution, legacy code
  - [x] [11-4-documentation](plans/11-4-documentation.md) — Update architecture, llm-api-guide, findings in bugs.md

## Phase 7.2 — Cursor-Parity Chat Experience
> Evolve the chatbox from a graph-editing assistant into a full-featured conversational development surface. Multi-mode interaction (Ask/Agent/Plan/Debug), rich context mentions (@Files/@Code/@Docs/@Web/@Past Chats), inline tool display with approval, conversation lifecycle (stop/queue/checkpoint/export/search), and measurable quality harness.

- [x] [12-cursor-parity-chat](plans/12-cursor-parity-chat.md) — Cursor-parity chat: modes, context, tools, lifecycle, quality *(core complete; stretch items deferred — see individual sub-plans)*
  - [x] [12-1-chat-reliability-polish](plans/12-1-chat-reliability-polish.md) — A. Fix trust gaps: revision concurrency, history compaction, dead CTAs, env vars, integration tests, plan doc reconciliation
  - [x] [12-2-multi-mode-chat](plans/12-2-multi-mode-chat.md) — B. Ask / Agent / Plan / Debug chat modes with mode-specific prompts and tool availability *(tasks 1–6 done; per-thread mode persistence, keyboard shortcuts, debug-fix tag, auto-mode detection deferred)*
  - [x] [12-3-rich-context-mentions](plans/12-3-rich-context-mentions.md) — C. @Files, @Code, @Docs, @Past Chats + server-side resolution + context budget *(tasks 1–5,7 done; @Web [task 6] and autocomplete UX polish [task 8] deferred)*
  - [x] [12-4-tool-display-execution](plans/12-4-tool-display-execution.md) — D. Inline tool call rendering, run output streaming *(tasks 1-4 done; approval gates [task 5] and sandbox display [task 6] deferred)*
  - [x] [12-5-conversation-lifecycle](plans/12-5-conversation-lifecycle.md) — E. Stop generation, checkpoints (basic), export, search, pin threads *(message queue [task 2], thread branching [task 7], restore checkpoints deferred)*
  - [x] [12-6-chat-quality-harness](plans/12-6-chat-quality-harness.md) — F. Regression tests, provider compatibility matrix, mutation metrics baseline (92 tests) *(latency benchmarks + E2E smoke tests deferred)*

## Phase 8 — Observe & Recover
> Execution persistence, debugging tools, and iterative refinement capabilities.

- [ ] 8: Observe & recover → (not yet planned)
- [ ] Run history / comparison — persist run artifacts (events, outputs, tokens, latency) to disk. History list in editor. Side-by-side comparison for iterative prompt tuning.
- [ ] Action audit log — persistent, queryable log of every action (tool calls, LLM outputs, decisions). Debugging + compliance + post-run analysis.
- [ ] Checkpoints as portals — tweak downstream subgraph and re-run from checkpoint without restarting. Extends existing checkpoint/resume for partial re-runs.
- [ ] Variable inspector in config panel — show available upstream data at each node. Leverage typed edge schemas.
- [ ] Node test cases / annotations — pin expected input/output pairs per node for isolated testing. Right-click → "Add test case."

## Phase 9 — Application Layer
> Higher-level coordination patterns and external integrations.

- [ ] 9: Application layer → (not yet planned)
- [ ] Agent teams — group-chat style multi-agent coordination with "@" routing, handoffs, and conversational context.
- [ ] Messaging/comm integrations — email, Slack, Discord, Telegram, WhatsApp adapters. Trigger workflows from external messages.
- [ ] User system — login, auth, per-user data isolation. Graph store, runs, checkpoints scoped to user. Multi-user/team/cloud deployments.

## Phase 10 — Author & Distribute
> CLI, publish as API/MCP, shareable blocks, PyPI package, lightweight skills.

- [ ] 10-R: Author & distribute (remaining) → (not yet planned)
- [ ] CLI mode — run workflows in terminal/background. Supervisor-style: start, check progress, inspect logs without blocking. Complements visual editor for headless/CI/server deployments.
- [ ] Publish workflow as API/MCP — build a workflow, publish as a callable MCP server or HTTP endpoint. Turns workflows into consumable services (Coze-style).
- [ ] Shareable blocks — publish and import reusable agent-blocks. Registry/marketplace for community sharing.
- [ ] PyPI package — `pip install dan` with stable public API.
- [ ] Lightweight skills (prompt injection) — skills as prompt-prefix injections scoped by node tag/type. No full hyperedge hooks; just "prepend this text to LLM nodes tagged X."

## Phase 11 — Deep Systems
> Architectural additions for advanced use cases. Build when real workflows demand them.

- [ ] 11: Deep systems → (not yet planned)
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
- [ ] **Manager vs worker node distinction** — manager nodes orchestrate and may spawn new nodes; worker nodes only execute and do not hire new nodes. Bottom-layer nodes are workers. Enables token/node budget caps (e.g. limit total tokens or total nodes used).
- [ ] **Optimize token usage** — reduce token consumption across workflows (prompt compression, context pruning, caching, smaller models for simple tasks, truncation policies).
- [ ] [7-8-workflow-node-api-hardening](plans/7-8-workflow-node-api-hardening.md) — Markdown round-trip lossless, ContextEdge validation, gate defaults, strict parse, mutator diagnostics; core mechanisms for convenient workflow building
- [ ] **Async loop design** — Orchestrator runs independently with access to current progress and can emit commands anytime; departments work in tandem (parallel, no cross-deps). Today: orchestrator runs once per iteration at the start, then all depts run; iterations are strictly sequential. Target: orchestrator as long-running/streaming process that pushes work to departments as they become free, or event-driven model where orchestrator and depts can overlap.
- [ ] NL mutation quality tracking bundle — now tracked under [12-6-chat-quality-harness](plans/12-6-chat-quality-harness.md) (CI variant, baseline capture, acceptance targets, end-of-phase report).
- [x] Investigate React Flow for graph rendering — adopted in Phase 2, `@xyflow/react` v12
- [ ] Thread timeline view — vertical timeline of graph evolution through conversation (stretch, from 10-5 task 5-3)
- [ ] Canvas drag-to-mention — drag node from canvas onto chat input to create mention (stretch, from 10-2 task 6-4)
- [ ] **Self-evolving orchestrator** — orchestrator persists error memories across runs (artifact store + RAG retrieval), reflects on failures to extract causal principles, and injects retrieved lessons into future decisions via prompt augmentation. Three layers: (A) persistent error memory store, (B) reflection node that distills failures into actionable principles, (C) dynamic prompt injection of relevant past mistakes before orchestrator routing decisions. Eventually: reflection generates hyperedge rules that constrain future behavior. Depends on memory system (Phase 11) for full cross-run persistence; prompt-augmentation variant works with existing RAG infra.
- [ ] Survey EvoAgentX for reusable multi-agent patterns
- [ ] Coding assistant proof-of-concept — build Cursor-like agent mode as a DAN graph (~15 node types, ReAct while-loop + tool operators). Validate Ask/Agent/Debug/Plan modes as graph templates.
- [ ] science-cursor rebuild — extract scholar engines as DAN agents. Build PaperOrchestrator as a DAN network. VS Code extension as thin rendering client.
- [ ] Copy selection to new workflow — lasso/shift-click, paste into new tab or blank template. Extract subgraph as standalone reusable workflow.
- [x] Vibe research example (`examples/vibe_research_md/`) — multi-dept workflow, run_multi_dept.py. Design: WORKFLOW.md.
  - [x] Debug run-input propagation in loop path (InputNode + while-gate continue scheduling)
  - [x] Fix custom strategy `KeyError: ['ret']` by normalizing CRSP loader output contract
  - [x] Remove obsolete legacy strategy-creation files from example folder
  - [x] Fix checkpoint circular-reference crash in custom strategy tool output
  - [x] Expand code sandbox builtins (`iter`/`next`) for generated strategy scripts
  - [x] v2 rewrite: fixed 6 departments, state_schema loop state, orchestrator dynamic themes/halt
  - [x] Normalize state_schema key convention (flat key→schema map)
  - [x] Generate per-department plots inside the department pipeline (`bug_fixer`) immediately after backtest output is available
  - [x] Harden FUND custom strategy execution (`run_strategy_script`): safe `merge_asof`, Compustat alias normalization, and factor parquet persistence for custom strategies
  - [x] Serialize department execution one-by-one (MOM→REV→FUND) and update tracking results immediately after each backtest
  - [x] Make `max_factors` cap attempts (not only successful results), align builtin `strategy_name` with department IDs, and persist immediate result JSON snapshots in `output/results/`
  - [x] Align department composite output contract: `save_tracking.result` now passes through backtest/plot fields required by downstream `merge`/governor
  - [x] Handle generated `merge_asof(tolerance=pd.DateOffset(...))` incompatibility in custom strategy runtime to avoid FUND build failures
  - [x] Isolate `skip` branch from `bug_fixer` gate wiring so normal department runs still execute immediate plotting
  - [x] Enforce CRSP formation-month minimum price filter (`|price| >= 1`) across builtin and custom backtests
  - [x] Persist generated custom strategy scripts before execution (`output/scripts/{strategy}.py`) and pass script paths through department outputs
  - [x] Capture generated `build_factor` stdout/stderr and fail fast on empty factor outputs to avoid silent “factor-only/no-plot” runs
