---
type: composite
---

> Accepts: input (object)
> Returns: result (object)

One iteration: unpack → orchestrator → persist state → strategy_creation_multi. Governor normalizes output.

## Agents

- [unpack](unpack_multi_dept_input.md)
- [orchestrator](orchestrator.md)
- [persist_dept_state](persist_dept_state.md)
- [strategy_creation_multi](strategy_creation_multi.md)
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
persist_dept_state → strategy_creation_multi
strategy_creation_multi → governor
