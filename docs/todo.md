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

- [x] [7-core-hardening](plans/7-core-hardening.md) — retry/fallback, multi-provider LLM, built-in tools, templates, observability, node state simplification
  - [x] [7-1-runtime-reliability](plans/7-1-runtime-reliability.md) — `RetryPolicy` model on `NodeBase`, ToolExecutor retry/backoff, fallback model, halt semantics, concurrency audit
  - [x] [7-2-multi-provider-llm](plans/7-2-multi-provider-llm.md) — provider registry (OpenAI, Anthropic, Google), per-node model dispatch, key management, cost table
  - [x] [7-3-built-in-tools](plans/7-3-built-in-tools.md) — `dan.tools` package (11 tools: file, web, shell, PDF, utility), auto-registration, ACI quality
  - [x] [7-4-templates-observability](plans/7-4-templates-observability.md) — 5 workflow templates, per-node token/cost display, LogPanel enhancements
  - [x] [7-5-general-tool-design](plans/7-5-general-tool-design.md) — Generic run_python tool, deprecate plot_backtest/save_grid_csv; agent-generated code
  - [x] [7-6-node-state-simplification](plans/7-6-node-state-simplification.md) — Loop-scoped state bag, code node port defaults, struct (spread) edges; eliminate state-threading boilerplate
  - [x] [7-7-editor-navigation-layout-hardening](plans/7-7-editor-navigation-layout-hardening.md) — Nested drill-in/out/save fix (depth-3 cap), deterministic port ordering (logic+rules), edge routing optimization
  - [x] [7-8-workflow-node-api-hardening](plans/7-8-workflow-node-api-hardening.md) — Loader/validation/builder/mutator correctness; round-trip, gate defaults, strict mode, mutator diagnostics
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

- [x] [13-observe-recover](plans/13-observe-recover.md) — execution persistence, debugging tools, and iterative refinement capabilities
  - [x] [13-1-run-observability-history](plans/13-1-run-observability-history.md) — run artifacts, history/comparison, and action audit log foundation
  - [x] [13-2-recovery-debug-workbench](plans/13-2-recovery-debug-workbench.md) — checkpoint portals, variable inspector, node test cases UX, backend tests + docs *(multi-tab consistency [5-5] + frontend tests [6-2] deferred)*

## Phase 9 — Deep Systems
> Architectural additions for advanced use cases. Grouped into four clusters; each cluster gets a plan file just-in-time when work begins.

### 9A — Memory & Cross-Run State
- [x] [14-memory-cross-run-state](plans/14-memory-cross-run-state.md) — memory and cross-run state foundation (session persistence, boundary scoping, long-chain memory)
  - [x] [14-1-session-conversation-memory](plans/14-1-session-conversation-memory.md) — lightweight persistence for conversation history + key-value state across multiple `Engine.run()` invocations (self-evolving orchestrator Tier 1 enabler)
  - [x] [14-2-context-scoping-boundaries](plans/14-2-context-scoping-boundaries.md) — formalize `global`/`local`/`pass_down`/`emit_up` with explicit agent-boundary schemas and sticky/non-sticky upward signals
  - [x] [14-3-long-chain-memory-system](plans/14-3-long-chain-memory-system.md) — short-term vs long-term memory pipeline (encoding, consolidation, retrieval) and executable memory policy defaults

### 9B — Behavior Modifiers
- [x] [15-behavior-modifiers](plans/15-behavior-modifiers.md) — hyperedges (skills, guardrails, style rules, overrides) as first-class graph-level constructs + dynamic model selection
  - [x] [15-1-hyperedge-engine-runtime](plans/15-1-hyperedge-engine-runtime.md) — hyperedge models, graph schema, execution hooks (pre_prompt, tool_call, post_output, validation), attachment resolution, precedence, migration from SKILL_LIBRARY
  - [x] [15-2-hyperedge-markdown-syntax](plans/15-2-hyperedge-markdown-syntax.md) — skill/rule `.md` file format, workflow reference syntax, loader/compiler/decompiler, builder API, editor integration
  - [x] [15-3-dynamic-model-selection](plans/15-3-dynamic-model-selection.md) — `model_policy` field, strategies (static/budget/cascade/capability/router), cost tracking, budget enforcement

