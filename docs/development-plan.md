# Development Plan

## Product naming

**Dear Diane** is the product; **Diane** is the assistant. Subtitle: “A workspace for research, code, and ideas.” Existing technical IDs remain compatible. See [rename](plans/4-10-dear-diane.md).

## Vision

Make difficult agent work durable, steerable, verifiable, and understandable without multiplying product modes or task-specific runtimes.

## Active roadmap

1. [Universal Cell](plans/1-universal-cell.md) — completed.
2. [Universal Organism](plans/2-universal-organism.md) — completed.
3. [Super DAN](plans/3-super-dan.md) — completed core runtime/TUI.
4. [Work and Notes GUI](plans/4-work-notes-gui.md) — active UX refinement.
5. [Universal Product Cutover](plans/5-universal-product-cutover.md) — completed; legacy code/docs are archived in Git and the boundary is locked.
6. [Remote control](plans/6-remote-control.md) — implementing Mac-managed SSH profiles, persistent execution, and authenticated private-network desktop/phone access through a replaceable `ny` relay; test on mini, leave s600 to the user.

7. [Phone personal agent](plans/7-phone-personal-agent.md) — browser-first extension for commitments. Shared Mac/browser foundation, bounded document/image capture, structured extraction, durable inbox reminders and task lifecycle implemented. Follow remaining Stage 0–4 gates for monetary budgets, approved Google actions, phone notifications and the physical iPhone/Android pilot; see the [acceptance ledger](personal/acceptance.md). Later providers/jobs remain gated.

## Near-term priorities

- Personal interaction is conversation first: users express needs, Diane clarifies only missing information and handles supported local actions. Activity contains record forms, history and spending controls. Keep advanced capabilities behind this interaction without implying unimplemented external-account access.

- Independent harness/model-source selection is delivered and live-verified for Codex/Claude with OpenRouter. SSH remote execution follows this shared profile contract; see [4-4](plans/4-4-agent-model-selection.md) and [6](plans/6-remote-control.md).

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

### Plan 7 platform update — 2026-09-29
- User prioritizes Mac alongside phone. Reuse Electron and the shared responsive browser interface; Mac hosts records/execution for now. Physical-phone HTTPS/push and live provider gates remain open.
