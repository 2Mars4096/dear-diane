# 32-4: Progressive NL Refinement

**Parent:** [32-workflow-optimization](32-workflow-optimization.md)
**Status:** completed
**Goal:** Make structural workflow modifications via NL follow-ups reliable — "add a review loop", "fan out this step", "insert a validation gate" — without triggering a full rebuild.

## Problem

DAN has two generation paths:
1. **Builder codegen** (24-1) — one-shot generation from scratch. Reliable for new workflows but rebuilds the entire graph.
2. **Mutation path** (`GraphMutator`) — incremental edits via `MUTATION_TOOL_SCHEMA`. Good for small changes (rename, edit prompt) but fragile for structural changes (add loop, fan out).

The gap: users start with a simple workflow and want to progressively add complexity. "Add a review loop after the summarize step" should modify the existing graph, not generate a new one. But `GraphMutator` requires exact node IDs, port names, and edge types — things the LLM frequently gets wrong for structural changes.

## Design

### Structural mutation macros

High-level graph transformations that map NL intents to reliable, validated modifications:

| NL Intent | Macro | What It Does |
|-----------|-------|-------------|
| "Add a review loop after X" | `wrap_in_review_loop(graph, node_id, reviewer_prompt, max_rounds)` | Inserts reviewer + gate after target node, rewires downstream edges through the loop |
| "Fan out X to process items in parallel" | `fan_out_node(graph, node_id, items_expr)` | Wraps target node in for_each, adds reduce node downstream |
| "Add a validation gate between X and Y" | `insert_validator(graph, source_id, target_id, rules)` | Inserts validator node on the edge between source and target |
| "Add a tool step before/after X" | `insert_tool(graph, anchor_id, tool_id, config, position)` | Inserts tool node before/after anchor, rewires edges |
| "Make X and Y run in parallel" | `parallelize(graph, node_ids)` | Wraps listed nodes in parallel_subagents, adds merge |
| "Remove the review loop" | `unwrap_loop(graph, loop_node_id)` | Removes gate + feedback edges, straightens the pipeline |

### Macro contract

Each macro:
1. Validates that target node(s) exist and the operation is structurally valid
2. Performs the graph transformation at the `Graph` model level
3. Runs `validate_graph()` on the result
4. Returns the modified graph on success, or a clear error on failure

### Node resolution

The user says "the summarize step", not "node_abc123". The dispatcher needs to resolve natural-language node references:

1. **Exact name match** — node ID or label matches the user's text
2. **Fuzzy name match** — Levenshtein distance on node labels/IDs
3. **Prompt content match** — keyword overlap with node prompt text
4. **Position match** — "the last step", "the first node", "step 3"

### Graph context for follow-up codegen

When no macro matches and the system falls through to codegen for a follow-up turn:

1. `summarize_graph(graph)` produces a compact text representation: node list (id, type, prompt snippet), edge list, topology features
2. The codegen prompt includes this summary + the original build prompt + the follow-up request
3. This prevents the "clean slate" problem where the LLM produces an unrelated workflow

## Tasks

- [x] 1. Structural mutation macros
  - [x] 1-1. `wrap_in_review_loop(graph, node_id, reviewer_prompt, max_rounds)` — insert reviewer + gate after target, rewire downstream edges through loop. Handle: target is terminal node; target has multiple downstream edges.
  - [x] 1-2. `fan_out_node(graph, node_id, items_expr)` — wrap target in for_each, add reduce downstream. Handle: target has downstream chain.
  - [x] 1-3. `insert_validator(graph, source_id, target_id, rules)` — insert validator on edge. Handle: multiple edges between source and target.
  - [x] 1-4. `insert_tool(graph, anchor_id, tool_id, config, position="after")` — insert tool before/after anchor, rewire edges.
  - [x] 1-5. `parallelize(graph, node_ids)` — wrap nodes in parallel_subagents + add merge. Handle: nodes with existing downstream dependencies.
  - [x] 1-6. `unwrap_loop(graph, loop_node_id)` — remove loop structure, reconnect writer directly to downstream.
  - [x] 1-7. All macros: validate inputs → transform → `validate_graph()` → return modified graph or structured error.

