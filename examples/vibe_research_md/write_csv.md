---
type: tool
tool_id: save_grid_csv
tool_config: {}
---

> Accepts: results (array), input (object)
> Returns: results (array), csv_path (string)

Writes grid_summary.csv from backtest results. Accepts results directly or extracts from input (loop output). Passes results through for downstream (e.g. each(plot_one)). Saves to output dir.
