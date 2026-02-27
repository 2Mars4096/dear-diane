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
) -> pd.DataFrame:
    """
    Build momentum factor: past N-month return, skip most recent K months.
    Returns: DataFrame with date, permno, mom_* (and optionally ticker, gvkey).
    """
    df = load_crsp(crsp_path, start_year=start_year, end_year=end_year)
    df = df.sort_values(["permno", "date"]).reset_index(drop=True)

    if "return_month" in df.columns:
        df["ret"] = df["return_month"]
    elif "ret" in df.columns:
        df["ret"] = df["ret"]
    else:
        raise KeyError("CRSP data missing both 'return_month' and 'ret' columns")
    df["ret"] = df["ret"].replace([np.inf, -np.inf], np.nan)

    # Past N-month return, skip most recent K months (t-(K+1) to t-(K+N))
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
    out = out.rename(columns={col: "mom"})
    if "market_cap_month" in df.columns:
        out["market_cap_month"] = df["market_cap_month"].values
    if "ticker" in df.columns:
        out["ticker"] = df["ticker"].values
    if "gvkey" in df.columns:
        out["gvkey"] = df["gvkey"].values
    out = out.dropna(subset=["mom"])
    return out
