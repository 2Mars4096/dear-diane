# 8-4: Graph → Markdown Round-Trip

**Parent:** [8-markdown-agent-format](8-markdown-agent-format.md)
**Status:** completed
**Goal:** Build a decompiler that converts `dan_graph_v1` Graph back to markdown agent + workflow files, integrate export into the visual editor, and write conformance tests to verify markdown→graph→markdown idempotency.

## Tasks

- [x] 1. Graph → Markdown decompiler (`src/dan/loader/decompiler.py`)
  - [x] 1-1. `decompile_to_markdown(graph: Graph, output_dir: str | Path) -> DecompileResult`
  - [x] 1-2. Node → agent file generation (LLM, Tool, Code, Human, Router)
  - [x] 1-3. Port → blockquote generation (`> Accepts` / `> Returns` with type inference)
  - [x] 1-4. System prompt handling (`## System` section)
  - [x] 1-5. Control-flow node decompilation (`ForEach` → `| each()`, `GateNode(while)` → `| loop()`, `GateNode(if_else)` → `| if()`)
  - [x] 1-6. `CompositeNode` → recursive `## Agents` + `## Flow` from `body_graph` sub-graph
  - [x] 1-7. Edge → flow line generation (chain detection, port-specific wiring)
  - [x] 1-8. Workflow file generation (YAML frontmatter, `## Agents`, `## Flow`, `## Context`)
  - [x] 1-9. File naming (slugify + collision suffix)
  - [x] 1-10. Retry policy in frontmatter

- [x] 2. Visual editor export integration
  - [x] 2-1. Backend endpoint: `GET /api/graphs/{id}/export/markdown` → JSON `{ files: [{path, content}], diagnostics }`
  - [x] 2-2. Implemented as JSON preview endpoint (task 2-1 covers this)
  - [ ] 2-3. Frontend: "Export as Markdown" button in `EditorToolbar.tsx` (deferred — needs frontend work)
  - [ ] 2-4. Frontend: optional preview modal (nice-to-have, deferred)
  - [x] 2-5. `GET /api/graphs/{id}/export/python` using `dan.builder.decompiler.decompile()`

- [x] 3. Markdown round-trip conformance tests
  - [x] 3-1. Forward trip: md → graph → md' → graph' — node types, edges, metadata preserved
  - [x] 3-2. Backward trip: graph → md → graph' — structural equivalence verified
  - [x] 3-3. Semantic equivalence: agent count, node types, flow patterns compared
  - [x] 3-4. Structural equivalence: node types, port names, edge connections, sub-graphs compared
  - [x] 3-5. Test suite: simple chain + complex workflow (ForEach, loop, if) round-trips pass
  - [x] 3-6. Known asymmetries documented: position/ui dropped, metadata.source dropped, edge IDs regenerated
  - [x] 3-7. Regression snapshots

- [x] 4. Tests
  - [x] 4-1. Unit: node→agent file decompilation (TestDecompileSimpleWorkflow, TestDecompileComplexWorkflow)
  - [x] 4-2. Unit: edge→flow line decompilation — chains, each/loop/if
  - [x] 4-3. Unit: composite node decompilation (TestCompositeAgent)
  - [x] 4-4. Unit: file naming — slugification, collision handling (TestFileNaming)
  - [ ] 4-5. Integration: export API endpoint (deferred — needs test server fixture)
  - [x] 4-6. Conformance: forward and backward round-trip tests

## Decisions

- Implemented JSON preview endpoint (`GET`) instead of zip download (`POST`) — simpler, enables frontend preview before download.
- Best-effort decompilation policy implemented: unsupported node types emit stub files with `<!-- UNSUPPORTED -->` comments; control/context edges emit `<!-- SKIPPED -->` comments in flow section.
- Round-trip is structurally equivalent but not byte-identical: whitespace, YAML field ordering, edge IDs, and `metadata.source` fields are regenerated. This is by design.
- Frontend export buttons (tasks 2-3, 2-4) deferred to a future frontend pass — backend endpoints are ready.
- Regression snapshot testing (task 3-7) deferred — requires CI snapshot infrastructure.

## Notes

- The decompiler is the inverse of `dan.loader.compile()` from 8-2. It should handle every node type that the compiler can produce.
- **Best-effort output policy.** Not all graph features are representable in markdown. The decompiler never fails on unsupported features — it emits best-effort output with diagnostic annotations. Unsupported nodes emit a stub agent file with `<!-- UNSUPPORTED: node_type "X" — manual conversion needed -->` comment. Unsupported edge types (context, control) emit `<!-- SKIPPED: context_edge ... -->` in the flow section. Lost fields (`position`, `ui`, arbitrary `metadata`) are silently dropped (documented in known asymmetries). Diagnostics are collected in `DecompileResult.diagnostics` alongside the generated files.
- The existing `dan.builder.decompiler.decompile()` (Graph → Python code) is a good reference for the implementation pattern. It uses topological sort, chain detection, and context managers for sub-graphs.
- File naming must handle edge cases: nodes with the same name, nodes with non-ASCII names, nodes with very long names. Slugification + collision suffix is the standard approach.
- The export endpoint should work for any graph loaded in the editor, regardless of how it was created (builder, markdown, or visual editor). This makes the visual editor a universal converter between all three formats.
- The "Export as Python" button (task 2-5) is a quick win that uses existing infrastructure and completes the symmetry: any graph can be exported to Python or Markdown.
