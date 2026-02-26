# 7-5: General Tool Design — Agent-Generated Code

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** not-started
**Goal:** Replace domain-specific tools (plot_backtest, save_grid_csv) with a general "node writes code, generic runner executes" pattern — aligned with state-of-the-art IDEs (Cursor, Claude Code).

## Problem

- **plot_backtest** — Hardcodes backtest schema (dates, cumulative_quintiles, ls_cumulative), quant_lib.visualize, fixed output paths. Not general.
- **save_grid_csv** — Hardcodes columns (strategy, lookback, skip, spread_q5_q1_bps, n_dates, error) and row structure. Tied to one result schema.
- Both assume a single domain. Nodes are thin wrappers; they don't "write" anything.
- **State-of-the-art contrast:** Cursor uses `run_terminal`; Claude API uses `code_execution`. Both rely on one generic execution tool; the model generates code/commands, the tool runs them. No domain-specific plot/CSV tools.

## Design

### Core pattern

- **Generic execution tool:** `run_python` accepts `code: str` and `context: dict` (or kwargs). Injects context as variables, runs code in sandbox, returns `{ result, stdout, stderr }`.
- **run_strategy_script:** Keeps backtest-specific logic (factor validation, `build_factor`, etc.). Internally uses shared `_execute_python()` so both tools share sandbox + execution path.
- **plot_one / write_csv:** Become LLM nodes (or composite: LLM → run_python) that generate code from the actual data shape. Or code nodes with dynamic `code` from upstream when schema is known.

### Tool interface (run_python)

```
run_python(code: str, **context)
  - code: Python code. Must assign to `result` (or `output`) for structured return.
  - context: keyword args injected into exec namespace (e.g. item=..., results=..., out_dir=...)
  - Returns: { result, stdout, stderr, error? }
  - Sandbox: reuse SandboxRunner or inline exec with _ALLOWED_BUILTINS; configurable timeout, memory.
```

### Architecture

```
ToolRegistry
  run_python      — generic; shared executor
  run_strategy_script — domain-specific; calls executor + validate_factor_df + run_backtest_from_factor_df
  plot_backtest   — deprecated; optional fallback
  save_grid_csv   — deprecated; optional fallback
```

### Safety

- Reuse existing `SandboxRunner` (subprocess, memory limits) or CodeExecutor inline path.
- Optional: path allow/deny for file ops (DAN_WORKSPACE_ROOT pattern from 7-3).
- Optional: hooks (before_run / after_run) for audit — Cursor-style; low priority for v1.

## Tasks

1. **Extract shared executor**
   - [ ] `dan.tools` or `dan.server.exec` module: `execute_python(code, namespace, timeout=60, memory_mb=512)` → `{ result, stdout, stderr, error? }`
   - [ ] run_strategy_script refactor to call shared executor instead of raw `exec()`

2. **Add run_python tool**
   - [ ] `run_python(code, **context)` — inject context into namespace, call shared executor, return normalized outputs
   - [ ] Register in app.py; add to ToolRegistry
   - [ ] Write tool spec (Accepts/Returns) for markdown agent format

3. **Refactor plot_one**
   - [ ] Option A: LLM node that receives `item`, generates plotting code, passes to run_python
   - [ ] Option B: Code node with `code` from upstream (if schema is fixed)
   - [ ] Update plot_one.md: either LLM or composite(LLM + run_python)
   - [ ] Prompt: "Generate Python to plot item. Use matplotlib. Save to out_dir. Context: item keys: {keys}"

4. **Refactor write_csv**
   - [ ] LLM or code node that receives `results`, generates CSV-writing code, passes to run_python
   - [ ] Update write_csv.md similarly
   - [ ] Prompt: "Generate Python to write results to CSV. Context: results is list of dicts. Infer columns from first row."

5. **Deprecate domain tools**
   - [ ] Mark plot_backtest, save_grid_csv deprecated in docs; keep registered for backwards compat
   - [ ] Add env: `DAN_USE_LEGACY_PLOT_CSV=1` to prefer legacy tools (optional)
   - [ ] Remove after workflow_multi_dept migrates

6. **Workflow and docs**
   - [ ] Update examples/vibe_research_md workflow: plot_one, write_csv use new pattern
   - [ ] Update llm-api-guide: preferred pattern is agent-generated code + run_python
   - [ ] Update architecture.md: tool design principles (generic over domain-specific)

## Decisions

- **Executor sharing:** Extract `execute_python()`; run_strategy_script and run_python both call it. run_strategy_script adds factor validation + backtest orchestration on top.
- **Plot/CSV agents:** Start with LLM-generated code (adds latency but maximizes flexibility). Later: templated code node if schema is predictable.
- **run_bash:** Out of scope for v1. run_python can call `subprocess` for shell if needed; dedicated run_bash could follow in a later plan.
- **MCP:** Not in scope. MCP is for external tool servers; run_python is a built-in.

## State-of-the-art alignment

| Aspect        | Cursor        | Claude API    | DAN (this plan)   |
|---------------|---------------|---------------|-------------------|
| Execution     | run_terminal  | code_execution | run_python        |
| Domain tools  | None          | None          | Deprecate plot/csv|
| Sandbox       | Landlock/seatbelt | Anthropic container | SandboxRunner    |
| Model writes  | Shell commands| Bash/Python   | Python            |

## Notes

- run_strategy_script already proves the pattern for strategy code. This plan generalizes it for any post-processing.
- Reduces server-side hardcoding; workflows become portable across domains.
- Optional follow-up: `run_bash` for shell-heavy workflows (e.g. build scripts, package install).
