# 7-2: Multi-Provider LLM Registry

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** completed
**Goal:** Replace the single-provider `AsyncOpenAI` client with a provider registry that routes per-node `model` fields to the correct API (OpenAI, Anthropic, Google), enabling mixed-model workflows within a single graph.

## Tasks

- [x] 1. Provider protocol
  - [x] 1-1. Define `LLMProvider` protocol in new `src/dan/providers/__init__.py`: `async complete(messages, model, temperature, max_tokens, stream, **kwargs) -> CompletionResult`
  - [x] 1-2. Define `CompletionResult` dataclass: `text: str`, `usage: dict[str, int] | None`, `model: str`
  - [x] 1-3. Define `StreamChunk` dataclass for streaming: `delta: str`, `accumulated: str`, `done: bool`, `usage: dict[str, int] | None`
  - [x] 1-4. Provider protocol should also expose `async stream(messages, model, ...) -> AsyncIterator[StreamChunk]`
  - [x] 1-5. Define `ProviderConfig` dataclass: `api_key: str`, `base_url: str | None`, `default_model: str | None`, `extra: dict[str, Any]` — each provider instance is configured with one of these
- [x] 2. Built-in providers
  - [x] 2-1. `OpenAIProvider` in `src/dan/providers/openai_provider.py` — wraps `AsyncOpenAI`, supports any OpenAI-compatible endpoint (current behavior extracted)
  - [x] 2-2. `AnthropicProvider` in `src/dan/providers/anthropic_provider.py` — wraps `anthropic.AsyncAnthropic`, maps chat messages to Anthropic format (system prompt handling)
  - [x] 2-3. `GoogleProvider` in `src/dan/providers/google_provider.py` — wraps `google.generativeai` async client, maps messages to Gemini format
  - [x] 2-4. Each provider handles its own transient error types for retry (provider-specific exception classes propagate to caller's retry loop)
- [x] 3. Provider registry
  - [x] 3-1. `ProviderRegistry` class: `register(name, provider)`, `resolve(model_name) -> LLMProvider`
  - [x] 3-2. Resolution order: (1) exact model → provider override map, (2) prefix pattern match (`gpt-*` → openai, `claude-*` → anthropic, `gemini-*` → google), (3) `default` provider as final fallback
  - [x] 3-3. **Explicit `default` provider** — `EngineConfig.llm_base_url` + `llm_api_key` create the `default` OpenAI-compatible provider. Current vectorengine setup works unchanged because `claude-sonnet-4-6` resolves to `default` (not to an Anthropic native provider) unless the user explicitly registers an `anthropic` provider
  - [x] 3-4. **Model → provider override map** — `EngineConfig.model_provider_map: dict[str, str]` lets users pin specific models to specific providers
  - [x] 3-5. Named provider registration: `providers: dict[str, ProviderConfig]` in `EngineConfig` — each entry creates a named provider instance
  - [x] 3-6. Singleton registry created in `Engine.__init__` from config
- [x] 4. Key management
  - [x] 4-1. Extend `EngineConfig` with `providers: dict[str, ProviderConfig]` and `model_provider_map: dict[str, str]`
  - [x] 4-2. Backward compatible: existing `llm_api_key` + `llm_base_url` auto-create a `"default"` provider entry. Zero config change for existing users
  - [x] 4-3. Env var convention: `DAN_OPENAI_API_KEY`, `DAN_ANTHROPIC_API_KEY`, `DAN_GOOGLE_API_KEY` auto-populate named providers when present
  - [x] 4-4. Server `app.py` scans env for `DAN_*_API_KEY` patterns, builds `providers` dict automatically
  - [x] 4-5. Update `.env.example` with all provider key placeholders
- [x] 5. Refactor `LLMExecutor` + `RouterExecutor`
  - [x] 5-1. Replace direct `AsyncOpenAI` usage in `LLMExecutor` with `provider.stream()` / `provider.complete()` via `_resolve_provider(model, context)`
  - [x] 5-2. Streaming path uses `provider.stream(...)` → emits `intermediate_text` events as before
  - [x] 5-3. Preserve output normalization loop — re-prompt uses same provider/model
  - [x] 5-4. `fallback_model` from `retry_policy` (7-1) can now cross providers (e.g., `claude-sonnet-4` → `gpt-4o`)
  - [x] 5-5. `_get_client` retained as backward-compat fallback when `provider_registry` is None
  - [x] 5-6. Refactor `RouterExecutor` — `_call_router_llm` dispatches via `context.provider_registry.resolve(model)` with fallback to direct `AsyncOpenAI`
- [x] 6. Cost table
  - [x] 6-1. Static `COST_PER_1K_TOKENS` dict in `src/dan/providers/costs.py` mapping `model_name -> {prompt: float, completion: float}`
  - [x] 6-2. Cover major models: GPT-4o, GPT-4o-mini, GPT-4.1, o1, o3-mini, Claude Opus/Sonnet/Haiku, Gemini 2.0/2.5 Pro/Flash
  - [x] 6-3. `estimate_cost(model, prompt_tokens, completion_tokens) -> float | None` utility function
  - [x] 6-4. Cost table is best-effort — unknown models return `None` (no crash)
- [x] 7. Config panel UI — basic + advanced pattern
  - [x] 7-1. **Basic settings** (always visible): model input with datalist autocomplete grouped by provider, temperature, system prompt textarea
  - [x] 7-2. **Advanced settings** (collapsible, closed by default): `max_tokens`
  - [x] 7-3. Show provider badge next to model name (OpenAI/Anthropic/Google color-coded)
  - [ ] 7-4. Fallback model field in retry policy section gets same model autocomplete — deferred
  - [ ] 7-5. The advanced section extensible pattern — deferred (base_url/api_key per-node overrides)
- [x] 8. Tests
  - [x] 8-1. Unit: `ProviderRegistry` — resolve by prefix, exact model override, `default` fallback, custom named provider (11 tests)
  - [x] 8-2. Unit: `OpenAIProvider.complete()` with mock client (4 tests)
  - [x] 8-3. Unit: `AnthropicProvider.complete()` with mock — verify message format translation (3 tests)
  - [x] 8-4. Unit: `GoogleProvider.complete()` with mock — verify message format translation (2 tests)
  - [x] 8-5. Unit: `LLMExecutor` dispatches to correct provider based on model name
  - [x] 8-6. Unit: `RouterExecutor` dispatches through provider registry
  - [x] 8-7. Unit: cross-provider fallback (Claude primary → GPT-4o fallback)
  - [x] 8-8. Integration: graph with mixed models (Claude + GPT nodes) runs end-to-end with mocks
  - [x] 8-9. Backward compat: existing 338 tests pass unchanged + single-key config still works
  - [x] 8-10. Unit: `model_provider_map` pins model to explicit provider, overriding prefix match
- [x] 9. Dependencies
  - [x] 9-1. Add `anthropic>=0.40` to `pyproject.toml` (optional dependency group)
  - [x] 9-2. Add `google-generativeai>=0.8` to `pyproject.toml` (optional dependency group)
  - [x] 9-3. Providers gracefully handle missing SDK — `ImportError` → clear error message at constructor time
- [ ] 10. Docs sync — deferred per instructions (do NOT update changelog, todo, or architecture docs)
  - [ ] 10-1. Update `architecture.md` *(deferred — docs sync low priority)*
  - [ ] 10-2. Update `llm-api-guide.md` *(deferred — docs sync low priority)*
  - [ ] 10-3. Update `README.md` *(deferred — docs sync low priority)*
  - [ ] 10-4. Update `todo.md` / `changelog.md` *(deferred — docs sync low priority)*

## Decisions

- **File naming**: Used `openai_provider.py`, `anthropic_provider.py`, `google_provider.py` (not `openai.py`) to avoid name collisions with the SDK packages themselves.
- **`_get_client` retained**: Instead of removing `_get_client`, kept it as backward-compat fallback. `_resolve_provider` is the primary path; `_get_client` only used when `provider_registry` is None and `self._client` is injected.
- **`OpenAIProvider.from_client()` classmethod**: Added for backward compat — existing tests that inject `AsyncOpenAI` mocks can wrap them in an `OpenAIProvider` without a config.
- **Unknown provider names treated as OpenAI-compatible**: If a user registers a provider with a custom name (e.g., `"my-local-llm"`), the engine creates an `OpenAIProvider` since most local LLMs expose OpenAI-compatible APIs.
- **Prefix-only matches against registered providers**: If `openai` is not registered as a provider, `gpt-4o` falls through to `default` instead of erroring. This preserves backward compat for users running everything through a single OpenAI-compatible proxy.
- **Streaming-first with fallback**: `_call_via_provider` tries `provider.stream()` first, catches any exception and falls back to `provider.complete()`. This matches the pre-refactor behavior.
- **Frontend SKIP_FIELDS**: Added `model`, `temperature`, `system_prompt`, `max_tokens` to SKIP_FIELDS so they don't render in the generic field loop — they now have a dedicated `LLMConfigSection` with provider badge and datalist.
- **7-4 and 7-5 deferred**: Fallback model autocomplete in retry policy and per-node base_url/api_key overrides are nice-to-haves that can be added without breaking changes.

## Notes

- **Routing safety:** prefix-only routing (`claude-*` → Anthropic) would break the current default where `claude-sonnet-4-6` runs through `vectorengine.ai` (OpenAI-compatible). The resolution order (exact override → prefix → `default` fallback) prevents this. The `default` provider is always the backward-compat safe path.
- **RouterExecutor uses LLM calls directly** — refactored to use `_call_router_llm` which dispatches via provider registry with backward compat fallback.
- Anthropic SDK uses a different message format (system prompt is a top-level parameter, not a message role). The `AnthropicProvider._split_system()` handles this translation transparently.
- Google Gemini SDK has its own async interface that differs from OpenAI's. The `GoogleProvider._to_gemini_messages()` handles role mapping (assistant→model, system→system_instruction).
- Provider SDKs are **optional dependencies** — users who only use OpenAI don't need `anthropic` or `google-generativeai`. Clear `ImportError` messages at constructor time.
- The cost table is intentionally static and best-effort. Dynamic pricing APIs are out of scope. Users can extend/override the table.
- `vectorengine.ai` (current default) is OpenAI-compatible, so it continues to work through `OpenAIProvider` with a custom `base_url` as the `"default"` provider.
- **Basic + Advanced UI pattern:** most users only need model selection and temperature. The collapsible advanced section keeps the common case clean.
- All 466 tests pass (338 existing + 128 new provider tests).
