"""
Config for vibe research quant library.
Data paths point to auto-quant by default.
"""

from pathlib import Path
import os

# Default: auto-quant data folder
AUTO_QUANT_ROOT = Path(
    os.environ.get(
        "AUTO_QUANT_ROOT",
        str(Path.home() / "Dropbox/CUHK-phd/projects/auto-quant"),
    )
)
DATA_DIR = AUTO_QUANT_ROOT / "data"

CRSP_PATH = DATA_DIR / "crsp_security_month_returns.csv.gz"
COMPUSTAT_PATH = DATA_DIR / "compustat_fundamentals_quarterly_200701_202506.dta"

# Output paths (within vibe_research_md)
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"  # vibe_research_md/output
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
