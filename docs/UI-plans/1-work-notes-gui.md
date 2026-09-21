# 1: Work and Notes GUI

**Status:** in-progress
**Goal:** Refine one calm workspace for Super DAN sessions, task state, artifacts, previews, and notes.

## Tasks
- [x] Delete rejected icon variants, retain v7 source/prompt, reinstall signed DAN with v7, and verify launch/backend health.
- [x] Adopt user-selected ivory v7 for desktop packaging (PNG/ICNS/ICO) and browser favicon; retain existing local-updater icon preservation.
- [x] Replace v4's purple palette with three painted-icon alternatives: blush pink v5, slate blue v6, and warm ivory v7; save artwork and prompts.
- [x] Generate colorful gouache (v3) and impressionist (v4) variants of the letter-free agent-network icon, with saved prompts.
- [x] Revise icon direction to a letter-free agent network with a shared coordination core; save v2 artwork and prompt.
- [x] Generate a retro-futuristic DAN app-icon candidate; save artwork and prompt in `output/imagegen/` for review.
- [x] Add eight operator-specified paired light/dark color schemes to DAN Settings with live preview, independent Light/Dark/System mode, and persisted selection.
- [x] Show only waiting queue entries, with accurate counts, numbered previews, keyboard-expandable full text, and matching Work surface styling.

- [x] Replace olive-tinted Work dark mode with charcoal/graphite surfaces, soft white text, sand focus/activity accents, and matching native/utility controls.

- [x] Use Work and Notes as the only top-level destinations.
- [x] Show durable sessions, active work, task graph state, evidence, and previews.
- [x] Support note collections, Markdown editing, course/progress metadata, and attachments.
- [x] Remove legacy editor modes and native integrations not used by this workspace.
- [ ] Continue accessibility, responsive, and interaction-polish passes.

## Decisions

- User selected ivory v7; retain original artwork, prompt, and platform exports under `editor/resources/icons/`. Earlier variant-generation tasks are historical; unused artwork was deleted on request.

- New capabilities extend Work or Notes instead of introducing another mode.
- Active work must remain recoverable from the Agent V2 store after restart.

## September workbench redesign
- [x] Replace Work’s default blueprint grid and project accordion with conversation, an optional project-local session shelf, project carousel, and stable session wheel. See [4-1](../plans/4-1-agent-workbench.md).
- [x] Browser-fixture checks: keyboard focus, drafts, project isolation, selection reply, mobile sizing, reduced motion. Live mixed-agent acceptance remains in [native worker integration](2-native-agent-workers.md).
