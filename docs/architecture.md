# Architecture

## Tech Stack

- **Core engine:** Python 3.11+, Pydantic v2, OpenAI SDK
- **Execution server:** FastAPI, uvicorn, websockets
- **Visual editor:** TypeScript, React, React Flow v12 (`@xyflow/react`), Zustand, Tailwind CSS v4, Vite
- **Schema validation:** JSON Schema (typed edges)
- **Shared contract:** versioned graph JSON (`dan_graph_v1`) between Python and TypeScript
- **Testing:** pytest, pytest-asyncio, httpx (ASGI test client); vitest (editor unit tests)

## Directory Structure

```
deep-agent-network/
  docs/                          # All project tracking and documentation
    development-plan.md          # Vision, roadmap, research landscape
    architecture.md              # This file — tech stack, conventions, decisions
    changelog.md                 # Append-only log of completed work
    todo.md                      # High-level task list, links to plan files
    bugs.md                      # Known issues and failed approaches
    llm-api-guide.md             # LLM-facing API reference (auto-updated on API changes)
    plans/                       # Numbered detailed plans (just-in-time); 11 = Phase 7.1 structure review
  src/dan/                       # Python package
    __init__.py                  # Top-level package exports
    models/                      # Phase 0 — formal type system
      ports.py                   # InputPort, OutputPort
      context.py                 # NodeLocalState, SharedContextDeclaration, ArtifactRef, ContextProjection, FeedbackSelector, CompactionRule, policies
      nodes.py                   # NodeBase, LLMOperator, ToolOperator, CodeOperator
      control_flow.py            # GateNode (unified if_else/while), IfElse, WhileLoop, ForEach, ParallelSubagentsNode, OrchestratorNode, Reduce, Router, HumanInTheLoop, ValidatorNode, CompositeNode
      edges.py                   # DataEdge, ControlEdge, ContextEdge
      graph.py                   # Graph container, Node/Edge discriminated unions, dan_graph_v1 contract
    validation/
      schema.py                  # Port schema compatibility (MVP structural check)
      graph.py                   # Graph well-formedness validation
      boundaries.py              # Boundary auto-insert: generate entry/exit ValidatorNodes for composite nodes
    migration/
      gate_migration.py          # Legacy IfElse/WhileLoop → GateNode graph-dict migration helpers
    registry.py                  # NodeTypeRegistry — maps node_type strings to classes
    providers/                   # Phase 4 — multi-provider LLM abstraction
      __init__.py                # LLMProvider protocol, CompletionResult, StreamChunk, ProviderConfig
      openai_provider.py         # OpenAIProvider — wraps AsyncOpenAI (any OpenAI-compatible endpoint)
      anthropic_provider.py      # AnthropicProvider — wraps AsyncAnthropic (optional dep)
      google_provider.py         # GoogleProvider — wraps google.generativeai (optional dep)
      registry.py                # ProviderRegistry — model→provider routing (override→prefix→default)
      costs.py                   # Static COST_PER_1K_TOKENS table + estimate_cost()
    tools/                       # Phase 4 — built-in tool library (dan.tools)
      __init__.py                # get_all_tools() auto-discovery
      _workspace.py              # Workspace root sandboxing utility
      file_read.py               # Read file with line range, size guard
      file_write.py              # Write/append with parent dir creation
      list_directory.py          # List with glob and recursive mode
      web_search.py              # DuckDuckGo search (optional dep)
      web_fetch.py               # URL content fetch via httpx
      http_request.py            # General HTTP client
      shell_command.py            # Subprocess with timeout and allowlist
      pdf_read.py                # PDF text extraction (optional dep)
      text_chunk.py              # Text chunking with overlap
      json_extract.py            # Dot-notation JSON extraction
      regex_match.py             # Regex match/replace
    sandbox/                     # Phase 6 — subprocess sandbox (operational guardrails)
      __init__.py                # SandboxConfig (Pydantic), SandboxResult (dataclass), defaults
      adapters.py                # LanguageAdapter protocol, PythonAdapter, ShellAdapter, ADAPTERS registry
      runner.py                  # SandboxRunner — subprocess exec with timeout, memory limits, env filtering, output truncation
    engine/                      # Phase 1 — async execution engine
      __init__.py                # Public API: Engine, EngineConfig, RunResult, etc.
      state.py                   # NodeStatus, PortDataStore, ExecutionState
      context_runtime.py         # SharedContextStore, ArtifactStore, LocalStateManager, ScopedContextView (Layers 2-4 + boundary isolation)
      executor.py                # EngineConfig, NodeExecutor protocol, ExecutionContext, ExecutorRegistry
      conditions.py              # Safe expression evaluator for IfElse/WhileLoop conditions
      normalizer.py              # OutputNormalizer — JSON extraction, schema validation, re-prompt
      checkpoint.py              # CheckpointStore protocol, FileSystemCheckpointStore
      events.py                  # EngineEvent, EventType — typed runtime events
      memory.py                  # Phase 9A — MemoryEntry, MemoryScope, WriteMode, MemoryWriteRequest models
      memory_store.py            # Phase 9A — MemoryStore protocol, FileSystemMemoryStore (atomic JSON, index sidecar)
      memory_pipeline.py         # Phase 9A — ShortTermMemory buffer, CompactionStrategy activation, ConsolidationPipeline
      scheduler.py               # Topological sort (DAG fast-path + cycle-aware for gate loops), parallel dispatch, Engine.run()/resume(), event emission
    rag/                         # Phase 6 — RAG / knowledge retrieval subsystem
      __init__.py                # EmbeddingProvider protocol, EmbeddingResult, OpenAI/Local providers, EmbeddingRegistry
      indexer.py                 # Indexer — create/populate/manage vector store indexes with chunking + batch embedding
      stores/
        __init__.py              # VectorStore protocol, DocumentRecord, QueryResult, VectorStoreConfig, VectorStoreFactory
        memory.py                # MemoryVectorStore — pure-Python stdlib-only (cosine sim via math), O(n) scan
        faiss_store.py           # FAISSVectorStore — faiss.IndexFlatIP, L2-normalized inner product, persistence, metadata sidecar
        chroma_store.py          # ChromaVectorStore — chromadb.PersistentClient, native metadata filtering
    utils/                       # Phase 9A — shared utilities
      tokens.py                  # estimate_tokens() — tiktoken-backed or character approximation
    executors/                   # Phase 1 — built-in node executors
      __init__.py                # Auto-registers built-in executors
      llm.py                     # LLMExecutor — OpenAI-compatible (vectorengine.ai default)
      tool.py                    # ToolExecutor + ToolRegistry — function dispatch
      code.py                    # CodeExecutor — sandboxed Python exec
      rag.py                     # RAGExecutor — embed query → vector search → chunk retrieval, event emission, store caching
      control_flow.py            # GateExecutor (unified branching/looping), IfElse, WhileLoop, ForEach, ParallelSubagents, Orchestrator, Reduce, Router, HumanInTheLoop
      validator.py               # ValidatorExecutor — rule-based data validation with valid/invalid routing
    builder/                     # Phase 1.5 — fluent workflow builder DSL
      __init__.py                # Public API: workflow(), WorkflowBuilder, NodeRef, PortRef, decompile(), namespace_graph, derive_ports
      refs.py                    # NodeRef, PortRef — compile-time proxies with __format__, __rshift__, __getitem__
      builder.py                 # WorkflowBuilder — node creation, edge registration, context managers, import_workflow()
      compiler.py                # Compile builder state -> Graph model (marker resolution, port/edge generation)
      importer.py                # namespace_graph(), derive_ports() — import pre-built Graph as composite node
      decompiler.py              # Graph -> Python builder code string (for visual editor round-trip)
    loader/                      # Phase 5 — markdown authoring surface (workflow.md + agent .md files)
      __init__.py                # Public API: load(), load_agents(), compile_workflow()
      models.py                  # Parsed markdown IR: AgentSpec, WorkflowSpec, FlowStatement, PortSpec
      parser.py                  # Markdown parser (frontmatter, sections, ports, context, flow extraction)
      flow_parser.py             # Flow-line parser (chain / each / loop / if)
      types.py                   # Port schema inference + linked-schema loading
      compiler.py                # Markdown→Graph compiler (agent→node, flow→edge, auto-wiring, InputNode, diagnostics)
      decompiler.py              # Graph→Markdown decompiler (node→agent.md, edge→flow, round-trip)
      diagnostics.py             # Diagnostic, CompileResult, DecompileResult, format_diagnostics()
    server/                      # Phase 2 — FastAPI backend for visual editor
      __init__.py
      __main__.py                # CLI entry point: `dan-serve` / `python -m dan.server`
      app.py                     # FastAPI application — CRUD, runs, WebSocket, built-in tool registry
      exec.py                    # execute_python() — shared Python executor for run_python and run_strategy_script
      graph_store.py             # Filesystem-based graph JSON persistence
      graph_mutator.py           # GraphMutator: applies MutationPlan (add/remove/edit nodes+edges) to graph dicts with transactional semantics + dry-run; TOOL_PORT_MANIFESTS for tool-specific port declarations; ApplySkill mutation op
      skill_library.py           # SKILL_LIBRARY: domain-specific prompt-injection skills (management_science_writing, informs_latex_style) targeted by node tags
      chat_manager.py            # ChatManager: graph-aware LLM conversations, function-calling for graph mutations (MUTATION_TOOL_SCHEMA), text-streaming fallback, context window management (MODEL_CONTEXT_WINDOWS, estimate_tokens, compact_history)
      chat_store.py              # Filesystem-based chat persistence (per-workflow threads)
      run_manager.py             # Background run execution + event pubsub + catch-up + ToolRegistry injection + human-input registry + streaming coalescing + RunStore integration + metric enrichment
      run_store.py               # Filesystem-backed persistence for run summaries (JSON) and event logs (JSONL). Layout: runs/{workflow_id}/{run_id}.json + .events.jsonl
      scoped_run.py              # Scoped execution: full/node/subgraph run builder
      layout.py                  # Topological layout for graph JSON (DAN_LAYOUT_ON_LOAD)
      mutation_metrics.py        # Mutation quality metrics for chat/LLM feedback
  editor/                        # Phase 2+3.5 — React Flow visual editor
    package.json                 # Dependencies: react, @xyflow/react, zustand, tailwindcss, dagre, allotment, highlight.js, lucide-react
    vite.config.ts               # Vite config: Tailwind plugin, /api proxy to backend
    tsconfig.json                # TypeScript config
    src/
      types/graph.ts             # TypeScript types mirroring dan_graph_v1 + NODE_DESCRIPTIONS
      types/chat.ts              # ChatMessage, ChatThread, ChatStreamEvent types
      lib/graphAdapter.ts        # Bidirectional DAN <-> React Flow conversion + EDGE_COLORS + edge labels
      lib/api.ts                 # HTTP/WebSocket API client
      lib/paletteTemplates.ts    # Extensible template factories (ReAct, Plan-Execute)
      lib/connectionValidation.ts # isValidConnection — no self-connect, no duplicates
      lib/graphImporter.ts       # Workflow-as-node: converts saved graph into CompositeNode with autonomous-entry filtering, node-aware port mappings, entry/exit validation
      lib/layout.ts              # Auto-layout via dagre (LR direction)
      lib/nodeIcons.tsx          # Inline SVG icons for all 16 node types
      lib/mentionParser.ts       # @mention serialization (`@[name](type:id)`), parsing, cursor detection, co-navigation dispatch, type colors
      lib/graphDiff.ts           # Before/after graph diff computation
      lib/portOrdering.ts        # Deterministic port ordering (orderPorts, computePortReorder) for DanNode display
      store/useGraphStore.ts     # Zustand store — graph, selection, run state, events, layers, toasts, timings, clipboard, history, port ops, loop iterations, streaming, human input, workflow import
      hooks/useKeyboardShortcuts.ts # Keyboard shortcuts: save, undo/redo, copy/paste/duplicate
      components/DanNode.tsx     # Custom node: port handles, status ring, pulse/glow, duration badge, icons, dimming, inline rename, loop badges/counters
      components/AnimatedEdge.tsx # Custom edge: particle flow on active edges, dimming on inactive
      components/NodePalette.tsx  # Searchable categorized sidebar: templates, edge selector, hover previews, saved workflows
      components/ConfigPanel.tsx  # Node/edge property editor, port editor (add/rename/delete), SchemaEditor (visual + raw JSON)
      components/ContextMenu.tsx  # Right-click context menu: canvas/node/edge actions (paste, copy, delete, edge type)
      components/GraphCanvas.tsx  # Main canvas: drop handling, drill-in, validation, animated edges, context menu, edge reconnection
      components/EditorToolbar.tsx # Merged toolbar: graph selector + run controls + auto-layout
      components/TabBar.tsx       # Horizontal workflow tabs with run status badge, close, "+ New" template picker
      components/CommandPalette.tsx # Cmd+K modal: search nodes by name/type, center viewport on select
      components/LoopGroupNode.tsx  # Collapsed/expanded loop group visualization (visual-only node)
      components/RunInputsDialog.tsx # Modal for collecting entry-point input variables before run
      components/BreadcrumbBar.tsx # Layer navigation: Root > Node1 > Node2
      components/PortMappingOverlay.tsx # Input/output port mapping display when drilled in
      components/LogPanel.tsx     # Rich structured logs: grouped by node, icons, filtering, click-to-select
      components/ExecutionTimeline.tsx # Horizontal timeline bar with per-node segments
      components/OutputPreview.tsx    # Per-node output viewer with streaming text support
      components/HumanInputDialog.tsx # Modal popup for mid-run human-in-the-loop input submission
      components/MentionAutocomplete.tsx # Floating @ mention dropdown: nodes/workflows/subgraphs, keyboard nav, fuzzy filter
      components/ChatPanel.tsx        # Resizable chat sidebar: message send/stream, @ mention integration, mutation event handling + GraphDiffPreview
      components/ChatMessage.tsx      # Message bubble: markdown render, mention chips with click-to-navigate, tool call cards, run output blocks
      components/ToolCallCard.tsx     # Expandable tool call card: status icon, args/output sections, operations list, duration badge
      components/RunOutputBlock.tsx   # Structured run output: per-node status, collapsible output, timing, "View logs" / "View in History" links
      components/RunHistoryPanel.tsx  # Run history bottom panel tab: filterable run list, event replay view, side-by-side comparison, deep-link support
      components/GraphDiffPreview.tsx  # Mutation diff preview modal: accept/reject/partial-accept
      components/ToastContainer.tsx   # Fixed bottom-right toast notifications
      components/Spinner.tsx          # Reusable loading spinner
      components/RunPanel.tsx         # (deprecated — merged into EditorToolbar)
      components/GraphSwitcher.tsx    # (deprecated — merged into EditorToolbar)
      App.tsx                    # Main layout: toolbar + palette + canvas + panels + toasts
  examples/                      # Phase 3+ — runnable workflow scripts
    paper_writing.py             # INFORMS-oriented workflow: internet-grounded lit search, human interview loop, parallel section drafting, multi-role review, LaTeX/PDF packaging
    paper_writing_md/            # Markdown rewrite of paper-writing pipeline (Phase 5 validation fixture)
    simple_chain.py              # Phase 4 template: 3-node linear pipeline (LLM→LLM→Code)
    fan_out_fan_in.py            # Phase 4 template: ForEach + Reduce parallel processing
    review_revise.py             # Phase 4 template: GateNode while-loop draft→review→revise
    rag_qa.py                    # Phase 4 template: tool-based RAG Q&A (no vector DB)
    react_agent.py               # Phase 4 template: ReAct agent loop with web tools
  tests/                         # pytest suite (771 passed, 15 skipped)
    test_models/                 # Unit tests for all model types
    test_validation/             # Validation logic tests
    test_examples/               # Paper-writing motivating example + e2e tests
    test_engine/                 # Engine unit + integration tests
    test_builder/                # Builder DSL unit + integration tests
    test_loader/                 # Markdown loader parser/compiler/type-inference tests
    test_migration/              # Migration helper tests (legacy → gate)
    test_server/                 # Server API, run manager, and event tests
  graphs/                        # Saved graph JSON files (filesystem persistence)
  pyproject.toml                 # Pydantic v2 + OpenAI SDK + FastAPI + uvicorn + httpx + pytest; optional: anthropic, google-generativeai, pypdf, duckduckgo-search
  README.md                      # User-facing project overview, quick start, feature summary
  .env.example                   # Environment variable template
  .cursor/rules/                 # AI agent rules
```

