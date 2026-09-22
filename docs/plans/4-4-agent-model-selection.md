# 4-4: Independent agent and model selection

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Choose the orchestration harness independently from the model source, model, reasoning, and supported speed options for both leads and teammates.

## Tasks
- [x] Define shared source capabilities and migrate existing saved choices.
- [x] Route Codex and Claude Code directly through OpenRouter with subprocess-scoped credentials and provider-scoped continuation.
- [x] Forward DAN/OpenRouter reasoning options through lead, sidecar, and team execution.
- [x] Unify lead/team controls with explicit agent, model source, model, reasoning, and fast settings; explain unsupported combinations.
- [x] Verify routing, credential isolation, saved selections, live Codex transport, and frontend build/browser behavior.
- [x] Update user/API documentation and tracking; commit implementation in logical batches.
- [x] Push completed implementation batches to origin/main.

## Decisions
- This precedes SSH implementation at the user's request.
- Codex + OpenRouter means OpenRouter powers Codex itself, independently of team delegation.
- Native means the selected harness's account/configuration. DAN native configuration is an API configuration, not a Codex/Claude subscription.
- Cursor/Antigravity retain native sources until an external provider route is verified. Claude non-Anthropic OpenRouter models are experimental; OpenRouter guarantees Claude Code compatibility only with Anthropic first-party routing.
- Preserve existing native account state and user CLI configuration; never persist keys in browser profiles or command arguments.

## Sources
- https://learn.chatgpt.com/docs/config-file/config-reference
- https://openrouter.ai/docs/cookbook/coding-agents/codex-cli
- https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration
- https://cursor.com/docs/cli/reference/parameters
- https://antigravity.google/docs/cli/reference/

## Validation
- 149 targeted backend/server tests pass, including .env startup discovery, per-process credential isolation, unsupported combinations, provider-scoped continuation, and Codex app-server resume routing.
- Live fixed-reply checks: Codex + OpenRouter DeepSeek V4.1 Flash, Claude Code + the same model, and DAN's gateway provider with medium reasoning each returned `DAN_MODEL_OK`. Temporary workspaces and state under `/private/tmp/`; keys loaded from the existing project `.env`, never printed.
- Live checks establish response routing, not a full coding benchmark or universal compatibility for every gateway model.
- 223 focused frontend tests and production build/bundle budgets pass. Browser checks cover Codex Astra/medium/fast → OpenRouter DeepSeek/high → native restoration, persistence after reload, and menu containment at 390×844. Screenshots: `output/playwright/model-selection-desktop.png`, `output/playwright/model-selection-phone.png`; no browser errors observed.
- Desktop packaging/replacement is not part of this source change. Gateway-specific native transcript discovery/token analysis can be extended separately for Claude's isolated configuration directory.
