# Deep Agent Network — Development Plan

## 1. Vision

LLM applications are getting complicated. A single API call becomes wrapped in system prompts, skills, MCPs, tool calls — yet most coding IDEs and CLIs still funnel everything through one monolithic agent. This breaks down as tasks grow in complexity.

**Deep Agent Network** treats agents like nodes in a network — analogous to a supply chain, or the layout of a neural network. Each node is a functional unit (an LLM call, a tool call, a sub-workflow). Edges define how information is sourced, routed, and delivered between nodes. The user designs the topology: sequential chains, parallel fan-outs, conditional branches, loops — whatever the task demands.

The key insight: different tasks need different network topologies, different models at different nodes, and structured management of inputs and outputs across the entire flow. No single agent should do everything.

### Motivating Example: Multi-Agent Paper Writing

A concrete workflow that stress-tests the concept:

```
[Idea Generator] → [Outline Planner] → [Section Writers (parallel)]
                                              ↓
                                    ┌─────────┼─────────┐
                                    ↓         ↓         ↓
                              [Data Proc] [Analysis] [Fig Gen]  ← functional agents / tool calls
                                    └─────────┼─────────┘
                                              ↓
                                    [Section Assembler]
                                              ↓
                                 ┌──→ [Reviewer Panel] ──→ [Reviser] ──┐
                                 │         (exit if negligible           │
                                 │          comments or max retries)     │
                                 └──────────────────────────────────────┘
```

This requires: sequential dependencies, parallel fan-out within a stage, heterogeneous tool calls inside nodes, and a **while-loop with exit conditions** (review-revise cycle). Any framework that claims to be general-purpose must handle this gracefully.

---

## 2. Landscape: Academic Research

### Do They Work?

**Honest answer: mostly no — not for real workflows.** The research is interesting but benchmark-oriented. Every paper below tests on standardized tasks (HumanEval, MBPP, GSM8K, MATH, HotpotQA) — solving a single math problem or answering a single question. None of them orchestrate a multi-stage, multi-tool pipeline like the paper-writing example above.

They are solving a different problem: "given an atomic task, what is the optimal graph of LLM calls to solve it?" We are asking: "let me design a graph myself for a complex, multi-stage pipeline with heterogeneous nodes."

### Key Papers

