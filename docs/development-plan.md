# Development Plan

## Vision

Make difficult agent work durable, steerable, verifiable, and understandable without multiplying product modes or task-specific runtimes.

## Active roadmap

1. [Universal Cell](plans/1-universal-cell.md) — completed.
2. [Universal Organism](plans/2-universal-organism.md) — completed.
3. [Super DAN](plans/3-super-dan.md) — completed core runtime/TUI.
4. [Work and Notes GUI](plans/4-work-notes-gui.md) — active UX refinement.
5. [Universal Product Cutover](plans/5-universal-product-cutover.md) — completed; legacy code/docs are archived in Git and the boundary is locked.
6. [Remote control](plans/6-remote-control.md) — planned: bootstrap persistent execution through SSH, then use authenticated desktop/phone access through `ny` while the laptop is offline.

## Near-term priorities

- Desktop local update/restart flow is implemented. Activate signed release delivery and package/version the separate Python backend before treating desktop releases as standalone full-product upgrades.

- Workbench presentation refresh delivered: conversation-first Work, spatial project/session navigation, progressive activity/tool disclosure, and responsive keyboard access.
- Native Codex/Claude/Antigravity worker baseline and forked session imports delivered; next verify authenticated runs and add restart/approval controls; see `docs/UI-plans/2-native-agent-workers.md`.
- Tighten Super DAN efficiency without weakening evidence or validation.
- Complete the approved human-assist academic and market evaluations.
- Keep task blueprints protected while allowing execution attempts to adapt.
- Add capabilities only as tools, skills, briefs, or control-plane contracts inside the existing stack.

## Non-goals

- Restoring the visual graph builder or separate Code/Research/Content/Operations modes.
- Adding task-family organism classes.
- Reintroducing concierge, messaging, RAG, publishing, marketplace, or thin-client products without a new explicit roadmap decision.
- Remote control is the explicitly requested extension of the existing Work/Notes client and Agent V2 backend; it does not introduce a separate agent product or scheduler.
- Treating more cells as automatically better; concurrency must be justified by dependency structure and expected value.

## Success criteria

- A user can start one Super DAN session, steer it, inspect live state, recover after restart, and verify the result.
- Work and Notes present the same durable task/run truth as the terminal UI.
- Every retained module has a direct dependency path to the four-layer product.
- Default deterministic tests and builds pass without live credentials.
