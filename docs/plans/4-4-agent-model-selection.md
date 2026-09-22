# 4-4: Independent agent and model selection

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** in-progress
**Goal:** Choose the orchestration harness independently from the model source, model, reasoning, and supported speed options for both leads and teammates.

## Tasks
- [x] Define shared backend source capabilities; migrate existing saved choices in the frontend batch.
- [x] Route Codex and Claude Code directly through OpenRouter with subprocess-scoped credentials and provider-scoped continuation.
- [x] Forward DAN/OpenRouter reasoning options through lead, sidecar, and team execution.
- [ ] Unify lead/team controls with explicit agent, model source, model, reasoning, and fast settings; explain unsupported combinations.
- [ ] Verify routing, credential isolation, saved selections, live Codex transport, and frontend build/browser behavior.
- [ ] Update user/API documentation and tracking; commit implementation in logical batches and push.

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