| System | What It Does | Venue | Has Code? | Handles Real Workflows? |
|--------|-------------|-------|-----------|------------------------|
| **AFlow** | Two-level abstraction (operators + nodes), MCTS-based workflow search | ICLR 2025 Oral | Yes ([GitHub](https://github.com/foundationagents/aflow)) | No — optimizes within narrow benchmarks, not user-designed pipelines |
| **A²Flow** | Extends AFlow with auto-discovered abstraction operators | arXiv Nov 2025 | Pending | Same limitation as AFlow |
| **GPTSwarm** | Agents as optimizable computational graphs, automatic prompt + edge optimization | arXiv 2024 | Yes ([GitHub](https://github.com/metauto-ai/gptswarm)) | No — focused on agent collaboration optimization, not pipeline orchestration |
| **EvoFlow** | Genetic algorithm evolves populations of heterogeneous workflows | arXiv Feb 2025 | Yes ([GitHub](https://github.com/bingreeky/EvoFlow)) | No — auto-generates workflows for benchmarks, not user-designable |
| **MacNet** | DAG-based multi-agent collaboration, tested up to 1000+ agents | arXiv 2024 | Yes | No — studies scaling behavior, not practical pipeline building |
| **AgentNet** | Decentralized DAG with RAG-enhanced agents, dynamic routing | NeurIPS 2025 | Yes ([GitHub](https://github.com/zoe-yyx/AgentNet)) | No — focuses on decentralized coordination and privacy |
| **G-Designer** | GNN-based automatic topology design for agent communication | ICML 2025 Spotlight | Yes ([GitHub](https://github.com/yanweiyue/GDesigner)) | No — learns topologies for benchmark tasks |
| **Agentic Neural Networks** | Agents as layered neurons with "textual backpropagation" | arXiv Jun 2025 | Yes | No — the "learning" analogy taken literally, benchmark-only |

### What IS Useful From This Research

Despite not being directly usable, these papers establish important design principles:

- **AFlow's two-level abstraction** (operator → composite agent) is the right granularity model
- **GPTSwarm's typed edges** and automatic graph optimization point toward schema-validated connections
- **G-Designer's insight** that topology matters enormously — chain vs. star vs. DAG can change performance by 95% on the same task
- **MacNet's scaling law** — collaborative emergence follows logistic growth, irregular topologies beat regular ones
- **EvoFlow's heterogeneity** — different sub-tasks need different workflow structures, one size never fits all

### Related: Multi-Agent Paper Writing Systems

Some systems already tackle the paper-writing use case specifically:

- **LiRA** — multi-agent framework for literature review generation (outlining, writing, editing, reviewing agents). Outperforms baselines but is hardcoded for lit reviews.
- **PaperDebugger** — plugin-based multi-agent system integrated into LaTeX editors (Overleaf). Uses MCP for external tool access. Closest to a real workflow, but locked into the editor plugin paradigm.
- **EvoAgentX** — open-source platform that auto-optimizes multi-agent workflows using TextGrad/AFlow/MIPRO. Most general-purpose of the three.

---

## 3. Landscape: Existing Visual Builder Tools

### Can They Handle the Paper-Writing Workflow?

| Tool | Loops? | Conditionals? | Parallel Fan-Out? | Composable Sub-Graphs? | Multi-Model? | Verdict |
|------|--------|---------------|-------------------|----------------------|-------------|---------|
| **Langflow** | List iteration only | If/Else exists but **incompatible with Loop component** | Limited | No | Yes (per node) | Cannot do while-loop review cycle natively |
| **Flowise** | AgentFlow V2 supports loops | Conditional nodes exist but **convergence bug** (branches silently fail when merging) | Yes | No | Yes | Conditional loops are broken in practice |
| **Dify** | Basic loop support | Conditional branching | Parallel execution | No nested sub-workflows | Yes | Closest to working, but no composability |
| **n8n** | Yes (general automation) | Yes | Yes | Limited | Yes | Not agent-native; bolted-on AI support |

### Specific Failure Points for Our Motivating Example

1. **The while-loop (review-revise cycle)**: Langflow's loop only iterates over lists, not until a condition is met. Flowise's router agents can enter infinite loops with no graceful termination. Neither handles "loop until reviewers are satisfied OR max retries" natively.

2. **Parallel section writing with heterogeneous sub-agents**: Fan-out is possible in some tools, but fan-in (reassembling results from parallel branches) is poorly supported. No tool lets you define typed input/output schemas for each branch.

3. **Nested composition**: None of these tools let you double-click a node to reveal its internal sub-graph. Every agent is flat — you can't wrap a "data processing pipeline" as a reusable block inside a "section writer" node.

4. **Production fragility**: Real users report Langflow workflows become fragile as they grow (prompt drift, edge case accumulation). Flowise has concurrency issues under mild load (2 RPS causing timeouts).

---

## 4. Build vs. Buy Analysis

### Arguments for Using Langflow/Flowise (Don't Build)

| # | Argument | Weight |
|---|----------|--------|
| 1 | Visual graph editor alone is months of engineering work | Strong |
| 2 | Active communities, hundreds of pre-built integrations | Strong |
| 3 | The paper-writing workflow is *probably* buildable with workarounds | Moderate |
| 4 | You'd spend 6+ months on infrastructure before reaching the interesting parts | Strong |
| 5 | These tools are improving fast — the loop/conditional bugs may get fixed | Moderate |

### Arguments for Building Your Own

| # | Argument | Weight |
|---|----------|--------|
| 1 | **The while-loop problem is fundamental, not a bug.** The review-revise cycle is basic workflow logic. If the tool can't do `while(condition) { ... }` natively, you'll fight it on every non-trivial workflow. | Strong |
| 2 | **No composability.** Zoom-in/zoom-out on sub-graphs is not a feature request — it's a different architecture. You can't bolt this onto Langflow/Flowise. | Strong |
| 3 | **Typed edges don't exist.** You want supply-chain-style management: every node declares its input/output schema, edges validate compatibility. None of these tools do this. | Strong |
| 4 | **Fragility at scale is structural.** Both tools were designed for simple chatbot flows and grew organically. Complex pipelines hit their architectural limits, not just bugs. | Moderate |
| 5 | **The research gives you an intellectual edge.** AFlow's abstraction model, G-Designer's topology insights, and EvoFlow's heterogeneity principle can inform your design from day one. Existing tools don't incorporate any of this. | Moderate |

### Recommendation (and what we did)

**Don't build a general-purpose visual agent builder from scratch on day one.** Use a phased approach:

- ~~**Start with a Python-first orchestration core** that implements typed nodes, typed edges, control-flow primitives, and formal graph serialization.~~ **Done** — Phases 0-1.
- ~~**Immediately after the core engine works, build a full visual editor baseline** (React Flow + TypeScript) so workflows can be authored and tested through a UI early.~~ **Done** — Phase 2.
- ~~**Validate with the paper-writing workflow**, followed by deeper composability and advanced debugging overlays.~~ **Done** — Phases 3, 3.5, 3.75.
- **Next: harden the platform** (multi-provider LLM, built-in tools, retry policies, templates), then add the markdown authoring surface and extended capabilities (RAG, HTTP, sandbox). Followed by distribution (CLI, publish-as-API/MCP), observability (run history, audit log, checkpoint portals), and application-layer features (agent teams, chat integrations).

This approach preserved early backend validation while moving quickly to a practical Langflow/Flowise-like user experience. The foundation (engine, builder, editor, execution UX) is complete; the next phases furnish it for real-world use.

---

## 5. Design Decisions

### Three Authoring Surfaces, One IR

Every workflow, agent, and operator must be definable in file-based formats — not just through the visual editor. All authoring surfaces compile to the same `dan_graph_v1` JSON intermediate representation and coexist:

| Surface | Strength | Format | When to use |
|---------|----------|--------|-------------|
| **Python builder DSL** (`dan.builder`) | Most programmable — loops, conditionals, parameterization, testing | `.py` files | Power users, CI pipelines, programmatic workflow generation |
| **Markdown agent files** (`dan.loader`) | Most accessible — natural language prompts, minimal syntax, skill-like | `.md` files (one per agent + one workflow) | Rapid authoring, vibe-coding with LLMs, non-programmer-friendly |
| **Visual editor** | Most interactive — drag-and-drop, live execution, debugging overlays | React Flow canvas (reads/writes graph JSON) | Exploration, debugging, demos |

**Why file-based authoring matters:**

- **Vibe-codeable** — an LLM (Cursor, Claude Code, etc.) can generate, modify, and debug workflows by writing Python or markdown. Markdown is trivially generatable.
- **Version-controllable** — file diffs are readable; visual graph diffs are not.
- **Testable** — Python workflows can be unit tested, parameterized, and CI'd.
- **Documentable** — a markdown agent file IS its own documentation. The prompt is the file.
- **Composable** — reference agents by file path (markdown) or import as modules (Python).

**What this requires:** a high-level builder API / DSL on top of the raw Pydantic models, and a markdown loader that parses agent/workflow files. Both compile to the same graph JSON. The builder makes programmatic patterns readable:

```python
from dan.builder import workflow, llm, code, for_each, while_loop

paper = workflow("paper_writing")

# Chain operators
ideas = paper.llm("idea_gen", model="opus", prompt="Generate ideas about {topic}")
outline = paper.llm("planner", prompt="Create outline for: {ideas}")

# Parallel fan-out over sections
sections = paper.for_each(
    "write_sections",
    items=outline["sections"],
    body=lambda s: paper.llm("writer", prompt="Write section: {s}"),
    max_concurrency=4,
)
draft = paper.reduce("assemble", inputs=sections, strategy="concatenate")

# Review-revise loop
final = paper.while_loop(
    "review_revise",
    body=[
        paper.llm("reviewer", model="opus", prompt="Review: {draft}"),
        paper.llm("reviser", prompt="Revise based on: {comments}"),
    ],
    exit_when="verdict == 'accept' or iteration >= 5",
)

# Compile to Graph (same JSON the visual editor uses)
graph = paper.build()
```

This compiles down to the same `dan_graph_v1` JSON that the visual editor reads. Round-trip: code → Graph JSON → visual editor → Graph JSON → code. No information loss.

### The Block: Two-Level Abstraction (inspired by AFlow)

**Level 1 — Operator (atomic):** A single LLM call with parameters (model, prompt template, temperature, output schema). Also includes non-LLM atoms: API calls, code execution, database queries, conditionals. This is the fundamental unit.

**Level 2 — Agent (composite):** A group of operators wired into a sub-graph that behaves as a single unit with a defined interface (input schema → output schema). Users can collapse complexity into a reusable block. Each block is inspectable — double-click to zoom into its internal graph.

A user starts simple (chain a few operators) and progressively wraps them into reusable agent-blocks.

### The Edges: Typed Channels (inspired by supply chain management)

Edges are not just "output → input." They carry structure:

- **Data edges**: structured output of node A feeds into node B. Defined with schemas (JSON Schema) so nodes validate compatibility at design time.
- **Control edges**: conditional routing (if/else), loops (for-each, while), parallel fan-out/fan-in, retry logic.
- **Context edges**: shared memory or state (conversation history, accumulated knowledge, file system) that multiple agents can read/write.

### Control-Flow Primitives (special nodes)

| Primitive | Behavior |
|-----------|----------|
| **If/Else** | Route based on condition (evaluated on data from upstream node) |
| **While Loop** | Repeat until condition is met or max iterations reached |
| **For-Each / Map** | Fan-out: apply a sub-graph to each item in a list, in parallel |
| **Reduce** | Fan-in: aggregate results from parallel branches |
| **Router** | LLM-powered routing — the model decides which branch to take |
| **Human-in-the-Loop** | Pause execution, wait for human input, resume |

### Model Heterogeneity

Each operator node independently specifies its model. A cheap, fast model for classification. A strong model for reasoning. A code-specialized model for code generation. This is a first-class design principle, not an afterthought.

### Output Normalization (built-in, like batch norm in DNNs)

Every LLM operator produces unstructured text, but downstream edges expect typed, schema-validated data. Rather than making every user handle this, **output normalization is a deterministic, built-in layer on every LLM operator** — the same way batch normalization is a standardized layer inserted between compute layers in a neural network. The user never drags this onto the canvas; it's automatic.

The pipeline for every LLM operator output:

1. **Parse** — attempt to extract structured data from the LLM response (JSON, key-value, etc.)
2. **Validate** — check the parsed result against the operator's declared output schema
3. **Re-prompt on failure** — if parsing or validation fails, automatically re-call the LLM with the error message appended ("Your output didn't match the expected format. Error: ... Please try again.")
4. **Retry budget** — repeat steps 1-3 up to N times (configurable, default 3). If still failing, emit a structured error output.

This is deterministic and rule-based — no LLM involved in the normalization logic itself. It guarantees that every data edge in the graph carries schema-valid data, or an explicit error.

### Error Handling / Retry Policy (operator-level)

Every operator carries a `retry_policy` controlling what happens when the call itself fails (rate limit, timeout, network error, content filter):

- `max_retries`: number of retry attempts (default 3)
- `backoff`: exponential backoff strategy
- `fallback_model`: optional fallback model to try if the primary model fails repeatedly
- `on_failure`: what to do after exhausting retries — `error` (propagate structured error), `skip` (pass a null/default output), or `halt` (stop the graph)

This is separate from output normalization. Output normalization handles "the LLM responded, but the output is malformed." Retry policy handles "the LLM didn't respond at all."

### Checkpointing / Resumability (Phase 1)

Long workflows (paper writing can take hours) must survive crashes. After each node completes, the engine persists:

- The node's output (edge data)
- Artifact store state (refs + versions)
- Shared context store snapshot
- Graph execution pointer (which nodes are complete, which are pending)

On restart, the engine detects the last completed node and resumes from there. Artifacts (Layer 4) are already immutable and versioned, so they naturally support this. Node-local state within a composite must also be checkpointed for mid-loop recovery.

### Context Management: Four-Layer Model

Direct input/output (data edges) is straightforward. The hard problem is that messages grow as they pass through layers, and some state needs to be dynamically shared or updated. This is solved with four distinct layers, each handling a different kind of context.

**Layer 1 — Edge Data (direct, typed, bounded).** What flows along data edges. Always schema-defined, always bounded. A node's output contains its result, not accumulated history. A reviewer outputs `{ verdict, comments }`, never the full history of all reviews. Supply chain analogy: you ship the product, not the factory's production log.

**Layer 2 — Node-Local State (scoped to a composite agent).** Each composite agent (Level 2) maintains private working memory that does not leak upward unless explicitly exposed through the output schema. The review-revise loop tracks iteration count, current draft, review history, convergence trend — but its output to the parent graph is just the final polished draft. This is information hiding: the parent graph doesn't know or care how many iterations happened.

**Layer 3 — Shared Context Store (blackboard).** A namespaced key-value store that nodes opt into. Nodes declare their dependencies at design time:

- `reads`: which keys a node consumes (e.g., `context.outline`, `context.style_guide`)
- `writes`: which keys a node produces (e.g., `context.bibliography`)
- Write modes: `read`, `write`, `append`

This enables design-time validation (detect circular dependencies, race conditions) and graph-editor visibility (show which nodes touch which shared state). For parallel branches: read-many is fine; write conflicts need explicit merge rules.

**Layer 4 — Artifact Store (large objects by reference).** Drafts, datasets, figures — anything too large to be edge data. Stored by reference, fetched on demand. Artifacts are immutable: each revision creates a new version, giving free provenance tracking.

### Context Projection at Scope Boundaries

At each scope boundary (entering a sub-graph, entering a loop iteration), a **projection** extracts only what the next consumer needs from the available state. This is fractal — the same pattern applies at every nesting level.

Example — the review-revise loop has three distinct projections:

| Consumer | What It Sees | What It Doesn't See |
|----------|-------------|-------------------|
| **Loop controller** (should we continue?) | iteration count, max_iterations, verdict, open_comment_count, convergence_direction | Draft text, comment details, anything content-level |
| **Reviser** (fix what's broken) | current draft ref, latest comments, convergence hint ("2 minor issues remain, down from 8 major in round 1") | Iteration count, max_iterations, prior draft versions |
| **Parent graph** (after loop exits) | final polished draft | Everything internal — iteration count, review history, convergence data |

### Composite Node Contract

Every composite/loop node formalizes:

| Field | Purpose |
|-------|---------|
| `external_input_schema` | What the parent graph sends in |
| `external_output_schema` | What the parent graph gets back |
| `control_state` | Iteration count, stop flags, thresholds — used by the loop controller only |
| `local_working_set` | Latest draft/comments/summary — NOT full history |
| `read_set` / `write_set` | Declared dependencies on shared context store |
| `compaction_rule` | How local history is summarized between iterations (sliding window, summarization gate, or diff-based) |

### Policies (defined in Phase 0)

- **Mutation policy**: which nodes can write shared context; default is read-only unless declared
- **Parallel merge policy**: for fan-out branches, define deterministic merge rules — append, last-write-wins, or explicit reducer node
- **Compaction policy**: when local history is summarized/trimmed (every iteration, every N rounds, on threshold)
- **Failure semantics**: standard exit modes for loops — `max_iterations`, `stagnation` (no convergence progress), `timeout`

### Memory System (backlog — not yet designed)

The four-layer context model handles within-run state, but very long chains (hundreds of nodes, multi-day workflows) need something more: a memory system analogous to human cognition.

**The problem**: as a workflow grows long, early results become invisible. A node at step 50 can't feasibly receive the full output of steps 1-49 as context. The current compaction rules help within a single loop, but across the entire graph, distant context drops off entirely.

**Directional thinking** (to be refined later):

- **Short-term memory**: the current `local_working_set` and recent edge data. What's actively in the node's prompt. Small, fast, lossy (overwritten each step).
- **Long-term memory**: completed sub-graph results compressed into indexed, retrievable summaries. A node at step 50 doesn't see step 12's full output, but can *query* for it by relevance ("what did the data analysis find about variable X?"). This is closer to RAG than to context passing.
- **Encoding**: when a sub-graph completes, its output is summarized and indexed (embeddings, tags, structured metadata) into a persistent memory store.
- **Consolidation**: periodically or at scope boundaries, related memories are merged/compressed (analogous to sleep consolidation — multiple related findings become a single coherent summary).
- **Retrieval**: nodes declare what kind of memory they might need (via query templates or semantic search), and relevant memories are injected into their context at runtime.

This is distinct from the shared context store (Layer 3), which is designed for known, declared keys. The memory system handles *unknown* retrieval — a node doesn't know in advance which earlier result is relevant.

**Related work**: MemGPT (tiered memory for LLM agents), AgentNet's RAG-based adaptive learning, Letta's agent memory architecture.

---

## 6. Applications: Rebuilding Real Systems on DAN

The lego architecture is general enough to express existing complex systems as graph configurations. Two concrete targets validate this.

### 6.1 Coding Assistant (Cursor-like)

Cursor's agentic workflow decomposes into ~15 node types and a handful of graph templates.

**Node catalog:**

| Category | Nodes |
|----------|-------|
| Operators (atomic) | `LLMOperator`, `ToolOperator` (read_file, edit_file, search, shell, web_search, web_fetch, etc.), `ContextRetriever` (symbol search + ripgrep + ranking + budget), `DiffGenerator`, `CheckpointOperator` |
| Control nodes | `Router` (LLM-powered tool dispatch), `WhileLoop` (ReAct), `FanOut`/`Reduce` (parallel subagents), `Conditional` |
| Special nodes | `HumanNode` (blocking user I/O), `MCPBridge` (external tool servers) |
| Hyperedges | `Skill` (knowledge/capability), `Rule` (constraint/override) |

**Every mode is a graph template:**

| Mode | Graph Structure |
|------|----------------|
| Ask | `[Human] → [Context] → [LLM (read-only tools)] → [Human]` |
| Agent | `[Human] → [Context] → [ReAct while-loop (all tools)] → [Human]` |
| Debug | `[Human] → [Diagnostics] → [Hypothesize] → [Test] → [Fix] → [Human]` |
| Plan | `[Human] → [Decompose] → [Tree search] → [Outline] → [Human]` |
| Background | Agent graph with HumanNodes removed, CheckpointOperator every N steps |
| Parallel | `[Planner] → [Classifier] → [FanOut] → [Agent×N] → [Reduce]` |

Mode switching is not a feature — it's activating a different graph template. The four top-level agents (Ask, Agent, Debug, Plan) are independent DAN networks sharing a common context layer (see architecture.md, "Four Top-Level Agents Architecture").

**What's hard (not the graph):** Context retrieval quality (codebase indexing, ranking), prompt engineering, streaming UX, and editor integration. The graph itself is ~10% of the work. But building it on DAN makes the workflow portable — same graph runs behind CLI, web app, VS Code extension, or notebook.

### 6.2 Research IDE (science-cursor)

The science-cursor project (Scholar IDE) has three engines and an orchestrator that map directly to DAN agents:

| science-cursor Component | DAN Agent | Internal Operators |
|--------------------------|-----------|-------------------|
| `LiteratureEngine` | `literature-agent` | `pdf-ingest`, `embed-chunk`, `rag-retrieve`, `citation-format` |
| `DataScientistEngine` | `execution-agent` | `python-exec`, `stata-exec`, `jupyter-run`, `figure-export` |
| `WritingEngine` | `writing-agent` | `section-draft`, `latex-compile`, `tikz-generate`, `bibtex-build` |
| `PaperOrchestrator` | DAN network | Composes the three agents above with control flow |

The paper-writing workflow already in `development-plan.md` Section 1 is the target DAN network. Skills as hyperedges replace the skill-loader system (scoped to relevant nodes, not injected into a monolithic prompt). Rules as hyperedges replace the rules-loader (guardrails, style constraints, tool overrides).

**Rebuild strategy:** Extract the workflow layer into a standalone DAN-based Python package. The VS Code extension becomes a thin rendering client that resolves HumanNode I/O and displays graph execution. The sidecar remains as a communication bridge, but orchestration logic moves into the DAN graph.

## 7. Development Roadmap

| Phase | Milestone | Deliverable | Status |
|-------|-----------|-------------|--------|
| **0** | Solidify abstraction model | Formal spec: node types, edge types, schema format (91 tests) | **Done** |
| **1** | Python orchestration library | Async engine: typed nodes, while-loops, fan-out/fan-in, checkpointing (140 tests) | **Done** |
| **1.5** | Workflow builder API | Fluent Python DSL (`dan.builder`) compiling to `dan_graph_v1` JSON (243 tests) | **Done** |
| **2** | Visual editor baseline | FastAPI + React Flow: CRUD, live streaming execution, composite preview (167 tests) | **Done** |
| **3** | Paper-writing proof of concept | End-to-end paper-writing workflow running on engine + editor + code (252 tests) | **Done** |
| **3.5** | Frontend design | Multi-layer nav, execution viz, rich logging, build palette, UI polish (256 tests) | **Done** |
| **3.75** | Visual editor full editing | Undo/redo, copy/paste, ports, context menus, gate loops, multi-tab, workflow-as-node (341 tests) | **Done** |
| **4** | Core hardening | Multi-provider LLM, built-in tools, retry policies, templates, observability | Not started |
| **5** | Markdown agent format | `dan.loader`: markdown agent/workflow files → `dan_graph_v1` JSON (third authoring surface) | Not started |
| **6** | Extended capabilities | RAG node (upgrade from tool-based), script execution / sandbox, handoff validators | Not started |
| **7** | Author & chat | Conversational workflow authoring, NL→graph mutations, cursor-parity chat | **Done** |
| **8** | Observe & recover | Run history, audit log, checkpoint portals, variable inspector, node test cases | **Completed** |
| **9** | Deep systems | Memory & cross-run state (9A), behavior modifiers/hyperedges (9B), execution primitives (9C) | Not started |
| **10** | Token optimization | Prompt compression, provider caching, truncation policies, token analytics & waste detection | Not started |
| **11** | Meta-Orchestrator | Goal→workflow planning, similarity retrieval+adaptation, graduated repair (L1–L4), autonomous execution with human override | Not started |
| **12** | Author & distribute | CLI, publish as API/MCP, messaging integrations, shareable blocks, PyPI package | **Completed** |
| **20** | Telegram Multi-Bot Platform | Adapter upgrade, BotFleet, MessageRouter, native features (polls, streaming edits, reactions, forum topics), `dan-bot` CLI, unified `dan` CLI | **Completed** |
| **21** | Daily-Use QoL | Model control, visibility, capability exposure, CLI power-user features, defaults overhaul | **Completed** |
| **22** | Workflow generation optimization | Convenience layer, intent expansion, smart defaults, domain profiles. **32-7:** Direct execution architecture — eliminated codegen→sandbox roundtrip for complex tasks. `IntentCompiler.build_graph()` constructs Graph objects in-process. | In progress |

---

## 8. References

1. AgentNet: Decentralized Evolutionary Coordination for LLM-based Multi-Agent Systems. NeurIPS 2025. [Paper](https://neurips.cc/virtual/2025/poster/115584) | [GitHub](https://github.com/zoe-yyx/AgentNet)
2. AFlow: Automating Agentic Workflow Generation. ICLR 2025 Oral. [Paper](https://arxiv.org/abs/2410.10762) | [GitHub](https://github.com/foundationagents/aflow)
3. A²Flow: Automating Agentic Workflow Generation via Self-Adaptive Abstraction Operators. arXiv Nov 2025. [Paper](https://arxiv.org/abs/2511.20693)
4. GPTSwarm: Language Agents as Optimizable Graphs. arXiv 2024. [Paper](https://arxiv.org/abs/2402.16823) | [GitHub](https://github.com/metauto-ai/gptswarm)
5. EvoFlow: Evolving Diverse Agentic Workflows On The Fly. arXiv Feb 2025. [Paper](https://arxiv.org/abs/2502.07373) | [GitHub](https://github.com/bingreeky/EvoFlow)
6. MacNet: Scaling Large Language Model-based Multi-Agent Collaboration. arXiv 2024. [Paper](https://arxiv.org/abs/2406.07155)
7. G-Designer: Architecting Multi-agent Communication Topologies via Graph Neural Networks. ICML 2025 Spotlight. [Paper](https://arxiv.org/abs/2410.11782) | [GitHub](https://github.com/yanweiyue/GDesigner)
8. Agentic Neural Networks: Self-Evolving Multi-Agent Systems via Textual Backpropagation. arXiv Jun 2025. [Paper](https://arxiv.org/abs/2506.09046)
9. Graph-Augmented Large Language Model Agents: Current Progress and Future Prospects. Survey. [Paper](https://arxiv.org/abs/2410.05130)
10. LangChain Deep Agents. [GitHub](https://github.com/langchain-ai/deepagents)
11. LiRA: A Multi-Agent Framework for Reliable and Readable Literature Review Generation. arXiv Oct 2025. [Paper](https://arxiv.org/abs/2510.05138)
12. PaperDebugger: A Plugin-Based Multi-Agent System for In-Editor Academic Writing. arXiv Dec 2025. [Paper](https://arxiv.org/abs/2512.02589)
13. Langflow — Low-code AI builder. [Website](https://www.langflow.org) | [Loop docs](https://docs.langflow.org/loop)
14. Flowise — AgentFlow V2. [Docs](https://docs.flowiseai.com/using-flowise/agentflowv2)
15. Dify — Agentic Workflow Builder. [Website](https://dify.ai)
16. n8n — Workflow Automation. [Website](https://n8n.io)