## Import and API Conventions

- **Top-level `dan`:** Exposes models (InputPort, OutputPort, node/edge types, Graph, NodeTypeRegistry). Does not expose Engine, builder, loader, or mutator.
- **Subpackages:** `dan.engine` (Engine, EngineConfig, RunResult, ExecutionContext, etc.), `dan.builder` (workflow, decompile, NodeRef, PortRef), `dan.loader` (load, compile_workflow), `dan.validation` (internal). Public vs internal is implicit — `dan.engine.scheduler` is importable but not re-exported at package level.
- **Graph schema:** snake_case everywhere (Python models, JSON, TypeScript graph.ts). REST/WebSocket payloads use snake_case; chat message format uses camelCase↔snake_case conversion at API boundary.

## Core Abstractions

### Object Design Principles (NodeBase)

All 16 node types inherit from `NodeBase` with fields: `id`, `name`, `description`, `input_ports`, `output_ports`, `position`, `ui`, `metadata`, `retry_policy`, `read_set`, `write_set`. Composite/loop nodes override `read_set`/`write_set` for context declarations. GateNode and ValidatorNode use `model_post_init` to set default output ports when empty. InputNode uses `variables` instead of `input_ports` for external inputs. Sub-graph keys follow `{parent_id}__body` or `{parent_id}__{branch_name}`.

