"""
Plot cumulative quintile returns and LS strategy returns.
One figure per factor (strategy).
"""

from pathlib import Path
from typing import Any

import pandas as pd


def plot_cumulative_and_ls(
    dates: list[str],
    cumulative_quintiles: dict[str, dict[str, float]],
    ls_cumulative: list[float],
    out_path: Path,
    title: str = "Quintile & LS Cumulative Returns",
    strategy_name: str = "",
) -> None:
    """Save matplotlib figure: cumulative Q1–Q5 and LS strategy for one factor."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for plots. Install with: pip install matplotlib"
        ) from e

    # cumulative_quintiles: {date -> {Q1..Q5 -> value}}; .T gives index=dates, cols=Q1..Q5
    df = pd.DataFrame(cumulative_quintiles).T
    if df.empty or len(df) < 2:
        raise ValueError(f"Not enough data to plot: {len(df)} rows")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # Ensure Q1..Q5 column order for consistent legend
    q_cols = [c for c in ["Q1", "Q2", "Q3", "Q4", "Q5"] if c in df.columns]
    if not q_cols:
        q_cols = sorted([c for c in df.columns if str(c).startswith("Q")])
    df = df[[c for c in q_cols if c in df.columns]]

    fig, ax = plt.subplots(figsize=(10, 6))
    for col in q_cols:
        if col in df.columns:
            ax.plot(df.index, df[col], label=col, alpha=0.8)
    ls_series = pd.Series(ls_cumulative, index=df.index[: len(ls_cumulative)])
    ax.plot(ls_series.index, ls_series.values, label="LS (Q5-Q1)", color="black", linewidth=2, linestyle="--")
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative return (1 = 100%)")
    ax.set_title(title or (f"{strategy_name} — Quintile & LS" if strategy_name else "Quintile & LS Cumulative Returns"))
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.axhline(1, color="gray", linestyle=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close()
