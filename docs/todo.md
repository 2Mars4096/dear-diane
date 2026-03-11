# Todo

## External Automation Tasks
- [x] [2026-03-06-orbis-resume-state-design](plans/2026-03-06-orbis-resume-state-design.md) — design for matching-only persisted resume and stage-aware continuation in the external Orbis runner
- [x] [2026-03-06-orbis-resume-state-implementation](plans/2026-03-06-orbis-resume-state-implementation.md) — implemented and verified resume flow, current-page shortcut, and safe task-reopen logic

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

- [x] [18-token-optimization](plans/18-token-optimization.md) — smart context assembly, caching, agent-directed context architecture, token analytics, task-level model tiering
  - [x] [18-1-prompt-compression](plans/18-1-prompt-compression.md) — advisory token budgets, context deferral, JIT schema loading, context tools, summarization, reference passing
  - [x] [18-2-caching-layer](plans/18-2-caching-layer.md) — provider prompt caching, node memoization, semantic cache, cache APIs
  - [x] [18-3-context-window-management](plans/18-3-context-window-management.md) — externalized state, history policy, safe loop compaction, advisory token budgets
  - [x] [18-4-token-analytics](plans/18-4-token-analytics.md) — per-node breakdown, waste detection, optimization recommendations, evolving playbooks, editor visualization *(token flow edges and before/after estimation deferred)*
  - [x] [18-5-task-level-model-tiering](plans/18-5-task-level-model-tiering.md) — 3-dimension scoring (difficulty/impact/recoverability), 4 model tiers, TierPolicy, adaptive escalation/de-escalation, docs, editor tier badges + analytics, tier de-escalation telemetry (4-2)

## Phase 11 — Meta-Orchestrator
> Autonomous planning, execution, and self-repair. Given a high-level goal ("write a paper on X in Y format"), the meta-orchestrator discovers relevant past workflows, plans/adapts/generates a workflow graph, executes it, diagnoses failures at every severity level, and applies graduated repairs — from prompt tweaks to full redesign — while allowing human intervention at any step.

- [x] [19-meta-orchestrator](plans/19-meta-orchestrator.md) — autonomous workflow planning, execution, graduated repair, cross-workflow learning, **self-authoring**
  - [x] [19-1-workflow-experience-memory](plans/19-1-workflow-experience-memory.md) — experience schema/store/index, incremental consolidation, dedupe, auto-indexing, cross-workflow principle sharing (global scope on ErrorMemoryIndex + PrincipleStore + EngineConfig flag)
  - [x] [19-2-workflow-planner](plans/19-2-workflow-planner.md) — reuse-first planner with deterministic Generate compiler, PlanReview, EngineConfig fields, few-shot examples, builder-code path (sandbox subprocess), LLM integration tests
  - [x] [19-3-structural-repair](plans/19-3-structural-repair.md) — graduated repair engine: RepairClassifier, ParameterRepairGenerator, StructuralRepairPlanner, RedesignTrigger, RepairEscalator, RepairActionStore, RedesignResult; all unit + LLM + engine pipeline tests pass
  - [x] [19-4-autonomous-execution-controller](plans/19-4-autonomous-execution-controller.md) — meta-session loop: create/run/pause/resume/events, experience feedback (success+failure), cross-session learning, WebSocket event stream wired end-to-end
  - [x] [19-5-self-knowledge-rag](plans/19-5-self-knowledge-rag.md) — index DAN's own docs into dedicated RAG collection; planner retrieves relevant API sections before every planning invocation
  - [x] [19-6-runtime-authoring](plans/19-6-runtime-authoring.md) — dynamically generate, sandbox-test, register, and persist custom tools and skills at runtime
  - [x] [19-7-system-architect-mode](plans/19-7-system-architect-mode.md) — decompose complex multi-workflow intents into coordinated systems with shared memory, tools/skills, and routing

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
  - [x] [21-6-cli-chat-mode](plans/21-6-cli-chat-mode.md) — `dan-chat` REPL for conversational workflow authoring (CLI parity with editor ChatPanel)
  - [x] [21-7-gateway-text-dispatch](plans/21-7-gateway-text-dispatch.md) — implement text dispatch so `dan-run "goal"` works with server
  - [ ] ~~[21-8-local-cli-chat-fallback](plans/21-8-local-cli-chat-fallback.md)~~ — promoted to [26-1-local-chat-and-launcher](plans/26-1-local-chat-and-launcher.md) under Phase 16

## Phase 12.1 — Cross-Phase Deferred Completion Waves
> Parallel closeout waves used to finish high-ROI deferred tasks across existing phases without opening new architecture scope.

- [x] [22-deferred-wave-1](plans/22-deferred-wave-1.md) — wave 1: tiering finish, token runtime (loop compaction/cache/budget), and mention UX polish
- [x] [22-deferred-wave-2](plans/22-deferred-wave-2.md) — wave 2: tier docs/editor integration, deferred runtime features, and frontend analytics polish (plus review hardening)
- [x] [22-3-review-hardening](plans/22-3-review-hardening.md) — round 3 review: graph ID sanitization, approval race, cancel route, human-input ownership, context-edge semantics, strict-edge restore, alias collisions, CLI startup, docs cleanup

## Phase 13 — Multi-Surface Gateway
> Unify all interaction surfaces (CLI, messaging adapters, MCP) through `dan-serve` as a central hub with shared run management, cross-surface event streaming, and activity tracking. Any surface can trigger a workflow, observe activity from any other surface, and resolve HumanNode prompts cross-surface.

- [x] [23-multi-surface-gateway](plans/23-multi-surface-gateway.md) — gateway API, thin client protocol, surface refactors
  - [x] [23-1-gateway-api](plans/23-1-gateway-api.md) — A. Server-side dispatch endpoint, activity tracker, global event bus, cross-surface HumanNode resolution
  - [x] [23-2-thin-client-protocol](plans/23-2-thin-client-protocol.md) — B. Shared `DanClient` library: HTTP dispatch, WebSocket events, HumanNode relay, fallback mode
  - [x] [23-3-cli-thin-client](plans/23-3-cli-thin-client.md) — C. Refactor `dan-run`/`dan-status`/`dan-logs` to use `DanClient` with direct-engine fallback
  - [x] [23-4-adapter-thin-client](plans/23-4-adapter-thin-client.md) — D. Refactor messaging adapters to use `DanClient`, cross-surface HumanNode pickup
  - [x] [23-5-mcp-thin-client](plans/23-5-mcp-thin-client.md) — E. Refactor `dan-publish` MCP/HTTP to use `DanClient`, server-side sessions
  - [x] Unified `PublishRuntime` — `GatewayRuntime` (primary) + `LocalRuntime` (fallback) behind ABC; fixes broken MCP status/submit in server mode; `--server`/`--local` flags on `dan-publish` CLI

