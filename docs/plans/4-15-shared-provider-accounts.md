# 4-15: Linked-app credential fallback

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Let Todo use Diane's file-backed credentials by default and its own credentials otherwise, without modifying .env or Keychain.

## Tasks
- [x] Inspect Diane and `/Volumes/data/Dropbox/Projects/todo-sticky` provider handling.
- [x] Publish only credential-file locations from the desktop backend.
- [x] Read matching Diane keys natively in Todo; fall back to Todo's own key and retain independent models.
- [x] Add default-on preference, availability feedback and connection-test/quick-switch support in Todo.
- [x] Verify fixture-based source preservation, provider/endpoint isolation, missing/corrupt files, backend tests and frontend builds.
- [ ] Installed-app acceptance after updating both apps.

## Decisions
- Latest user direction supersedes both the Keychain-sharing proposal and the briefly discussed hosted-login service.
- No Keychain writes, cloud account service, copied secrets, .env edits, background Diane requirement or cross-device synchronization.
- Paths-only `~/.dan/linked-credentials.json` supports desktop discovery. Existing `~/.dan/graphs_dir` remains an older-install fallback.
- Standard OpenRouter/OpenAI/Moonshot endpoints are shared with Todo; custom/other providers use Todo's own configuration. Process-only environment keys cannot be discovered while Diane is closed.
- Earlier synthetic Keychain writes both failed with -60006; no real credentials were migrated. Removed prototype integration; Todo's prototype was never applied.

## Validation
- Nine Python tests and 24 Todo native tests pass; frontend builds pass.
- Todo full TypeScript checking reports 16 pre-existing Star Focus errors, identical before and after this change.
