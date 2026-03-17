# 31-5: Defaults & Documentation

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Make all features discoverable via a comprehensive `.env.example`, startup log, feature bundles, and README update so new and existing users can find and activate everything DAN offers.

## Problem

`.env.example` documents 3 env vars (`DAN_LLM_API_KEY`, `DAN_LLM_BASE_URL`, `DAN_LLM_MODEL`). The project has 30+ configurable env vars controlling model selection, learning features, notifications, MCP servers, concierge behavior, cost thresholds, and more. A new user — or even the developer — can't discover most features without reading source code.

## Tasks

- [x] 1. `.env.example` overhaul
  - [x] 1-1. Audit all `os.environ.get("DAN_` and `os.getenv("DAN_` calls across the codebase. Produce a complete list of env vars with defaults, types, and one-line descriptions.
  - [x] 1-2. Rewrite `.env.example` organized by section:
    - **LLM Provider** — `DAN_LLM_API_KEY`, `DAN_LLM_MODEL`, `DAN_LLM_BASE_URL`, `DAN_CHAT_MODEL`, `DAN_OPENAI_API_KEY`, `DAN_ANTHROPIC_API_KEY`, `DAN_GOOGLE_API_KEY`
    - **Model Tiering** — `DAN_ENABLE_TIER_POLICY`, `DAN_TIER_MAP`
    - **Learning & Evolvement** — `DAN_LEARNING_MODE`, `DAN_PROMPT_OPTIMIZATION`, `DAN_MODEL_LEARNING`, `DAN_TOPOLOGY_LEARNING`, `DAN_SKILL_LEARNING`, `DAN_MEMORY_EXTRACTION`, `DAN_MEMORY_EXTRACTION_LLM`, `DAN_MEMORY_DUAL_WRITE`
    - **Concierge Behavior** — `DAN_CONCIERGE_AUTONOMY`, `DAN_CONCIERGE_REASSURANCE_DELAY`, `DAN_CONCIERGE_REASSURANCE_INTERVAL`, `DAN_CONCIERGE_PARALLEL_PREP`, `DAN_CONCIERGE_PREP_TIMEOUT`
    - **Identity & Display** — `DAN_BOT_NAME`, `DAN_SHOW_COST`
    - **Notifications** — `DAN_NOTIFY_BELL`, `DAN_NOTIFY_WEBHOOK_URL`
    - **Cost & Safety** — `DAN_COST_CONFIRM_THRESHOLD`, `DAN_MUTATION_CONFIRM`
    - **MCP Servers** — `DAN_MCP_CONFIG`
    - **Tools** — `DAN_STATA_PATH`, `DAN_WORKSPACE_ROOT`, `DAN_WHISPER_API_KEY`
    - **Service** — `DAN_DATA_DIR`, `DAN_LOG_LEVEL`, `DAN_EVOLVEMENT_LOG_LEVEL`, `DAN_MAX_CONCURRENT_LLM`, `DAN_MAX_CONCURRENT_RUNS`, `DAN_MAX_MEMORY_MB`
  - [x] 1-3. Every entry: variable name, default value, one-line description, example. Commented out by default (except API key).

- [x] 2. Startup feature log
  - [x] 2-1. At server/chat startup, log a summary of active features: model name, tier policy on/off, learning features on/off, MCP servers connected, notification channels active, autonomy level.
  - [x] 2-2. In `dan-chat` REPL, print a one-line startup banner: `Model: claude-sonnet-4-6 | Tier: off | Learning: off | MCP: stata(3 tools)`.
  - [x] 2-3. Wire into `chat_factory.py` `build_chat_services()` and `app.py` lifespan startup.

- [x] 3. Feature bundle env vars
  - [x] 3-1. `DAN_LEARNING_MODE=1` — activates all safe learning features (implemented in 31-3).
  - [x] 3-2. `DAN_FULL_TOOLS=1` — registers all 13 newly-exposed tools (from 31-3) plus the existing set. When `0` (default), only the current tool set is registered. This provides backward compatibility while making it easy to opt in.
  - [x] 3-3. Document bundles in `.env.example` with a "Quick Profiles" section at the top:
    ```
    ## Quick Profiles (uncomment one line to activate a bundle)
    # DAN_LEARNING_MODE=1      # Turn on all safe learning features
    # DAN_FULL_TOOLS=1         # Expose all 32+ tools in chat
    # DAN_ENABLE_TIER_POLICY=1 # Auto-assign models by task difficulty
    ```