### Two-Level Node Model (inspired by AFlow)

- **Operator (atomic):** Single LLM call, API call, code execution, database query, or conditional. The fundamental unit. Each operator independently specifies its model.
- **Agent (composite):** A group of operators wired into a sub-graph that behaves as a single unit with a defined interface (input schema → output schema). Inspectable — double-click to zoom into internal graph.

### Typed Edges (inspired by supply chain management)

| Edge Type | Purpose |
|-----------|---------|
| **Data edge** | Structured output of node A feeds node B. Validated with JSON Schema at design time. |
| **Control edge** | Conditional routing (if/else), loops (for-each, while), parallel fan-out/fan-in, retry logic. |
| **Context edge** | Shared memory or state (conversation history, accumulated knowledge, file system) readable/writable by multiple agents. |

### Control-Flow Primitives

| Primitive | Behavior |
|-----------|----------|
| If/Else | Route based on condition evaluated on upstream data |
| While Loop | Repeat until condition met or max iterations reached |
| For-Each / Map | Fan-out: apply sub-graph to each item in a list, in parallel |
| Parallel Subagents | Fan-out: run heterogeneous sub-graphs concurrently, merge at fan-in |
| Reduce | Fan-in: aggregate results from parallel branches |
| Router | LLM-powered routing — model decides which branch |
| Human-in-the-Loop | Pause execution, wait for human input, resume |

### Model Heterogeneity

Each operator node independently specifies its model. Cheap/fast for classification, strong for reasoning, code-specialized for generation. First-class design principle, not afterthought.

### Output Normalization (built-in)

Like batch normalization in DNNs, every LLM operator has a deterministic, built-in output normalization layer: parse → validate against output schema → re-prompt with error on failure → retry up to N times. This is automatic (not a user-visible node) and guarantees every data edge carries schema-valid data or an explicit error.

### Error Handling / Retry Policy

Every operator carries a `retry_policy`: `max_retries`, `backoff`, `backoff_max`, `fallback_model`, `on_failure` (error / skip / halt). Separate from output normalization — this handles call-level failures (rate limits, timeouts, network errors). `on_failure="halt"` stops the engine at the current topological level (already-running parallel nodes finish) and writes a checkpoint for later resume.

### Checkpointing / Resumability

After each topological level completes, the engine persists outputs, artifact state, context store snapshot, and execution pointer. On restart, resumes from the last completed level.

### Four-Layer Context Model

Direct edge data handles simple input/output. Growing payloads, shared state, and dynamic updates are handled by four distinct layers:

| Layer | What It Holds | Scope | Mutability |
|-------|--------------|-------|------------|
| **1. Edge Data** | Typed, bounded payloads on data edges | Between two nodes | Immutable per edge |
| **2. Node-Local State** | Private working memory (iteration history, convergence metrics) | Scoped to a composite agent / loop | Mutable within scope, invisible to parent |
| **3. Shared Context Store** | Namespaced key-value blackboard (`context.outline`, `context.bibliography`) | Graph-wide, opt-in via declared `read_set` / `write_set` | Mutable; write modes: `write`, `append` |
| **4. Artifact Store** | Large objects (drafts, datasets, figures) stored by reference | Graph-wide | Immutable (new version per revision) |

> **Layer 2 active usage:** `LocalStateManager` is now used for loop-scoped state in while-gate loops (Plan 7-6). When `GateNode.state_schema` is present, the scheduler maintains a state bag via `LocalStateManager` scoped to the gate — body nodes receive state fields as regular inputs and outputs matching `state_schema` keys are merged back into scope automatically.

