# 11-2: File Audit

**Parent:** [11-structure-review](11-structure-review.md)
**Status:** completed
**Goal:** Audit and improve project file organization: directory layout, module boundaries, import structure, separation of concerns.

## Tasks

### 1. Module boundaries

- [x] 1-1. **Package separation** — Are `models/`, `engine/`, `executors/`, `builder/`, `loader/`, `server/` clearly separated? Document boundaries.
- [x] 1-2. **Circular imports** — Run import analysis; document any cycles.

**Primary files:** `src/dan/__init__.py`, imports across packages

---

### 2. Directory layout

- [x] 2-1. **Consistency** — Is `rag/stores/` vs `providers/` vs `tools/` consistent? Are `sandbox/` and `executors/` in the right place?
- [x] 2-2. **Architecture doc alignment** — Does `docs/architecture.md` match actual layout?

**Primary files:** `docs/architecture.md`, `src/dan/`

---

### 3. Import structure

- [x] 3-1. **Public API** — What does `from dan import ...` expose? Document public vs internal.
- [x] 3-2. **pyproject.toml** — Package entry points, optional dependencies.

**Primary files:** `src/dan/__init__.py`, `pyproject.toml`

---

### 4. Separation of concerns

- [x] 4-1. **graph_mutator** — In `server/` appropriate, or should it live in shared layer (e.g. `dan.mutator`)?
- [x] 4-2. **exec.py** — In `server/` the right home? Shared Python executor for run_python, run_strategy_script.
- [x] 4-3. **Other candidates** — Any modules that span concerns?

**Primary files:** `server/`, `builder/`, `loader/`

---

### 5. Editor internal structure

- [x] 5-1. **Module boundaries** — Store (Zustand) vs components vs lib vs hooks. Clear separation?
- [x] 5-2. **Shared vs editor-specific** — What lives in `lib/` vs component-local? Any blur?

**Primary files:** `editor/src/`

---

### 6. Test layout

- [x] 6-1. **Mirror structure** — Does `tests/test_*/` mirror `src/dan/*`?
- [x] 6-2. **Orphaned tests** — Any misplaced or orphaned test files?

**Primary files:** `tests/`

---

### 7. Examples

- [x] 7-1. **Organization** — Are `examples/` and `examples/vibe_research_md/` consistent?
- [x] 7-2. **Dependencies** — Clear separation from core package?

**Primary files:** `examples/`

---

## Deliverables

- File audit checklist with findings
- List of refactor candidates (e.g. move `graph_mutator` to `dan.mutator`, split `server/`)
- Updated `docs/architecture.md` with layout and import conventions

## Implementation

- **Output:** Write findings to `docs/plans/11-2-findings.md`
- **Method:** Read primary files, run import analysis (e.g. `python -c "import dan; ..."` or grep), document findings per task.

## Decisions

- Audit-first: document before refactoring. Refactors are optional follow-up (see 11-2-findings.md).

## Notes

- Audit-first: document before refactoring. Refactors are optional follow-up.
