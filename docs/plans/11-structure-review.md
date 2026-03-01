# 11: Phase 7.1 — Structure Review (Files & Objects)

**Status:** completed
**Goal:** Audit and improve project file organization and object/data structure design. This is an **intermediate review-and-patch phase** — no new features, only documentation, findings, and targeted fixes for maintainability and correctness.

## Rationale

The codebase has grown across many phases. Multiple authoring surfaces (builder, loader, editor) share `dan_graph_v1`. Upcoming work (10-9 meta-orchestrator, 7-9 async subagents) will touch many subsystems. A structured audit reduces risk and establishes a clean baseline.

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [11-1](11-1-object-audit.md) | Object Audit | Node types, graph schema, edges, execution state, API contracts, TS parity | `models/`, `engine/state.py`, `editor/src/types/graph.ts`, `validation/` |
| [11-2](11-2-file-audit.md) | File Audit | Directory layout, module boundaries, imports, separation of concerns | `src/dan/`, `editor/`, `examples/`, `tests/` |
| [11-3](11-3-cross-cutting-audit.md) | Cross-Cutting Audit | Serialization, naming, validation, schema evolution, legacy, config, trust boundaries | `graphAdapter.ts`, `builder/`, `loader/`, `server/` |
| [11-4](11-4-documentation.md) | Documentation | Update architecture, llm-api-guide, findings in bugs.md | `docs/` |

## Sequencing

1. **11-1 (Object audit)** first — stabilizes data model and contracts; file layout decisions depend on it.
2. **11-2 (File audit)** second — layout and boundaries.
3. **11-3 (Cross-cutting)** — serialization, naming, validation, schema evolution, legacy.
4. **11-4 (Documentation)** — Task 4 (docs vs code drift) can run early; Tasks 1–3 depend on 11-1–11-3 findings. Final pass at end to consolidate.

## Dependencies

- **7-8** — Already fixed several object-level issues (ContextEdge, gate defaults, round-trip). Object audit treats 7-8 as baseline.
- **7-6** — Node state simplification may change `state_schema`, `state_defaults`. Consider scheduling object audit after 7-6 if that work is imminent.
- **7-9** — Async parallel subagents may affect execution state. Object audit should flag those areas.

## Decisions

(To be filled during execution)

## Notes

- Audit-first: document findings before committing to refactors.
- Effort estimate: ~5–8 days for full review with documentation; ~2–3 days for audit checklist only.

## Implementation (parallel)

- **Phase 1 (parallel):** 11-1, 11-2, 11-3, 11-4 Task 4 — all can run concurrently. Output: `docs/plans/11-1-findings.md`, `11-2-findings.md`, `11-3-findings.md`, `11-4-drift-findings.md`.
- **Phase 2 (sequential):** 11-4 Tasks 1–3 — consume findings, update `docs/architecture.md`, `llm-api-guide.md`, `bugs.md`.
