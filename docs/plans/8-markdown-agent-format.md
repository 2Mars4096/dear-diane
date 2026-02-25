# 8: Phase 5 — Markdown Agent Format

**Status:** completed
**Goal:** Add a third authoring surface — one `.md` per agent, one workflow `.md` to wire them — that compiles to the same `dan_graph_v1` JSON as the Python builder DSL and visual editor. Markdown is the most accessible and LLM-generatable format.

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [8-1](8-1-format-design-parser.md) | Format Design & Parser | Agent file format (frontmatter + blockquotes + body), workflow file format (`## Flow` + agent list), flow notation parser, port type inference, format versioning | new `src/dan/loader/`, `parser.py`, `models.py` |
| [8-2](8-2-markdown-graph-compiler.md) | Markdown → Graph Compiler | Auto-wiring (port name matching + explicit overrides), `dan.loader.compile()` → `dan_graph_v1`, compiler diagnostics with `file.md:line` source maps | `src/dan/loader/compiler.py`, `wiring.py`, `diagnostics.py` |
| [8-3](8-3-validation-parity-advanced.md) | Validation, Parity & Advanced Features | Paper-writing rewrite as markdown agents, `dan.loader` ↔ `dan.builder` parity checklist, coexistence policy, composite agents, linked JSON Schema files | `examples/paper_writing/`, `src/dan/loader/` extensions |
| [8-4](8-4-round-trip-decompiler.md) | Graph → Markdown Round-Trip | Decompiler (graph JSON → markdown files), visual editor export, `markdown → graph → markdown` conformance tests | `src/dan/loader/decompiler.py`, editor integration, tests |

## Dependencies / Sequencing

```
8-1 (format + parser)  ──→  8-2 (compiler)  ──→  8-3 (validation + advanced)  ──→  8-4 (round-trip)
```

Strictly sequential — each builds on the previous. 8-1 defines what the parser produces, 8-2 consumes it to produce graphs, 8-3 validates the compiler and adds extensions, 8-4 inverts the compiler.

**Recommended execution order:** 8-1 → 8-2 → 8-3 → 8-4 (no parallelism within this phase).

## Shared Decisions

- **`dan.loader` is the package name** — mirrors `dan.builder`. Public API: `dan.loader.load(path) -> Graph` for a single workflow, `dan.loader.load_agents(dir) -> dict[str, AgentSpec]` for a directory of agent files.
- **One `.md` per agent, one workflow `.md` to wire them** — agent files are self-contained (prompt, ports, model config). The workflow file declares the agent roster and flow topology. This mirrors the two-level abstraction (operators vs. composites).
- **YAML frontmatter for structured config, markdown body for prompt** — consistent with how LLMs naturally generate markdown. Frontmatter carries machine-readable fields (type, model, temperature); the body is the prompt template.
- **Flow notation is minimal and readable** — `→` for chaining, `.port` for specific ports, `| each()` / `| loop()` / `| if()` for control flow. Designed to be writable by hand AND generatable by LLMs.
- **Compilation target is identical `dan_graph_v1` JSON** — no special markdown-only graph variant. The output of `dan.loader.compile()` is the same `Graph` model the builder's `.build()` returns and the visual editor reads.
- **Round-trip is a goal, not a constraint** — `markdown → graph → markdown` should be idempotent for the subset of features markdown supports. Features only expressible in Python (programmatic loops, dynamic node creation) are out of scope for markdown round-trip.
- **Format versioning from day one** — `format_version: 1` in workflow frontmatter. Breaking changes increment the version. Loader validates version on parse.
- **Gate-style flat control flow, not legacy sub-graph loops.** `| loop()` compiles to `GateNode(gate_mode="while")` with back-edges — the body is a peer node, not nested in a sub-graph. `| if()` compiles to `GateNode(gate_mode="if_else")` with branch edges. The scheduler's cycle-aware scheduling handles iteration. Legacy `WhileLoopNode`/`IfElseNode` are deprecated and not targeted. `| each()` keeps sub-graph semantics via `ForEachNode.body_graph` (parallel per-item execution requires isolation).
- **`InputNode` for graph-level variables.** Unresolved `{variable}` placeholders in entry-point agents are formalized via an auto-generated `InputNode` with `InputVariable` entries — not by overloading `Graph.entry_points` (which holds node IDs, not variable names). This matches the visual editor's `RunInputsDialog` pattern.
- **Best-effort decompilation.** The markdown decompiler (8-4) never fails on unsupported graph features. Unsupported nodes emit stub agent files with HTML comment annotations; unsupported edge types emit `<!-- SKIPPED -->` markers in the flow section; visual-only fields (`position`, `ui`) are silently dropped. Diagnostics are collected alongside generated files.

## Notes

- The markdown format is inspired by Cursor's AGENTS.md and similar "agent-as-file" patterns. Each agent file IS its documentation.
- `{variable}` placeholders in markdown prompts use the same syntax as `dan.builder` f-string markers — the compiler resolves them to input ports and DataEdges.
- Agent files can reference other agent files by relative path — this enables directory-based organization (e.g., `agents/reviewer.md`, `agents/writer.md`).
- The `paper_writing.py` example (2400+ lines) is the primary validation target. If the markdown format can express this workflow, it can express most practical workflows.
- Composite agents in markdown (`type: composite`) have their own internal `## Flow` section, creating a recursive structure that maps to `sub_graphs` in the graph model.
