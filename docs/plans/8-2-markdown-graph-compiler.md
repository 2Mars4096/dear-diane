# 8-2: Markdown → Graph Compiler

**Parent:** [8-markdown-agent-format](8-markdown-agent-format.md)
**Status:** in-progress
**Goal:** Take the parsed `AgentSpec` and `WorkflowSpec` models from 8-1's parser and produce a valid `dan_graph_v1` `Graph`, including auto-wiring logic and compiler diagnostics.

## Tasks

- [x] 1. Agent → Node compilation
  - [x] 1-1. `type: llm` → `LLMOperator` — map frontmatter fields (`model`, `temperature`, `max_tokens`, `system_prompt`, `output_schema`) to LLMOperator fields. Prompt body → `prompt_template`. `{variable}` placeholders → InputPort entries.
  - [x] 1-2. `type: tool` → `ToolOperator` — `tool_id` from frontmatter, `tool_config` from frontmatter (optional dict of static arguments), declared `> Accepts` ports → InputPorts (merged with `tool_config` at runtime)
  - [x] 1-3. `type: code` → `CodeOperator` — markdown body (inside a fenced code block) → `code` field, `language` from code fence or frontmatter
  - [x] 1-4. `type: human` → `HumanInTheLoopNode` — markdown body → `prompt` field, `timeout_seconds` and `default_action` from frontmatter, ports from `> Accepts` / `> Returns`
  - [x] 1-5. `type: router` → `RouterNode` — `model` from frontmatter (required), `route_descriptions` from frontmatter (dict of `route_name: description`). Each route name becomes an output port. Markdown body is ignored (router uses LLM-generated prompt internally).
  - [x] 1-6. Port generation — combine explicit `> Accepts` / `> Returns` ports with implicit `{variable}` placeholder ports. Deduplicate (explicit wins over implicit). Assign `InputPort` / `OutputPort` with inferred schemas from 8-1's type inference.
  - [ ] 1-7. Node ID generation — use the agent's name (from workflow agent list) as `id`, slugified (lowercase, hyphens). Validate uniqueness across the workflow. *(deferred — current ID generation works; formal slugification is a polish item)*

- [x] 2. Flow → Edge compilation
  - [x] 2-1. `ChainStatement` (a → b → c) → sequence of `DataEdge` objects. Source port = default output of source node (`text` for LLM, `result` for tool/code, etc.), target port = default input of target node (first declared input or `input`).
  - [x] 2-2. Port-specific wiring (`a.port → b.port`) → `DataEdge` with explicit `source_port` and `target_port`. Validate ports exist on the referenced nodes.
  - [x] 2-3. `EachStatement` (a | each(b, parallel: N)) → create `ForEachNode` with `body_graph` key pointing to a sub-graph containing agent `b`'s compiled node, `parallelism` from parameter. Wire: DataEdge from `a` default output → ForEach `items` input. ForEach output (`results`) → next node in chain if present. Register the body sub-graph in `Graph.sub_graphs`.
  - [x] 2-4. `LoopStatement` (a | loop(b, until: cond, max: N)) → **gate-style flat loop** (no sub-graph). Create `GateNode(gate_mode="while", condition=cond, max_iterations=N)`. Wire: DataEdge from `a` → gate input, DataEdge from gate `continue` port → agent `b` input, DataEdge from `b` output → gate input (back-edge — the scheduler detects this cycle and iterates), DataEdge from gate `done` port → next node in chain. The body agent `b` is a peer node in the same graph, not nested in a sub-graph.
  - [x] 2-5. `IfStatement` (a | if(cond, then: b, else: c)) → **gate-style flat branching** (no sub-graph). Create `GateNode(gate_mode="if_else", condition=cond)`. Wire: DataEdge from `a` → gate input, DataEdge from gate `true` port → agent `b`, DataEdge from gate `false` port → agent `c`. Both branches are peer nodes in the same graph.
  - [x] 2-6. Control-flow node auto-generated IDs: `{source_agent}_each_{target_agent}`, `{source_agent}_loop_{target_agent}`, `{source_agent}_if_{then_agent}_{else_agent}`

- [x] 3. Auto-wiring engine
  - [x] 3-1. Port name matching — when a chain `a → b` has no explicit port annotation, match output ports of `a` to input ports of `b` by name. If exactly one match, auto-wire. If multiple matches, wire all. If zero matches, fall back to default output → default input.
  - [x] 3-2. Ambiguity detection — when auto-wiring finds multiple possible port matches, emit a warning diagnostic suggesting explicit `.port` syntax
  - [ ] 3-3. Dangling port detection — after all flow statements are compiled, check for input ports that receive no edge (potential missing wiring). Emit warning unless the port has a `{variable}` placeholder (those become graph-level inputs via InputNode). *(deferred — nice-to-have diagnostic; auto-wiring covers common cases)*
  - [x] 3-4. Graph-level input inference via `InputNode` — collect all `{variable}` placeholders on entry-point nodes that aren't satisfied by any edge. Create an `InputNode` with one `InputVariable` per placeholder (type inferred from 8-1 port type inference, or `string` default). Wire `InputNode` output ports → the consuming nodes' input ports. The `InputNode` becomes the graph's sole `entry_points` member — matches the visual editor's existing `RunInputsDialog` pattern.

