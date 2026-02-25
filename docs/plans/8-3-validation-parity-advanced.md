# 8-3: Validation, Parity & Advanced Features

**Parent:** [8-markdown-agent-format](8-markdown-agent-format.md)
**Status:** completed
**Goal:** Validate the markdown compiler by rewriting paper_writing.py as markdown agents, build a parity checklist against dan.builder, define coexistence policy, and implement advanced markdown features (composite agents, linked JSON Schema files).

## Tasks

- [x] 1. Paper-writing workflow rewrite
  - [x] 1-1. Create `examples/paper_writing_md/` directory structure — one `.md` per agent, one `workflow.md`
  - [x] 1-2. Write agent files for each agent in paper_writing.py
  - [x] 1-3. Write `workflow.md` with `## Agents` and `## Flow`
  - [x] 1-4. Validation: structural comparison test in `tests/test_loader/test_paper_writing_parity.py` (17 passed, 2 skipped for known gaps)
  - [x] 1-5. Document gaps: GateNode vs WhileLoopNode, foreach granularity, reduce not in markdown

- [x] 2. `dan.loader` ↔ `dan.builder` parity checklist
  - [x] 2-1. Build a feature matrix: rows = every builder method / feature, columns = [builder support, markdown support, notes]
  - [x] 2-2. Core node types: `llm`, `tool`, `code`, `human`, `router` — all must be supported in markdown
  - [x] 2-3. Control flow: `for_each`, `while_loop` (via `| each()` / `| loop()` flow notation), `if_else` (via `| if()`) — must be supported
  - [x] 2-4. Composite nodes: `wf.composite()` / `wf.import_workflow()` — markdown equivalent is `type: composite` (task 4)
  - [x] 2-5. Edge types: data edges (covered by flow notation), control edges (partial — flow notation covers basic routing), context edges (partial — `## Context` section)
  - [x] 2-6. Advanced features: `wf.reduce()` (needs markdown syntax or auto-infer), `wf.gate()` (covered by `| loop()` / `| if()`), `wf.input_node()` (implicit from graph-level inputs), `wf.artifact_ref()` (not in markdown — document as gap), `wf.shared_context()` (via `## Context`)
  - [x] 2-7. Explicitly out-of-scope for markdown: programmatic node generation (Python loops creating nodes), dynamic edge creation, arbitrary metadata/ui fields, `>>` operator (replaced by `→`), f-string magic (replaced by `{variable}` placeholders)
  - [x] 2-8. Write checklist as a markdown table in this plan's Decisions section during execution

- [x] 3. Markdown/Python coexistence policy
  - [x] 3-1. Document the relationship: both compile to the same `dan_graph_v1` JSON. They are peers, not layers.
  - [x] 3-2. Mixed workflows — can a workflow reference agents defined in Python? Decision: initially no. A workflow `.md` references only `.md` agent files. Python workflows use `dan.builder`. The visual editor can load either output.
  - [x] 3-3. Migration path — `dan.builder.decompile()` → Python code, `dan.loader.decompile()` (8-4) → markdown files. Users can convert between formats via decompilation.
  - [x] 3-4. Recommendation guidance — when to use markdown (simple agents, LLM-generated workflows, documentation-first), when to use Python (complex logic, parameterized templates, testing/CI), when to use the visual editor (exploration, debugging, demos)
  - [x] 3-5. Write policy document as a section in `docs/llm-api-guide.md` or `docs/architecture.md`

- [x] 4. Composite agents in markdown
  - [x] 4-1. `type: composite` frontmatter
  - [x] 4-2. Internal `## Agents` + `## Flow` sections within composite agent file
  - [x] 4-3. Compilation: `type: composite` → `CompositeNode` with `body_graph`
  - [x] 4-4. Nested composites supported via recursive compilation (with cycle detection)
  - [x] 4-5. File-reference composites implemented (inline subsections deferred)

- [x] 5. Linked JSON Schema files
  - [x] 5-1. Syntax: `> Returns: outline (schema: schemas/outline.json)`
  - [x] 5-2. Schema file resolution relative to agent file directory
  - [x] 5-3. Loaded schema becomes `OutputPort.schema`
  - [x] 5-4. Missing/malformed schema produces compiler diagnostic
  - [x] 5-5. Supported on `> Accepts` as well

- [x] 6. Tests
  - [x] 6-1. Integration: `test_paper_writing_parity.py` — 17 pass, 2 skip
  - [x] 6-2. Unit: composite agent parsing + compilation (5 tests in `test_advanced.py`)
  - [x] 6-3. Unit: linked JSON Schema (4 tests in `test_advanced.py`)
  - [x] 6-4. Unit: parity covered by parity checklist + existing test suite
  - [x] 6-5. Fixture: `examples/paper_writing_md/` + `tests/fixtures/markdown/composite/` + `tests/fixtures/markdown/schemas/`