### 9C — Execution Primitives
- [x] [16-execution-primitives](plans/16-execution-primitives.md) — agent teams, voting/ensemble, HumanNode generalization, loop context managers, async loop design
  - [x] [16-1-agent-teams](plans/16-1-agent-teams.md) — group-chat style multi-agent coordination with `@` routing, handoffs, turn strategies, shared conversational context
  - [x] [16-2-voting-ensemble](plans/16-2-voting-ensemble.md) — `VoteNode` with majority/weighted/judge/unanimous strategies, same-model voting + cross-model ensemble, `wf.vote()` builder sugar
  - [x] [16-3-human-node-generalization](plans/16-3-human-node-generalization.md) — typed I/O schemas, render modes (approval/form/selection/text), rendering surface protocol, chat-as-renderer, adjustable autonomy via topology
  - [x] [16-4-loop-context-manager](plans/16-4-loop-context-manager.md) — feedback selectors (new `FeedbackSelector` model), `artifact_ports`, selective feedback filtering, loop context management
  - [x] [16-5-async-loop-design](plans/16-5-async-loop-design.md) — orchestrator runs independently as an event-driven LLM process, dispatching commands to teams dynamically

### 9D — Self-Evolving Orchestrator
> Orchestrator persists error memories across runs, reflects on failures to extract causal principles, and injects retrieved lessons into future decisions via prompt augmentation. Three tiers of increasing capability; Tier 1 is immediately feasible.

- [x] [17-self-evolving-orchestrator](plans/17-self-evolving-orchestrator.md) — error memory, reflection, and self-generating rules for orchestrator self-improvement
  - [x] [17-1-error-memory-rag](plans/17-1-error-memory-rag.md) — Tier 1: error capture pipeline, RAG indexing of run errors, retrieval at LLM decision points, prompt injection. *(Core complete: ErrorRecord, ErrorMemoryIndex, ErrorContextProvider, LLMExecutor injection, REST API. Integration tests deferred.)*
  - [x] [17-2-reflection-node](plans/17-2-reflection-node.md) — Tier 2: `ReflectionNode` post-processes failed runs, distills errors into structured causal principles, persists to `PrincipleStore`. *(Core complete: model, executor, principle store, scheduling, graduated repair classification. Authoring surfaces and integration tests deferred.)*
  - [x] [17-3-self-generating-rules](plans/17-3-self-generating-rules.md) — Tier 3: principle → hyperedge guidance + runtime parameter mutations, `RuleLifecycleManager` lifecycle/effectiveness tracking, safety bounds.
  - [x] [17-4-observability-event-wiring](plans/17-4-observability-event-wiring.md) — Wire all 11 self-evolving `EventType` values into emission sites. Pure instrumentation — debugging and audit trail.
  - [x] ~~[17-5-workflow-experience-summaries](plans/17-5-workflow-experience-summaries.md)~~ → deferred and promoted to [19-1-workflow-experience-memory](plans/19-1-workflow-experience-memory.md) under Phase 11 (meta-orchestrator).

## Phase 10 — Token Optimization
> Minimize token consumption and maximize cost-efficiency. Model selection (15-3) picks the right model; this phase reduces tokens sent regardless of model — smart context assembly, caching, agent-directed context architecture, and analytics.

- [ ] [18-token-optimization](plans/18-token-optimization.md) — smart context assembly, caching, agent-directed context architecture, token analytics, task-level model tiering
  - [x] [18-1-prompt-compression](plans/18-1-prompt-compression.md) — advisory token budgets, context deferral, JIT schema loading, context tools, summarization, reference passing
  - [x] [18-2-caching-layer](plans/18-2-caching-layer.md) — provider prompt caching, node memoization, semantic cache, cache APIs
  - [x] [18-3-context-window-management](plans/18-3-context-window-management.md) — externalized state, history policy, safe loop compaction, advisory token budgets
  - [x] [18-4-token-analytics](plans/18-4-token-analytics.md) — per-node breakdown, waste detection, optimization recommendations, evolving playbooks, editor visualization *(token flow edges and before/after estimation deferred)*
  - [ ] [18-5-task-level-model-tiering](plans/18-5-task-level-model-tiering.md) — 3-dimension scoring (difficulty/impact/recoverability), 4 model tiers, TierPolicy, adaptive escalation/de-escalation

