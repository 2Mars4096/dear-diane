---
type: code
language: python
---

> Accepts: backtest_result (object)
> Returns: result (object)

Bug fixer: catches malformed or failed backtest output, returns a safe structure so the loop never breaks, and generates a plot when cumulative series are present. Port default provides `{}` for backtest_result.

```python
r = backtest_result if isinstance(backtest_result, dict) else {}
if not r:
    result = {}
elif r.get("error"):
    safe = {
        "strategy_name": r.get("strategy_name", ""),
        "error": r.get("error", "backtest failed"),
        "spread_q5_q1_bps": 0,
        "quintiles": [],
        "dates": [],
        "cumulative_quintiles": {},
        "ls_cumulative": [],
        "plot_saved_path": "",
        "factor_saved_path": r.get("factor_saved_path", ""),
        "factor_save_error": r.get("factor_save_error", ""),
        "script_saved_path": r.get("script_saved_path", ""),
        "script_save_error": r.get("script_save_error", ""),
        "build_factor_log": r.get("build_factor_log", ""),
    }
else:
    safe = dict(r)

if r:
    plot_saved_path = ""
    plot_error = ""
    dates = safe.get("dates", [])
    cum = safe.get("cumulative_quintiles", {})
    ls_cum = safe.get("ls_cumulative", [])
    name = str(safe.get("strategy_name", "backtest")) or "backtest"
    safe_name = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in name)

    if dates and cum and ls_cum and not safe.get("error"):
        try:
            from pathlib import Path
            from examples.vibe_research_v2_md.quant_lib.visualize import plot_cumulative_and_ls

            out_dir = Path("examples/vibe_research_v2_md/output/plots")
            out_dir.mkdir(parents=True, exist_ok=True)
            plot_file = out_dir / (safe_name + ".png")
            plot_cumulative_and_ls(
                dates=dates,
                cumulative_quintiles=cum,
                ls_cumulative=ls_cum,
                out_path=plot_file,
                title=name + " - Quintile & LS Cumulative Returns",
                strategy_name=name,
            )
            if plot_file.exists():
                plot_saved_path = str(plot_file)
        except Exception as e:
            plot_error = str(e)

    safe["plot_saved_path"] = plot_saved_path
    if plot_error:
        safe["plot_error"] = plot_error
    result = safe
```
