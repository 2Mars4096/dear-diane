---
type: code
language: python
---

> Accepts: results (array), input (object), csv_path (string, optional)
> Returns: results (array), csv_path (string)

Writes grid_summary.csv from backtest results. Accepts results directly or extracts from input (loop output). Passes results through for downstream (e.g. each(plot_one)). Saves to output dir.

<!-- Design note: This uses inline code (Option B) because the backtest result
     schema is well-known. For unknown/evolving schemas, prefer the LLM-generated
     code pattern (Option A): an LLM node generates CSV code, then a companion
     run_python tool node executes it. See docs/plans/7-5-general-tool-design.md. -->

```python
import csv
from pathlib import Path

results_val = results if isinstance(results, list) else []

# Prefer input when results is empty — workflow passes [] and shadows loop output
input_val = inputs.get("input")
if not isinstance(input_val, dict):
    input_val = {}

if not results_val and input_val:
    obj = input_val
    obj = obj.get("input", obj) if isinstance(obj, dict) else obj
    obj = obj.get("result", obj) if isinstance(obj, dict) else obj
    if isinstance(obj, dict) and isinstance(obj.get("results"), list):
        results_val = list(obj["results"])

csv_path_val = inputs.get("csv_path")
if isinstance(csv_path_val, str) and csv_path_val.strip():
    out_path = Path(csv_path_val.strip())
else:
    out_path = Path("examples/vibe_research_md/output/grid_summary.csv")
out_path.parent.mkdir(parents=True, exist_ok=True)

cols = ["strategy", "lookback", "skip", "spread_q5_q1_bps", "n_dates", "error"]
with out_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(cols)
    for r in results_val:
        if isinstance(r, dict):
            writer.writerow([
                str(r.get("strategy_name", "")),
                str(r.get("lookback_months", "")),
                str(r.get("skip_months", "")),
                str(r.get("spread_q5_q1_bps", "")),
                str(r.get("n_dates", "")),
                str(r.get("error", "")),
            ])
        else:
            writer.writerow(["", "", "", "", "", str(r)])

result = {"csv_path": str(out_path), "results": results_val}
```
