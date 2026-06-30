# 57-13: Skill Mention UX

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Make DAN skills discoverable and invocable through passive auto-selection plus explicit `$skill-name` mentions, with lightweight autocomplete in GUI and TUI surfaces.

## Tasks
- [ ] 1. Freeze the invocation contract
  - [x] 1-1. Support passive auto-selection from the loaded skill catalog.
  - [x] 1-2. Support active `$skill-name` mentions such as `$idea-cart` and `$frontend-design`.
  - [x] 1-3. Do not document or special-case `$skill:name`.
  - [x] 1-4. Preserve skill provenance in run metadata without expanding tool permissions.
- [ ] 2. Add a parser and disambiguation layer
  - [x] 2-1. Parse exact `$skill-name` mentions in user text.
    - [x] Shared `src/dan/skills/invocation.py` leading `$skill-name` parsing landed; TUI/CLI call it instead of owning separate syntax.
  - [x] 2-2. Avoid false positives for dollar amounts, shell variables, env vars, and prose like `$5`.
    - [x] Shared parser ignores `$5`, uppercase env-like `$PATH`, and plain unknown lowercase `$foo` while still blocking skill-like unknown names such as `$not-a-skill`.
  - [x] 2-3. Resolve aliases and ambiguous matches deterministically or ask for clarification.
    - [x] Shared parser reports ambiguous prefixes such as `$scaffold`; TUI displays the same result through its picker/help copy.
  - [x] 2-4. Feed selected skills into the existing brief-level skill packet path.
    - [x] TUI selected skills now reach `_request_from_live_brief(...)` as explicit selected-skill metadata and force the matching Super DAN skill packet before passive auto-selection runs.
    - [x] Explicit selected skills now add required selected-skill constraints to the live brief and include bounded companion `references/*.md` excerpts for file-backed skills, so scaffold skills carry their concrete file-tree/spec guidance.
    - [x] The Super DAN runner now runs any matching file-backed preflight hook under conventional `scripts/*_preflight.py` / `scripts/*_docs.py` / `scripts/*_scaffold.py` names before the model run; GUI/TUI do not own injection.
- [ ] 3. Add catalog-backed suggestions
  - [x] 3-1. Expose a capped skill suggestion API or local helper over the read-through catalog.
  - [x] 3-2. GUI: show suggestions after `$` in the composer.
    - [x] The `#workspace` composer now opens a compact suggestion menu for `$` skill mentions from `/api/workspace-skills`.
    - [x] The same composer menu also restores `/` Super TUI command suggestions and `@` workspace file/path suggestions.
  - [x] 3-3. TUI: show command-line suggestions or a `/skills` picker when possible.
    - [x] Bare `$` now opens the TUI skill dropdown instead of waiting for a typed skill prefix.
  - [x] 3-4. Use `$idea-cart` as the first end-to-end test target.
- [ ] 4. Preserve surface parity
  - [ ] 4-1. Let Telegram and plain CLI accept raw `$skill-name` text even without autocomplete.
    - [x] Plain `dan super-organism "$skill-name ..."` parses selected skills before the runner.
    - [ ] Telegram request metadata still needs the same selected-skill field.
  - [ ] 4-2. Record selected skills consistently in V2 task/run metadata.
    - [x] GUI Agent-run requests now carry selected `$skill-name` tokens in `surface_context.selected_skills`, and the Super DAN Chat V2 backend hands them to the shared skill invocation parser before execution.
  - [ ] 4-3. Show selected skill chips or concise labels in GUI/TUI progress views.
    - [x] TUI direct runs add selected skills to the compact Recent Events feed.
- [ ] 5. Validate behavior
  - [x] 5-1. Test passive skill selection still works without mentions.
  - [x] 5-2. Test `$idea-cart` forces the expected skill packet.
  - [x] 5-3. Test `$5`, `$PATH`, and shell-like text do not trigger skill selection.
  - [x] 5-4. Test GUI/TUI suggestion filtering and selection against the same catalog data.
    - [x] TUI candidate filtering and prompt-toolkit `/` / `$` key-binding coverage landed.
    - [x] GUI composer tests cover `/`, `$`, and `@` token detection, filtering, skill invocation extraction, and mentioned-file extraction.

## Decisions
- Two invocation levels are enough: passive auto-selection and active `$skill-name` mention.
- Autocomplete is a discoverability aid, not a required syntax for using skills.
- Passive skills remain advisory prompt/context packets; explicitly selected skills become required run guidance and may run deterministic preflight hooks when the skill provides a conventional local script. Skill hooks still do not expand model tool permissions.
- Skill syntax, resolution, metadata normalization, references, and preflight live in the shared skill invocation layer; TUI/GUI only provide autocomplete/picker UX and pass selected tokens forward.

## Notes
- Checked out from idea-cart items `IC-015` and `IC-016`.
- This plan builds on the 2026-05-11 skill read-through and brief-level skill packet work already tracked in `docs/todo.md`.
- 2026-05-11: The Super DAN TUI now loads the same read-through skill catalog as Super DAN live, shows prompt-toolkit suggestions after `$`, and exposes `/skills [filter]` for terminals without completion popups.
- 2026-05-11: Bare TUI `$skill-name` input no longer starts a run; exact mentions are selected, unknown mentions are actionable, ambiguous prefixes list matches, and leading mentions with trailing objective text are stripped from the objective and recorded on `_tui_selected_skill_mentions`. Hard-forcing those mentions into Super DAN's brief-level skill packet path remains open.
- 2026-05-11: TUI-selected `$skill-name` mentions now hard-force the corresponding Super DAN skill packet through `_request_from_live_brief(...)`; focused tests cover explicit `$scaffold-research` forcing, selected-skill constraints, companion reference excerpts, a generic `$demo-skill` preflight hook, no-hook prompt-contract activation, and passive fallback selection.
- 2026-05-11: Extracted `$skill-name` parsing, selected-skill metadata helpers, explicit skill resolution, companion reference excerpting, and conventional preflight hooks into `src/dan/skills/invocation.py`. The TUI now delegates parsing/selection to that shared layer and no longer owns preflight/injection.
- 2026-05-11: Made the TUI suggestion menu open on bare `$` with prompt-toolkit key bindings, while `/skills [filter]` remains the non-dropdown picker fallback.
- 2026-06-29: Restored the `#workspace` composer shortcut menu for `/` commands, `$` installed skills, and `@` workspace paths. Selected GUI skills now travel through Chat V2 surface context into the shared Super DAN skill parser; selected `@` paths become bounded `mentioned_files` context.