- **Code node port defaults (7-6):** `CodeExecutor` injects type-appropriate defaults for missing optional input ports based on `json_schema` (array→[], object→{}, number→0, string→"", boolean→False). Eliminates `try/except NameError` boilerplate.
- **Spread edges (7-6):** `DataEdge` with `spread=True` destructures source dict fields into target node input ports. One edge replaces many scalar edges for struct passthrough.

### Context Projection

At every scope boundary (entering a sub-graph, entering a loop iteration), a **projection function** extracts only what the next consumer needs. Each consumer gets a minimal view — the loop controller sees only iteration count + convergence metrics, the reviser sees only current draft + latest comments, the parent graph sees only the final output.

### Composite Node Contract

Every composite/loop node declares:
- `external_input_schema` / `external_output_schema` — what the parent sees
- `control_state` — iteration count, stop flags, thresholds (loop controller only)
- `local_working_set` — latest working data, not full history
- `read_set` / `write_set` — declared dependencies on shared context store (composite/loop nodes; atomic operators inherit from NodeBase for context-edge targets)
- `compaction_rule` — how local history is summarized between iterations
- `feedback_selector` — `FeedbackSelector(include/exclude/rename/transform)` on `GateNode`/`WhileLoopNode` controls which body outputs cycle back vs. become side-effect artifacts; `artifact_ports` is sugar for extracting and accumulating named ports across iterations

### Context Policies

- **Mutation**: nodes read shared context by default; writes require declaration
- **Parallel merge**: fan-out branches must specify merge rules (append, last-write-wins, or reducer node)
- **Compaction**: configurable per composite node (sliding window, summarization gate, diff-based)
- **Failure exits**: `max_iterations`, `stagnation`, `timeout`

### Context Scoping Across Agent Boundaries

The four-layer context model describes *what kinds* of context exist. Context *scoping* describes *where* context is visible when agents are nested (agents containing sub-agents containing sub-sub-agents).

Four scopes govern visibility at every nesting level:

| Scope | Analogy | Direction | What It Holds |
|-------|---------|-----------|---------------|
| **global** | Global variable | Everywhere (read by all layers) | Codebase index, conversation history, workspace config, rules |
| **local** | Local variable | Stays at current layer | Working memory, retry counts, loop counters, chain-of-thought |
| **pass_down** | Function arguments | Parent → child | Task description, relevant files, constraints, plan |
| **emit_up** | Return value | Child → parent | Result summary, status, discovered signals |

**`pass_down` is explicit, not inherited.** A parent doesn't dump its local context to children. Each child declares an input schema — only what it needs crosses the boundary. This prevents context pollution.

**`emit_up` is explicit, not leaked.** A child returns a structured output, not its entire working memory. The parent decides what to do with it. This prevents noise.

**`global` is read-heavy, write-careful.** Most nodes only read global context. Writes need declaration and conflict resolution (especially during parallel fan-out).

**`local` is invisible outside.** Bulk of working memory. Dies when the agent finishes.

#### Upward Signals

Not everything emitted upward has the same semantics:

- **Results** — the expected structured output. Schema-validated. Consumed by the immediate parent.
- **Signals** — unexpected discoveries that higher layers should know about. Two sub-types:
  - **Sticky signals** — written to global context (everyone should know). Example: "this codebase uses pnpm, not npm."
  - **Non-sticky signals** — propagate up one layer. The parent decides whether to act, relay further, or discard. Example: "circular import detected in module X."

#### Agent Boundary Contract (revised)

Every agent (composite node) formalizes its boundary:

```python
agent PaperWriter:
  accepts:       { topic: str, papers: Paper[], data: Dataset }   # pass_down schema
  returns:       { draft: LaTeX, figures: Fig[], bib: BibTeX }    # emit_up schema
  reads_global:  [codebase_index, style_rules]                    # global dependencies
  writes_global: []                                               # global mutations
  signals:       [quality_warning, missing_data, style_violation] # possible upward signals
```

This supersedes the earlier composite node contract for cross-layer communication. The original `external_input_schema` / `external_output_schema` / `read_set` / `write_set` still apply for the within-graph four-layer model; the boundary contract adds `signals` and clarifies directional semantics.

### Hyperedges: Skills and Rules

Standard edges connect two nodes. **Hyperedges** connect an arbitrary subset of nodes simultaneously. Skills and rules are modeled as hyperedges — graph-level constructs that apply to multiple nodes at once.

```
            ┌──────────────────────────────────┐
            │  "INFORMS Style Guide" (skill)   │  ← hyperedge
            └──┬──────────┬───────────┬────────┘
               ↓          ↓           ↓
         [section-draft] [citation-fmt] [latex-compile]
```

#### Hyperedge Types

| Type | Semantics | Execution Hook | Example |
|------|-----------|----------------|---------|
| **Skill** | Adds knowledge/capability to attached nodes | `pre_prompt` — injected into LLM context | "Scientific writing conventions" |
| **Rule (guardrail)** | Constrains behavior | `post_output` + `validation` — checks output | "Never use GPT-3.5 for final output" |
| **Rule (style)** | Enforces consistency | `pre_prompt` — style context injected | "APA 7th edition citations" |
| **Rule (override)** | Intercepts/rewrites | `tool_call` — modifies or blocks tool invocations | "All shell commands require approval" |

#### Attachment Scope

Hyperedges attach to nodes by:

- **Node ID** — specific node (`attach_to: ["section-draft-1"]`)
- **Node type** — all nodes of a type (`attach_to_type: "llm_operator"`)
- **Tags** — user-defined labels (`attach_to_tags: ["writing", "review"]`)
- **Subgraph** — all nodes within a composite (`attach_to_subgraph: "paper-writer"`)

Inheritance: hyperedges on a parent graph propagate to sub-graphs unless explicitly excluded.

#### Precedence

When multiple hyperedges attach to the same node, they compose in order: `policy > rule > skill`. Within the same type, more specific scope wins (node ID > tag > type > subgraph).

#### Why Hyperedges, Not Context Edges

Context edges (Layer 3) carry *data* — key-value pairs that nodes read/write. Hyperedges carry *behavior modifiers* — they change how nodes execute, not what data they consume. A skill doesn't add a key to the shared context store; it modifies the prompt of every node it's attached to. This is a fundamentally different concern.

### HumanNode (Generalized)

The Human-in-the-Loop control-flow primitive is generalized into a first-class node type: `HumanNode`. The human is not outside the graph talking *to* it — the human is a node *in* the graph.

**Interface:** Same as any other node — typed input schema (what to show the human) and typed output schema (what the human provides).

**Behavior:** Execution pauses at a HumanNode. The rendering layer (chat panel, web UI, CLI) presents the input and collects the output. Execution resumes.

**Implications:**

- **Chat is rendering.** The chat panel is a view that renders whichever HumanNode is currently active. Message appears → human types → output flows to the next node.
- **Adjustable autonomy is topology.** Full autopilot = no HumanNodes in the graph. Careful oversight = HumanNode between every agent. Approve only final output = one HumanNode at the end. This is a graph design decision, not a mode switch.
- **Background mode = zero HumanNodes.** A background agent is just a graph with no human nodes. "Check in every N steps" is a HumanNode inside a while-loop with a counter-based conditional.
- **Multi-point interaction.** Different HumanNodes ask different things. One asks "which papers?", another asks "approve this figure?", another asks "accept this draft?". The rendering layer sequences them.
- **Rendering is decoupled.** The same graph runs behind a CLI, a web app, a VS Code extension, or a Jupyter notebook. The rendering surface resolves HumanNode I/O; everything else is identical.