- [x] 4. `docs/cli.md` update
  - [x] 4-1. Add `dan-ask` entry (new command from 31-4): synopsis, options, examples (`dan-ask "summarize this file"`, `echo "question" | dan-ask --pipe`). Place after `dan-chat` section since it's a thin wrapper.
  - [x] 4-2. Update `dan-chat` section: add `--ask`, `--pipe`, `--output`, `--model` flags to the options table. Add examples for pipe/one-shot usage.
  - [x] 4-3. Update "REPL commands" table in `dan-chat` section (currently lines 166-180): add `/model [name]`, `/cost`, `/status`, `/retry`, `/memory-delete <id>`, `/memory-forget <query>`, `/memory-confirm`, `/memory-reject`.
  - [x] 4-4. Update "Environment Variables" table (currently lines 25-36): add all new env vars from Phase 21 (`DAN_ENABLE_TIER_POLICY`, `DAN_TIER_MAP`, `DAN_LEARNING_MODE`, `DAN_FULL_TOOLS`, `DAN_SHOW_COST`, `DAN_CONCIERGE_PREP_TIMEOUT`).

- [x] 5. `README.md` update
  - [x] 5-1. Add "Configuration" section with link to `.env.example` and description of key env var groups.
  - [x] 5-2. Add "Chat Commands" section listing all slash commands: `/model`, `/cost`, `/status`, `/retry`, `/save`, `/build-*`, `/memory-*`, `/mcp`, `/undo`, `/show`, `/list`, `/run`, `/help`.
  - [x] 5-3. Add "Quick Start" snippet for common daily-use setup (model + learning + tools).

- [x] 6. `docs/llm-api-guide.md` update
  - [x] 6-1. Add a "Chat Capability Tools" section documenting all 13 newly-exposed tools (from 31-3): `python_eval`, `csv_read`, `compress`, `file_copy`, `file_move`, `file_delete`, `git_status`, `git_diff`, `git_log`, `git_branch`, `git_commit`, `git_worktree`, `notify`, `text_diff`. For each: brief description, mode availability, parameter summary.
  - [x] 6-2. Document new introspection tools: `inspect_node`, `list_test_cases`, `run_test_case`.
  - [x] 6-3. Document `get_config` and expanded `set_config` as capability tools LLM callers can use.
  - [x] 6-4. Document `DAN_FULL_TOOLS=1` requirement for the new tools to be available.

- [x] 7. `docs/architecture.md` update
  - [x] 7-1. Update "Concierge Runtime" section: document new fast commands (`/model`, `/cost`, `/status`, `/retry`, `/memory-delete`, `/memory-forget`, `/memory-confirm`, `/memory-reject`), error retry UX, and cost tracking in chat path.
  - [x] 7-2. Add env vars to relevant sections: `DAN_ENABLE_TIER_POLICY`, `DAN_TIER_MAP`, `DAN_LEARNING_MODE`, `DAN_FULL_TOOLS`, `DAN_SHOW_COST`, `DAN_CONCIERGE_PREP_TIMEOUT`.
  - [x] 7-3. Document new capability handler patterns: 13 tool-wrapping handlers, `DAN_FULL_TOOLS` gate, workflow introspection tools.
  - [x] 7-4. Document `dan-ask` CLI entry point in directory structure / CLI overview.

- [x] 8. `docs/development-plan.md` update
  - [x] 8-1. Add Phase 21 to the roadmap section with a one-line description: "Phase 21 — Daily-Use QoL: model control, visibility, capability exposure, CLI power-user features, defaults overhaul."
  - [x] 8-2. If any "Future" items in the roadmap are now addressed by Phase 21, mark them as completed/promoted.

- [x] 9. Changelog
  - [x] 9-1. Add changelog entry for Phase 21 completion (after all subplans are done).

## Decisions

- `.env.example` uses `#` comments for all values except the API key line, so copy-paste doesn't accidentally activate features.
- Startup banner is always printed in `dan-chat`; server mode logs at INFO level.
- Feature bundles use `os.environ.setdefault()` so explicit per-feature overrides always win.

## Notes

- This plan runs last (after 31-1 through 31-4) so it can document everything that was added.
- The env var audit may discover additional undocumented vars; add them to `.env.example` as found.
- 2026-03-17 follow-up: `.env.example` now explicitly distinguishes the baseline required LLM path from optional native-provider, tiering, and feature-specific settings for single-provider setups.
- `docs/cli.md` is the most impactful doc to update — it's the primary reference for all commands and flags. `dan-ask` is a new entry point and all new slash commands need to go in the REPL commands table.
- `docs/llm-api-guide.md` is what LLM callers use to construct valid tool calls — 13 new capability tools is a major surface area expansion that must be documented here.
- `docs/bugs.md` is not updated in this plan — it's a tracking doc, not project-facing. Any bugs found during implementation go there as they're discovered.
