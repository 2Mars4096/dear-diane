# 11-3: Cross-Cutting Audit

**Parent:** [11-structure-review](11-structure-review.md)
**Status:** completed
**Goal:** Audit serialization paths, naming conventions, validation contracts, schema evolution, legacy code, config handling, cross-surface feature parity, and trust boundaries.

## Tasks

### 1. Data flow and serialization paths

- [x] 1-1. **End-to-end trace** — JSON file → Graph model → builder/loader → mutator → editor → API. Document the path.
- [x] 1-2. **Lossy conversions** — Any places where data is dropped or coerced? (Markdown round-trip issues were fixed in 7-8; check JSON ↔ Python ↔ TS.)
- [x] 1-3. **Dict vs model** — Where are raw dicts used instead of typed Pydantic models?

**Primary files:** `graphAdapter.ts`, `builder/`, `loader/`, `server/`

---

### 2. Naming and casing conventions

- [x] 2-1. **Python vs JSON vs TypeScript** — Python `snake_case`, JSON/TS `camelCase` or `snake_case`? Document convention.
- [x] 2-2. **API payloads** — Which convention in REST/WebSocket?
- [x] 2-3. **graphAdapter** — Reference 11-1 task 6-2 findings; check naming consistency in conversion (no duplicate audit).

**Primary files:** `editor/src/lib/graphAdapter.ts`, API handlers

---

### 3. Validation and error contracts

- [x] 3-1. **When validation runs** — Design-time (builder/loader) vs runtime (engine)?
- [x] 3-2. **Invalid graph semantics** — Fail-fast vs partial execution?
- [x] 3-3. **Centralization** — Are validation rules duplicated across surfaces?

**Primary files:** `validation/`, `builder/`, `loader/`, `engine/`

---

### 4. Schema evolution and versioning

- [x] 4-1. **dan_graph_v1** — Migration strategy for new fields or node types?
- [x] 4-2. **Backward compatibility** — How are new optional fields introduced?
- [x] 4-3. **Migration layer** — Does one exist for older graphs?

**Primary files:** `models/`, `migration/`

---

### 5. Legacy and dead code

- [x] 5-1. **Legacy node types** — See 11-1 task 1-3 for migration status. Identify orphaned code paths from legacy migration.
- [x] 5-2. **Orphaned paths** — Any other dead code or unused branches?

**Primary files:** `models/control_flow.py`, `migration/`, `registry.py`

---

### 6. Cross-surface feature parity

- [x] 6-1. **Feature matrix** — Builder vs Loader vs Editor. Which features does each support?
- [x] 6-2. **Gaps** — ContextEdge, control edges, `state_schema` — any surface missing support?
- [x] 6-3. **Round-trip loss** — Features that don't survive round-trip?

**Primary files:** `builder/`, `loader/`, `editor/`, `decompiler.py`

---

### 7. Config and env handling

- [x] 7-1. **Config loading** — `.env`, `EngineConfig`, server config. Where does config live?
- [x] 7-2. **Schema for config** — Is config part of graph schema or external? Optional deps (anthropic, pypdf) patterns.

**Primary files:** `engine/executor.py`, `server/app.py`, `pyproject.toml`, `.env.example`

---

### 8. Trust and sandbox boundaries (lower priority)

- [x] 8-1. **Code executor, shell tool, sandbox** — Where are trust boundaries defined?
- [x] 8-2. **Model vs runtime** — Is sandbox config part of graph schema or external?

**Primary files:** `sandbox/`, `executors/code.py`, `tools/`

---

## Deliverables

- [x] Cross-cutting audit checklist with findings → [11-3-findings](11-3-findings.md)
- [x] Recommendations for naming, validation centralization, schema evolution, config handling

## Implementation

- **Output:** [11-3-findings](11-3-findings.md)
- **Method:** Read primary files, trace data flow, document findings per task.
- **Parallel note:** Tasks 2-3 and 5-1 reference 11-1. If 11-1 not done: do lightweight check (grep graphAdapter, grep IfElseNode/WhileLoopNode); note "full audit requires 11-1 findings".

## Decisions

(To be filled during execution)

## Notes

- Complements 11-1 (object) and 11-2 (file) with operational and consistency checks.