## Phase 11 — Meta-Orchestrator
> Autonomous planning, execution, and self-repair. Given a high-level goal ("write a paper on X in Y format"), the meta-orchestrator discovers relevant past workflows, plans/adapts/generates a workflow graph, executes it, diagnoses failures at every severity level, and applies graduated repairs — from prompt tweaks to full redesign — while allowing human intervention at any step.

- [x] [19-meta-orchestrator](plans/19-meta-orchestrator.md) — autonomous workflow planning, execution, graduated repair, and cross-workflow learning
  - [x] [19-1-workflow-experience-memory](plans/19-1-workflow-experience-memory.md) — experience schema/store/index, incremental consolidation, dedupe, auto-indexing, cross-workflow principle sharing (global scope on ErrorMemoryIndex + PrincipleStore + EngineConfig flag)
  - [x] [19-2-workflow-planner](plans/19-2-workflow-planner.md) — reuse-first planner with deterministic Generate compiler, PlanReview, EngineConfig fields, few-shot examples, builder-code path (sandbox subprocess), LLM integration tests
  - [x] [19-3-structural-repair](plans/19-3-structural-repair.md) — graduated repair engine: RepairClassifier, ParameterRepairGenerator, StructuralRepairPlanner, RedesignTrigger, RepairEscalator, RepairActionStore, RedesignResult; all unit + LLM + engine pipeline tests pass
  - [x] [19-4-autonomous-execution-controller](plans/19-4-autonomous-execution-controller.md) — meta-session loop: create/run/pause/resume/events, experience feedback (success+failure), cross-session learning, WebSocket event stream wired end-to-end

## Phase 11.5 — Patch & Polish
> Close documentation debt, polish chat/editor UX with deferred quick-wins, and activate recently-built backend features with frontend integration. No new architecture — purely finishing deferred work.

- [x] [20-patch-polish](plans/20-patch-polish.md) — docs sync, chat/editor UX polish, checkpoint UI, auto-mode detection
  - [x] [20-1-docs-quick-wins](plans/20-1-docs-quick-wins.md) — A. Docs sync (retry, multi-provider, built-in tools) + per-thread mode persistence, keyboard shortcut, debug diff tag
  - [x] [20-2-chat-editor-polish](plans/20-2-chat-editor-polish.md) — B. "Fix this" shortcut, collapse run output, fuzzy mention search, markdown export buttons, sortable log columns
  - [x] [20-3-checkpoint-ui-automode](plans/20-3-checkpoint-ui-automode.md) — C. Run history checkpoint UI, multi-tab checkpoint consistency, auto-mode detection

## Phase 12 — Author & Distribute
> CLI, publish as API/MCP, messaging adapters, shareable blocks, PyPI package. Comes after Meta-Orchestrator so API surface, cost controls, and autonomous execution are stable before packaging.

- [x] [21-author-distribute](plans/21-author-distribute.md) — CLI, publish as API/MCP, messaging adapters, shareable blocks, PyPI package
  - [x] [21-1-pypi-package](plans/21-1-pypi-package.md) — public API surface, package structure, version 0.1.1, entry points, optional deps, CLI placeholders, LICENSE, PACKAGE_SPLIT.md
  - [x] [21-2-cli-mode](plans/21-2-cli-mode.md) — `dan-run` with Rich TUI, `--interactive` HumanNode, meta-orchestrator NL path, background mode
  - [x] [21-3-publish-api-mcp](plans/21-3-publish-api-mcp.md) — MCP server generation, HTTP REST fallback, stateful streaming, easy portal, SSE/WebSocket streaming, rate limiting, dan-serve integration
  - [x] [21-4-messaging-adapters](plans/21-4-messaging-adapters.md) — email + Telegram + WhatsApp adapters as interactive HumanNode renderers, server integration, email retry *(adapter log streaming to editor deferred)*
  - [x] [21-5-shareable-blocks](plans/21-5-shareable-blocks.md) — block package format, export/import, versioning, local registry, editor integration, backend API endpoints