## Phase 14 — Reliable Workflow Generation
> Fix the core "describe what you want → working workflow" loop. The current mutation-JSON path
> requires LLMs to produce structurally precise graph operations (exact port names, node configs,
> edge types) — a task they fail at frequently (12+ schema-drift fixes in the last week alone).
> Switch to higher-level generation where LLMs produce what they're good at (intent, structure,
> code) and deterministic compilers handle the rest. Keep all four sub-plans, but execute them
> with hard boundaries: prove each block standalone first, then compose them. Do not add heavy
> runtime autonomy or long-lived self-repair loops to the core path. The goal is boringly reliable
> generation, not a more complicated orchestrator.

- [x] [24-reliable-generation](plans/24-reliable-generation.md) — builder codegen, intent compiler, quality suite, and bounded diagnosis with composition gates
  - [x] [24-1-builder-codegen-path](plans/24-1-builder-codegen-path.md) — A. Make builder DSL codegen the main generation path for new workflows. LLM generates `dan.builder` Python, run it in `SandboxRunner`, compile to graph, validate, and only then save/apply. Keep scope narrow: one-shot generation + deterministic validation + clear error reporting. No hidden retries beyond a small bounded repair pass. Acceptance: generated builder code is readable, reproducible, and round-trips cleanly through the existing graph pipeline.
  - [x] [24-2-intent-compiler](plans/24-2-intent-compiler.md) — B. Add a second, constrained path for common workflow shapes. Phase 1: LLM emits a compact structured intent (goal, stages, inputs, outputs, loop/review requirements, data sources). Phase 2: a deterministic compiler maps that intent to templates/builder code. Keep it narrow and explicit: only support a small catalog of common patterns at first, with strict diagnostics when intent exceeds compiler coverage. Acceptance: common cases are more predictable than free-form codegen, and unsupported cases fail fast back to the primary path.
  - [x] [24-3-generation-quality-suite](plans/24-3-generation-quality-suite.md) — C. Build the verification harness before trusting composition. Curate golden intents across the main workload families (paper writing, literature review, RAG QA, multi-step analysis, review loop, fan-out, tool-heavy flows). Evaluate both paths independently: intent/codegen → graph validation → compile/load round-trip → dry-run/smoke-run. Track pass rates, failure modes, and regression snapshots. Acceptance: every new generation change must prove it did not break known cases before rollout.
  - [x] [24-4-bounded-diagnosis-loop](plans/24-4-bounded-diagnosis-loop.md) — D. Add a small, explicit repair loop for failed generations/runs: extract validation/runtime errors, map them to the smallest responsible artifact (intent schema, builder code block, graph edge/port, node config), and attempt a targeted correction. Keep this outside the steady-state runtime path: bounded attempts only, no autonomous long-running redesign loop inside the core engine. Acceptance: diagnosis improves recovery on known failures without making normal generation slower or harder to reason about.
  - [x] Post-completion hardening: enforce quality-suite fixture constraints (`min_nodes`, `node_types`) and baseline total-count regression checks; wire diagnosis port auto-fix to real `edge_endpoint` errors with fallback to re-prompt when deterministic repair is unavailable; scope port rewrites to wiring contexts and emit explicit mismatch error taxonomy for quality triage.

## Phase 15 — Chat as Unified Control Plane
> Collapse all existing capabilities behind the conversational interface. Today DAN has 8 CLI
> commands, a visual editor, messaging adapters, and a meta-orchestrator — but they're separate
> surfaces with separate UX. The goal: **chat is the single front door**, whether the user is in
> `dan-chat`, the editor ChatPanel, Telegram, WhatsApp, or another conversational client. The
> meta-orchestrator routes intent to the right subsystem, and the user never leaves the
> conversation. "Have you done a lit review before?" → memory query. "Adapt it for supply chain"
> → planner. "Run it" → engine. "Share it with John" → publish. Same behavior across chat surfaces.