```
┌─────────────────────────────────────────────────────────┐
│                    DAN Graph                             │
│                                                          │
│  Nodes:   [Human] [LLM Operator] [Tool Op] [Agent]     │
│  Edges:   data ──→  control ──→  context ──→            │
│  Hyperedges:  ═══ skills ═══  ═══ rules ═══             │
│                                                          │
└─────────────────────────────────────────────────────────┘
         ↕ render                    ↕ render
   ┌────────────┐            ┌──────────────┐
   │ Chat Panel  │            │ React Flow    │
   │ (human I/O) │            │ (graph viz)   │
   └────────────┘            └──────────────┘
```

### Four Top-Level Agents Architecture

For application-level systems (coding assistants, research IDEs), a practical architecture is four independent top-level agents sharing a common context layer:

```
┌───────────────────────────────────────────────────────┐
│              Shared Context Layer                      │
│  (codebase index, conversation history, file state,   │
│   linter output, workspace config, rules, skills)     │
├─────────────┬─────────────┬────────────┬──────────────┤
│  Ask Agent  │ Agent Mode  │Debug Agent │ Plan Agent   │
│  (Q&A       │ (ReAct +    │(hypothesis │ (tree search │
│   graph)    │  tools +    │ driven +   │  + outline   │
│             │  fan-out)   │ auto-diag) │  generation) │
└─────────────┴─────────────┴────────────┴──────────────┘
     each is a complex DAN sub-graph internally
```

The shared context layer is **not** part of any graph. It's a read/write store that all four agents access. Each agent internally is a full DAN network with its own working memory and control flow.

**Why four:** These represent fundamentally different control-flow patterns (linear Q&A vs. ReAct loop vs. hypothesis-driven diagnosis vs. tree search), different tool sets, and different stopping conditions.

**Mode switching:** Serialize the active agent's relevant outputs to the shared context layer → activate the new agent → it reads from shared context on startup. The conversation history carries over; the internal working memory does not.

**Context model:**
- **Global** (shared context layer) — codebase index, conversation history, workspace config, session state. All agents read; writes are declared.
- **Local** (within each agent) — the agent's DAN sub-graph manages its own working memory, loop state, intermediate results. Private. Dies when the agent finishes or the user switches modes. Only durable outputs (file changes, conversation messages, plan artifacts) persist to global.
- **pass_down / emit_up** — standard directional scoping within each agent's internal sub-graph.

## Execution Engine (Phase 1)

### Engine API

```python
from dan.engine import Engine, EngineConfig

config = EngineConfig(
    llm_base_url="https://api.vectorengine.ai/v1",
    llm_api_key="...",
    llm_default_model="claude-sonnet-4-6",
)
engine = Engine(config)
result = await engine.run(graph, inputs={"idea": "..."})
result = await engine.resume(graph, run_id="abc123")
```

### Scheduling

- Async-first: `Engine.run()` is async; parallel fan-out uses `asyncio.gather()`
- Kahn's algorithm groups nodes into topological levels; nodes in the same level execute concurrently
- Cycle-aware scheduling for `GateNode(while)` back-edges: detects gate-controlled cycles, iterates cycle regions bounded by `max_iterations`, DAG fast-path preserved for non-cyclic graphs
- Input injection is virtualized per node (`__input__<node_id>`). Scheduler maps these values into both standard `input_ports` and `InputNode.variables` so `Engine.run(inputs=...)` reaches workflow InputNodes.
- While-gate `continue/loop` routing is phase-aware: loop bodies wait for the initial gate signal, then consume virtual loop-feedback injections during subsequent iterations.
- Sub-graph execution is recursive: WhileLoop/ForEach/Composite executors call back into the scheduler
- Legacy `IfElseNode`/`WhileLoopNode` continue to work (with deprecation warnings); migration helpers in `dan.migration` convert to gate patterns

### Executor Protocol

- `NodeExecutor` is a `Protocol` with `async execute(node, inputs, context) -> NodeResult`
- `ExecutorRegistry` maps `node_type` strings to executor instances; users can register custom executors
- Built-in executors for all 16 node types (including `CompositeExecutor`, `ParallelSubagentsExecutor`, `OrchestratorExecutor`, `RAGExecutor`, `ValidatorExecutor`) auto-registered on Engine creation

### LLM Integration

- **Multi-provider dispatch:** `ProviderRegistry` routes model names to the correct API. Resolution: exact `model_provider_map` override → prefix pattern match (`gpt-*`→OpenAI, `claude-*`→Anthropic, `gemini-*`→Google) → `default` fallback (OpenAI-compatible endpoint).
- **Built-in providers:** `OpenAIProvider` (any OpenAI-compatible endpoint, default), `AnthropicProvider` (optional), `GoogleProvider` (optional). Provider SDKs are optional deps.
- **Backward compatible:** `EngineConfig.llm_api_key` + `llm_base_url` auto-create a `"default"` provider. Existing vectorengine.ai setup works unchanged.
- Output normalization built into LLM executor: extract JSON -> validate against schema -> re-prompt with error -> retry
- Transient API errors (rate limits, timeouts) retried with configurable `retry_policy`
- **Cost estimation:** static `COST_PER_1K_TOKENS` table in `providers/costs.py` covering major models. `estimate_cost()` utility function. Best-effort — unknown models return None.

### Built-in Tools (`dan.tools`)

- 11 batteries-included tools: file I/O (sandboxed to workspace root), web search/fetch, HTTP requests, shell commands (allowlist-enforced), PDF reading, text chunking, JSON extraction, regex matching
- Auto-registered during server lifespan via `ToolRegistry.register_builtin_tools()` — custom tools can override built-in IDs
- Graceful degradation: optional SDK tools (pypdf, duckduckgo-search) skip with warning if SDK not installed
- Workspace root sandboxing: all file tools enforce `DAN_WORKSPACE_ROOT` boundary

### Tool design (Plan 7-5)

- **Generic over domain-specific:** State-of-the-art IDEs (Cursor, Claude Code) use a single generic execution tool; the model generates code, the tool runs it. `run_python(code, **context)` executes model-generated Python with injected context; returns `{ result, stdout, stderr }`. Replaces hardcoded `plot_backtest`/`save_grid_csv` (deprecated). Shared executor in `dan.server.exec`.

### Condition Evaluation

- IfElse/WhileLoop `condition` strings evaluated as Python expressions via restricted `eval()`
- No `__builtins__`; whitelist of safe functions (len, min, max, all, any, etc.)
- Variables populated from upstream port data

### Checkpointing

- `CheckpointStore` protocol with filesystem default (`FileSystemCheckpointStore`)
- Checkpoint written after each topological level completes
- `Engine.resume()` loads checkpoint and continues from pending nodes

## Workflow Builder API (Phase 1.5)

### Builder DSL

```python
from dan.builder import workflow, decompile

paper = workflow("paper_writing")
ideas = paper.llm("idea_gen", model="claude-opus-4", prompt="Generate ideas about {topic}")
outline = paper.llm("planner", prompt=f"Create outline for: {ideas}")
ideas >> outline
graph = paper.build()  # -> validated Graph (dan_graph_v1)
code = decompile(graph)  # -> executable Python that reconstructs the graph
```

