"""
Load CRSP monthly returns from auto-quant data.
"""

import pandas as pd
from pathlib import Path

from .config import CRSP_PATH


def load_crsp(
    path: Path | str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
) -> pd.DataFrame:
    """Load CRSP monthly returns."""
    p = Path(path) if path else CRSP_PATH
    if not p.exists():
        raise FileNotFoundError(f"CRSP file not found: {p}")

    df = pd.read_csv(p, compression="gzip" if str(p).endswith(".gz") else "infer")
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month

    # Contract for strategy scripts: expose a canonical "ret" column.
    if "ret" not in df.columns:
        if "return_month" in df.columns:
            df["ret"] = df["return_month"]
        elif "return_ex_div_month" in df.columns:
            df["ret"] = df["return_ex_div_month"]

    # Keep both aliases available for strategy scripts/prompts.
    if "market_cap_month" in df.columns and "mktcap" not in df.columns:
        df["mktcap"] = df["market_cap_month"]
    elif "mktcap" in df.columns and "market_cap_month" not in df.columns:
        df["market_cap_month"] = df["mktcap"]

    if start_year is not None:
        df = df[df["year"] >= start_year]
    if end_year is not None:
        df = df[df["year"] <= end_year]

    return df
