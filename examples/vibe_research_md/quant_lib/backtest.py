"""
Quintile portfolio backtest with cumulative returns and LS series.
"""

import pandas as pd
import numpy as np
from pathlib import Path

from .factor_momentum import build_momentum


def run_backtest(
    factor_name: str = "momentum",
    start_year: int = 2008,
    end_year: int = 2024,
    crsp_path: Path | str | None = None,
    lookback_months: int = 12,
    skip_months: int = 1,
    return_series: bool = False,
) -> dict:
    """
    Run quintile backtest on momentum factor.
    Returns dict with quintiles, spread, cumulative returns, LS series.
    """
    mom = build_momentum(
        crsp_path=crsp_path,
        start_year=start_year,
        end_year=end_year,
        lookback_months=lookback_months,
        skip_months=skip_months,
    )
    if mom.empty:
        return {"quintiles": [], "error": "No factor data"}

    factor_col = "factor" if "factor" in mom.columns else "mom"
    mom = mom.dropna(subset=[factor_col, "ret"])

    def _qcut_safe(x):
        try:
            return pd.qcut(x, 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
        except Exception:
            return pd.Series([np.nan] * len(x), index=x.index)

    mom["q"] = mom.groupby("date")[factor_col].transform(_qcut_safe)

    q_ret = mom.groupby(["date", "q"], observed=True)["ret"].mean().reset_index()
    q_ret["ret_bps"] = q_ret["ret"] * 10000

    quintiles = []
    for q in [1, 2, 3, 4, 5]:
        sub = q_ret[q_ret["q"] == q]
        if sub.empty:
            quintiles.append({"quintile": q, "avg_return_bps": 0, "n_stocks": 0})
        else:
            n_per_date = mom[mom["q"] == q].groupby("date").size()
            quintiles.append({
                "quintile": int(q),
                "avg_return_bps": round(sub["ret_bps"].mean(), 2),
                "n_stocks": int(n_per_date.mean()),
            })

    spread = quintiles[-1]["avg_return_bps"] - quintiles[0]["avg_return_bps"] if quintiles else 0
    out = {
        "quintiles": quintiles,
        "spread_q5_q1_bps": round(spread, 2),
        "n_dates": int(q_ret["date"].nunique()),
    }

    if return_series:
        # Pivot to wide: date x quintile; cumulative return
        wide = q_ret.pivot(index="date", columns="q", values="ret").sort_index()
        wide.columns = [f"Q{c}" for c in wide.columns]
        cum = (1 + wide).cumprod()
        ls_ret = wide.get("Q5", pd.Series(0, index=wide.index)) - wide.get("Q1", pd.Series(0, index=wide.index))
        ls_cum = (1 + ls_ret).cumprod().tolist()
        # Use string keys for JSON serialization
        out["cumulative_quintiles"] = {str(k): v for k, v in cum.to_dict(orient="index").items()}
        out["ls_returns"] = ls_ret.tolist()
        out["ls_cumulative"] = ls_cum.tolist() if hasattr(ls_cum, "tolist") else ls_cum
        out["dates"] = [str(d) for d in cum.index]
        out["strategy_name"] = f"momentum_{lookback_months}m_skip{skip_months}"
        out["lookback_months"] = lookback_months
        out["skip_months"] = skip_months

    return out


def run_backtest_from_factor_df(
    factor_df: "pd.DataFrame",
    return_series: bool = False,
    strategy_name: str = "custom",
) -> dict:
    """Run quintile backtest on a factor DataFrame (from custom strategy script).

    factor_df must have columns: date, permno, ret, and factor or mom.
    """
    from .factor_schema import validate_factor_df

    valid, err = validate_factor_df(factor_df)
    if not valid:
        return {"quintiles": [], "error": f"Invalid factor format: {err}"}
    mom = factor_df.dropna(subset=["ret"])
    factor_col = "factor" if "factor" in mom.columns else "mom"
    mom = mom.dropna(subset=[factor_col])

    def _qcut_safe(x):
        try:
            return pd.qcut(x, 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
        except Exception:
            return pd.Series([np.nan] * len(x), index=x.index)

    mom["q"] = mom.groupby("date")[factor_col].transform(_qcut_safe)
    q_ret = mom.groupby(["date", "q"], observed=True)["ret"].mean().reset_index()
    q_ret["ret_bps"] = q_ret["ret"] * 10000

    quintiles = []
    for q in [1, 2, 3, 4, 5]:
        sub = q_ret[q_ret["q"] == q]
        if sub.empty:
            quintiles.append({"quintile": q, "avg_return_bps": 0, "n_stocks": 0})
        else:
            n_per_date = mom[mom["q"] == q].groupby("date").size()
            quintiles.append({
                "quintile": int(q),
                "avg_return_bps": round(sub["ret_bps"].mean(), 2),
                "n_stocks": int(n_per_date.mean()),
            })

    spread = quintiles[-1]["avg_return_bps"] - quintiles[0]["avg_return_bps"] if quintiles else 0
    out = {
        "quintiles": quintiles,
        "spread_q5_q1_bps": round(spread, 2),
        "n_dates": int(q_ret["date"].nunique()),
        "strategy_name": strategy_name,
    }

    if return_series:
        wide = q_ret.pivot(index="date", columns="q", values="ret").sort_index()
        wide.columns = [f"Q{c}" for c in wide.columns]
        cum = (1 + wide).cumprod()
        ls_ret = wide.get("Q5", pd.Series(0, index=wide.index)) - wide.get("Q1", pd.Series(0, index=wide.index))
        ls_cum = (1 + ls_ret).cumprod().tolist()
        out["cumulative_quintiles"] = {str(k): v for k, v in cum.to_dict(orient="index").items()}
        out["ls_returns"] = ls_ret.tolist()
        out["ls_cumulative"] = ls_cum.tolist() if hasattr(ls_cum, "tolist") else ls_cum
        out["dates"] = [str(d) for d in cum.index]

    return out