- [x] [25-chat-control-plane](plans/25-chat-control-plane.md) — chat as unified shell over all existing subsystems and chat surfaces
  - [x] [25-1-capability-router](plans/25-1-capability-router.md) — A. `ChatCapabilityRegistry` + `CapabilityContext` + multi-tool dispatch in `ChatManager`. Mode-aware filtering (ask/plan read-only). Base tools: `list_graphs`, `get_activity`.
  - [x] [25-2-experience-in-chat](plans/25-2-experience-in-chat.md) — B. 5 experience tools: `search_workflow_history`, `get_workflow_details`, `search_run_history`, `get_learned_principles`, `discover_capabilities`. Lexical fallback when embedding index unavailable.
  - [x] [25-3-publish-share-from-chat](plans/25-3-publish-share-from-chat.md) — C. 8 publish/share/export tools: `publish_workflow`, `unpublish_workflow`, `export_workflow`, `share_workflow`, `list_published`, `get_publish_status`, `import_block`, `list_blocks`.
  - [x] [25-4-run-lifecycle-from-chat](plans/25-4-run-lifecycle-from-chat.md) — D. 9 run lifecycle tools: `start_run`, `get_run_status`, `list_active_runs`, `cancel_run`, `resume_run`, `get_run_logs`, `get_run_checkpoints`, `rerun_from_checkpoint`, `submit_human_input`. Resolved references ("latest", "last_failed", "paused").
  - [x] [25-5-auto-approve-undo](plans/25-5-auto-approve-undo.md) — E. Auto-apply mutations by default, `/undo` command with graph snapshot stack (max 10), `--confirm` flag / `DAN_MUTATION_CONFIRM=1` restores approval prompt.

  - [x] [25-6-intent-dispatcher](plans/25-6-intent-dispatcher.md) — F. Concierge Runtime foundation: shared `Project` / `Task` scope, deterministic fast paths, handler backends, summary-first context loading, and common surface wiring above `ChatManager`. This is now the transitional substrate for the later solver-first runtime rather than the final top-level router.
    - [x] 25-6-1. `SurfaceMessage` model + `ProjectStore` (Project/Task persistence, create/search/append)
    - [x] 25-6-2. `ProjectContextResolver` — infer project+task from turns, runs, mentions, similarity; auto-label
    - [x] 25-6-3. Intent taxonomy + heuristic classifier (10 categories, keyword rules, LLM fallback < 0.6 confidence, bias toward direct simple-task handling)
    - [x] 25-6-4. Handler registry + 9 handlers (File, DirectTask, Run, Status, Experience, Publish, WorkflowBuild, Conversation, MetaGoal)
    - [x] 25-6-5. Concierge dispatch loop + `[DAN - <Project>]` / `[DAN - <Project> / <Task>]` reply labels
    - [x] 25-6-6. Wire into surfaces (server endpoint, dan-chat, adapter, editor)
    - [x] 25-6-7. Summary-first context loading (project summary + task turns as history, auto-summarization)
  - [x] [25-7-action-autonomy-policy](plans/25-7-action-autonomy-policy.md) — G. Shared execution policy substrate: one-round clarification, queue, progress, workflow promotion, destructive/cost confirmation, and messaging-surface defaults. This remains the policy layer for the later solver-first runtime.
    - [x] 25-7-1. `ActionPolicy` + `ExecutionPolicy` models, `resolve_policy()` with user overrides + destructive/cost detection
    - [x] 25-7-2. Clarification protocol — `ClarificationRequest`/`Response`, single round, fallback to highest-confidence
    - [x] 25-7-3. Project-aware message queue — immediate for status, queued for active project, parallel for new topic
    - [x] 25-7-4. Progress reporting — `ProgressReporter` with push (interval) + pull (instant status), `[DAN - <Project>]` format
    - [x] 25-7-5. Workflow promotion after success — `WorkflowPromoter`, propose `/save` for >3-node successes
    - [x] 25-7-6. Safety rules + messaging surface defaults
    - [x] 25-7-7. Docs + end-to-end integration tests
  - [x] [25-8-solver-runtime](plans/25-8-solver-runtime.md) — H. Solver-first runtime: `Understand -> Plan -> Act -> Reflect`, `GoalResolver`, `PlanBuilder`, `SolverDecision`, one-question clarification, and deliverable-first planning above the concierge foundation.
  - [x] [25-9-workflow-memory-and-reuse](plans/25-9-workflow-memory-and-reuse.md) — I. Make workflow/experience memory a semantic planning input: retrieve similar workflows, choose reuse vs. adapt vs. build, and learn from corrections.
  - [x] [25-10-execution-selector](plans/25-10-execution-selector.md) — J. Turn solver decisions into concrete execution backends while preserving deterministic prechecks and shared handlers as actuators.
  - [x] [25-11-fallback-and-completion-policy](plans/25-11-fallback-and-completion-policy.md) — K. Encode the "never just stop" runtime contract: alternate paths, useful subsets, capability-building, human-action scaffolding, and productive terminal outcomes.
  - [x] [25-12-tool-aware-conversation](plans/25-12-tool-aware-conversation.md) — L. Eliminate fabricated live-data responses: give conversation paths real tool access (web search), activate solver runtime on adapter surfaces, add post-execution validation for unsourced claims.

- [x] [25-13-conversation-file-tools](plans/25-13-conversation-file-tools.md) — file_read + pdf_read as capability tools, web search provider cascade (Tavily/Brave/DDG), CAPABILITY_TOOLS_REFERENCE fixes, FunctionCall XML leak fix

## Phase 16 — Always-On Personal Service
> Once chat is the unified control plane (Phase 15), make it persistent: always running, always
> reachable, always remembering. DAN becomes a background service you talk to anytime — from
> `dan-chat`, the editor chat, Telegram, WhatsApp, or any future chat surface. It remembers your
> past work, preferences, and patterns across sessions. It notifies you when things finish or need
> attention.

- [x] [26-always-on-service](plans/26-always-on-service.md) — daemon mode, zero-friction entry, persistent memory, notifications, rich CLI display *(phase complete; optional LLM-based preference extraction remains deferred)*
  - [x] [26-1-local-chat-and-launcher](plans/26-1-local-chat-and-launcher.md) — A. `dan-chat` works without `dan-serve` (promotes deferred 21-8): in-process ChatManager plus local `RunManager`/`RunStore` for `/run` parity, cancellation, and HumanNode handling. `dan up` checks for running server, uses startup lock (`~/.dan/server.lock`), starts in background if needed, and drops into chat. `dan down` stops background server. PID file at `~/.dan/server.pid`.
  - [x] [26-2-daemon-mode](plans/26-2-daemon-mode.md) — B. `dan-service install/uninstall/start/stop/status/health/logs`. macOS launchd plist + Linux systemd unit. Auto-start at login, `KeepAlive`/`Restart`. Health checks. Log rotation to `~/.dan/logs/`. 51 tests.
  - [x] [26-3-persistent-user-context](plans/26-3-persistent-user-context.md) — C. `UserProfile` model + `PreferenceExtractor` (heuristic) + `ConversationMemoryStore` (keyword search) plus `ChatManager` prompt injection, startup quick-resume, and one-per-session preference suggestions. *(Optional LLM extraction still deferred)*
  - [x] [26-4-notifications](plans/26-4-notifications.md) — D. `NotificationManager` + `MacOSNotifier` + `WebhookNotifier` + `TerminalBellNotifier` + `NotificationConfig`, including `app.py` GlobalEventBus lifecycle wiring.
  - [x] [26-5-rich-cli-display](plans/26-5-rich-cli-display.md) — E. ASCII DAG renderer for `/show` (topological sort, box-drawing). Streaming node-by-node progress during `/run`. Mutation diff display. Rich table for `/list`. `/show --code`/`--json`/`--stats`. Graceful degradation without Rich. (~2 days)
  - [x] [26-6-whatsapp-web-adapter](plans/26-6-whatsapp-web-adapter.md) — F. WhatsApp Web adapter for personal use (QR code pairing, no Business API). `WhatsAppWebAdapter` with neonize, QR pairing, LID→phone resolution, self-message echo suppression, chat-mode wiring, `/find` + `/send` file commands, size checks, force-exit handling, adapter context prompt. 16 tests. *(Mutation-confirmation UX on messaging surfaces deferred to 25-7.)*
  - [x] [26-7-whatsapp-inbound-media](plans/26-7-whatsapp-inbound-media.md) — G. WhatsApp inbound media & voice input. `_handle_incoming` handles all media types (documents, images, video, audio/voice, stickers, contacts, location). Voice transcription via cascading Whisper provider resolution (`DAN_WHISPER_*` → `DAN_OPENAI_*` → `DAN_LLM_*`). Adapter chat-mode parses `[Attachment:]` and `[Voice note:]` prefixes, routes PDFs to review path, fallback on transcription failure. Media saved to `~/.dan/whatsapp-web/media/` with 1-hour cleanup. 10 tests.

