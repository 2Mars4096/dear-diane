---
type: composite
---

> Accepts: input (object)
> Returns: result (object)

**Orchestrator + departments (flattened).** Unpack → orchestrator → persist → expand_departments → each(department_run, parallel) → merge_dept_results → governor.

All nodes visible in one drill. Loop structure shows what's inside.

## Agents

- [unpack](unpack_multi_dept_input.md)
- [orchestrator](orchestrator.md)
- [persist_dept_state](persist_dept_state.md)
- [expand_departments](expand_departments.md)
- [department_run](department_run.md)
- [merge_dept_results](merge_dept_results.md)
- [governor](governor.md)

## Flow

unpack.results → orchestrator.results_summary
unpack.iteration → orchestrator.iteration
unpack.max_factors → orchestrator.max_factors
unpack.active_departments → orchestrator.active_departments
unpack.deleted_departments → orchestrator.deleted_departments
orchestrator.to_delete → persist_dept_state.to_delete
orchestrator.to_create → persist_dept_state.to_create
unpack.results → persist_dept_state.results
unpack.strategies_tried → persist_dept_state.strategies_tried
unpack.iteration → persist_dept_state.iteration
unpack.try_more → persist_dept_state.try_more
unpack.start_year → persist_dept_state.start_year
unpack.end_year → persist_dept_state.end_year
unpack.max_factors → persist_dept_state.max_factors
unpack.active_departments → persist_dept_state.active_departments
unpack.deleted_departments → persist_dept_state.deleted_departments
persist_dept_state → expand_departments
expand_departments | each(department_run, parallel: 6)
persist_dept_state.result → merge_dept_results.persist_output
expand_departments_each_department_run.results → merge_dept_results.dept_results
merge_dept_results.results → governor.results
merge_dept_results.strategies_tried → governor.strategies_tried
merge_dept_results.iteration → governor.iteration
merge_dept_results.try_more → governor.try_more
merge_dept_results.start_year → governor.start_year
merge_dept_results.end_year → governor.end_year
merge_dept_results.max_factors → governor.max_factors
merge_dept_results.active_departments → governor.active_departments
merge_dept_results.deleted_departments → governor.deleted_departments
