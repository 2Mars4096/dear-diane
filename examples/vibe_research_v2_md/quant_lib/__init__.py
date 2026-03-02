"""
Quant library for vibe research: CRSP, Compustat, factor construction, backtest.
"""

from .config import DATA_DIR, CRSP_PATH, COMPUSTAT_PATH
from .load_crsp import load_crsp
from .load_compustat import load_compustat
from .factor_momentum import build_momentum
from .backtest import run_backtest
from .factor_schema import validate_factor_df, compustat_lag_months, REQUIRED_COLUMNS, FACTOR_COL

__all__ = [
    "DATA_DIR", "CRSP_PATH", "COMPUSTAT_PATH",
    "load_crsp", "load_compustat",
    "build_momentum", "run_backtest",
    "validate_factor_df", "compustat_lag_months", "REQUIRED_COLUMNS", "FACTOR_COL",
]
