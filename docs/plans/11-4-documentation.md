# 11-4: Documentation

**Parent:** [11-structure-review](11-structure-review.md)
**Status:** completed
**Goal:** Update project documentation with structure review findings, conventions, and any schema/contract clarifications.

## Tasks

### 1. Architecture doc

- [x] 1-1. **Directory structure** — Update if 11-2 file audit finds layout changes or conventions.
- [x] 1-2. **Import conventions** — Document public vs internal API; add any new patterns.
- [x] 1-3. **Object design principles** — Add section if 11-1 object audit identifies principles worth documenting.

**Primary files:** `docs/architecture.md`

---

### 2. LLM API guide

- [x] 2-1. **Schema reference** — Extend with any `dan_graph_v1` / node / edge clarifications from 11-1.
- [x] 2-2. **Contract examples** — Add if audit finds undocumented contracts.

**Primary files:** `docs/llm-api-guide.md`

---

### 3. Bugs and findings

- [x] 3-1. **bugs.md** — Add any new findings from audits (known limitations, workarounds).
- [x] 3-2. **Failed approaches** — If audit tries an approach that fails, document in Failed Approaches.

**Primary files:** `docs/bugs.md`

---

### 4. Docs vs code drift

- [x] 4-1. **Systematic check** — Do `llm-api-guide`, `architecture`, plan files match current code?
- [x] 4-2. **Outdated sections** — Identify aspirational vs implemented content.

**Primary files:** `docs/`

---

## Deliverables

- Updated `docs/architecture.md`
- Updated `docs/llm-api-guide.md` (if needed)
- Updated `docs/bugs.md` (findings)
- List of doc sections that need future updates

## Implementation

- **Output:** Task 4 → `docs/plans/11-4-drift-findings.md`; Tasks 1–3 → update `docs/` in place after 11-1–11-3 findings available.
- **Method:** Task 4 can run early: compare docs to code, write drift findings. Tasks 1–3 consume 11-1, 11-2, 11-3 findings.

## Decisions

(To be filled during execution)

## Notes

- **Task 4 (docs vs code drift)** — Can run early; does not depend on 11-1–11-3 findings.
- **Tasks 1–3** — Depend on 11-1, 11-2, 11-3 findings; do incrementally as those complete.
- **Final pass** — At end of structure review to consolidate all doc updates.
