"""Backtesting, strategy execution, and quant grid/plot tools."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dan.server.exec import execute_python


async def _run_strategy_script(
    code: str = "",
    params: dict | None = None,
    start_year: int = 2008,
    end_year: int = 2024,
    data_dir: str = "",
    strategy_name: str = "custom",
    return_series: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute strategy code (script-as-param), validate factor format, run backtest.

    The code must define: def build_factor(crsp_path, start_year, end_year, **params) -> pd.DataFrame
    Factor DataFrame must have columns: date, permno, ret, and factor or mom.
    """
    if not code or not code.strip():
        return {"quintiles": [], "error": "No strategy code provided"}

    project_root = Path(__file__).resolve().parents[4]
    vibe_root = project_root / "examples" / "vibe_research_md"
    safe_strategy_name = "".join(
        ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in str(strategy_name or "custom")
    )
    script_saved_path = ""
    script_save_error = ""

    def _attach_script_metadata(payload: dict[str, Any]) -> dict[str, Any]:
        if script_saved_path:
            payload["script_saved_path"] = script_saved_path
        if script_save_error:
            payload["script_save_error"] = script_save_error
        return payload

    for p in (project_root, vibe_root):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    try:
        save_script_dir = kwargs.get("save_script_dir") or kwargs.get("script_dir")
        if not save_script_dir:
            save_script_dir = project_root / "examples" / "vibe_research_md" / "output" / "scripts"
        script_dir = Path(save_script_dir)
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / f"{safe_strategy_name}.py"
        script_path.write_text(str(code), encoding="utf-8")
        script_saved_path = str(script_path)
    except Exception as e:
        script_save_error = str(e)

    try:
        from examples.vibe_research_md.quant_lib.config import CRSP_PATH, DATA_DIR
        from examples.vibe_research_md.quant_lib.load_crsp import load_crsp
        from examples.vibe_research_md.quant_lib.load_compustat import load_compustat
        from examples.vibe_research_md.quant_lib.factor_schema import validate_factor_df
        from examples.vibe_research_md.quant_lib.backtest import run_backtest_from_factor_df
        import pandas as pd
        import numpy as np
    except ImportError as e:
        return _attach_script_metadata({"quintiles": [], "error": f"quant_lib import failed: {e}"})

    def _normalize_compustat_param_aliases(raw: dict[str, Any]) -> dict[str, Any]:
        alias = {
            "at": "atq",
            "ceq": "ceqq",
            "seq": "seqq",
            "sale": "saleq",
            "revt": "revtq",
            "ib": "ibq",
            "ni": "niq",
            "csho": "cshoq",
            "mkvalt": "mkvaltq",
        }
        out = dict(raw)
        for key in ("compustat_book_equity_field", "numerator_field", "denominator_field", "asset_field"):
            val = out.get(key)
            if isinstance(val, str):
                out[key] = alias.get(val.lower(), val)
        return out

    original_merge_asof = pd.merge_asof

    def _normalize_asof_tolerance(val: Any) -> Any:
        """Normalize merge_asof tolerance to timedelta-like when possible.

        Generated scripts sometimes pass `pd.DateOffset(months=...)`, which is not
        accepted by pandas merge_asof with datetime64 keys. Convert to a concrete
        Timedelta approximation so joins proceed instead of hard-failing.
        """
        if val is None:
            return None
        if isinstance(val, pd.DateOffset):
            try:
                base = pd.Timestamp("2000-01-01")
                td = (base + val) - base
                if isinstance(td, pd.Timedelta):
                    return abs(td)
            except Exception:
                pass
            return None
        return val

    def safe_merge_asof(left, right, on, by=None, **kw):
        """merge_asof that auto-sorts and repairs common tolerance incompatibilities."""
        sort_cols = [on]
        if by is not None:
            if isinstance(by, (list, tuple)):
                sort_cols = [on] + list(by)
            else:
                sort_cols = [on, by]
        left = left.sort_values(sort_cols).reset_index(drop=True)
        right = right.sort_values(sort_cols).reset_index(drop=True)

        if "tolerance" in kw:
            tol = _normalize_asof_tolerance(kw.get("tolerance"))
            if tol is None:
                kw.pop("tolerance", None)
            else:
                kw["tolerance"] = tol

        try:
            return original_merge_asof(left, right, on=on, by=by, **kw)
        except Exception as exc:
            if "tolerance" in kw and "incompatible tolerance" in str(exc).lower():
                kw.pop("tolerance", None)
                return original_merge_asof(left, right, on=on, by=by, **kw)
            raise

    crsp_path = str(DATA_DIR / "crsp_security_month_returns.csv.gz")
    if data_dir:
        crsp_path = str(Path(data_dir) / "crsp_security_month_returns.csv.gz")
    params = _normalize_compustat_param_aliases(params or {})
    raw_min_price = kwargs.get("min_price")
    if raw_min_price is None and isinstance(params, dict):
        raw_min_price = params.get("min_price_filter", params.get("min_price"))
    try:
        min_price = float(raw_min_price) if raw_min_price is not None else 1.0
    except (TypeError, ValueError):
        min_price = 1.0

    namespace = {
        "pd": pd,
        "np": np,
        "Path": Path,
        "load_crsp": load_crsp,
        "load_compustat": load_compustat,
        "safe_merge_asof": safe_merge_asof,
        "CRSP_PATH": crsp_path,
        "build_factor": None,
    }

    pd.merge_asof = safe_merge_asof
    try:
        exec_out = execute_python(code, namespace, include_namespace=True)
        if exec_out.get("error"):
            return _attach_script_metadata({
                "quintiles": [],
                "error": f"Strategy script failed to compile/run: {exec_out['error']}",
            })

        build_factor = exec_out.get("namespace", {}).get("build_factor")
        if not callable(build_factor):
            return _attach_script_metadata({
                "quintiles": [],
                "error": "Code must define build_factor(crsp_path, start_year, end_year, **params) -> pd.DataFrame",
            })

        import contextlib
        import io

        bf_stdout = io.StringIO()
        bf_stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(bf_stdout), contextlib.redirect_stderr(bf_stderr):
                factor_df = build_factor(crsp_path, start_year, end_year, **params)
        except Exception as e:
            script_output = (bf_stdout.getvalue() + "\n" + bf_stderr.getvalue()).strip()
            msg = f"build_factor failed: {e}"
            if script_output:
                msg += f" | script_output: {script_output[:500]}"
            return _attach_script_metadata({"quintiles": [], "error": msg})
    finally:
        pd.merge_asof = original_merge_asof

    build_factor_log = (bf_stdout.getvalue() + "\n" + bf_stderr.getvalue()).strip()
    if factor_df is None or getattr(factor_df, "empty", True):
        msg = "build_factor returned empty factor dataframe"
        if build_factor_log:
            msg += f" | script_output: {build_factor_log[:500]}"
        return _attach_script_metadata({"quintiles": [], "error": msg})

    valid, err = validate_factor_df(factor_df)
    if not valid:
        return _attach_script_metadata({"quintiles": [], "error": f"Invalid factor format: {err}"})

    factor_saved_path = ""
    factor_save_error = ""
    try:
        save_factor_dir = kwargs.get("save_factor_dir") or kwargs.get("out_dir")
        if not save_factor_dir:
            save_factor_dir = project_root / "examples" / "vibe_research_md" / "output" / "factors"
        save_dir = Path(save_factor_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        factor_path = save_dir / f"{safe_strategy_name}.parquet"
        factor_df.to_parquet(factor_path, index=False)
        factor_saved_path = str(factor_path)
    except Exception as e:
        factor_save_error = str(e)

    out = run_backtest_from_factor_df(
        factor_df,
        return_series=return_series,
        strategy_name=strategy_name,
        crsp_path=crsp_path,
        min_price=min_price,
    )
    if build_factor_log:
        out["build_factor_log"] = build_factor_log[:2000]
    out["factor_saved_path"] = factor_saved_path
    if factor_save_error:
        out["factor_save_error"] = factor_save_error
    return _attach_script_metadata(out)


async def _run_backtest(
    factor: str = "momentum",
    factor_type: str = "",
    start_year: int = 2008,
    end_year: int = 2024,
    data_dir: str = "",
    lookback_months: int = 12,
    skip_months: int = 1,
    return_series: bool = False,
    item: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run factor backtest using quant_lib (CRSP data from auto-quant)."""
    factor = factor_type or factor
    strategy_name = ""
    raw_min_price = kwargs.get("min_price", kwargs.get("min_price_filter", 1.0))
    if isinstance(item, dict):
        lookback_months = int(item.get("lookback", lookback_months))
        skip_months = int(item.get("skip", skip_months))
        start_year = int(item.get("start_year", start_year))
        end_year = int(item.get("end_year", end_year))
        factor = item.get("factor_type", factor)
        strategy_name = str(item.get("name", "")).strip()
        raw_min_price = item.get("min_price_filter", item.get("min_price", raw_min_price))
    try:
        min_price = float(raw_min_price)
    except (TypeError, ValueError):
        min_price = 1.0
    project_root = Path(__file__).resolve().parents[4]
    script = project_root / "examples" / "vibe_research_md" / "quant_lib" / "run_backtest.py"
    if not script.exists():
        return {"quintiles": [], "error": f"Backtest script not found: {script}"}
    cmd = [
        sys.executable,
        str(script),
        "--factor", factor,
        "--start", str(start_year),
        "--end", str(end_year),
        "--lookback", str(lookback_months),
        "--skip", str(skip_months),
        "--min-price", str(min_price),
    ]
    if strategy_name:
        cmd.extend(["--name", strategy_name])
    if return_series:
        cmd.append("--series")
    if data_dir:
        cmd.extend(["--data-dir", data_dir])
    save_factor_dir = kwargs.get("save_factor_dir") or kwargs.get("out_dir")
    if not save_factor_dir:
        save_factor_dir = project_root / "examples" / "vibe_research_md" / "output" / "factors"
    cmd.extend(["--save-factor-dir", str(save_factor_dir)])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_root),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        out = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            return {"quintiles": [], "error": stderr.decode("utf-8", errors="replace")[:500]}
        payload = json.loads(out)
        if strategy_name and isinstance(payload, dict):
            payload["strategy_name"] = strategy_name
        return payload
    except asyncio.TimeoutError:
        return {"quintiles": [], "error": "Backtest timed out after 120s"}
    except json.JSONDecodeError as e:
        return {"quintiles": [], "error": f"Invalid JSON output: {e}"}


async def _save_grid_csv(
    results: list | None = None,
    input: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Write grid_summary.csv from backtest results. Accepts results array or input (loop output) with result.results.

    DEPRECATED: Prefer run_python(code, results=...) with agent-generated CSV code.
    Kept for backwards compatibility.
    """
    project_root = Path(__file__).resolve().parents[4]
    out_path = project_root / "examples" / "vibe_research_md" / "output" / "grid_summary.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    use_input = (results is None or (isinstance(results, list) and len(results) == 0)) and input
    if use_input:
        obj = input or {}
        obj = obj.get("input", obj) if isinstance(obj, dict) else obj
        obj = obj.get("result", obj) if isinstance(obj, dict) else obj
        if isinstance(obj, dict) and isinstance(obj.get("results"), list):
            results = list(obj["results"])
    results = results or []
    cols = ["strategy", "lookback", "skip", "spread_q5_q1_bps", "n_dates", "error"]
    lines = [",".join(cols)]
    for r in results:
        if isinstance(r, dict):
            row = [
                str(r.get("strategy_name", "")),
                str(r.get("lookback_months", "")),
                str(r.get("skip_months", "")),
                str(r.get("spread_q5_q1_bps", "")),
                str(r.get("n_dates", "")),
                str(r.get("error", "")).replace(",", ";"),
            ]
        else:
            row = ["", "", "", "", "", str(r).replace(",", ";")]
        lines.append(",".join(row))
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return {"csv_path": str(out_path), "results": results}


async def _plot_backtest(
    item: dict,
    out_dir: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Plot cumulative quintile and LS returns for one backtest result.

    DEPRECATED: Prefer run_python(code, item=..., out_dir=...) with agent-generated
    plotting code. Kept for backwards compatibility.
    """
    if not item or not isinstance(item, dict):
        return {"saved_path": "", "error": "item is None or not a dict"}
    project_root = Path(__file__).resolve().parents[4]
    default_out = project_root / "examples" / "vibe_research_md" / "output" / "plots"
    out_path = Path(out_dir) if out_dir else default_out
    out_path.mkdir(parents=True, exist_ok=True)
    name = item.get("strategy_name", "backtest")
    plot_file = out_path / f"{name}.png"
    dates = item.get("dates", [])
    cum = item.get("cumulative_quintiles", {})
    ls_cum = item.get("ls_cumulative", [])
    if not dates or not cum or not ls_cum:
        return {"saved_path": "", "error": "Missing dates, cumulative_quintiles, or ls_cumulative"}
    try:
        project_root_str = str(project_root)
        if project_root_str not in sys.path:
            sys.path.insert(0, project_root_str)
        from examples.vibe_research_md.quant_lib.visualize import plot_cumulative_and_ls
        plot_cumulative_and_ls(
            dates=dates,
            cumulative_quintiles=cum,
            ls_cumulative=ls_cum,
            out_path=plot_file,
            title=f"{name} — Quintile & LS Cumulative Returns",
            strategy_name=name,
        )
        if not plot_file.exists():
            return {"saved_path": "", "error": "Plot file was not created"}
        return {"saved_path": str(plot_file)}
    except ImportError as e:
        return {"saved_path": "", "error": f"matplotlib required: {e}. Install with: pip install matplotlib"}
    except Exception as e:
        return {"saved_path": "", "error": str(e)}
