---
type: code
language: python
---

> Accepts: dept_MOM_result (object), dept_REV_result (object), dept_FUND_result (object)
> Returns: new_results (array), new_strategies (array)

Merges department outputs into consolidated lists. Each department output IS the backtest result dict (from bug_fixer), containing quintiles, spread_q5_q1_bps, strategy_name, error, etc. Skipped/halted departments produce empty dicts which are filtered out.

```python
dept_inputs = []
for name in ["dept_MOM_result", "dept_REV_result", "dept_FUND_result"]:
    val = inputs.get(name)
    if isinstance(val, dict) and val:
        dept_inputs.append(val)

new_results = []
new_strategies = []
for c in dept_inputs:
    sname = c.get("strategy_name", "")
    # Keep per-strategy outcomes (including failures) so users can see immediate results.
    if sname:
        new_results.append(c)
    elif not c.get("error") and c.get("quintiles"):
        new_results.append(c)
    if sname:
        new_strategies.append({
            "strategy_id": sname,
            "strategy_type": c.get("strategy_type", ""),
            "params": c.get("params", {}),
            "spread_q5_q1_bps": c.get("spread_q5_q1_bps", 0),
        })

result = {
    "new_results": new_results,
    "new_strategies": new_strategies,
}
```
