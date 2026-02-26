# 7: Phase 4 — Core Hardening

**Status:** completed
**Goal:** Make existing nodes robust and the platform practically usable. Fill gaps that prevent real workflows from running reliably — retry/fallback, multi-provider LLM dispatch, batteries-included tools, example templates, and per-node cost visibility.

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [7-1](7-1-runtime-reliability.md) | Runtime Reliability | Retry policy model, ToolExecutor retry, fallback model, concurrency audit | `models/nodes.py`, `executors/llm.py`, `executors/tool.py`, `executors/control_flow.py`, `ConfigPanel.tsx` |
| [7-2](7-2-multi-provider-llm.md) | Multi-Provider LLM Registry | Provider abstraction, OpenAI/Anthropic/Google, key management, per-node dispatch | `executors/llm.py`, new `providers/`, `engine/executor.py`, `ConfigPanel.tsx` |
| [7-3](7-3-built-in-tools.md) | Built-in Tool Library (`dan.tools`) | ~10 common tools, auto-registration, ACI quality | new `src/dan/tools/`, `server/app.py`, `executors/tool.py` |
| [7-4](7-4-templates-observability.md) | Templates + Observability | 5 example workflows, per-node token/cost display | `examples/`, `DanNode.tsx`, `LogPanel.tsx`, `ConfigPanel.tsx` |
| [7-5](7-5-general-tool-design.md) | General Tool Design | Generic run_python, deprecate plot_backtest/save_grid_csv; agent-generated code | `app.py`, `dan.tools`, `SandboxRunner` |

## Dependencies / Sequencing

```
7-1 (retry/concurrency)  ──→  7-4 (templates + observability)
                            ↗
7-2 (multi-provider LLM) ──→  7-4
                            ↗
7-3 (built-in tools)     ──→  7-4
```

7-1, 7-2, and 7-3 are independent — can proceed in any order or in parallel. 7-4 is the capstone that depends on all three.

**Recommended execution order:**
1. **7-1** first — small, unblocks retry for tool and LLM executors
2. **7-2** or **7-3** next (independent of each other) — pick based on what workflow you want to run first
3. **7-4** last — templates exercise tools + multi-provider; observability is the polish layer

## Shared Decisions

- **`retry_policy` lives on `NodeBase`** — every node type can opt in. Executors that don't support retry ignore it. This avoids per-type duplication. Field names (`backoff`, `fallback_model`, `on_failure`) match `architecture.md` contract.
- **`on_failure="halt"` = stop + checkpoint** — the scheduler stops dispatching new topological levels. Already-running parallel nodes in the same level finish (async tasks can't be safely cancelled). A checkpoint is written at the halt point. `RunResult(success=False)` is returned. This enables inspect → fix → resume.
- **Provider dispatch: explicit `default` + optional overrides** — `EngineConfig.llm_base_url` + `llm_api_key` create a `"default"` OpenAI-compatible provider (backward compat with vectorengine). Named providers (`"openai"`, `"anthropic"`, `"google"`) are opt-in via `EngineConfig.providers`. Resolution order: exact `model_provider_map` → prefix pattern match → `default` fallback. Current `claude-sonnet-4-6` via vectorengine works unchanged.
- **Config panel: basic + advanced** — LLM node config shows model, temperature, system prompt by default. Collapsible "Advanced" section reveals per-node `base_url` override, `api_key` override, `max_tokens`, and extensible extra kwargs. Keeps common case clean, power users can customize.
- **`dan.tools` auto-registers with graceful degradation** — server lifespan discovers and registers all `dan.tools.*` functions automatically. Missing optional SDKs skip individual tools (log warning), not crash. All file tools enforce workspace-root sandboxing.
- **ACI quality is inline, not a separate pass** — every tool in 7-3 ships with rich descriptions, example usage, and input validation from day one.
- **`max_concurrency` audit, not rebuild** — `ForEachNode.parallelism` already works via semaphore. 7-1 audits whether a graph-wide ceiling is needed (likely just an `EngineConfig` field).
- **Observability extends 6-11** — run-level token aggregation and `RunSummaryBar` already exist. 7-4 adds per-node granularity and estimated dollar cost.
- **Code nodes don't retry** — `exec()` is deterministic with no timeout mechanism. `retry_policy.max_retries` is ignored on code nodes. `on_failure` (skip/halt) is still honored.

## Notes

- `ForEachNode.parallelism` + `asyncio.Semaphore` already implements per-node concurrency control (Phase 0). The todo item "`max_concurrency`" is largely done — 7-1 audits whether a graph-wide cap is needed.
- `LLMExecutor` already has hardcoded `max_retries=3, backoff=1.0` for transient API errors. 7-1 replaces this with the configurable `retry_policy` from architecture docs.
- 6-11 already added: `NodeResult.metadata.usage`, aggregate token counts in `run_completed`, `RunSummaryBar`, `nodeTimings`, duration badges. 7-4 extends to per-node token display and cost estimation.
