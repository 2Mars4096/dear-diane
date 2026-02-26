"""
Load Compustat quarterly fundamentals from auto-quant data.
"""

import pandas as pd
from pathlib import Path

from .config import COMPUSTAT_PATH


def load_compustat(
    path: Path | str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Load Compustat quarterly fundamentals."""
    p = Path(path) if path else COMPUSTAT_PATH
    if not p.exists():
        raise FileNotFoundError(f"Compustat file not found: {p}")

    default_cols = [
        "gvkey", "datadate", "fyearq", "fqtr", "fyr",
        "atq", "revtq", "prccq", "cshoq",
        "sic", "naics",
    ]
    cols = columns or default_cols
    try:
        df = pd.read_stata(p, columns=cols)
    except Exception:
        df = pd.read_stata(p)
        df = df[[c for c in cols if c in df.columns]]

    df.columns = [c.lower() for c in df.columns]
    df["datadate"] = pd.to_datetime(df["datadate"])
    df["year"] = df["datadate"].dt.year
    df["quarter"] = df["datadate"].dt.quarter
    df["yq"] = (df["year"] - 1960) * 4 + df["quarter"] - 1

    if start_year is not None:
        df = df[df["year"] >= start_year]
    if end_year is not None:
        df = df[df["year"] <= end_year]

    return df
