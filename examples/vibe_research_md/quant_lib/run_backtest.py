#!/usr/bin/env python3
"""
CLI to run factor backtest. Callable from DAN workflow via shell_command.

Usage:
    python examples/vibe_research_md/quant_lib/run_backtest.py
    python examples/vibe_research_md/quant_lib/run_backtest.py --factor momentum --start 2010 --end 2023
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root and quant_lib parent for package imports
_project_root = Path(__file__).resolve().parents[2]  # deep-agent-network
_quant_parent = Path(__file__).resolve().parents[1]  # vibe_research_md
for p in (_project_root, _quant_parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from quant_lib.backtest import run_backtest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor", default="momentum")
    parser.add_argument("--start", type=int, default=2008)
    parser.add_argument("--end", type=int, default=2024)
    parser.add_argument("--lookback", type=int, default=12)
    parser.add_argument("--skip", type=int, default=1)
    parser.add_argument("--data-dir", help="Override data dir (parent of crsp/compustat)")
    parser.add_argument("--series", action="store_true", help="Include cumulative returns and LS series")
    parser.add_argument("--save-factor-dir", help="Save factor parquet to this dir (filename: momentum_Lm_skipS.parquet)")
    args = parser.parse_args()

    crsp_path = None
    if args.data_dir:
        from examples.vibe_research_md.quant_lib.config import DATA_DIR
        crsp_path = Path(args.data_dir) / "crsp_security_month_returns.csv.gz"

    result = run_backtest(
        factor_name=args.factor,
        start_year=args.start,
        end_year=args.end,
        crsp_path=crsp_path,
        lookback_months=args.lookback,
        skip_months=args.skip,
        return_series=args.series,
    )
    if args.save_factor_dir:
        from quant_lib.factor_momentum import build_momentum
        save_dir = Path(args.save_factor_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        name = f"momentum_{args.lookback}m_skip{args.skip}"
        mom = build_momentum(
            crsp_path=crsp_path,
            start_year=args.start,
            end_year=args.end,
            lookback_months=args.lookback,
            skip_months=args.skip,
        )
        if not mom.empty:
            mom.to_parquet(save_dir / f"{name}.parquet", index=False)
    print(json.dumps(result, indent=2))
    return 0 if result.get("quintiles") else 1


if __name__ == "__main__":
    sys.exit(main())
