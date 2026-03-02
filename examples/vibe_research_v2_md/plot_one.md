---
type: code
language: python
---

> Accepts: item (object), out_dir (string, optional)
> Returns: saved_path (string), error (string)

Plots cumulative quintile and LS returns for one backtest result using matplotlib. Self-contained code node — no external tool dependency.

<!-- Design note: This uses inline code (Option B) because the backtest result
     schema is well-known. For unknown/evolving schemas, prefer the LLM-generated
     code pattern (Option A): an LLM node generates plotting code, then a companion
     run_python tool node executes it. See docs/plans/7-5-general-tool-design.md. -->

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path

item_val = item if isinstance(item, dict) else {}
if not item_val:
    result = {"saved_path": "", "error": "item is None or not a dict"}
else:
    name = str(item_val.get("strategy_name", "backtest")) or "backtest"
    safe_name = "".join(
        ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in name
    )
    dates = item_val.get("dates", [])
    cum = item_val.get("cumulative_quintiles", {})
    ls_cum = item_val.get("ls_cumulative", [])

    if not dates or not cum or not ls_cum:
        result = {
            "saved_path": "",
            "error": "Missing dates, cumulative_quintiles, or ls_cumulative",
        }
    else:
        try:
            out_dir_val = inputs.get("out_dir")
            if isinstance(out_dir_val, str) and out_dir_val.strip():
                out_dir = Path(out_dir_val.strip())
            else:
                out_dir = Path("examples/vibe_research_v2_md/output/plots")
            out_dir.mkdir(parents=True, exist_ok=True)
            plot_file = out_dir / (safe_name + ".png")

            df = pd.DataFrame(cum).T
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            q_cols = [c for c in ["Q1", "Q2", "Q3", "Q4", "Q5"] if c in df.columns]
            if not q_cols:
                q_cols = sorted(c for c in df.columns if str(c).startswith("Q"))
            df = df[[c for c in q_cols if c in df.columns]]

            fig, ax = plt.subplots(figsize=(10, 6))
            for col in q_cols:
                if col in df.columns:
                    ax.plot(df.index, df[col], label=col, alpha=0.8)
            ls_series = pd.Series(ls_cum, index=df.index[: len(ls_cum)])
            ax.plot(
                ls_series.index, ls_series.values,
                label="LS (Q5-Q1)", color="black", linewidth=2, linestyle="--",
            )
            ax.set_xlabel("Date")
            ax.set_ylabel("Cumulative return (1 = 100%)")
            ax.set_title(name + " — Quintile & LS Cumulative Returns")
            ax.legend(loc="upper left")
            ax.grid(True, alpha=0.3)
            ax.axhline(1, color="gray", linestyle=":", alpha=0.5)
            fig.tight_layout()
            fig.savefig(plot_file, dpi=150)
            plt.close(fig)

            result = {
                "saved_path": str(plot_file) if plot_file.exists() else "",
                "error": "",
            }
        except Exception as e:
            result = {"saved_path": "", "error": str(e)}
```