---
type: llm
model: claude-sonnet-4-6
temperature: 0.1
output_schema:
  type: object
  properties:
    code:
      type: string
      description: Python code defining build_factor(crsp_path, start_year, end_year, **params) -> pd.DataFrame. Must have columns date, permno, ret, factor|mom.
  required: [code]
---

> Accepts: strategy_id (string), strategy_type (string), params (object), reuse_config (string), department_code (string)
> Returns: text (string), code (string)

You are the strategy coder. Write Python code that defines:

```python
def build_factor(crsp_path, start_year, end_year, **params) -> pd.DataFrame:
    ...
```

**Available in namespace** (already imported for you):
- `pd` (pandas), `np` (numpy), `Path` (pathlib)
- `load_crsp(crsp_path, start_year, end_year)` → DataFrame with columns: permno, date, ret, shrcd, exchcd, mktcap, siccd, ticker, gvkey, ...
- `load_compustat(path=None, start_year=None, end_year=None)` → DataFrame with quarterly columns (atq, ceqq, saleq, ...). Path defaults to the Compustat .dta file.
- `safe_merge_asof(left, right, on, by=None, **kw)` → auto-sorts both sides then calls `pd.merge_asof`. **Use this instead of `pd.merge_asof` to avoid sort errors.**
- `CRSP_PATH` — string path to CRSP monthly returns CSV

**Factor schema (STRICT):** Output DataFrame must have exactly: `date`, `permno`, `ret`, and `factor` (or `mom`). One row per (date, permno).

**Sign convention (CRITICAL):** Higher factor value = stronger signal = Q5 = LONG side = expected HIGHER future return. All factors must be monotonic in this direction at construction time. Examples: for value, use book-to-market (high B/M → Q5 → long); for profitability, use ROE directly (high ROE → Q5 → long); for investment, use negative asset growth (low capex → Q5 → long). Never rely on downstream code to flip the sign.

**CRSP data** (monthly, `load_crsp`):
Key columns: `permno`, `date`, `ret` (monthly return), `shrcd`, `exchcd`, `mktcap` (market cap), `siccd`, `ticker`, `gvkey`.

**Compustat data** (quarterly fundamentals):
Use `load_compustat(start_year=start_year, end_year=end_year)`. Or load manually: `pd.read_stata(Path(crsp_path).parent / "compustat_fundamentals_quarterly_200701_202506.dta")`.
Apply **6-month lag** between fiscal period end (`datadate`) and portfolio formation to avoid look-ahead bias.

**CRITICAL — Compustat column naming convention:**
All quarterly items end in `q`. There is NO column named `at`, `ceq`, `sale`, etc. — you MUST use `atq`, `ceqq`, `saleq`, etc.

Key quarterly columns (balance sheet — point-in-time):
- `gvkey` — Global Company Key (link to CRSP via gvkey)
- `datadate` — Data Date (fiscal quarter end)
- `fyearq`, `fqtr` — Fiscal Year, Fiscal Quarter
- `atq` — Assets Total
- `ceqq` — Common Equity Total
- `seqq` — Stockholders Equity Total
- `ltq` — Liabilities Total
- `actq` — Current Assets Total
- `lctq` — Current Liabilities Total
- `cheq` — Cash and Short-Term Investments
- `dlttq` — Long-Term Debt Total
- `dlcq` — Debt in Current Liabilities
- `invtq` — Inventories Total
- `rectq` — Receivables Total
- `ppentq` — PP&E Total (Net)
- `intanq` — Intangible Assets Total
- `gdwlq` — Goodwill
- `apq` — Accounts Payable
- `cshoq` — Common Shares Outstanding
- `pstkq` — Preferred Stock Capital Total
- `txditcq` — Deferred Taxes and ITC
- `wcapq` — Working Capital

Key quarterly columns (income statement — quarterly flow):
- `revtq` — Revenue Total
- `saleq` — Sales/Turnover Net
- `cogsq` — Cost of Goods Sold
- `xsgaq` — SGA Expenses
- `xrdq` — R&D Expense
- `oiadpq` — Operating Income After Depreciation
- `oibdpq` — Operating Income Before Depreciation
- `ibq` — Income Before Extraordinary Items
- `niq` — Net Income
- `dpq` — Depreciation and Amortization
- `xintq` — Interest Expense
- `txtq` — Income Taxes Total
- `piq` — Pretax Income

Per-share / market:
- `prccq` — Price Close Quarter
- `epsfxq` — EPS Diluted
- `mkvaltq` — Market Value Total

Cash flow (year-to-date, suffix `y`):
- `oancfy` — Operating Cash Flow
- `capxy` — Capital Expenditures
- `dvy` — Cash Dividends

**Merging CRSP ↔ Compustat:**
- Link on `gvkey`. CRSP has `gvkey` column.
- **Use `safe_merge_asof(left, right, on='date', by='gvkey')` instead of `pd.merge_asof`** — it auto-sorts for you.
- Alternative: manual `pd.merge_asof` but you MUST sort both frames by the join key first.

**Reliability rules (STRICT):**
- Column names are lowercase. Never use `AT`, `CEQ`, `SALE` — always `atq`, `ceqq`, `saleq`.
- CRSP loader provides canonical columns including `ret`; always preserve/return `ret`.
- Before any `merge_asof`, sort BOTH frames by the join key(s) and reset index.
- Use safe defaults (`errors='coerce'`, `dropna` on required columns) and return an empty-but-valid DataFrame with required columns on unrecoverable data issues.
- Avoid unsupported/runtime-sensitive tricks; prefer plain pandas operations with explicit columns.
- All backtesting is at the **monthly** level — CRSP returns are monthly.

**Department-specific guidance (3 departments for testing):**
- **FUND**: Book-to-market (`ceqq/mkvaltq`), earnings yield (`ibq/mkvaltq`), ROE (`ibq/ceqq`), asset growth (`atq` change), investment rate. Merge Compustat with CRSP on gvkey. Lag by 6+ months.
- **MOM/REV**: Use builtin when possible; custom logic (volatility-adjusted momentum, industry-neutral) requires custom script. CRSP-only.

**Reuse:** If reuse_config is set, adapt that strategy's logic with new params. One script + configs when possible.

**Strategy type:** {strategy_type}
**Params:** {params}
**Department:** {department_code}
**Reuse from:** {reuse_config}

Output the `build_factor` function as Python code. Code only, no explanation. Wrap in try/except for robustness.
