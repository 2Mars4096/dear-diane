# 4-5: Browser research from ordinary chat

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Let DAN and native leads use the same visible browser for requested research and citation extraction.

## Tasks
- [x] Recheck GUI dispatch, native tool bridges, browser defaults, and installed dependencies.
- [x] Make browser tools available to ordinary DAN chats and native leads without prompt keyword routing.
- [x] Isolate browser state per run, close it on completion/Stop, and keep downloads inside the workspace.
- [x] Default to visible browser on desktop hosts; expose accurate headless/session status.
- [x] Exercise search, citation extraction, downloads, permissions, and cleanup; attempt live Scholar retrieval.
- [x] Document behavior and remaining external limitations; clean up task-specific scripts.

## Decisions
- Reuse the existing Playwright controller and native file bridge pattern; do not rebuild DAN.
- Cleanup refers to coding scripts, not Codex accounts/history or unrelated in-progress remote work.
- Browser availability is not authority to submit external changes; follow the user's request and existing Plan restrictions.

## Validation
- 422 focused tests passed; one existing skip. Coverage includes GUI tool selection, native RPC, visibility, Plan restrictions, download boundaries, cancellation, and session isolation.
- Real headed-browser fixture passed search/fill, citation dialog, popup selection, extraction, download, and controller cleanup.
- Direct Google Scholar search/export passed for Attention Is All You Need (Vaswani et al., 2017).
- Authenticated Codex lead passed both the fixture and the exact Scholar-to-`references.bib` request through DAN’s bridge; native transient script directories were removed.
- Evidence: `output/playwright/browser-research/acceptance-2cdb4e003feb.json` and `native-2cdb4e003feb/references.bib`. Native Claude/Antigravity integration has adapter regressions; live acceptance here used Codex.
- Already-running backends need a restart to import the changed Python modules. No frontend rebuild is required; this session did not restart a user backend.

## Everyday chat acceptance
- [x] Prefer the ready DAN connection for fresh tasks; preserve an explicitly requested native logged-in-browser path.
- [x] Recover from manually closed windows without replaying an action; unit and real-browser fixtures pass.
- [x] Launch the installed Mac app and verify its actual chat execution path; no app reinstall is needed for these Python-only changes.

- Activation follow-up: running installed app verified via the GUI admission API with native Codex/app-server. Run `arun-c4698dee0995` completed visible browsing, citation extraction, exact file verification, and cleanup. Evidence: `output/playwright/browser-research/acceptance-41b2c2fd52f8.json`.
- 257 focused regression tests pass (one existing skip). Closed-window reopen verified with a real browser.
- Native Claude’s selected default account needs `/login`; its app-level attempt ended before browser use. Browser integration remains available after account authentication; live Claude acceptance is pending that external prerequisite.

## Commit verification (2026-09-24)
- [x] Review and rerun browser, native-lead/worker, CLI/TUI, and organism regressions: 460 passed, one existing skip.
- [x] Organize runtime/permission support, native/GUI integration, and acceptance tooling/documentation into separate commits.
