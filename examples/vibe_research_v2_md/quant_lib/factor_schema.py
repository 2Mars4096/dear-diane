"""
Canonical factor output schema for strategy scripts.

All strategy coders and strategy managers MUST produce/expect this format.
Validated in compile_check and run_strategy_script tool.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"date", "permno", "ret"}
FACTOR_COL = "factor"  # or "mom" for backwards compat

# Accept either "factor" or "mom" as the signal column
FACTOR_COL_ALT = "mom"


def validate_factor_df(df: Any) -> tuple[bool, str]:
    """Validate DataFrame has required structure for backtest.

    Required columns: date, permno, ret.
    Must have at least one of: factor, mom.
    Returns (valid, error_message).
    """
    try:
        import pandas as pd
    except ImportError:
        return False, "pandas not available"

    if not isinstance(df, pd.DataFrame):
        return False, "Output must be a pandas DataFrame"

    if df.empty:
        return False, "DataFrame is empty"

    cols = set(df.columns)
    missing = REQUIRED_COLUMNS - cols
    if missing:
        return False, f"Missing required columns: {sorted(missing)}. Required: {REQUIRED_COLUMNS}"

    if FACTOR_COL not in cols and FACTOR_COL_ALT not in cols:
        return False, f"Must have factor column: {FACTOR_COL} or {FACTOR_COL_ALT}"

    # Ensure no all-nan in key columns
    if df["ret"].isna().all():
        return False, "Column 'ret' is all NaN"
    factor_col = FACTOR_COL if FACTOR_COL in cols else FACTOR_COL_ALT
    if df[factor_col].isna().all():
        return False, f"Factor column '{factor_col}' is all NaN"

    return True, ""


def compustat_lag_months() -> int:
    """Required gap between Compustat data date (last day of snapshot) and portfolio formation.

    Use at least 6 months to avoid look-ahead bias (data availability lag).
    When merging Compustat to returns, ensure datadate + 6 months <= formation_date.
    """
    return 6
