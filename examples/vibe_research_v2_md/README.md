# Vibe Research v2 — State Schema Validation

Refactored version of `vibe_research_md/` that fully leverages Plan 7-6 features:
**loop-scoped state** (`state_schema`/`state_defaults`), **code node port defaults**,
and **scope injection**.

Runtime behavior is identical to v1 given the same inputs and LLM responses.

## What Changed

### 1. Eliminated all `try/except NameError` boilerplate

v1 code nodes used defensive blocks like:
```python
try:
    results = list(results) if isinstance(results, list) else []
except NameError:
    results = []
```

In v2, these are gone. Two mechanisms make them unnecessary:
- **Scope injection** — loop state fields (`results`, `strategies_tried`, `iteration`, `start_year`, `end_year`, `max_factors`) are automatically injected into every body node's inputs by the scheduler during `_iterate_cycle`.
- **Code node port defaults** — declared input ports with `json_schema` types get type-appropriate defaults (`array` → `[]`, `integer` → `0`, etc.) when no edge or scope provides a value.

### 2. Removed scope-redundant edges in department pipeline

`start_year`/`end_year` are loop state fields available via scope injection. In v1, explicit edges threaded them from `dept_gate` to `strategy_to_item` and `run_strategy`. In v2, scope provides them directly — 4 edges removed.

### 3. Cleaner port declarations

All code nodes now declare their full input signatures with proper types in `> Accepts:` lines, documenting what each node expects (from scope, edges, or defaults).

## Metrics

### Code Reduction (code block lines)

| File | v1 | v2 | Δ |
|------|----|----|---|
| governor.md | 71 | 37 | −34 (−48%) |
| entry.md | 22 | 12 | −10 (−45%) |
| pass_assignment_after_prev.md | 12 | 2 | −10 (−83%) |
| dept_gate.md | 20 | 17 | −3 (−15%) |
| dept_halt.md | 5 | 1 | −4 (−80%) |
| load_tracking.md | 18 | 15 | −3 (−17%) |
| save_tracking.md | 109 | 98 | −11 (−10%) |
| bug_fixer.md | 60 | 56 | −4 (−7%) |
| merge.md | 27 | 27 | — |
| strategy_to_item.md | 14 | 14 | — |
| **Total** | **358** | **279** | **−79 (−22%)** |

**Boilerplate specifically:** 14 `try/except NameError` blocks totaling 79 lines → 0 lines (100% removed).

### Edge Reduction

| Scope | v1 | v2 | Δ |
|-------|----|----|---|
| workflow_multi_dept.md | 1 | 1 | — |
| iteration.md | 12 | 12 | — |
| department.md | 28 | 24 | −4 |
| **Total data edges** | **41** | **37** | **−4** |

Note: v1 already uses `state_schema` on the loop, which eliminated ~46 state-threading edges from the original pre-state_schema design (~87 edges). The v2 refactoring removes 4 more scope-redundant edges and focuses on code boilerplate.

## Architecture

```
entry → while(try_more, max: 60) {
    orchestrator → [dept_MOM, dept_REV, dept_FUND] → merge → governor
} → write_csv
```

Same structure as v1. The while-gate's `state_schema` declares 7 loop state fields with defaults. Scope injection makes them available to all body nodes automatically.

## Running

```bash
python examples/vibe_research_v2_md/run_multi_dept.py --start 2012 --end 2022 --max-factors 10
python examples/vibe_research_v2_md/run_multi_dept.py --debug-events
python examples/vibe_research_v2_md/run_multi_dept.py --build-only
python examples/vibe_research_v2_md/run_multi_dept.py --emit-python
```

## Requirements

- `DAN_LLM_API_KEY` in `.env` or environment
- `AUTO_QUANT_ROOT` pointing to data directory (CRSP + Compustat)
- `pip install matplotlib` for plots