### Four Connection Mechanisms

1. **f-string magic**: `prompt=f"Use: {ideas}"` — `NodeRef.__format__` emits a compile-time marker `<<dan:node_id:port>>`. The compiler parses prompts, creates DataEdges, and replaces markers with sanitized input port aliases.
2. **`>>` operator**: `a >> b` — DataEdge from default output to default input. Chainable: `a >> b >> c`.
3. **PortRef passing**: `items=node["port"]` — subscript on NodeRef returns PortRef, resolved at compile time.
4. **Explicit edge**: `wf.edge(a["out"], b["in"])` — fully explicit port-to-port wiring.

Builder also supports typed non-data edges: `wf.control_edge(...)` and `wf.context_edge(...)`, plus graph-level artifacts via `wf.artifact_ref(...)`.

### Sub-Graph Context Managers

```python
with wf.while_loop("loop", condition="x < 5", max_iterations=10) as body:
    body.llm("step", ...)
with wf.for_each("fan", items=node["items"], parallelism=4) as body:
    body.code("proc", ...)
with wf.composite("block") as sub:
    sub.llm("inner", ...)
```

### Node-Type Output Contract Map

Each node type has a known default output port matching the runtime executor (e.g., `llm_operator` -> `text`, `for_each` -> `results`, `if_else` -> `branch`). The compiler uses this map for `>>` wiring and f-string marker resolution. **Mode-aware gate defaults (7-8):** While-mode gates use `continue` (not `true`) for chain wiring; if_else gates use `true`.

### Decompiler

`decompile(graph: Graph) -> str` produces an executable Python module string. Topological sort with deterministic ordering, chain detection for `>>` sugar, context managers for sub-graph nodes, `NodeRef` wrappers for sub-graph edge wiring. Preserves `ui`, `metadata`, `shared_context`, and all edge types.

### Workflow pipeline hardening (7-8)

- **Strict parse mode:** `compile_workflow(path, strict=True)` treats flow parse failures and ambiguous bare-edges as fatal (default `strict=False` for backward compat). Recommended for LLM-generated workflows.

## Visual Editor Backend (Phase 2)

### Server Architecture

Local full-stack: FastAPI backend + React Flow frontend. Runs locally like Jupyter — `dan-serve` or `python -m dan.server` starts the server, open `localhost:8000` in browser.

### Engine Event System & Per-Node Logs

- 14 typed events: `run_started`, `run_completed`, `run_failed`, `node_started`, `node_completed`, `node_failed`, `node_skipped`, `node_output`, `log`, `llm_thinking`, `tool_call_started`, `tool_call_result`, `code_output`, `intermediate_text`
- Opt-in `event_callback` parameter on `Engine` constructor — no events emitted if not set (backward compatible)
- `ExecutionContext.emit_event()` — executors emit rich events (LLM thinking, tool calls, code output) during execution. The engine automatically tags every emitted event with the active `node_id`.
- **Per-Node Log Aggregation:**
  - **Storage:** `RunStore` persists all raw events sequentially to `{run_id}.events.jsonl`, inherently preserving the `node_id` association for every token, tool call, and state change.
  - **Editor Log Panel:** `LogPanel.tsx` groups the event stream by `node_id` (falling back to `"__run__"`). This creates a collapsible, node-centric timeline where all interleaved execution outputs (e.g. parallel branches) are cleanly segregated by their source node.
  - **Chat Run Output:** `RunOutputBlock.tsx` derives a condensed per-node status list from the stream, selectively parsing `node_started`/`completed`/`failed`/`output` events to show high-level node progress and final output snippets directly in the chat, while providing deep-links to the full per-node log history.
- Sub-graph events use parent `run_id` (unified stream) — `_run_subgraph` inherits parent state's run_id
- Events are fire-and-forget; callback failures never break execution

### Run Manager

- Executes `Engine.run()` / `Engine.resume()` as asyncio background tasks
- Accepts `ToolRegistry` — creates `ExecutorRegistry` with pre-configured `ToolExecutor` per run so Engine inherits server-registered tools
- Multiplexes events to WebSocket subscribers via async queues
- Catch-up snapshot on subscribe: current node statuses + buffered recent events (latest 500, rolling window)
- Tracks active/completed runs with status snapshots
- Built-in tools registered in `app.py` lifespan: `save_paper`, `search_papers`, `citation_verifier`, `check_latex_deps`, `compile_latex`, `package_submission` (paper-writing workflow)
- `compile_latex` hardening: auto-bootstrap `informs3.cls` into `output/`, normalize LaTeX preamble for `plainnat` compatibility (`hyperref`, `\newblock`), and auto-fill missing BibTeX citation keys with placeholder entries before `pdflatex`/`bibtex` passes

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/graphs` | List all graphs + last opened |
| POST | `/api/graphs` | Create new graph |
| GET | `/api/graphs/{id}` | Load graph JSON |
| PUT | `/api/graphs/{id}` | Save graph JSON |
| DELETE | `/api/graphs/{id}` | Delete graph |
| POST | `/api/graphs/{id}/nodes/{nid}/add-boundary-validators` | Insert entry/exit validator nodes around a composite |
| POST | `/api/graphs/{id}/apply-mutation` | Apply chat-generated mutation plan (GraphMutator.apply), persist, return new graph |
| POST | `/api/graphs/{id}/validate` | Validate graph (design-time checks), return errors/warnings |
| GET | `/api/graphs/{id}/export/markdown` | Export graph as markdown workflow |
| GET | `/api/graphs/{id}/export/python` | Export graph as Python builder code |
| GET | `/api/metrics/mutations` | Get mutation quality metrics (apply_success_rate, etc.) |
| POST | `/api/metrics/mutations/reset` | Reset mutation metrics |
| GET | `/api/rag/collections` | List RAG collections |
| POST | `/api/rag/collections` | Create collection with documents |
| GET | `/api/rag/collections/{name}/stats` | Collection stats |
| POST | `/api/rag/collections/{name}/documents` | Add documents |
| DELETE | `/api/rag/collections/{name}` | Delete collection |
| POST | `/api/runs` | Start execution |
| POST | `/api/runs/{id}/resume` | Resume checkpointed run |
| GET | `/api/runs/{id}` | Get run status snapshot |
| GET | `/api/runs` | List all runs |
| WS | `/api/runs/{id}/events` | Live event stream |
| POST | `/api/chat/message` | Send chat message, get streaming response |
| WS | `/api/chat/{channel_id}/events` | Chat token streaming |
| GET | `/api/chats/{workflow_id}` | List chat threads |
| GET | `/api/chats/{workflow_id}/{thread_id}` | Load chat thread |
| POST | `/api/chats/{workflow_id}` | Create chat thread |
| PUT | `/api/chats/{workflow_id}/{thread_id}` | Update chat thread |
| DELETE | `/api/chats/{workflow_id}/{thread_id}` | Delete chat thread |
| POST | `/api/runs/scoped` | Start scoped run (full/node/subgraph) |

### Graph Persistence

- Filesystem-based: JSON files in `./graphs/` directory
- `last_opened` tracking for auto-load on editor open
- `dan_graph_v1` JSON contract unchanged — the backend reads/writes the same format
- **Server-side layout** — `GET /api/graphs/{id}?layout=true` (or `DAN_LAYOUT_ON_LOAD=1`) applies topological layout. Optionally flattens while-loop body composites (`flatten_loop_bodies`) for a flat view; disable with `DAN_FLATTEN_LOOP_BODIES=0`. No example-specific logic.

## Visual Editor Frontend (Phase 2)

### DAN <-> React Flow Adapter

Bidirectional conversion layer (`graphAdapter.ts`):
- DAN `input_ports`/`output_ports` map to React Flow handles via `port:<name>` ID convention
- 3 edge types visually differentiated: data (indigo), control (amber), context (emerald, animated)
- All 16 node types rendered through a single `DanNode` custom component with per-type color coding
- Node execution status shown as colored rings (yellow=running, green=completed, red=failed)

### UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  EditorToolbar (graph selector, run controls, auto-layout)       │
├──────┬─────────────────────────────┬──────────────┬─────────────┤
│      │                             │              │             │
│ Node │      GraphCanvas            │  Config      │   Chat      │
│Palette│   (React Flow + minimap)   │  Panel       │   Panel     │
│      │                             │              │             │
│      ├─────────────────────────────┤              │             │
│      │ Logs | Output               │              │             │
│      │ (tab bar + scrolling panel) │              │             │
└──────┴─────────────────────────────┴──────────────┴─────────────┘
```