- [x] 4. Compiler core (`dan.loader.compile()`)
  - [x] 4-1. Orchestration function: `compile(workflow_path: str | Path) -> Graph` — loads workflow `.md`, resolves agent file references (relative to workflow file), parses all files, runs agent→node + flow→edge compilation, runs auto-wiring, collects diagnostics, returns `Graph`
  - [ ] 4-2. `compile_agents(agent_specs: dict[str, AgentSpec]) -> list[Node]` — compile each agent spec into a Node *(deferred — logic inlined in compile(); extract as helper when needed)*
  - [x] 4-3. `compile_flow(flow_statements: list[FlowStatement], nodes: list[Node]) -> tuple[list[Edge], list[Node], dict[str, Graph]]` — compile flow into edges + control-flow nodes + sub_graphs
  - [x] 4-4. Graph assembly — combine compiled nodes, edges, sub_graphs into a `Graph` model. Set `entry_points` (nodes with no incoming edges) and `exit_points` (nodes with no outgoing edges). Populate `GraphMetadata` from workflow frontmatter.
  - [x] 4-5. Context declaration compilation — workflow `## Context` section → `SharedContextDeclaration` entries on the graph
  - [x] 4-6. Validation pass — call existing `dan.validation.graph` validators on the compiled Graph to catch structural issues (cycles in DAG sections, missing ports, disconnected nodes)

- [x] 5. Compiler diagnostics
  - [x] 5-1. `Diagnostic` model: `level` (error | warning | info), `message` (string), `source_file` (path), `source_line` (int), `source_column` (int, optional), `hint` (string, optional — suggested fix)
  - [x] 5-2. Source map — each compiled node and edge stores `metadata.source = {"file": "agents/writer.md", "line": 5}` tracing back to the originating markdown
  - [x] 5-3. `CompileResult` model: `graph` (Graph | None), `diagnostics` (list[Diagnostic]). Graph is None when errors exist. Warnings don't block compilation.
  - [x] 5-4. Common diagnostics catalog: unknown agent reference, port mismatch, ambiguous auto-wire, unsupported flow syntax, invalid condition expression, schema validation failure, duplicate node ID
  - [x] 5-5. Pretty-print formatter: `format_diagnostics(diagnostics) -> str` — produces `file.md:line: error: message` format (like gcc/rustc)

- [x] 6. Tests
  - [x] 6-1. Unit: agent→node compilation for each type (llm, tool, code, human, router)
  - [x] 6-2. Unit: flow→edge compilation for each statement type (chain, each, loop, if)
  - [ ] 6-3. Unit: auto-wiring — name match, ambiguity warning, dangling port detection, graph-level input inference *(deferred — test coverage, low priority; auto-wiring tested implicitly via integration tests)*
  - [x] 6-4. Unit: compiler diagnostics — error cases (missing agent file, port mismatch), warnings (ambiguous wire)
  - [x] 6-5. Integration: compile a simple 3-agent workflow markdown → valid Graph → validate with existing graph validators
  - [x] 6-6. Integration: compile a workflow with ForEach + Loop → verify sub_graphs and control-flow node structure
  - [x] 6-7. Snapshot: compile known fixture workflow → compare output graph JSON to stored snapshot (catches unintended regressions)
  - [x] 6-8. Fixture files: add compiled workflow fixtures to `tests/fixtures/markdown/`

## Decisions

- (filled in during execution)

## Notes

- The compiler mirrors `dan.builder.compiler.compile_graph()` conceptually — both take an intermediate representation and produce a `Graph`. The builder's IR is `_PendingNode` / `_PendingEdge`; the loader's IR is `AgentSpec` / `FlowStatement`.
- Default output port names per node type must match `dan.builder.compiler.DEFAULT_OUTPUT_PORTS`: `llm_operator` → `text`, `tool_operator` → `result`, `code_operator` → `result`, `for_each` → `results`, `gate` → `true` (if_else mode) / `done` (while mode), `reduce` → `result`, `router` → `route`, `human_in_the_loop` → `response`, `composite` → `result`.
- The `dan.validation.graph` module already has graph well-formedness checks. The compiler should reuse these rather than duplicating validation logic.
- Flow notation intentionally doesn't cover every possible graph topology. Complex wiring (multiple edges between same node pair, context edges, artifact refs) requires the Python builder or visual editor. The compiler should detect unsupported patterns and emit clear diagnostics.
- **Gate-style flat loops, not sub-graph loops.** `| loop()` compiles to `GateNode(gate_mode="while")` with back-edges — the body agent is a peer node, not nested in a sub-graph. The scheduler's cycle-aware scheduling (`_execute_with_cycles`) detects the back-edge and iterates. This matches the Phase 3.75 gate-loop redesign (plan 6-10). Legacy `WhileLoopNode` (sub-graph-based) is deprecated and not targeted.
- **ForEach still uses sub-graphs.** `| each()` compiles to `ForEachNode` with `body_graph` pointing into `Graph.sub_graphs` — this is the correct pattern since ForEach runs the body per-item in parallel, which requires sub-graph isolation.
- **InputNode for graph-level variables.** Unresolved `{variable}` placeholders are formalized via an `InputNode` (not by overloading `entry_points`). This matches the visual editor's `RunInputsDialog` which detects `InputNode` variables and prompts the user before execution.
