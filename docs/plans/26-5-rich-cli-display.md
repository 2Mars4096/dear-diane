# 26-5: Rich CLI Display

**Parent:** [26-always-on-service](26-always-on-service.md)
**Status:** completed
**Goal:** Rich terminal output for `dan-chat`: ASCII DAG for `/show`, streaming node-by-node progress during `/run`, workflow diff on mutations, Rich table for `/list`.

## Context

- `dan-chat` REPL in `cli/chat.py` has `/show`, `/list`, `/run`, `/save`, etc.
- `/show` currently prints nodes and edges as simple text (node names + edge source→target).
- `/list` prints workflow names as a simple list.
- `/run` streams events and prints them as text lines.
- Mutation diffs are shown as text summaries.
- Rich library is optional: `_try_import_rich()` in `cli/__init__.py` returns `(Console, rich)` or `(None, None)`.
- `dan-run` has `TUIDisplay`, `PlainDisplay`, `QuietDisplay`, `JSONLDisplay` — rich TUI with status panels.
- `builder/decompiler.py` has `decompile(graph)` for Python code output.
- Graph structure: nodes with `id`, `name`, `node_type`, edges with `source_node`, `target_node`, `source_port`, `target_port`, `edge_type`.

## Tasks

- [x] 1. **ASCII DAG renderer**
  - [x] 1-1. Create `src/dan/cli/dag_display.py`. Topological sort → column assignment (each node gets a column).
  - [x] 1-2. Box-drawing characters for node boxes (`┌─┐│└─┘`), line-drawing for edges (`│`, `─`, `┬`, `└`, `├`).
  - [x] 1-3. Node box: `[type] name`. Color via Rich if available, but do not require a one-to-one color contract with the editor for v1.
  - [x] 1-4. Compact layout: max width 120 chars, wrap long names.
  - [x] 1-5. Used by `/show` in `dan-chat`, initially for the current/root graph only. Show sub-graph counts and entry points in v1 rather than recursively rendering every nested layer.

- [x] 2. **Streaming run progress**
  - [x] 2-1. During `/run`, show a live-updating display.
  - [x] 2-2. Each node: status icon (⏳ running, ✓ done, ✗ failed, ⏭ skipped), name, elapsed time.
  - [x] 2-3. Update in-place using `\r` / ANSI escape codes (or Rich Live if available).
  - [x] 2-4. Final summary: total time, nodes completed/failed/skipped, and token/cost data when present on the streamed events or follow-up run status payload.

- [x] 3. **Mutation diff display**
  - [x] 3-1. When a mutation is proposed, show a structured diff: added nodes (green +), removed nodes (red -), modified nodes (yellow ~), added/removed edges.
  - [x] 3-2. Format like a mini git diff.
  - [x] 3-3. Used by the mutation confirmation flow in `dan-chat`.

- [x] 4. **Rich table for `/list`**
  - [x] 4-1. Replace plain list with a Rich Table (or padded columns if Rich unavailable).
  - [x] 4-2. Columns: Name, Nodes, Edges, Last Modified.
  - [x] 4-3. Sort by last modified descending.

- [x] 5. **`/show` enhancements**
  - [x] 5-0. Extend the REPL parser in `cli/chat.py` so `/show`, `/show --code`, `/show --json`, and `/show --stats` are first-class command variants instead of a single exact-match string.
  - [x] 5-1. `/show --code` decompiles to Python builder DSL (via `decompile()`).
  - [x] 5-2. `/show --json` dumps raw graph JSON.
  - [x] 5-3. `/show --stats` shows node count, edge count, sub-graph count, estimated complexity.

- [x] 6. **Graceful degradation**
  - [x] 6-1. All rich output degrades gracefully when Rich is not installed.
  - [x] 6-2. Plain text fallback with basic formatting.
  - [x] 6-3. Box-drawing uses ASCII (`+`, `-`, `|`) if terminal doesn't support Unicode. Detect via `sys.stdout.encoding`.

- [x] 7. **Tests**
  - [x] 7-1. Unit tests for ASCII DAG renderer (known graph → expected output string).
  - [x] 7-2. Diff display tests.
  - [x] 7-3. Table formatting tests.
  - [x] 7-4. Snapshot tests for complex graphs.

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/cli/dag_display.py` | New ASCII DAG renderer |
| `src/dan/cli/chat.py` | Integrate DAG, run progress, diff, table, `/show` flags |
| `src/dan/cli/__init__.py` | Reuse `_try_import_rich()` from shared CLI helpers |
| `tests/test_cli/test_dag_display.py` | DAG renderer tests |
| `tests/test_cli/test_chat.py` | `/show` flag parsing and rich display integration tests |
| `tests/test_cli/test_rich_display.py` | Diff, table, snapshot tests |

## Decisions

- DAG renderer uses Kahn's algorithm consistent with `dan.engine.scheduler` and `dan.server.layout` patterns.
- Box-drawing falls back to ASCII (`+`, `-`, `|`) when `sys.stdout.encoding` doesn't contain `utf`.
- Mutation diff uses raw ANSI escape codes rather than Rich to keep the module dependency-free.
- `RunProgressTracker` is a standalone class (not integrated into the REPL event loop yet) — ready for `/run` integration when run streaming lands in chat.
- Existing `_format_graph_summary()` and `_format_workflow_list()` functions kept in `chat.py` for backward compatibility; call sites replaced.

## Notes

- Effort: ~2 days.
- No dependencies on other 26-* sub-plans (purely presentation layer).
- V1 should prefer readable, deterministic output over perfect graph-layout aesthetics; recursive nested graph rendering can stay out of scope initially.
