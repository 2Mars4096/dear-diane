---
type: code
language: python
---

> Accepts: department_code (string), updated_tracking (object), backtest_result (object)
> Returns: result (object)

Persists tracking to `output/tracking_{dept_code}.json` and stamps the latest strategy
status from `backtest_result` (success/failed + spread) immediately after each backtest.
Also writes per-strategy result JSON to `output/results/{strategy}.json` and
latest-per-department snapshot to `output/latest_result_{dept}.json`.
Returns passthrough backtest fields so department composite output remains compatible
with downstream merge/governor expectations.

```python
try:
    department_code = str(department_code) if department_code else "MOM"
except NameError:
    department_code = "MOM"
try:
    tracking = dict(updated_tracking) if isinstance(updated_tracking, dict) else {}
except NameError:
    tracking = {}
try:
    backtest = dict(backtest_result) if isinstance(backtest_result, dict) else {}
except NameError:
    backtest = {}

tried = list(tracking.get("tried", []))
strategy_name = str(backtest.get("strategy_name", "")).strip()
if backtest:
    status = "failed" if backtest.get("error") else "success"
    try:
        spread_bps = float(backtest.get("spread_q5_q1_bps", 0) or 0)
    except Exception:
        spread_bps = 0.0
    note = "runtime: " + (str(backtest.get("error")) if backtest.get("error") else "ok")

    # Prefer exact strategy_id match; fallback to the latest pending entry.
    idx = None
    if strategy_name:
        for i in range(len(tried) - 1, -1, -1):
            if str(tried[i].get("strategy_id", "")).strip() == strategy_name:
                idx = i
                break
    if idx is None:
        for i in range(len(tried) - 1, -1, -1):
            if str(tried[i].get("status", "pending")) == "pending":
                idx = i
                break

    if idx is not None:
        item = dict(tried[idx])
        item["status"] = status
        item["spread_bps"] = spread_bps
        prev_notes = str(item.get("notes", "")).strip()
        item["notes"] = (prev_notes + " | " + note) if prev_notes else note
        tried[idx] = item
    elif strategy_name:
        tried.append({
            "strategy_id": strategy_name,
            "strategy_type": str(backtest.get("strategy_type", "")),
            "params": backtest.get("params", {}),
            "status": status,
            "spread_bps": spread_bps,
            "notes": note,
        })

out_dir = Path("examples/vibe_research_md/output")
out_dir.mkdir(parents=True, exist_ok=True)
tracking_path = out_dir / f"tracking_{department_code}.json"
to_save = {
    "department_code": department_code,
    "tried": tried,
    "to_try": list(tracking.get("to_try", [])),
    "department_notes": str(tracking.get("department_notes", "")),
}
tracking_path.write_text(json.dumps(to_save, indent=2, default=str), encoding="utf-8")

result_saved_path = ""
latest_result_saved_path = ""
if backtest and strategy_name:
    results_dir = out_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(backtest)
    payload["department_code"] = department_code
    payload["tracking_status"] = "failed" if backtest.get("error") else "success"
    try:
        payload["tracking_spread_bps"] = float(backtest.get("spread_q5_q1_bps", 0) or 0)
    except Exception:
        payload["tracking_spread_bps"] = 0.0

    result_path = results_dir / (
        "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in strategy_name) + ".json"
    )
    result_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    result_saved_path = str(result_path)

    latest_path = out_dir / f"latest_result_{department_code}.json"
    latest_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    latest_result_saved_path = str(latest_path)

result = {
    "saved": str(tracking_path),
    "result_saved": result_saved_path,
    "latest_result_saved": latest_result_saved_path,
    # Passthrough fields for downstream merge/governor.
    "department_code": department_code,
    "strategy_name": strategy_name,
    "strategy_type": str(backtest.get("strategy_type", "")),
    "params": backtest.get("params", {}),
    "error": backtest.get("error", ""),
    "quintiles": backtest.get("quintiles", []),
    "spread_q5_q1_bps": backtest.get("spread_q5_q1_bps", 0),
    "dates": backtest.get("dates", []),
    "cumulative_quintiles": backtest.get("cumulative_quintiles", {}),
    "ls_cumulative": backtest.get("ls_cumulative", []),
    "factor_saved_path": backtest.get("factor_saved_path", ""),
    "factor_save_error": backtest.get("factor_save_error", ""),
    "script_saved_path": backtest.get("script_saved_path", ""),
    "script_save_error": backtest.get("script_save_error", ""),
    "plot_saved_path": backtest.get("plot_saved_path", ""),
    "plot_error": backtest.get("plot_error", ""),
}
```
