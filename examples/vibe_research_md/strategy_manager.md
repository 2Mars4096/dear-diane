---
type: llm
model: claude-sonnet-4-6
temperature: 0.2
output_schema:
  type: object
  properties:
    strategy_id:
      type: string
      description: department_code + numbering + desc e.g. MOM_001_momentum_12_1
    strategy_type:
      type: string
      enum: [momentum, reversal, fundamentals, cash_based, operation_based, ml]
    params:
      type: object
      description: Strategy parameters
    use_builtin:
      type: boolean
      description: True if built-in (momentum/reversal) covers this; false if custom script needed
    reuse_config:
      type: string
      description: If only params change, reuse script from this strategy_id. Empty string if none.
    updated_tracking:
      type: object
      description: Updated tracking list with tried, to_try, and department_notes
      properties:
        tried:
          type: array
          items:
            type: object
            properties:
              strategy_id: {type: string}
              strategy_type: {type: string}
              params: {type: object}
              status: {type: string, enum: [success, failed, pending]}
              spread_bps: {type: number}
              notes: {type: string}
            required: [strategy_id, status]
        to_try:
          type: array
          items:
            type: object
            properties:
              strategy_id: {type: string}
              strategy_type: {type: string}
              params: {type: object}
              priority: {type: integer}
              rationale: {type: string}
            required: [strategy_id, rationale]
        department_notes:
          type: string
      required: [tried, to_try, department_notes]
  required: [strategy_id, strategy_type, params, use_builtin, updated_tracking]
---

> Accepts: department_code (string), theme (string), params_hint (object), tracking_list (object)
> Returns: text (string), strategy_id (string), strategy_type (string), params (object), use_builtin (boolean), reuse_config (string), updated_tracking (object)

You are the strategy manager for department **{department_code}**. You decide what to try next.

**Current theme from orchestrator:** {theme}

**Naming:** department_code + numbering + desc. E.g. MOM_001_momentum_12_1, REV_003_reversal_3_0.

**use_builtin rules (STRICT):**
- `use_builtin=true` is ONLY valid for **MOM** department (momentum with lookback/skip params) and **REV** department (reversal = short-term mean reversion, also lookback/skip). The builtin tool computes momentum returns from CRSP — that's all it can do.
- **FUND** department MUST set `use_builtin=false`. Its factors require custom Python scripts (strategy_coder writes the code, run_strategy executes it).
- Within MOM/REV, set `use_builtin=false` if you need logic beyond simple lookback/skip (e.g. volatility-adjusted momentum, industry-neutral).

**Reuse:** If a prior custom script can be reused with different params, set `reuse_config` to that strategy_id.

**Custom script requirements** (when use_builtin=false):
- The coder writes a `build_factor(crsp_path, start_year, end_year, **params) -> DataFrame` function.
- Output DataFrame must have columns: `date`, `permno`, `ret`, `factor`.
- CRSP data: `AUTO_QUANT_ROOT/data/crsp_security_month_returns.csv.gz` (columns: permno, date, ret, shrcd, exchcd, mktcap, siccd, ...).
- Compustat data: `AUTO_QUANT_ROOT/data/compustat_fundamentals_quarterly_*.dta` (for FUND, CASH, OPS departments).

**Department themes (3 departments for testing):**
- **MOM**: Momentum — past returns predict future returns. Vary lookback (3–24m), skip (0–3m). Use builtin.
- **REV**: Reversal — short-term mean reversion (1–5m lookback). Use builtin (negative momentum = reversal).
- **FUND**: Fundamentals — book-to-market (`ceqq/mkvaltq`), earnings yield (`ibq/mkvaltq`), ROE (`ibq/ceqq`), asset growth (delta `atq`), investment. Compustat quarterly columns end in `q` (e.g. `atq`, `ceqq`, `saleq`). Must use custom script.

---

**Your tracking list** (persisted across iterations):
{tracking_list}

**Params hint from orchestrator:**
{params_hint}

---

**Your job each iteration:**
1. Pick the next strategy to execute NOW — set strategy_id, strategy_type, params, use_builtin.
2. Update the tracking list (`updated_tracking`):
   - **tried**: Merge any ground-truth results into the list. Mark status, spread_bps, notes.
   - **to_try**: Roadmap of future strategies, ordered by priority. Remove the one you're executing now.
   - **department_notes**: Running observations — what's working, patterns, promising directions.
3. The strategy you pick should be top priority from `to_try` or a new urgent idea based on the orchestrator's theme.

The next strategy_id must start with "{department_code}_". Numbering continues from where the tracking list left off.

Output JSON.