## Phase 17 — Async Message Dispatch
> Process independent user messages concurrently across projects while serializing within the same
> task. The router feels like a parallel assistant, not a serial queue. Configurable bot identity
> replaces all hardcoded `[DAN` prefixes.

- [x] [27-async-message-dispatch](plans/27-async-message-dispatch.md) — concurrent project dispatch, same-task serialization, configurable bot name
  - [x] [27-1-configurable-bot-identity](plans/27-1-configurable-bot-identity.md) — A. Replace hardcoded `[DAN` with `DAN_BOT_NAME` env var, shared `identity.py` module. 26 tests.
  - [x] [27-2-concurrent-project-dispatcher](plans/27-2-concurrent-project-dispatcher.md) — B. `ConcurrentDispatcher` wrapping `Concierge`: per-project asyncio tasks, same-task serial queues, cross-project parallelism. `ChatQueuedEvent` for deferred response delivery. 6 tests.
  - [x] [27-3-surface-async-acceptance](plans/27-3-surface-async-acceptance.md) — C. Server endpoint returns `status: "processing"|"queued"`, pipes queued stream channels. Adapter uses shared `httpx.AsyncClient` + background task dispatch. `LocalChatRuntime` uses dispatcher. `dan-chat` CLI local mode gets queued event handling.

## Phase 18 — LLM-First Chat Architecture
> Move chat toward an LLM-first architecture with a unified prompt, multi-turn tool loop, and post-LLM safety/UX actions. Some mode-gated and text-only fallback behavior still remains while `28-5` decides what can be removed safely.

- [ ] [28-llm-first-chat](plans/28-llm-first-chat.md) — LLM-first chat: single path, all tools, multi-turn, response actions
  - [x] [28-1-strip-adapter-routing](plans/28-1-strip-adapter-routing.md) — A. Adapter is thin pipe: slash→NL translation, everything to server
  - [x] [28-2-unified-tool-dispatch](plans/28-2-unified-tool-dispatch.md) — B. Multi-turn tool loop (10 turns), broader tool visibility, initial `DAN_LLM_FIRST_CHAT` rollout
  - [x] [28-3-response-actions](plans/28-3-response-actions.md) — C. File delivery events, message splitting, claim validation
  - [x] [28-4-system-prompt-design](plans/28-4-system-prompt-design.md) — D. Unified prompt with tool catalog, surface hints, anti-fabrication rules
- [ ] [28-5-cleanup-dead-code](plans/28-5-cleanup-dead-code.md) — E. Remove dead classifier, handlers, mode-gating code *(in progress: removed unused classifier fallback/re-export, deprecated runtime queue plumbing, an unused `detect_chat_mode()` parameter, a tiny unused solver/executor interface, dead fallback-policy helpers, and the legacy prompt fallback/constants; handler, solver, and broader mode reductions now require runtime decisions)*
  - [x] [28-6-real-world-test-scenarios](plans/28-6-real-world-test-scenarios.md) — F. 5 scenarios, 38 tests: lit review, equity report, deep research, computer task, casual utility

## Phase 19 — Concierge-First Architecture: Memory, Reuse & Self-Evolvement
> Make the concierge the persistent intelligent agent that owns memory, drives iterative workflow
> building, and learns from every interaction. Workflows become compiled execution artifacts;
> the concierge is the brain. Unified typed memory kernel replaces 6 siloed stores. Reuse-first
> workflow selection. Multi-round build/test/diagnose loop. Automatic learning extraction.
> Concierge-level parallelism for independent sub-tasks.

- [x] [29-concierge-memory-evolvement](plans/29-concierge-memory-evolvement.md) — concierge-first architecture: memory kernel, workflow reuse, self-evolvement *(complete: all 9 sub-plans reconciled to the current codebase, including MCP bridge closeout)*
  - [x] [29-1-unified-memory-kernel](plans/29-1-unified-memory-kernel.md) — A. Typed memory store (fact/preference/pattern/failure/principle/episode/working_state), per-type ranking, task-specific retrieval policies, adapter layer over existing stores, temporal consolidation *(complete: all kernel CRUD, adapters, retrieval policies, dual-write, consolidation, preference parity shipped)*
  - [x] [29-2-concierge-as-orchestrator](plans/29-2-concierge-as-orchestrator.md) — B. Fold MetaController into concierge, stateful goals/plans spanning messages, autonomous build→run→diagnose loop, memory-informed decisions, autonomy levels (interactive/supervised/autonomous) *(complete: plan→execute→diagnose→repair→check-in loop, shared utility extraction in meta/utils.py)*
  - [x] [29-3-iterative-workflow-building](plans/29-3-iterative-workflow-building.md) — C. Multi-round build session: draft→validate→test→diagnose→modify→re-test state machine, smoke testing, user intervention, post-build memory extraction *(completed: diagnosis now surfaces failure patterns/principles explicitly and pauses for structural review outside autonomous mode; 61 tests)*
  - [x] [29-4-experience-driven-reuse](plans/29-4-experience-driven-reuse.md) — D. Wire experience retrieval into primary build path, reuse-first decision (REUSE/ADAPT/GENERATE), workflow catalog in chat, adapter parity, experience feedback loop *(complete: all 7 task groups shipped)*