## Decisions

### Builder ↔ Markdown Parity Checklist

| Feature | `dan.builder` | `dan.loader` (markdown) | Notes |
|---|---|---|---|
| **Core node types** | | | |
| LLM operator | ✅ `wf.llm()` | ✅ `type: llm` | Full parity |
| Tool operator | ✅ `wf.tool()` | ✅ `type: tool` | Full parity |
| Code operator | ✅ `wf.code()` | ✅ `type: code` | Full parity |
| Human-in-the-loop | ✅ `wf.human_in_the_loop()` | ✅ `type: human` | Full parity |
| Router | ✅ `wf.router()` | ✅ `type: router` | Full parity |
| **Control flow** | | | |
| ForEach (fan-out) | ✅ `wf.for_each()` | ✅ `\| each()` | Both use sub-graph `body_graph` |
| While gate (loop) | ✅ `wf.gate(mode="while")` | ✅ `\| loop()` | Flat back-edges, not sub-graph |
| If/else gate | ✅ `wf.gate(mode="if_else")` | ✅ `\| if()` | Flat `true`/`false` edges |
| Reduce | ✅ `wf.reduce()` | ❌ Not supported | Gap — no markdown syntax |
| **Composite** | | | |
| Composite node | ✅ `wf.composite()` | ✅ `type: composite` | Recursive `## Agents` + `## Flow` |
| Import workflow | ✅ `wf.import_workflow()` | ❌ Not supported | Gap — requires Python runtime |
| **Edges** | | | |
| Data edges | ✅ `wf.edge()` / `>>` | ✅ `→` / `.port →` | Full parity |
| Control edges | ✅ `wf.control_edge()` | ❌ Not in flow notation | Gate ports handle routing instead |
| Context edges | ✅ `wf.context_edge()` | ⚠️ `## Context` (declaration only) | No per-node edge wiring |
| **Graph-level** | | | |
| Shared context | ✅ `wf.context()` | ✅ `## Context` section | Declaration parity |
| Artifact refs | ✅ `wf.artifact_ref()` | ❌ Not supported | Gap |
| Input node | ✅ `wf.input_node()` | ✅ Auto-generated from `{variable}` | Implicit vs explicit |
| **Meta** | | | |
| Retry policy | ✅ kwargs | ✅ `retry_policy:` frontmatter | Full parity |
| Output schema | ✅ kwargs | ✅ `output_schema:` frontmatter | Full parity |
| Position / UI hints | ✅ Post-build assignment | ❌ Silently dropped | Visual-editor only |
| Programmatic loops | ✅ Python `for` → N nodes | ❌ Not possible | By design |

### Coexistence Policy

- **Peers, not layers.** `dan.builder` (Python), `dan.loader` (markdown), and the visual editor all compile to the same `dan_graph_v1` JSON. None is authoritative — the graph JSON is the shared contract.
- **No mixed workflows.** A `.md` workflow references only `.md` agent files. Python workflows use `dan.builder`. The visual editor can load output from either.
- **Migration.** `dan.builder.decompiler.decompile()` → Python code. `dan.loader.decompiler.decompile_to_markdown()` → markdown files. The visual editor can export to both.
- **When to use which:**
  - **Markdown** — simple agents, LLM-generated workflows, documentation-first, quick prototyping
  - **Python** — complex parameterized logic, testing/CI, programmatic node generation
  - **Visual editor** — exploration, debugging, demos, visual layout, export to either format

## Notes

- The paper-writing rewrite is the highest-priority task in this subplan — it's the real-world validation that the format and compiler work end-to-end.
- Current status: `examples/paper_writing_md/workflow.md` compiles successfully with `dan.loader.compile_workflow()`; structural parity comparison test against `examples/paper_writing.py` is still pending.
- Composite agents in markdown create a recursive structure: a `.md` file can contain `## Agents` + `## Flow` that reference other `.md` files. This maps directly to `CompositeNode.body_graph` + `sub_graphs` in the graph model.
- The parity checklist is a living document — it should be updated as new builder features are added. It's not a gate; markdown doesn't need 100% parity. The goal is to cover the 80% case for agent-first workflows.
- Linked JSON Schema files are important for LLM agents that produce structured output — the schema drives output normalization (parse → validate → re-prompt on failure). Without linked schemas, markdown agents are limited to free-text output.
- The coexistence policy should be pragmatic: markdown for authoring, Python for testing/CI, visual editor for debugging. They're not competing — they serve different workflows.
