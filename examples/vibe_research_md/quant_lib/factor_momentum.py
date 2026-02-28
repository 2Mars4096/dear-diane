"""
Build momentum factor from CRSP returns.
Parameterized lookback and skip months.
"""

import pandas as pd
import numpy as np
from pathlib import Path

from .load_crsp import load_crsp
from .config import CRSP_PATH


def build_momentum(
    crsp_path: Path | str | None = None,
    start_year: int = 2008,
    end_year: int = 2024,
    lookback_months: int = 12,
    skip_months: int = 1,
    negate: bool = False,
    min_price: float = 1.0,
) -> pd.DataFrame:
    """
    Build momentum/reversal factor: past N-month return, skip most recent K.

    Convention: higher ``factor`` value = stronger signal = Q5 = long side.
    For reversal pass ``negate=True`` so the sign is flipped at construction
    time (low past return → high factor value → Q5).

    Returns DataFrame with: date, permno, factor, ret  (+ optional ticker, gvkey,
    market_cap_month, close_price_month). Formation rows are filtered to
    |price| >= min_price when price is available.
    """
    df = load_crsp(crsp_path, start_year=start_year, end_year=end_year)
    df = df.sort_values(["permno", "date"]).reset_index(drop=True)

    if "return_month" in df.columns:
        df["ret"] = df["return_month"]
    elif "ret" not in df.columns:
        raise KeyError("CRSP data missing both 'return_month' and 'ret' columns")
    df["ret"] = df["ret"].replace([np.inf, -np.inf], np.nan)

    def _mom(x, lb, sk):
        out = np.full(len(x), np.nan)
        for i in range(lb + sk, len(x)):
            out[i] = np.prod(1 + x[i - lb - sk : i - sk]) - 1
        return out

    col = f"mom_{lookback_months}m_skip{skip_months}"
    df[col] = df.groupby("permno")["ret"].transform(
        lambda g: _mom(g.values, lookback_months, skip_months)
    )

    out = df[["date", "permno", col, "ret"]].copy()
    out = out.rename(columns={col: "factor"})
    if negate:
        out["factor"] = -out["factor"]
    if "close_price_month" in df.columns:
        out["close_price_month"] = df["close_price_month"].values
    elif "prc" in df.columns:
        out["close_price_month"] = df["prc"].values
    if "prc" in df.columns:
        out["prc"] = df["prc"].values
    elif "close_price_month" in out.columns:
        out["prc"] = out["close_price_month"]
    if "market_cap_month" in df.columns:
        out["market_cap_month"] = df["market_cap_month"].values
    if "ticker" in df.columns:
        out["ticker"] = df["ticker"].values
    if "gvkey" in df.columns:
        out["gvkey"] = df["gvkey"].values
    out = out.dropna(subset=["factor"])
    try:
        min_price_val = float(min_price)
    except (TypeError, ValueError):
        min_price_val = 1.0
    if min_price_val > 0 and "close_price_month" in out.columns:
        px = pd.to_numeric(out["close_price_month"], errors="coerce").abs()
        out = out[px >= min_price_val]
    return out