- [x] [29-5-concierge-parallelism](plans/29-5-concierge-parallelism.md) — E. Universal fan-out: all parallelism tasks complete. Turn prep, tool execution, diagnosis, validation, memory extraction, resource-based concurrency, priority queuing, unified queue, immediate-start, post-run learning fan-out, consolidation fan-out, concurrent fix application, reuse-gate parallelism; 22 tests in `test_parallelism_integration.py`
  - [x] [29-6-self-evolvement-loop](plans/29-6-self-evolvement-loop.md) — F. Passive learning (memory extraction, pattern filing, cross-section reinforcement) + active adaptation (prompt optimization via A/B testing, per-node model selection learning, skill/hyperedge evolution, topology suggestions) *(complete: prompt optimization, model learning, skill evolution, topology suggestions, monitoring, and documented test coverage are all represented in the current implementation/plan notes)*
  - [x] [29-7-essential-tools](plans/29-7-essential-tools.md) — G. Fill critical tool gaps: 32 tools shipped (current_datetime, clipboard, python_eval, send_email, notify, file ops, csv_read, spreadsheet_read, git tools, image_describe, audio_transcribe, compress, translate, diff)
  - [x] [29-8-practical-research-quality-and-audit](plans/29-8-practical-research-quality-and-audit.md) — H. Daily-use acceptance for equity research, deep research, PDF literature summary, and literature search + gated-paper handoff, plus end-to-end chat provenance/audit *(complete: audit model, research hardening, scenario regression)*
  - [x] [29-9-mcp-tool-bridge](plans/29-9-mcp-tool-bridge.md) — I. Consume external MCP servers (Stata, R, databases) as first-class chat tools. `/mcp install` from chat. Auto-connect on startup. *(complete: server + local startup wiring, concierge command coverage, optional real round-trip test, README quick-start)*

## Phase 20 — Telegram Multi-Bot Platform
> Make Telegram a first-class DAN surface with multi-bot group chats, per-bot project focus,
> Telegram-native UX (forum topics, message editing, reactions, polls), and zero-friction bot
> management. Telegram natively supports multiple bots in one group — the only messaging platform
> that does. Each bot focuses on its assigned projects. Friends in the group get responses too.

- [x] [30-telegram-platform](plans/30-telegram-platform.md) — Telegram multi-bot platform: adapter upgrade, multi-bot group chat, native features, bot management
  - [x] [30-1-adapter-upgrade](plans/30-1-adapter-upgrade.md) — A. Feature-parity single bot: chat-mode routing via concierge, media handling, voice transcription, file commands, surface hints, `dan-adapter telegram` chat-mode
  - [x] [30-2-multi-bot-group-chat](plans/30-2-multi-bot-group-chat.md) — B. BotFleet coordinator, MessageRouter (@mention / topic / keyword / default routing), per-bot project assignment, friend interactions, cross-bot awareness, shared concierge
  - [x] [30-3-telegram-native-features](plans/30-3-telegram-native-features.md) — C. Forum topics, streaming edits, reactions, polls (`telegram_poll` capability), file support, reply-to, bot commands, inline keyboards, pinned messages. Mini App WebApp frontend (10-3, 10-5) deferred — needs HTTPS deployment.
  - [x] [30-4-bot-management](plans/30-4-bot-management.md) — D. `dan-bot` CLI (create/list/start/stop/start-all/remove/edit/assign), `dan bot` unified CLI, `~/.dan/telegram/config.json`, BotFather guide, privacy mode, fleet daemon, per-bot stop via control file

## Phase 21 — Daily-Use Quality-of-Life & Power-Ups
> Activate dormant features, add missing control surfaces, improve visibility, expose hidden
> capabilities, and polish rough edges. `31-1` through `31-5` are mostly wiring/docs closeout;
> `31-6` onward adds focused execution, continuity, UX, and learning architecture to make DAN work
> smoothly for daily use.

- [x] [31-daily-use-qol](plans/31-daily-use-qol.md) — daily-use QoL: model control, visibility, capability exposure, power-user speed, defaults & docs, execution intelligence, safety, continuity *(core modules and most hardening landed: all 17 subplans shipped code/tests, plus command/help/runtime cleanup, scheduled-trigger persistence fixes, scoped progress overrides, hybrid classifier/experience-routing cleanup from live chat traces, and verified live LLM memory extraction with `/corrections`/`/adaptations` wired on the real server; the remaining live-wiring follow-ups stay tracked separately below)*
  - [x] [31-1-model-control](plans/31-1-model-control.md) — A. `/model` command, TierPolicy activation (`DAN_ENABLE_TIER_POLICY`), `get_config` tool, `set_config` expansion for `DAN_LLM_*`, model name in surface hints
  - [x] [31-2-visibility-feedback](plans/31-2-visibility-feedback.md) — B. Chat cost tracking (`/cost`), notification wiring for chat-initiated runs, error retry UX (`/retry`), `/status` fast command
  - [x] [31-3-capability-exposure](plans/31-3-capability-exposure.md) — C. Expose 13 built-in tools (python_eval, csv_read, git tools, etc.) as chat capabilities, workflow introspection tools, `DAN_LEARNING_MODE` bundle
  - [x] [31-4-power-user-speed](plans/31-4-power-user-speed.md) — D. CLI pipe/one-shot mode (`dan ask`), memory management commands (`/memory-delete`, `/memory-forget`, `/memory-confirm`), prep timeout, file auto-read
  - [x] [31-5-defaults-and-docs](plans/31-5-defaults-and-docs.md) — E. `.env.example` overhaul (30+ vars), startup feature banner, feature bundles, `docs/cli.md` update (`dan-ask`, new flags, 8+ slash commands), `docs/llm-api-guide.md` (13 new capability tools), `docs/architecture.md`, `docs/development-plan.md`, README
  - [x] [31-6-goal-oriented-loop](plans/31-6-goal-oriented-loop.md) — F. Autonomous iteration, strategy escalation, `/goal` command *(core module/command landed; real execution-loop wiring remains in the Phase 21 live-wiring follow-up slice)*
  - [x] [31-7-scheduled-tasks](plans/31-7-scheduled-tasks.md) — G. Cron/interval task scheduling, `/schedule` commands *(complete: 92 tests + lifespan background task)*
  - [x] [31-8-plan-dependency-optimization](plans/31-8-plan-dependency-optimization.md) — H. RCPSP scheduler with critical path and LRP scheduling *(complete: 98 unit + 25 integration tests — pre-flight validation, mid-execution dependency, LLM prompts with 5 golden examples, template deps, experience estimation, concierge execution path, workflow engine DAG conversion, /plan command, execution events)*
  - [x] [31-9-completion-guard](plans/31-9-completion-guard.md) — I. Requirement extraction and completeness validation *(complete: 76 tests — concierge wiring with skip guards for commands/greetings/follow-ups/short responses, 7 integration tests)*
  - [x] [31-10-pii-tokenization](plans/31-10-pii-tokenization.md) — J. Sensitive data masking before LLM API calls *(complete: 92 tests — ContextVar for request-scoped PIISession, DAN_PII_SKIP_CODE_BLOCKS support, edge-case coverage for code blocks/tool args/system prompts)*
  - [x] [31-11-cross-session-resume](plans/31-11-cross-session-resume.md) — K. Task state persistence, `/resume`, auto-resume *(complete: 71 tests — auto-populate wiring in `_finalize_task`, compact history with first-sentence truncation, cross-surface integration test)*
  - [x] [31-12-proactive-follow-up](plans/31-12-proactive-follow-up.md) — L. DAN-initiated follow-ups, quiet hours, rate limiting *(stale-task queue/engine landed; schedule-result and run-completion trigger emission remain in the Phase 21 live-wiring follow-up slice)*
  - [x] [31-13-multi-surface-continuity](plans/31-13-multi-surface-continuity.md) — M. Cross-surface context sharing, `/sync` command *(complete: 46 tests + presence tracking + handoff injection)*
  - [x] [31-14-progressive-response-ux](plans/31-14-progressive-response-ux.md) — N. Phase-chunked progressive disclosure, surface-adaptive verbosity *(renderers/session setup landed; phase-event wiring beyond session initialization remains in the Phase 21 live-wiring follow-up slice)*
  - [x] [31-15-learning-evolution-optimization](plans/31-15-learning-evolution-optimization.md) — O. Tiered learning, correction memory, adaptation governance *(complete: 130 tests — all 8 task groups done: tier activation with promotion gates + env overrides, node-level quality signals, correction→memory wiring, /status learning section, type-indexed MemoryKernel, planning calibration wiring)*
  - [x] [31-16-command-surface-unification](plans/31-16-command-surface-unification.md) — P. Canonical command registry, unified dispatch, `/help` *(complete: registry-driven adapter routing, REPL tab-completion, unknown-command suggestions, WhatsApp plain-text help, health check, command-doc generation, 94 tests)*
  - [x] [31-17-computer-control-and-browser-automation](plans/31-17-computer-control-and-browser-automation.md) — Q. Browser automation, desktop control, safety-first approvals *(policy/config/runtime-command core landed; chat capability/controller exposure remains in the Phase 21 live-wiring follow-up slice)*

