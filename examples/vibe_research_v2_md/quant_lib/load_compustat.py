"""
Load Compustat quarterly fundamentals from auto-quant data.

Data is quarterly (one row per gvkey per fiscal quarter).
All quarterly balance-sheet items end in 'q' (e.g. atq = total assets).
Year-to-date flow items end in 'y' (e.g. revty = revenue YTD).
"""

import pandas as pd
from pathlib import Path

from .config import COMPUSTAT_PATH

KEY_QUARTERLY_COLS = [
    "gvkey", "datadate", "fyearq", "fqtr", "fyr",
    # Balance sheet
    "atq",      # Assets - Total
    "ceqq",     # Common Equity - Total
    "seqq",     # Stockholders Equity - Total
    "ltq",      # Liabilities - Total
    "lctq",     # Current Liabilities - Total
    "actq",     # Current Assets - Total
    "cheq",     # Cash and Short-Term Investments
    "dlttq",    # Long-Term Debt - Total
    "dlcq",     # Debt in Current Liabilities
    "invtq",    # Inventories - Total
    "rectq",    # Receivables - Total
    "ppentq",   # PP&E - Total (Net)
    "ppegtq",   # PP&E - Total (Gross)
    "intanq",   # Intangible Assets - Total
    "gdwlq",    # Goodwill (net)
    "apq",      # Accounts Payable
    "cshoq",    # Common Shares Outstanding
    "pstkq",    # Preferred Stock (Capital) - Total
    "txditcq",  # Deferred Taxes and Investment Tax Credit
    "wcapq",    # Working Capital
    # Income statement (quarterly)
    "revtq",    # Revenue - Total
    "saleq",    # Sales/Turnover (Net)
    "cogsq",    # Cost of Goods Sold
    "xsgaq",    # SGA Expenses
    "xrdq",     # R&D Expense
    "oiadpq",   # Operating Income After Depreciation
    "oibdpq",   # Operating Income Before Depreciation
    "ibq",      # Income Before Extraordinary Items
    "niq",      # Net Income (Loss)
    "dpq",      # Depreciation and Amortization
    "xintq",    # Interest Expense
    "txtq",     # Income Taxes - Total
    "piq",      # Pretax Income
    "spiq",     # Special Items
    # Per share
    "prccq",    # Price Close - Quarter
    "epsfxq",   # EPS (Diluted) excl extraordinary
    "epspxq",   # EPS (Basic) excl extraordinary
    "mkvaltq",  # Market Value - Total
    # IDs
    "sic", "naics",
]

# Backward-compat aliases used by older prompts/tracking params.
# New code should prefer canonical quarterly names (e.g., ceqq, atq, saleq).
ALIASES = {
    "at": "atq",
    "ceq": "ceqq",
    "seq": "seqq",
    "lt": "ltq",
    "act": "actq",
    "lct": "lctq",
    "che": "cheq",
    "dltt": "dlttq",
    "dlc": "dlcq",
    "invt": "invtq",
    "rect": "rectq",
    "ppent": "ppentq",
    "intan": "intanq",
    "gdwl": "gdwlq",
    "ap": "apq",
    "csho": "cshoq",
    "pstk": "pstkq",
    "txditc": "txditcq",
    "wcap": "wcapq",
    "revt": "revtq",
    "sale": "saleq",
    "cogs": "cogsq",
    "xsga": "xsgaq",
    "xrd": "xrdq",
    "oiadp": "oiadpq",
    "oibdp": "oibdpq",
    "ib": "ibq",
    "ni": "niq",
    "dp": "dpq",
    "xint": "xintq",
    "txt": "txtq",
    "pi": "piq",
    "prcc": "prccq",
    "epsfx": "epsfxq",
    "epspx": "epspxq",
    "mkvalt": "mkvaltq",
}


def load_compustat(
    path: Path | str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Load Compustat quarterly fundamentals.

    Returns one row per (gvkey, datadate). All column names are lowercase.
    Quarterly balance-sheet items end in 'q', YTD flows in 'y'.
    """
    p = Path(path) if path else COMPUSTAT_PATH
    if not p.exists():
        raise FileNotFoundError(f"Compustat file not found: {p}")

    cols = columns or KEY_QUARTERLY_COLS
    try:
        df = pd.read_stata(p, columns=cols)
    except Exception:
        df = pd.read_stata(p)
        available = set(df.columns.str.lower())
        df.columns = [c.lower() for c in df.columns]
        df = df[[c for c in cols if c.lower() in available]]

    df.columns = [c.lower() for c in df.columns]
    df["datadate"] = pd.to_datetime(df["datadate"])
    df["year"] = df["datadate"].dt.year
    df["quarter"] = df["datadate"].dt.quarter
    df["yq"] = (df["year"] - 1960) * 4 + df["quarter"] - 1

    if start_year is not None:
        df = df[df["year"] >= start_year]
    if end_year is not None:
        df = df[df["year"] <= end_year]

    for alias, canonical in ALIASES.items():
        if alias not in df.columns and canonical in df.columns:
            df[alias] = df[canonical]

    return df
