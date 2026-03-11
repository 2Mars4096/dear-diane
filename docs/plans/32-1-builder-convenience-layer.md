# 32-1: Builder Convenience Layer

**Parent:** [32-workflow-optimization](32-workflow-optimization.md)
**Status:** completed
**Goal:** Add high-level builder methods and syntax sugar that reduce the amount of code the LLM must generate for common workflow patterns, directly improving one-shot generation success.

## Problem

The current builder DSL is powerful but verbose. A simple 3-step chain requires:

```python
wf = workflow("my_chain")
research = wf.llm("research", prompt="Research {topic}")
analyze = wf.llm("analyze", prompt=f"Analyze: {research}")
summarize = wf.llm("summarize", prompt=f"Summarize: {analyze}")
research >> analyze >> summarize
graph = wf.build()
```

A review loop requires manually creating writer + reviewer nodes, a while_loop context manager, a gate, and feedback wiring — roughly 8-12 lines depending on complexity.

Every line is an error opportunity for LLM-generated code. The f-string marker interpolation (`{node_ref}`) and context-manager nesting are the most common failure points in codegen.

## Design

### New convenience methods on `WorkflowBuilder`

#### `wf.chain(*steps) → NodeRef`

Create a linear sequence of LLM nodes with auto-wiring.

```python
wf = workflow("my_chain")
result = wf.chain(
    ("research", "Research {topic}"),
    ("analyze", "Analyze the research findings"),
    ("summarize", "Write a concise summary"),
)
graph = wf.build()
```

Each step is a `(name, prompt)` tuple or `(name, prompt, model)` triple. Returns the last node's `NodeRef`. Auto-wires `>>` between consecutive nodes. `{topic}` in the first step's prompt is auto-detected as a graph-level input variable.

#### `wf.review_loop(writer_prompt, reviewer_prompt, ...) → NodeRef`

Create a writer-reviewer loop with gate in one call.

```python
wf = workflow("reviewed_draft")
draft = wf.review_loop(
    writer_prompt="Write a blog post about {topic}",
    reviewer_prompt="Review this draft for clarity and completeness",
    max_rounds=3,
)
graph = wf.build()
```

Internally creates: writer LLM → while_loop(reviewer LLM → gate). Gate condition: approved or max rounds reached. Returns the gate's approved-output `NodeRef`.

#### `wf.map_reduce(items_expr, map_prompt, reduce_prompt, ...) → NodeRef`

Fan-out over items with parallel processing and aggregation.

```python
wf = workflow("parallel_analysis")
result = wf.map_reduce(
    items_expr="{documents}",
    map_prompt="Summarize this document",
    reduce_prompt="Synthesize all summaries into a unified report",
)
graph = wf.build()
```

Internally creates: for_each(map LLM) → reduce LLM. Returns the reduce node's `NodeRef`.

#### `wf.tool_chain(*steps) → NodeRef`

Chain mixing LLM and tool nodes.

```python
wf = workflow("tool_pipeline")
result = wf.tool_chain(
    ("search", "web_search", {"query": "{topic}"}),
    ("analyze", None, "Analyze these search results"),
    ("save", "file_write", {"path": "report.md"}),
)
graph = wf.build()
```

Each step is `(name, tool_id_or_None, prompt_or_config)`. `tool_id=None` creates an LLM node. Otherwise creates a tool node. Auto-wires sequentially.

### Pipeline operator `|`

```python
search = wf.tool("search", tool_id="web_search")
analyze = wf.llm("analyze", prompt="Analyze findings")
report = wf.llm("report", prompt="Write report")
search | analyze | report
```

`|` is an alias for `>>` that reads more naturally for linear pipelines. Implemented via `__or__` on `NodeRef`.

### Automatic single-port wiring

When `>>` or `|` connects two nodes and the source has exactly one output port and the target has exactly one input port, skip explicit port names. This already works for LLM-to-LLM connections but should be extended to tool and code nodes with single-port configurations.

## Tasks

- [x] 1. `wf.chain()` method
  - [x] 1-1. Add `chain(*steps)` to `WorkflowBuilder`. Each step: `(name, prompt)` or `(name, prompt, model)`. Auto-creates LLM nodes, wires `>>` sequentially. Returns last `NodeRef`. First step's `{var}` patterns auto-detected as graph inputs.
  - [x] 1-2. Tests: 1-step, 3-step, 5-step chain, chain with model override, chain with variables, error on empty steps.
  - [x] 1-3. Compiler integration: `build()` produces valid graph from chain output.