### Multi-Layered Graph Navigation (Phase 3.5-A + 7-7 Hardening)

- **CompositeExecutor** — backend executor that maps input/output ports and delegates to `run_subgraph`; supports node-aware mapping format (`nodeId::portName`) for targeted per-entry-node input injection (backward compatible with legacy flat mappings); registered in scheduler alongside WhileLoop/ForEach
- **`_run_subgraph` targeted injection** — optional `targeted_inputs: dict[str, dict[str, Any]]` parameter routes inputs to specific entry-point nodes instead of broadcasting to all entries; solves routing collisions when multiple entry nodes share port names
- **`is_blackbox`** field on CompositeNode — when true, node is opaque (no drill-in, no sub-graph preview)
- **Canvas drill-in** — double-click composite/while_loop/for_each nodes to navigate into their sub-graph; read-only (no edits while drilled in)
- **`resolveGraphAtStack(root, stack)`** — single source of truth for nested graph resolution. Walks the layer stack by traversing `sub_graphs` at each depth level. Returns `null` on invalid path or depth > `MAX_DRILL_DEPTH` (3). All navigation/save code paths (`drillIn`, `drillOut`, `jumpToLayer`, `saveGraph`, `PortMappingOverlay`) use this helper — no ad-hoc `sub_graphs[key]` lookups.
- **`deepSetSubGraph(root, stack, updatedSub)`** — immutable deep update: produces a new root `DanGraph` with the sub-graph replaced at the depth indicated by the layer stack. Used by `saveGraph` for nested save.
- **Depth-3 cap** — `MAX_DRILL_DEPTH = 3` (root → level-1 → level-2 → level-3). `resolveGraphAtStack` returns `null` beyond this; callers auto-reset to root and show toast. BreadcrumbBar visually indicates max depth.
- **Loop feedback arrows** — when drilling into `while_loop` or `for_each`, synthetic dashed edges (tagged `data.synthetic=true`) are injected from exit-point output ports back to entry-point input ports by name matching; generic fallback arrow when names don't match; `saveGraph()` filters out synthetic edges before serialization
- **`layerStack`** in Zustand store — tracks navigation depth; `drillIn`/`drillOut`/`jumpToLayer` actions recompute React Flow nodes/edges from `danGraph.sub_graphs`
- **BreadcrumbBar** — "Root > Node1 > Node2" navigation bar; each segment clickable
- **PortMappingOverlay** — shows input/output port mappings when viewing a composite node's sub-graph
- **Animated zoom** — CSS fade-in + `fitView()` on layer change

### Port Ordering & Edge Routing (Plan 7-7)

- **`orderPorts(ports, edges, nodes, nodeId, direction, nodeType?, portReorder?)`** — deterministic display-only sort in `portOrdering.ts`. Scoring bands (non-overlapping): P0 gate pins (0–9, `true`/`continue` → 0, `false`/`done` → 1), P1 connected ports (1000–1999, peer Y clamped to [0,999]), P2 unconnected (10000+, alphabetical sub-sort). Alphabetical tie-breaker. Used in `DanNode.tsx`; `ConfigPanel` keeps raw authoring order.
- **`computePortReorder`** — crossing minimization heuristic, runs once post-layout. Results passed as `portReorder` hint to `orderPorts` for P1-band sub-sorting.
- **Edge routing optimizations** — dagre port-aware edge weights, per-port smoothstep offsets, data-edge label deduplication, crossing minimization via port reorder.

### Live Execution Visualization (Phase 3.5-B)

- **Node pulse/glow** — CSS `@keyframes dan-node-pulse` on active nodes; completion flash animation
- **Duration badges** — per-node "123ms" / "1.2s" shown on completed nodes; tracked via `nodeTimings` in store
- **AnimatedEdge** — custom React Flow edge with SVG particle flow (`<animateMotion>`) on active edges (source completed → target started); dimming on inactive edges
- **Execution path highlighting** — nodes without status dimmed to `opacity-40` during runs
- **ExecutionTimeline** — horizontal bar with colored segments per node (ordered by start time); click to select node

### Rich Logging (Phase 3.5-C)

- **5 new event types** — `LLM_THINKING`, `TOOL_CALL_STARTED`, `TOOL_CALL_RESULT`, `CODE_OUTPUT`, `INTERMEDIATE_TEXT`
- **`ExecutionContext.emit_event()`** — executors emit structured events during execution
- **Unified run stream** — sub-graph events inherit parent `run_id`; single WebSocket subscription per run
- **LogPanel rebuild** — grouped by node_id with collapsible sections, sub-grouped by `EVENT_CATEGORY` (thinking/tool/output/error/lifecycle), inline SVG icons, color coding, text/node/type filtering, click-to-select-node

### Build Palette (Phase 3.5-D)

- **Searchable sidebar** — text input filters NODE_TYPE_CATALOG; collapsible category sections
- **Template factories** — extensible `TemplateResult` contract (`{ node, rootSubGraphKey, subGraphs }`); ReAct and Plan-Execute pre-built templates
- **Edge type selector** — compact toggle (Data/Control/Context) using EDGE_COLORS; `onConnect` creates edges with selected type
- **MCP placeholders** — disabled entries with "Coming soon" badge
- **Hover previews** — `NODE_DESCRIPTIONS` with port info shown on tooltip

### UI/UX Polish (Phase 3.5-E)

- **Toast notifications** — Zustand slice (`addToast`/`removeToast`); all async actions wrapped with success/error toasts
- **Loading states** — `loadingGraph`/`savingGraph` flags; `Spinner.tsx` component
- **Connection validation** — `isValidConnection` (no self-connect, no duplicates, port existence)
- **Merged toolbar** — `EditorToolbar.tsx` combines GraphSwitcher + RunPanel into one bar (DAN branding, graph selector, save/run/resume, status, auto-layout)
- **Node type icons** — inline SVG icons for all 16 node types (in DanNode header and palette)
- **Keyboard shortcuts** — Cmd/Ctrl+S → save
- **Edge labels** — data edges show `source_port → target_port`
- **Auto-layout** — dagre-based (LR direction, `applyAutoLayout` store action)

