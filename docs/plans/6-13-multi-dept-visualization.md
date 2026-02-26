# 6-13: Multi-Department Visualization & Layout

**Parent:** [6-phase-3.75-visual-editor-editing](6-phase-3.75-visual-editor-editing.md)
**Status:** in-progress
**Goal:** Visible orchestrator, departments as first-class nodes in the editor, and dynamic backend layout for multi-dept workflows.

## Scope

Editor/visualization features for workflows with orchestrator + departments (e.g. vibe research). Workflow structure lives in the example: `examples/vibe_research_md/WORKFLOW.md`.

## User Vision

1. **Orchestrator visible** — See orchestrator node without drilling. Connect orchestrator to departments.
2. **Departments shown** — Departments as visual nodes/groups. Within each: strategy managers, coders, backtests.
3. **Backend dynamic layout** — Layout computed server-side when serving graphs, so editor loads pre-positioned nodes.

## Tasks

- [x] Backend: dynamic layout on GET /api/graphs/{id}?layout=true (editor requests by default)
- [ ] Workflow: restructure so orchestrator is visible at iteration level (or promote to summary view)
- [ ] Department grouping in editor (loop_groups-style metadata for departments)
- [ ] Editor: optional "outline" or expanded-by-default for key composites

## Decisions

- Layout: Python topological-level placement (no new deps) or networkx multipartite_layout.
- Department nodes: ForEach body per department, or visual grouping only.
- Orchestrator visibility: Restructure iteration_multi_dept to show unpack → orchestrator → departments → governor more clearly.

## Notes

- Current iteration_multi_dept is composite: unpack → orchestrator → persist → strategy_creation_multi → governor. Orchestrator is one level deep; user must drill into iteration to see it.
- Parallel each(dept) and strategy coder retry are example workflow concerns — see examples/vibe_research_md/WORKFLOW.md.