- [x] 2. `wf.review_loop()` method
  - [x] 2-1. Add `review_loop(writer_prompt, reviewer_prompt, *, name="review", max_rounds=3, writer_model=None, reviewer_model=None)` to `WorkflowBuilder`. Creates writer LLM + while_loop(reviewer LLM). Writer output wired to reviewer `text` input.
  - [x] 2-2. Tests: basic review loop, custom max_rounds, review loop after chain, model overrides.
  - [x] 2-3. Round-trip: generated graph matches hand-built while_loop equivalent.

- [x] 3. `wf.map_reduce()` method
  - [x] 3-1. Add `map_reduce(items_expr, map_prompt, reduce_prompt, *, name="map_reduce", map_model=None, reduce_model=None)` to `WorkflowBuilder`. Creates for_each + map LLM (inside body) + reduce LLM.
  - [x] 3-2. Tests: basic map_reduce, model override, chained after other nodes.

- [x] 4. `wf.tool_chain()` method
  - [x] 4-1. Add `tool_chain(*steps)` to `WorkflowBuilder`. Each step: `(name, tool_id, config_or_prompt)`. `tool_id=None` → LLM node. Otherwise → tool node. Auto-wires sequentially.
  - [x] 4-2. Tests: tool-only chain, mixed tool+LLM chain, chain with variables.

- [x] 5. Pipeline `|` operator
  - [x] 5-1. Add `__or__` to `NodeRef` as alias for `__rshift__` (`>>`).
  - [x] 5-2. Tests: `a | b | c` produces same graph as `a >> b >> c`.

- [x] 6. Automatic single-port wiring
  - [x] 6-1. `DEFAULT_OUTPUT_PORTS` and `DEFAULT_INPUT_PORTS` in `compiler.py` already map `tool_operator` and `code_operator` to default ports. The gap is nodes with **custom** multi-port configs (e.g. tool node with `result` + `status` outputs). Add a heuristic: if a node has exactly one non-default output port, auto-select it; otherwise require explicit port.
  - [x] 6-2. Tests: tool >> llm, code >> llm without explicit ports (confirm existing behavior). Add tests for custom multi-port nodes that should error clearly.
  - [x] 6-3. Clear error message when ambiguous (multiple ports, none specified). Include available port names in the error.

- [x] 7. Codegen prompt updates
  - [x] 7-1. Update `CodegenPromptBuilder` few-shot examples to prefer convenience methods for common patterns.
  - [x] 7-2. No separate RAG index update needed — `SelfKnowledgeIndex` auto-indexes `docs/llm-api-guide.md` on startup, so updating the guide (task 8-1) is sufficient.

- [x] 8. Documentation
  - [x] 8-1. Update `docs/llm-api-guide.md` with convenience methods section (chain, review_loop, map_reduce, tool_chain, `|` operator).
  - [x] 8-2. Update `docs/architecture.md` if needed.
  - [x] 8-3. Changelog entry.

## Files

| File | Action |
|------|--------|
| `src/dan/builder/builder.py` | Modify — add `chain()`, `review_loop()`, `map_reduce()`, `tool_chain()` |
| `src/dan/builder/refs.py` | Modify — add `__or__` to `NodeRef` |
| `src/dan/builder/compiler.py` | Modify — extend single-port auto-wiring to tool/code nodes |
| `src/dan/meta/planner.py` | Modify — update `CodegenPromptBuilder._SYSTEM_PROMPT` and few-shot examples |
| `docs/llm-api-guide.md` | Modify — add convenience methods section |
| `tests/test_builder/test_convenience.py` | Create — tests for all new methods |

## Decisions

- `review_loop()` uses explicit `body.edge(writer["text"], reviewer["text"])` instead of `writer >> reviewer` because the reviewer has an explicit `text` input port — the default `>>` wiring targets `input`, leaving the required `text` port unconnected and failing validation.

## Notes

- The convenience methods compile to the same graph primitives via the existing compiler. They don't add new node types or edge types.
- `chain()` and `tool_chain()` intentionally don't support branching. For non-linear topologies, use `>>` + context managers.
- `review_loop()` uses a specific gate condition pattern. For custom loop conditions, use `wf.while_loop()` directly.
- The `|` operator is a pure alias for `>>`. It exists because `a | b | c` reads more naturally for pipelines.
- Automatic single-port wiring should fail loudly (clear error message with port names) when it can't infer. Silent wrong wiring is worse than requiring explicit ports.
- These methods make codegen prompts simpler because the LLM targets fewer, higher-level APIs. The primary measure of success is reduced codegen failure rate.