### Node & Port Editing (Phase 3.75-C)

- **Port editor** — `ConfigPanel.tsx` renders editable port rows per node (input and output). Each row: name input (commit-on-blur), required checkbox (input only), delete button. "Add Port" button appends with auto-generated unique name (`input_N`/`output_N`). Validation: no duplicates, no empty names (inline red styling).
- **Atomic port rename** — `renamePort` store action updates the port name on the node AND all connected edges' `source_port`/`target_port` + React Flow `sourceHandle`/`targetHandle` in a single `pushSnapshot` (one undo step).
- **Port delete with edge cleanup** — `deletePort` store action removes the port and filters out all edges referencing it.
- **Inline node rename** — double-click the name span in `DanNode.tsx` header to enter edit mode (controlled `<input>`, transparent background matching header style). Enter/blur commits via `updateNodeData`; Escape reverts. `stopPropagation` prevents composite drill-in. Auto-select text via ref + useEffect.
- **Output schema editor** — `SchemaEditor` component (inline in ConfigPanel) for `llm_operator` and `router` nodes. Visual mode: property rows (name, type dropdown, required checkbox, delete). Raw JSON mode: textarea with parse-on-blur. Toggle between modes; invalid JSON blocks switch to visual. Empty/null schema initializes as `{type: "object", properties: {}}` on first visual switch.

## Conversational Workflow Authoring (Phase 7)

### Chat Panel
- Resizable right-side panel with streaming LLM responses
- Graph-aware system prompt: serializes current workflow as `GraphSummary` for LLM context
- `@` mention system: reference nodes, workflows, sub-graphs with Cursor-style autocomplete
- Thread management: per-workflow persistent chat history, thread list, auto-restore
- **Context window management:** `compact_history()` transparently compacts chat history to fit within model context window. 4-phase sliding window: (1) system prompt always kept, (2) recent N messages in full, (3) older assistant messages truncated (first + last sentence), (4) oldest dropped. `MODEL_CONTEXT_WINDOWS` lookup table (20 models). Configurable via `DAN_CHAT_MAX_CONTEXT_RATIO` (default 0.8) and `DAN_CHAT_RECENT_MESSAGES` (default 10). Token counting via `tiktoken` with `len//4` fallback. Header shows "~Xk / Yk" context usage indicator.

### NL→Graph Mutation Engine
- `GraphMutator` applies atomic operations (add/remove/edit nodes and edges) to graph dicts
- LLM function-calling: `plan_graph_mutations` tool returns structured `MutationPlan`
- Transactional by default (`all_or_nothing`); partial apply is opt-in
- Optimistic concurrency via `base_graph_revision` / hash matching
- `GraphDiffPreview` shows visual diff before applying; accept/reject/partial-accept
- **Validation gate:** After `GraphMutator.apply()` succeeds, the `apply-mutation` endpoint runs `Graph.model_validate()` + `validate_graph()` before persisting. Fatal validation errors reject the apply; warnings are returned alongside the saved graph.
- **Auto-retry:** If the LLM's mutation plan fails dry-run validation, the chat manager feeds the errors back to the LLM for one correction attempt before surfacing the failure to the user.
- **`TOOL_PORT_MANIFESTS`** — tool-specific port declarations for 10 common tools (`file_read`, `list_directory`, `pdf_read`, `compile_latex`, `save_paper`, `package_submission`, `citation_verifier`, `check_latex_deps`, `rag_index_documents`, `web_search`). Used by `_default_ports` to auto-declare input/output ports for `tool_operator` nodes by `tool_id`.
- **`ApplySkill` mutation op** — targets nodes by ID or `metadata.tags`; injects domain-specific prompt prefixes from `SKILL_LIBRARY` (in `skill_library.py`) into `system_prompt` (or `prompt_template` fallback). Skills: `management_science_writing`, `informs_latex_style`.
- **Mutator diagnostics (7-8):** When `add_edge` auto-creates a missing target port, a diagnostic is emitted. Optional `strict=True` on the op fails instead of auto-creating.
- **`clarify_intent()`** — `ChatManager` method that detects underspecified build-mode intents and asks the user for clarification before planning.

### Build-from-Intent Mode
- **Two-mode chat:** `ChatMessageRequest.mode` accepts `"mutate"` (default) or `"build"`. Mode `"build"` uses `BUILD_FROM_INTENT_PROMPT` (intent-first workflow creation); `"mutate"` uses `SYSTEM_PROMPT_TEMPLATE` (graph-aware editing). Empty graphs auto-switch to build mode regardless of the `mode` parameter.
- **Intent-first prompt:** `BUILD_FROM_INTENT_PROMPT` guides the LLM through task decomposition (goal → stages → node types → data flow), references the pattern library (chain, review_loop, fan_out, rag_qa), and maps common intents to patterns (paper writing → review_loop + chain, RAG QA → rag_qa).
- **Template registry:** `WORKFLOW_TEMPLATES` dict maps template names (paper_writing, rag_qa, chain_3) to pre-built `expand_pattern` operation sequences. Templates reduce LLM variability for common workflows.
- **Empty-graph bootstrap:** `build_graph_summary` handles empty graphs (nodes=[], edges=[]) — returns valid `GraphSummary` with `node_count=0` and a deterministic revision hash. `base_graph_revision` is injected from the empty graph state so the mutator's stale-plan check works for build-from-scratch.
- **Editor UX:** "Build with AI" entry point in TabBar creates a blank graph and opens the chat in build mode. After the LLM returns a mutation plan, the editor shows a diff preview (empty → new graph), and auto-switches to mutate mode on apply.

### Scoped Execution from Chat
- `/run`, `/run-node @Node`, `/run-subgraph @Node` commands in chat
- `build_scoped_graph()` derives minimal executable graphs for node or subgraph scopes
- Run events stream back into chat thread as status blocks

### Session-Scoped Rollback
- Each mutation records a frontend-only `historyCursor` marker
- "Revert to here" walks the undo stack; markers cleared on page reload

## Key Decisions

- **Build, don't buy.** Existing tools (Langflow, Flowise, Dify) cannot handle while-loops, composable sub-graphs, or typed edges natively. See development-plan.md sections 3-4 for full analysis.
- **Three authoring surfaces, one IR.** Python builder DSL (most programmable), markdown agent files (most accessible), and visual editor (most interactive) all compile to the same `dan_graph_v1` JSON. They coexist — users pick the surface that fits. Python and markdown are file-based and version-controllable; the visual editor is for interactive exploration and debugging.
- **Language split.** Python for orchestration runtime and validation; TypeScript for the visual editor and interaction layer.
- **Roadmap resequencing.** Build the core engine first, then immediately build a full visual editor baseline to test the system early via UI.
- **Hierarchical plan numbering.** Plan files use hierarchical numbering (`1-name`, `1-1-name`, `1-1-1-name`) to mirror the task tree.
