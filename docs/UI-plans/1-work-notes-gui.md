# 1: Work and Notes GUI

**Status:** in-progress
**Goal:** Refine one calm workspace for Super DAN sessions, task state, artifacts, previews, and notes.

## Tasks

- [x] Use Work and Notes as the only top-level destinations.
- [x] Show durable sessions, active work, task graph state, evidence, and previews.
- [x] Support note collections, Markdown editing, course/progress metadata, and attachments.
- [x] Remove legacy editor modes and native integrations not used by this workspace.
- [ ] Continue accessibility, responsive, and interaction-polish passes.

## Decisions

- New capabilities extend Work or Notes instead of introducing another mode.
- Active work must remain recoverable from the Agent V2 store after restart.

## September workbench redesign
- [x] Replace Work’s default blueprint grid and project accordion with conversation, an optional project-local session shelf, project carousel, and stable session wheel. See [4-1](../plans/4-1-agent-workbench.md).
- [x] Browser-fixture checks: keyboard focus, drafts, project isolation, selection reply, mobile sizing, reduced motion. Live mixed-agent acceptance remains in [native worker integration](2-native-agent-workers.md).