## Backlog (unphased)

### Infrastructure / CI
- [ ] **Playwright E2E browser tests** (12-6 tasks 5-6) — mode transitions, mention autocomplete, stop generation, export. Requires Playwright setup + CI pipeline.
- [ ] **CI regression job** (12-6 task 2-8, 3-6) — run NL→mutation golden suite and provider compat matrix on schedule. Requires CI runner.
- [ ] **Snapshot/regression tests** (8-2 task 6-7, 8-4 task 3-7) — compile known fixtures, compare output graph JSON to stored snapshots. Requires CI snapshot infrastructure.
- [ ] **Mutation metrics baseline** (12-6 tasks 4-5 through 4-7) — capture 3-5 day baseline on main, set sprint targets, end-of-phase report. Requires production deployment.

### Integration tests requiring real LLM
- [ ] **Error memory integration test** (17-1 tasks 5-4 through 5-6) — workflow fails → errors indexed → query returns relevant results → LLM receives error context. Requires full engine + embedding provider.
- [ ] **Reflection multi-run integration test** (17-2 task 8-3) — failed run → reflection triggered → principles stored → next run's prompt includes principles. Requires real LLM + multi-run orchestration.
- [ ] **Experience consolidation integration test** (19-1 tasks 6-4, 6-5) — multiple runs → experience auto-consolidated → semantic search returns relevant workflows. Requires full engine + embeddings.

### Frontend polish
- [x] ~~**Fuzzy search in mention autocomplete**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [ ] **Recently used mentions at top** (12-3 task 8-4)
- [ ] **Preview tooltip on mention hover** (12-3 task 8-5)
- [x] ~~**Sortable log columns**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [ ] **Token flow edge labels** (18-4 task 3-4) — token count on edges, may be visually noisy
- [ ] **Before/after token estimation** (18-4 task 4-4) — estimated next-run tokens if suggestion is applied
- [x] ~~**Per-thread mode persistence**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)
- [x] ~~**Keyboard shortcut to cycle chat modes**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)
- [x] ~~**Debug diff tag**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)
- [x] ~~**"Fix this" shortcut**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [ ] **Code syntax highlighting in mention context** (12-3 task 3-3)
- [x] ~~**Frontend export buttons**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [x] ~~**Collapse verbose run output**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [ ] **Analytics rule dashboard** (18-4 task 5-6) — show active/pending optimization rules, cumulative savings, effectiveness
- [x] ~~**Run history checkpoint UI**~~ → promoted to [20-3](plans/20-3-checkpoint-ui-automode.md)
- [x] ~~**Multi-tab checkpoint consistency**~~ → promoted to [20-3](plans/20-3-checkpoint-ui-automode.md)

### Docs sync (batched)
- [x] ~~**Runtime reliability docs**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)
- [x] ~~**Multi-provider docs**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)
- [x] ~~**Built-in tools docs**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)

### Requires new architecture
- [ ] **Per-operation approval gates** (12-4 task 5) — bidirectional WebSocket handshake for per-tool-call approve/reject
- [ ] **@Web mentions** (12-3 task 6) — async network calls during mention resolution, loading UX, attribution
- [ ] **Thread branching** (12-5 task 7) — "Branch from here", parent-child tree, branch indicator in thread list
- [ ] **Message queuing** (12-5 task 2) — type while LLM generates, queue management, re-resolve mentions on send
- [ ] **Sandbox execution display** (12-4 task 6) — terminal-like rendering, ANSI colors, file artifacts, resource usage