## Benchmark Suite — Prove Long-Tail Advantage
> Empirically prove DAN's typed-graph architecture outperforms monolithic agents on complex, multi-step tasks. Published academic benchmarks + custom long-tail scenarios. Analysis framework built first.

- [ ] [benchmark-plan](benchmark-plans/benchmark-plan.md) — master spec: thesis, metrics, baselines, execution order
  - [ ] [bench-5-analysis-framework](benchmark-plans/bench-5-analysis-framework.md) — metrics collector, ablation controller, results DB, visualization, report generator (build first)
  - [ ] [bench-1-worfbench](benchmark-plans/bench-1-worfbench.md) — WorFBench (ICLR 2025): workflow DAG generation from NL
  - [ ] [bench-2-gaia](benchmark-plans/bench-2-gaia.md) — GAIA Level 3 (ICLR 2024): 6+ step real-world tasks, public leaderboard
  - [ ] [bench-3-appworld](benchmark-plans/bench-3-appworld.md) — AppWorld (ACL 2024): complex multi-API control flow, MCP bridge
  - [ ] [bench-4-custom-longtail](benchmark-plans/bench-4-custom-longtail.md) — custom 5-scenario suite: while-loops, hyperedges, fan-out, learning
  - Tier 2 (future): Tau-bench, Jenova.ai, AgentBench → (not yet planned)
  - Tier 3 (future): SWE-bench Pro, MLAgentBench, OdysseyBench, AssistantBench → (not yet planned)

## Backlog (unphased)

### strengthen workflow to make it more powerful and easier to use
- [x] **IDE-compatible skill store** — `SkillStore` scans `~/.dan/skills/` (user) + `.dan/skills/` (project) + legacy `DAN_CUSTOM_SKILLS_DIR` for `SKILL.md` files. Frontmatter superset of Cursor/Claude/Codex format. `/skill list|info|import|scan` commands. 48 tests.
- [x] ~~**Next deferred cleanup slice — Phase 21 live-wiring follow-ups**~~ — goal loop execution wiring (`GoalLoopExecutor` now spawns from `/goal`, tier-specific prompts, RepairClassifier inter-attempt diagnosis, intent recognition), proactive follow-up trigger emission (run-completion and schedule-result triggers wired in app.py, surface routing via PresenceTracker, per-task opt-out), progressive response pipeline (instant acknowledgment before LLM prep, phase transitions on capability execution, heartbeat enhancement), computer-use capability tools (7 browser + 5 desktop tools registered as chat capabilities, observe-act-verify loop, foreground-only enforcement, abort/fail-safe), and centered command reference (`docs/commands.md` + generation script)

### Infrastructure / CI
- [ ] **Playwright E2E browser tests** (12-6 tasks 5-6) — mode transitions, mention autocomplete, stop generation, export. Requires Playwright setup + CI pipeline.
- [ ] **CI regression job** (12-6 task 2-8, 3-6) — run NL→mutation golden suite and provider compat matrix on schedule. Requires CI runner.
- [x] ~~**Snapshot/regression tests**~~ (8-2 task 6-7, 8-4 task 3-7) — `tests/test_snapshots/` with 8 golden snapshots (5 builder + 3 loader). `UPDATE_SNAPSHOTS=1` regenerates.
- [ ] **Mutation metrics baseline** (12-6 tasks 4-5 through 4-7) — capture 3-5 day baseline on main, set sprint targets, end-of-phase report. Requires production deployment.

### Integration tests requiring real LLM
- [ ] **Error memory integration test** (17-1 tasks 5-4 through 5-6) — workflow fails → errors indexed → query returns relevant results → LLM receives error context. Requires full engine + embedding provider.
- [ ] **Reflection multi-run integration test** (17-2 task 8-3) — failed run → reflection triggered → principles stored → next run's prompt includes principles. Requires real LLM + multi-run orchestration.
- [ ] **Experience consolidation integration test** (19-1 tasks 6-4, 6-5) — multiple runs → experience auto-consolidated → semantic search returns relevant workflows. Requires full engine + embeddings.

