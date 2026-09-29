# 4-10: Dear Diane product naming

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Rename the product Dear Diane and its assistant Diane while preserving existing user state and integrations.

## Tasks
- [x] Recover the earlier naming discussion and record the agreed distinction.
- [x] Rename desktop/browser/phone product surfaces, assistant labels, backend prompts, terminal copy, and package metadata.
- [x] Preserve profile paths, bundle identity, protocol/runtime IDs, imports, environment variables, remote services, and saved-state keys.
- [x] Recognize both old and new default session titles and generic agent receipts.
- [x] Add the `dear-diane` CLI alias and update current product documentation.
- [x] Validate frontend/backend regressions, desktop builds, and phone checks.
- [x] Prepare and inspect the renamed desktop bundle.

## Decisions
- Source: local session `01a0e884-f0fa-7ca1-b88b-6954b01792af`, started 2026-09-28. User preferred Dear Diane for its alliteration; public brand Dear Diane, conversational assistant Diane.
- Subtitle: “A workspace for research, code, and ideas.”
- Existing `dan` Python package/commands, `DAN_*`, `com.dan.desktop`, Application Support/dan, storage keys, Android application ID/channel, and repository/release origin remain compatibility contracts.
- Historical plans, changelog entries, and original artwork prompts retain provenance. The selected icon is retained.
- Existing unrelated window-control and idea-cart edits are preserved.

## Validation
- 365 frontend tests; 702 backend tests (one optional live-browser test skipped); one Flutter widget test pass. Final plan-wording adjustment also passes all 122 organism tests.
- Production build, bundle budgets, and Electron compilation pass.
- Real Electron smoke executes the compiled identity setup against an isolated temporary profile: Dear Diane display name and existing dan settings retained.
- macOS packaging succeeds. `editor/release/mac-arm64/Dear Diane.app` passes deep/strict ad-hoc signature verification; plist display/executable names, stable bundle ID, packaged main/web title, and new local-build discovery path verified.
- Installed `/Applications/Dear Diane.app` on 2026-09-29; verified archive equality, signature, owned backend/proxy health, and existing `Application Support/dan` process profile. Moved the inactive old `/Applications/DAN.app` to Trash after successful launch. Remote services, repository name, and published releases remain unchanged.
- Android launcher label is Dear Diane; package/channel remain unchanged. No APK installation performed.
