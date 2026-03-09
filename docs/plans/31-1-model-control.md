# 31-1: Model Control & Configuration

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Enable in-chat model switching, activate TierPolicy via env vars, add get_config/set_config expansion, and surface model info so the LLM can truthfully answer "what model are you?"

## Problem

Model selection is frozen at startup: `ChatManager._chat_model` is set once from `DAN_CHAT_MODEL` or `DAN_LLM_MODEL` and never updated. Changing models requires editing `.env` and restarting. TierPolicy (tier scoring, escalation/de-escalation) is fully implemented but never wired into `EngineConfig`. Users cannot query current config from chat, and `set_config` blocks `DAN_LLM_*` / `DAN_CHAT_*` keys.

## Tasks

- [x] 1. `/model` command — in-chat model switching
  - [x] 1-1. Add `/model` to `_FAST_COMMAND_PREFIXES` in `src/dan/server/concierge/runtime.py` (line 83) and `_BYPASS_PREFIXES` in `src/dan/server/concierge/dispatcher.py` (line 21)
  - [x] 1-2. Add `_handle_model_command(msg)` in `runtime.py`, following the pattern of `_handle_memory_command`. Call it from `_try_fast_command` after memory, before mcp
  - [x] 1-3. `/model` without args: return current model from `self.chat_manager._chat_model`
  - [x] 1-4. `/model <name>`: validate via `ProviderRegistry.resolve(name)` — access the registry through `self.chat_manager` (field name may be `_provider_registry` or passed via `CapabilityContext`; verify at implementation time). On success, set `self.chat_manager._chat_model = name`, respond with confirmation. On `KeyError`, respond with "Unknown model. Registered providers: …"
  - [x] 1-5. Support `/model --save <name>`: same as above, plus call `set_config`-style logic to persist `DAN_CHAT_MODEL` to `.env` (or `DAN_LLM_MODEL` if `DAN_CHAT_MODEL` not set). Per-session only when `--save` omitted
  - [x] 1-6. Add `/model` to adapter `/help` output in `src/dan/cli/adapter.py` (line 404): "Available commands: … /model [name], …"

- [x] 2. TierPolicy activation via env var
  - [x] 2-1. In `src/dan/server/chat_factory.py`, extend `_build_engine_config()` (or the function that builds `EngineConfig`): when `DAN_ENABLE_TIER_POLICY` is `1`/`true`/`yes`, set `default_model_policy=TierPolicy()`. Parse `DAN_TIER_MAP` (JSON string) if present and pass as `tier_map` to `TierPolicy(tier_map=...)`
  - [x] 2-2. Mirror the same logic in `src/dan/server/app.py` `_get_engine_config()` (lines 115–169): add `default_model_policy` when `DAN_ENABLE_TIER_POLICY` is on, with optional `DAN_TIER_MAP` override
  - [x] 2-3. Ensure `EngineConfig` in `src/dan/engine/executor.py` already has `default_model_policy` (it does at line 142). No change needed there

- [x] 3. `get_config` capability tool
  - [x] 3-1. Add `handle_get_config(args, ctx)` in `src/dan/server/capability_handlers.py`. Return non-sensitive config: current model (`ctx.chat_manager._chat_model`), base URL (redact to hostname only, e.g. `api.example.com`), bot name, active features (which `DAN_*` learning env vars are on), surface type, tier policy status, MCP servers connected
  - [x] 3-2. Redact API keys: show only last 4 chars (e.g. `sk-...xyz1`)
  - [x] 3-3. Register `get_config` in `ChatCapabilityRegistry` for all modes including `ask` (use `ALL_MODES` or equivalent)
  - [x] 3-4. Add `GET_CONFIG_CAPABILITY_SCHEMA` with no required params (optional `keys` filter if useful)

- [x] 4. Expand `set_config` allowed prefixes
  - [x] 4-1. Add `DAN_LLM_MODEL`, `DAN_CHAT_MODEL`, `DAN_LLM_BASE_URL` to `_CONFIGURABLE_PREFIXES` in `src/dan/server/capability_handlers.py` (lines 480–485)
  - [x] 4-2. Keep `DAN_LLM_API_KEY` blocked — user should use `DAN_OPENAI_API_KEY` etc. directly
  - [x] 4-3. Add `chat_manager: Any = None` to `CapabilityContext` in `src/dan/server/capability_registry.py` and populate it in `app.py` lifespan where the template context is built. When `DAN_LLM_MODEL` or `DAN_CHAT_MODEL` is changed via `set_config`, update `ctx.chat_manager._chat_model` in-memory. Update `handle_set_config` to call `ctx.chat_manager._chat_model = value` when key is one of these
  - [x] 4-4. Update `set_config` schema description to mention model/base URL

- [x] 5. Model visibility in surface hints
  - [x] 5-1. In `src/dan/server/chat_manager.py`, add `{model_name}` to the surface hint template. `SURFACE_HINTS` values (lines 428–468) are static strings; instead, interpolate at use site in `_build_messages` (around line 2799): append a line like "Current model: {model_name}" to the surface hint, or add a separate block. Use `self._chat_model` as the value
  - [x] 5-2. Ensure the model line is included in the system prompt so the LLM can truthfully answer "what model are you?"

- [ ] 6. Tests
  - [ ] 6-1. Add tests for `/model` in `tests/` (e.g. `tests/test_concierge_fast_commands.py` or similar): parsing, validation against ProviderRegistry, state update, `--save` persistence
  - [ ] 6-2. Add tests for TierPolicy activation: `DAN_ENABLE_TIER_POLICY=1` sets `default_model_policy` in EngineConfig; `DAN_TIER_MAP` override is applied
  - [ ] 6-3. Add tests for `get_config`: output structure, redaction of API keys, feature detection
  - [ ] 6-4. Add tests for `set_config` model change: when `DAN_CHAT_MODEL` is set, `ChatManager._chat_model` is updated in-memory

## Decisions

- (filled in during execution)

## Notes

- `ProviderRegistry.resolve(model)` raises `KeyError` when no provider matches — use try/except for validation
- `TierPolicy()` with no args uses `tier_map=None`; `ModelSelector._select_tier` will call `resolve_tier_map(configured_providers, user_map)` to get defaults
- `DAN_TIER_MAP` format: JSON object, e.g. `{"micro":"gpt-4o-mini","routine":"gpt-4o","reasoning":"o3-mini","critical":"o3"}`

## Estimate

~1.5 days
