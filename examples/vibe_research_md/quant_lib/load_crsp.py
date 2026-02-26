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

    if start_year is not None:
        df = df[df["year"] >= start_year]
    if end_year is not None:
        df = df[df["year"] <= end_year]

    return df
