# 57-13: Skill Mention UX

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Make DAN skills discoverable and invocable through passive auto-selection plus explicit `$skill-name` mentions, with lightweight autocomplete in GUI and TUI surfaces.

## Tasks
- [ ] 1. Freeze the invocation contract
  - [ ] 1-1. Support passive auto-selection from the loaded skill catalog.
  - [ ] 1-2. Support active `$skill-name` mentions such as `$idea-cart` and `$frontend-design`.
  - [ ] 1-3. Do not document or special-case `$skill:name`.
  - [ ] 1-4. Preserve skill provenance in run metadata without expanding tool permissions.
- [ ] 2. Add a parser and disambiguation layer
  - [ ] 2-1. Parse exact `$skill-name` mentions in user text.
  - [ ] 2-2. Avoid false positives for dollar amounts, shell variables, env vars, and prose like `$5`.
  - [ ] 2-3. Resolve aliases and ambiguous matches deterministically or ask for clarification.
  - [ ] 2-4. Feed selected skills into the existing brief-level skill packet path.
- [ ] 3. Add catalog-backed suggestions
  - [ ] 3-1. Expose a capped skill suggestion API or local helper over the read-through catalog.
  - [ ] 3-2. GUI: show suggestions after `$` in the composer.
  - [ ] 3-3. TUI: show command-line suggestions or a `/skills` picker when possible.
  - [ ] 3-4. Use `$idea-cart` as the first end-to-end test target.
- [ ] 4. Preserve surface parity
  - [ ] 4-1. Let Telegram and plain CLI accept raw `$skill-name` text even without autocomplete.
  - [ ] 4-2. Record selected skills consistently in V2 task/run metadata.
  - [ ] 4-3. Show selected skill chips or concise labels in GUI/TUI progress views.
- [ ] 5. Validate behavior
  - [ ] 5-1. Test passive skill selection still works without mentions.
  - [ ] 5-2. Test `$idea-cart` forces the expected skill packet.
  - [ ] 5-3. Test `$5`, `$PATH`, and shell-like text do not trigger skill selection.
  - [ ] 5-4. Test GUI/TUI suggestion filtering and selection against the same catalog data.

## Decisions
- Two invocation levels are enough: passive auto-selection and active `$skill-name` mention.
- Autocomplete is a discoverability aid, not a required syntax for using skills.
- Skills remain advisory prompt/context packets unless a separate permission grant is explicit.

## Notes
- Checked out from idea-cart items `IC-015` and `IC-016`.
- This plan builds on the 2026-05-11 skill read-through and brief-level skill packet work already tracked in `docs/todo.md`.
