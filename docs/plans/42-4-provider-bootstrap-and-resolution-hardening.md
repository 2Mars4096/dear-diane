# 42-4: Provider Bootstrap & Resolution Hardening

**Parent:** [42-benchmark-execution-trustworthiness](42-benchmark-execution-trustworthiness.md)
**Status:** completed
**Goal:** Make provider setup and resolution explicit enough that backend readiness is visible, environment variables are unambiguous, and strict-prefix compatibility fallback is surfaced honestly instead of silently disagreeing with runtime behavior.

## Problem

The current provider story is split across runtime config and prefix-based resolution:

- runtime config expects `DAN_OPENAI_API_KEY`, `DAN_ANTHROPIC_API_KEY`, and `DAN_GOOGLE_API_KEY` for named provider registration
- the default LLM client path reads `DAN_LLM_API_KEY` with fallback to `LLM_API_KEY` (unprefixed)
- `get_llm_api_key_status()` in startup also checks `OPENAI_API_KEY` (unprefixed), but `build_engine_config_from_env()` does **not** map `OPENAI_API_KEY` into `providers["openai"]` — so the system can look "configured" in health while the provider map stays empty
- prefix-based resolution (`claude-*` → `anthropic`, `gemini-*` → `google`) only matches if the named provider is actually registered; when it is **not**, resolution falls through to the `"default"` provider, which is **always** registered as `OpenAIProvider` with `config.llm_api_key` / `llm_base_url` (see `providers/factory.py`)

That is too easy to misconfigure. For benchmark runs, the backend should declare what it can actually serve and fail clearly when a requested provider is unavailable.

## Tasks

- [x] 1. Normalize provider bootstrap
  - [x] 1-1. Reconcile the env-var sets: `build_engine_config_from_env()` reads `DAN_*` prefixed vars for provider maps; `get_llm_api_key_status()` also checks `OPENAI_API_KEY` (unprefixed) for health display but does not wire it into providers. Decide whether to support the unprefixed alias or document that only `DAN_OPENAI_API_KEY` works.
  - [x] 1-2. Decide whether `LLM_API_KEY` (unprefixed fallback for the default client) should remain as a compatibility alias.
  - [x] 1-3. Make provider readiness visible in `/health` startup diagnostics — currently, missing API keys only produce log warnings via `log_startup_configuration_warnings()` and do **not** appear in `/health` `startup.issues`.

- [x] 2. Harden resolution rules
  - [x] 2-1. Make `claude-*` and `gemini-*` runtime fallback explicit instead of silent when the named provider (`anthropic`, `google`) is not registered.
  - [x] 2-2. Preserve explicit model-provider override failures rather than letting compatibility fallback mask them.
  - [x] 2-3. Make fallback behavior obvious and bounded in readiness/health output.

- [x] 3. Add regression coverage
  - [x] 3-1. Test provider env bootstrap from a clean backend shell.
  - [x] 3-2. Test requested model prefix resolution and failure modes.
  - [x] 3-3. Test that `/health` or startup diagnostics expose missing provider readiness.

## Likely Files

| File | Why |
|------|-----|
| `src/dan/server/runtime_config.py` | `build_engine_config_from_env()` — reads `DAN_*` prefixed env vars for provider maps |
| `src/dan/providers/registry.py` | `resolve_name()` — prefix-based resolution with `"default"` fallback |
| `src/dan/providers/factory.py` | `build_provider_registry()` — always registers `"default"` as `OpenAIProvider` |
| `src/dan/server/startup.py` | `get_llm_api_key_status()` and `log_startup_configuration_warnings()` — readiness checks (currently log-only, not in `/health`) |
| `src/dan/server/routers/misc.py` | `GET /health` — exposes `startup.status` / `startup.issues` from `_startup_degradations` |
| `tests/test_providers/` and runtime tests | Regression coverage (currently minimal — e.g. `test_tier_normalization.py`) |

## Notes

- The goal is to make backend readiness inspectable, not to add more provider complexity.
- For benchmarks, explicit failure is better than accidental fallback to a provider that happened to be registered.
- This plan should make the backend shell namespace requirements boring and deterministic.

## Audit Notes (2026-03-25)

- **Prefix resolution is conditional, not blind**: `resolve_name()` only routes `claude-*` → `anthropic` if `anthropic in self._providers`. When it is not, resolution falls through to `"default"` (always `OpenAIProvider`). The fix should either raise for known prefixes when the expected provider is absent, or warn loudly.
- **`"default"` is always registered** via `factory.py` — so `resolve_name()` never raises `KeyError` in practice; it silently uses `"default"` for any unrecognized prefix.
- **`get_llm_api_key_status()` checks `OPENAI_API_KEY`** (unprefixed) as "configured", but `build_engine_config_from_env()` does NOT wire `OPENAI_API_KEY` into `providers["openai"]` — only `DAN_OPENAI_API_KEY` does. This inconsistency can make the system look configured when it is not.
- **Missing API keys are log warnings only** — `log_startup_configuration_warnings()` prints to logger but does not record a `_startup_degradation`. `/health` will not reflect missing keys unless a new degradation entry is added.
- **`init_managers` degradation shape** (`{"component", "reason"}`) differs from `_record_startup_degradation`'s expected shape (`subsystem`, `message`) — may need normalization.

## Completion Notes (2026-04-04)

- Runtime config supports provider env aliases, normalizes readiness diagnostics, and exposes provider status through `/health`.
- `ProviderRegistry.resolve_runtime_name()` is now the shared source of truth for runtime and readiness. Exact provider overrides still fail closed when missing, while strict-prefix compatibility fallback to `"default"` is now surfaced as a degraded readiness issue instead of silently disagreeing with runtime behavior.
- `build_provider_registry()` now lazy-imports `OpenAIProvider`, which keeps the "provider SDKs are optional deps" story honest for import-time startup and targeted test collection.
- Regression coverage verifies alias bootstrap, runtime/readiness fallback parity, strict-prefix behavior, and readiness diagnostics.