- [x] 2. Node resolution
  - [x] 2-1. `resolve_node(graph, reference_text) → node_id | None` — match node by name, label, prompt keyword, or position.
  - [x] 2-2. Ambiguity handling: if multiple nodes match, return top-2 candidates for clarification.
  - [x] 2-3. Tests: exact match, fuzzy match, prompt keyword match, "the last step", "step 3".

- [x] 3. Macro dispatcher
  - [x] 3-1. `StructuralMutationDispatcher` — maps NL intent keywords to macros. Keywords: "add review loop", "fan out", "parallelize", "add validation", "add tool", "insert", "remove loop", "unwrap".
  - [x] 3-2. Extract target node reference + macro parameters from the user's follow-up message.
  - [x] 3-3. When macro matches: resolve node → execute macro → return modified graph.
  - [x] 3-4. When no macro matches: return `None` (caller falls through to codegen).

- [x] 4. Planner integration
  - [x] 4-1. In `planner.py`, detect structural follow-up intent before choosing codegen vs mutation path.
  - [x] 4-2. If `StructuralMutationDispatcher` returns a result, use it. If not, fall through to codegen with graph context.
  - [x] 4-3. `summarize_graph(graph)` — compact text: node list (id, type, prompt first 50 chars), edge list, loop/fan-out features.
  - [x] 4-4. Inject graph summary + original build prompt into codegen system prompt when `client_graph_revision > 0`.

- [x] 5. Tests
  - [x] 5-1. Each macro: before/after graph comparison (node counts, edge counts, validation).
  - [x] 5-2. Node resolution: exact, fuzzy, prompt keyword, positional.
  - [x] 5-3. Dispatcher: NL intent → correct macro selection.
  - [x] 5-4. Integration: 3-turn sequence → progressive graph evolution (not rebuild).
  - [x] 5-5. Regression: existing `GraphMutator` mutation tests still pass.

- [x] 6. Documentation
  - [x] 6-1. Update `docs/llm-api-guide.md` with structural mutation macros section.
  - [x] 6-2. Changelog entry.

## Files

| File | Action |
|------|--------|
| `src/dan/meta/structural_mutations.py` | Create — macros, node resolver, dispatcher |
| `src/dan/meta/planner.py` | Modify — structural intent detection, macro dispatch, graph context injection |
| `src/dan/meta/planner.py` | Modify — graph summary injection into `CodegenPromptBuilder` for follow-up turns |
| `src/dan/validation/graph.py` | Read — post-macro validation |
| `tests/test_meta/test_structural_mutations.py` | Create — macro + dispatcher tests |

## Decisions

- (filled in during execution)

## Notes

- Macros operate on the `Graph` model directly, not through the builder DSL. They're graph-level transformations, not codegen. When the target node lives inside a `sub_graph` (e.g., inside a `for_each` or `composite`), the macro must traverse into sub-graphs rather than only scanning top-level nodes.
- Each macro must handle edge cases: target node inside a sub-graph, target already in a loop, downstream nodes with incompatible port schemas. When a macro can't handle the case, it returns an error rather than producing a broken graph.
- The dispatcher uses keyword matching + node-name similarity, not a full LLM call. This keeps it fast (<100ms).
- "Remove the review loop" is included because users often experiment: add a loop, test it, decide it's too complex, remove it.
- Graph context for follow-up codegen is critical: without it, the LLM has no idea what the existing workflow does and produces unrelated output.
- The existing `GraphMutator` + `MUTATION_TOOL_SCHEMA` path is unchanged. It continues to handle fine-grained edits (rename node, change prompt, add single edge). Structural macros handle the medium-granularity gap between "edit a prompt" and "rebuild from scratch".
