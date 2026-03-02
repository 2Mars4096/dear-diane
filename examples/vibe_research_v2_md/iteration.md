---
type: composite
---

> Accepts: input (object)
> Returns: result (object)

**One iteration of the research loop.** Orchestrator reviews state → assigns themes/actions to 3 fixed departments (MOM, REV, FUND) → departments execute one-by-one (MOM → REV → FUND) → merge results → governor decides try_more.

Loop state fields (results, strategies_tried, iteration, start_year, end_year, max_factors) are injected by the while-gate's state_schema into every node inside this composite — no explicit state-threading edges needed. Only actual data-flow edges remain.

## Agents

- [orchestrator](orchestrator.md)
- [dept_MOM](department.md)
- [rev_after_mom](pass_assignment_after_prev.md)
- [dept_REV](department.md)
- [fund_after_rev](pass_assignment_after_prev.md)
- [dept_FUND](department.md)
- [merge](merge.md)
- [governor](governor.md)

## Flow

orchestrator.dept_MOM → dept_MOM.assignment
orchestrator.dept_REV → rev_after_mom.assignment
dept_MOM → rev_after_mom.prev_result
rev_after_mom.assignment → dept_REV.assignment
orchestrator.dept_FUND → fund_after_rev.assignment
dept_REV → fund_after_rev.prev_result
fund_after_rev.assignment → dept_FUND.assignment
dept_MOM → merge.dept_MOM_result
dept_REV → merge.dept_REV_result
dept_FUND → merge.dept_FUND_result
merge.new_results → governor.new_results
merge.new_strategies → governor.new_strategies