### Frontend polish
- [x] ~~**Fuzzy search in mention autocomplete**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [x] **Recently used mentions at top** (12-3 task 8-4)
- [x] **Preview tooltip on mention hover** (12-3 task 8-5)
- [x] ~~**Sortable log columns**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [x] ~~**Token flow edge labels**~~ (18-4 task 3-4) → completed in [22-deferred-wave-2](plans/22-deferred-wave-2.md) S3
- [x] ~~**Before/after token estimation**~~ (18-4 task 4-4) → completed in [22-deferred-wave-2](plans/22-deferred-wave-2.md) S3
- [x] ~~**Per-thread mode persistence**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)
- [x] ~~**Keyboard shortcut to cycle chat modes**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)
- [x] ~~**Debug diff tag**~~ → promoted to [20-1](plans/20-1-docs-quick-wins.md)
- [x] ~~**"Fix this" shortcut**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [x] **Code syntax highlighting in mention context** (12-3 task 3-3)
- [x] ~~**Frontend export buttons**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [x] ~~**Collapse verbose run output**~~ → promoted to [20-2](plans/20-2-chat-editor-polish.md)
- [x] ~~**Analytics rule dashboard**~~ (18-4 task 5-6) → completed in [22-deferred-wave-2](plans/22-deferred-wave-2.md) S3
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
- [x] ~~**Message queuing**~~ (12-5 task 2) — CLI implemented: background `select.select` + queue during streaming, processed after response. Editor re-resolve mentions deferred.
- [ ] **Sandbox execution display** (12-4 task 6) — terminal-like rendering, ANSI colors, file artifacts, resource usage

### Deferred runtime features
- [x] ~~**Loop compaction strategy runtime**~~ (18-3 tasks 3-3, 3-4, 3-5) → completed in [22-deferred-wave-1](plans/22-deferred-wave-1.md) S2
- [x] ~~**Persistent cross-run cache**~~ (18-2 task 2-3) → completed in [22-deferred-wave-1](plans/22-deferred-wave-1.md) S2
- [x] ~~**Memory-aware cache invalidation**~~ (18-2 task 2-5) → completed in [22-deferred-wave-1](plans/22-deferred-wave-1.md) S2
- [x] **Automatic state externalization** (18-3 task 1-3) — scheduler/executor writes loop/foreach/team state to StateStore automatically
- [x] **Run-level advisory token budget** (18-3 task 4-1) — `token_budget` on EngineConfig as global planning signal
- [x] **Principle compaction** (17-2 task 3-4) — PrincipleStore.compact() merges similar principles at threshold
- [x] ~~**ReflectionNode authoring surfaces**~~ (17-2 task 7) — builder DSL `wf.reflection()`, markdown `type: reflection`, both decompilers. Editor palette/config deferred (frontend).
- [x] **Two-tier reference resolution** (18-1 task 4-4) — artifact store + threshold-based ref passing; memory mirroring deferred
- [x] **Encode-to-memory pattern** (18-1 task 4-5) — large outputs stored as MemoryItems, downstream retrieves summary
- [x] **Hyperedge JIT loading** (18-1 task 7-5) — inject hyperedge summaries, load full rules on demand
- [x] ~~**Unified cross-source token budget**~~ (18-3 task 4-4) → completed in [22-deferred-wave-1](plans/22-deferred-wave-1.md) S2
- [x] **Checkpoint/resume for parallel subagents** (7-9 task 3) — capture per-branch completion status, resume pending branches
- [x] **Builder DSL for tools** (7-3 task 8-3) — `wf.tool("name", tool_id="file_read", config={...})` with `config` alias
- [x] **RAG reranking** (9-1 task 6-4) — LLM-based re-scoring of top_k*3 candidates
- [x] **Tier de-escalation telemetry** (18-5 task 4-2) — persist per-node tier success stats across runs, suggest cheaper tiers after repeated success
- [x] ~~**Tier badge in editor** (18-5 task 7-2)~~ — completed (L0–L3 badges + hover tooltip in DanNode)
- [x] ~~**Tier analytics panel** (18-5 task 7-3)~~ — completed (distribution, cost comparison, per-node table in TokenAnalyticsPanel)

### Stretch goals
- [x] ~~**Auto-mode detection**~~ → promoted to [20-3](plans/20-3-checkpoint-ui-automode.md)
- [ ] **Parallel subagent visualization** (7-9 task 5-3) — show parallel branches and fan-in in execution
- [x] **ToolExecutor integration test** (7-3 task 9-4) — end-to-end built-in tool via ToolExecutor
- [x] ~~**Cross-workflow error migration notes**~~ (17-1 task 6-3) — added to `docs/bugs.md`: collection naming, global vs workflow scope, cross-environment migration, principle dedup

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

### Execution intelligence
- [x] **Goal-oriented loop** — ~~set a target metric~~ → completed in [31-6](plans/31-6-goal-oriented-loop.md) (concierge loop + engine GoalLoopNode)
- [x] **Timed / scheduled tasks** — ~~TaskScheduler~~ → completed in [31-7](plans/31-7-scheduled-tasks.md) (cron/interval, lease/lock, NL parsing, result delivery)
- [x] **Plan dependency-graph optimization** — decompose goals into subtask DAG with time estimates, solve resource-constrained project scheduling (critical path + list scheduling heuristic) to minimize makespan under `DAN_MAX_CONCURRENT_LLM` constraint. Wire RCPSP schedule into ParallelSubagents/topological scheduler. Time estimation: heuristic classification + historical experience memory + LLM estimate with calibration.

### Safety & guardrails
- [x] **Completion guard** — ~~pre-delivery validation~~ → completed in [31-9](plans/31-9-completion-guard.md) (response pipeline wiring, skip conditions)
- [x] **PII / sensitive data tokenization** — ~~user-defined sensitive word list~~ → completed in [31-10](plans/31-10-pii-tokenization.md) (ContextVar, code-block skip, edge cases)
- [ ] **Cost hard limits** — `max_cost` per workflow run and per concierge session. Budget enforcer intervenes before each LLM call: downgrade model / skip optional step / checkpoint and ask user. Composes with TierPolicy and model_policy cascade.

