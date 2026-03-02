"""
Quintile portfolio backtest with cumulative returns and LS series.
"""

import pandas as pd
import numpy as np
from pathlib import Path

from .factor_momentum import build_momentum
from .load_crsp import load_crsp

_PRICE_COLS = ("close_price_month", "prc", "price_month", "price", "close_price")


def _detect_price_col(df: pd.DataFrame) -> str | None:
    for col in _PRICE_COLS:
        if col in df.columns:
            return col
    return None


def _attach_formation_price(
    factor_df: pd.DataFrame,
    crsp_path: Path | str | None = None,
) -> pd.DataFrame:
    """Attach CRSP formation-month price by (date, permno) when missing."""
    if factor_df.empty:
        return factor_df

    try:
        crsp = load_crsp(crsp_path)
    except Exception:
        return factor_df

    crsp_price_col = _detect_price_col(crsp)
    if crsp_price_col is None:
        return factor_df

    out = factor_df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["permno"] = pd.to_numeric(out["permno"], errors="coerce")

    px = crsp[["date", "permno", crsp_price_col]].copy()
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    px["permno"] = pd.to_numeric(px["permno"], errors="coerce")
    px = px.rename(columns={crsp_price_col: "close_price_month"})

    out = out.merge(px, on=["date", "permno"], how="left")
    out["prc"] = out["close_price_month"]
    return out


def _apply_min_price_filter(
    factor_df: pd.DataFrame,
    min_price: float = 1.0,
    crsp_path: Path | str | None = None,
) -> pd.DataFrame:
    """Keep formation rows with |price| >= min_price when price is available."""
    if factor_df.empty:
        return factor_df

    try:
        min_price_val = float(min_price)
    except (TypeError, ValueError):
        min_price_val = 1.0
    if min_price_val <= 0:
        return factor_df

    out = factor_df
    price_col = _detect_price_col(out)
    if price_col is None:
        out = _attach_formation_price(out, crsp_path=crsp_path)
        price_col = _detect_price_col(out)
        if price_col is None:
            return out

    px = pd.to_numeric(out[price_col], errors="coerce").abs()
    return out[px >= min_price_val]


def run_backtest(
    factor_name: str = "momentum",
    start_year: int = 2008,
    end_year: int = 2024,
    crsp_path: Path | str | None = None,
    lookback_months: int = 12,
    skip_months: int = 1,
    min_price: float = 1.0,
    return_series: bool = False,
) -> dict:
    """
    Run quintile backtest on momentum/reversal factor.
    Factor sign convention is enforced at construction time via build_momentum(negate=...),
    so the factor column is always monotonic: higher value = stronger signal = Q5 = long side.
    Returns dict with quintiles, spread, cumulative returns, LS series.
    """
    is_reversal = factor_name.lower() in ("reversal", "rev", "short_term_reversal")
    mom = build_momentum(
        crsp_path=crsp_path,
        start_year=start_year,
        end_year=end_year,
        lookback_months=lookback_months,
        skip_months=skip_months,
        negate=is_reversal,
        min_price=min_price,
    )
    mom = _apply_min_price_filter(mom, min_price=min_price, crsp_path=crsp_path)
    if mom.empty:
        return {"quintiles": [], "error": "No factor data after min-price filter"}

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
        label = "reversal" if is_reversal else "momentum"
        out["strategy_name"] = f"{label}_{lookback_months}m_skip{skip_months}"
        out["lookback_months"] = lookback_months
        out["skip_months"] = skip_months

    return out


def run_backtest_from_factor_df(
    factor_df: "pd.DataFrame",
    return_series: bool = False,
    strategy_name: str = "custom",
    crsp_path: Path | str | None = None,
    min_price: float = 1.0,
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
    mom = _apply_min_price_filter(mom, min_price=min_price, crsp_path=crsp_path)
    if mom.empty:
        return {"quintiles": [], "error": "No factor data after min-price filter"}

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