### Deferred runtime features
- [ ] **Loop compaction strategy runtime** (18-3 tasks 3-3, 3-4, 3-5) — sliding_window/summarize/diff_based/keep_last runtime implementations in control-flow executors
- [ ] **Persistent cross-run cache** (18-2 task 2-3) — disk-backed memoization with session memory coordination
- [ ] **Memory-aware cache invalidation** (18-2 task 2-5) — track memory_dependency_keys, invalidate on memory change
- [ ] **Automatic state externalization** (18-3 task 1-3) — scheduler/executor writes loop/foreach/team state to StateStore automatically
- [ ] **Run-level advisory token budget** (18-3 task 4-1) — `token_budget` on EngineConfig as global planning signal
- [ ] **Principle compaction** (17-2 task 3-4) — ConsolidationPipeline adapter for principle merging at threshold
- [ ] **ReflectionNode authoring surfaces** (17-2 task 7) — builder DSL `wf.reflection()`, markdown `type: reflection`, editor palette/config
- [ ] **Two-tier reference resolution** (18-1 task 4-4) — artifact store + memory mirroring for pass_by_reference
- [ ] **Encode-to-memory pattern** (18-1 task 4-5) — large outputs stored as MemoryItems, downstream retrieves summary
- [ ] **Hyperedge JIT loading** (18-1 task 7-5) — inject hyperedge summaries, load full rules on demand
- [ ] **Unified cross-source token budget** (18-3 task 4-4) — budget accounts for edges + system + context + hyperedge + memory + RAG
- [ ] **Checkpoint/resume for parallel subagents** (7-9 task 3) — capture per-branch completion status, resume pending branches
- [ ] **Builder DSL for tools** (7-3 task 8-3) — `wf.tool("name", tool_id="file_read")` out of the box
- [ ] **RAG reranking** (9-1 task 6-4) — LLM-based re-scoring of top_k*3 candidates
- [ ] **Tier de-escalation telemetry** (18-5 task 4-2) — persist per-node tier success stats across runs, suggest cheaper tiers after repeated success
- [ ] **Tier badge in editor** (18-5 task 7-2) — show L0/L1/L2/L3 tier alongside model name in DanNode during/after runs
- [ ] **Tier analytics panel** (18-5 task 7-3) — per-node tier assignment, score decomposition, cost comparison vs. uniform model

### Stretch goals
- [x] ~~**Auto-mode detection**~~ → promoted to [20-3](plans/20-3-checkpoint-ui-automode.md)
- [ ] **Parallel subagent visualization** (7-9 task 5-3) — show parallel branches and fan-in in execution
- [ ] **ToolExecutor integration test** (7-3 task 9-4) — end-to-end built-in tool via ToolExecutor
- [ ] **Cross-workflow error migration notes** (17-1 task 6-3) — migration guidance for collection naming/scope

### Existing backlog items
- [ ] **User system** — login, auth, per-user data isolation. Graph store, runs, checkpoints scoped to user. Multi-user/team/cloud deployments. (Low priority — revisit when cloud/SaaS deployment becomes a goal.)
- [ ] **Manager vs worker node distinction** — manager nodes orchestrate and may spawn new nodes; worker nodes only execute and do not hire new nodes. Bottom-layer nodes are workers. Enables token/node budget caps.
- [x] ~~Optimize token usage~~ → promoted to [Phase 10](#phase-10--token-optimization) (Plan 18)
- [x] [7-8-workflow-node-api-hardening](plans/7-8-workflow-node-api-hardening.md) — Markdown round-trip lossless, ContextEdge validation, gate defaults, strict parse, mutator diagnostics
- [x] ~~Async loop design~~ → promoted to [Phase 9C](#9c--execution-primitives) (Plan 16-5)
- [ ] NL mutation quality tracking bundle — now tracked under [12-6-chat-quality-harness](plans/12-6-chat-quality-harness.md)
- [x] Investigate React Flow for graph rendering — adopted in Phase 2, `@xyflow/react` v12
- [ ] Thread timeline view — vertical timeline of graph evolution through conversation (stretch, from 10-5 task 5-3)
- [ ] Canvas drag-to-mention — drag node from canvas onto chat input to create mention (stretch, from 10-2 task 6-4)
- [x] ~~Self-evolving orchestrator~~ → promoted to [Phase 9D](#9d--self-evolving-orchestrator)
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