### Workflow engine robustness (long-tail tasks)
- [ ] **Node-level checkpointing** — checkpoint after every node completion (not just topological level). Critical for multi-hour workflows.
- [ ] **Long-running workflow profile** — aggressive retry defaults for long-running mode: `max_retries=5`, `backoff=30s`, `backoff_max=1800s`, fallback model, `on_failure="skip"` (complete remaining branches, report partial results).
- [ ] **Wall-clock and cost ceilings on workflow runs** — `max_duration` and `max_cost` on workflow run config. When hit: checkpoint, report partial results, explain what remains.
- [ ] **Self-healing node execution** — on node failure, auto-diagnose via RepairClassifier and adjust (API timeout → retry with backoff, invalid JSON → re-prompt with stricter format, tool error → try alternative approach) before escalating.
- [ ] **Mid-execution adaptation** — modify pending nodes' prompts/config in a running workflow without restarting completed stages. Concierge translates NL change requests into targeted node modifications.
- [ ] **Human-readable workflow progress** — map engine events to natural language stage names and progress fractions. Concierge surfaces: "Completed literature review (3/7 stages). Writing methodology. ~2 hours remaining."
- [ ] **Dynamic topology** — workflow can spawn new branches at runtime based on intermediate results (e.g. "found 4 clusters in data, creating 4 parallel analysis branches").
- [ ] **Cross-workflow coordination** — output of one workflow becomes input of another, concierge as coordinator. "Run data pipeline, then start report workflow with its outputs."

### Continuity & polish
- [x] **Resumable cross-session work** — ~~structured task-level resume~~ → completed in [31-11](plans/31-11-cross-session-resume.md) (auto-populate, compact history, cross-surface resume)
- [x] **Proactive follow-up** — ~~initiative layer~~ → completed in [31-12](plans/31-12-proactive-follow-up.md) (stale-task queue, delivery engine, quiet hours)
- [x] **Multi-surface task continuity** — ~~start a task on desktop~~ → completed in [31-13](plans/31-13-multi-surface-continuity.md) (presence tracking, handoff injection, /sync)
- [x] **Telegram project tracing** — compact `[Project]` header in all Telegram replies (converted from `[DAN - Project]`), combined with `reply_to_message_id` threading. `_format_for_telegram()` replaces `_strip_prefix_and_html()`.
- [x] **Adaptive progress frequency** — exponential backoff for Telegram progress updates (10s → 20s → 30s → ... → max 5min). Configurable via `DAN_TELEGRAM_PROGRESS_MAX_INTERVAL` and `DAN_TELEGRAM_PROGRESS_BACKOFF`. Sends new message instead of editing when conversation has moved on.
- [x] **Classifier: project-status intent fix** — "project review", "project progress", "how's the project going" now correctly route to `STATUS_CHECK` instead of `CONVERSATION`/`FILE_REQUEST`. Context-aware heuristic distinguishes DAN project queries from external topic queries ("Panama canal expansion project status"). Prevents eager file sends on status questions.

### Adapter abstraction & custom frontend
> Today WhatsApp, Telegram, CLI, and the editor each carry their own rendering/routing logic. Extract a shared adapter interface so adding a new surface is minimal wiring. Then build a dedicated DAN web frontend (beyond the workflow editor) for managing bots, runs, projects, memory, and schedules in one place.

- [ ] **Adapter interface extraction** — define a common `SurfaceAdapter` ABC that captures the shared contract across Telegram, WhatsApp, CLI, and editor adapters: message ingestion, reply rendering, media handling, slash-command dispatch, HumanNode resolution, presence tracking. Each existing adapter becomes a thin implementation of this interface. Reduces duplication and makes adding new surfaces (Discord, Slack, web UI) trivial.
- [ ] **Surface capability matrix** — catalog which features each adapter supports (inline keyboards, reactions, file upload, streaming edits, forum topics, voice, polls) vs. graceful degradation. Use this to drive surface hints and feature-flag rendering logic rather than per-adapter `if` branches.
- [ ] **DAN management frontend** — standalone web UI (separate from the workflow editor) for day-to-day operations: bot fleet status/control, active & historical runs, project/task overview, memory browser (facts, preferences, principles, corrections), schedule manager, cost dashboard, notification history. Acts as the "admin panel" that messaging surfaces can't provide.
- [ ] **Unified adapter test harness** — shared test suite that every adapter must pass: message round-trip, slash commands, media, HumanNode prompt/response, concierge routing, error surfacing. New adapters get instant validation.

### Revenue-generating autonomous workflows
> Use DAN's always-on orchestration to run workflows that directly translate tokens/compute into money. Each path should be a self-contained loop that DAN can execute autonomously on a schedule.

- [ ] **Autonomous factor generation** — continuously generate, backtest, and rank quantitative alpha factors (momentum, value, sentiment, alternative data). Loop: ideate factor → code → backtest on CRSP/Compustat → score → persist winners. Pairs with vibe research infra and scheduled tasks.
- [ ] **Kaggle competition strategist** — monitor active Kaggle competitions, auto-scaffold projects, generate/evaluate submissions. Loop: pick competition → EDA → feature engineering → model ensemble → submit → analyze leaderboard feedback → iterate. Use DAN's iterative build/test/diagnose loop.
- [ ] **Crypto mining orchestration** — manage mining operations (pool selection, hardware monitoring, profitability switching). DAN as the control plane for hashrate allocation and profit optimization.
- [ ] **Revenue path discovery** — systematic search for new token-to-money conversion paths. Survey: freelance automation (Upwork/Fiverr bots), content generation pipelines, data labeling/annotation services, API-as-a-service (publish DAN workflows as paid endpoints), algorithmic trading signal subscriptions, automated research reports for sale, SEO/content farms, synthetic data generation. Evaluate each on effort-to-revenue ratio and regulatory risk.

### Future vision
- [x] ~~**Multi-agent group chat**~~ → promoted and completed in [Phase 20](#phase-20--telegram-multi-bot-platform) (Plan 30, Telegram-first). Discord adapter deferred to future phase.
- [x] ~~**In-chat model switching**~~ → promoted to [31-1-model-control](plans/31-1-model-control.md) under Phase 21
- [ ] **Project retrospective distillation** — auto-review completed projects and distill reusable artifacts: generate workflow templates from successful run patterns, extract skills/rules/hyperedges from repeated working patterns, and codify domain-specific conventions. Periodic or on-demand; feeds back into experience memory and planner few-shot examples.
