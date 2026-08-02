# Development Plan

## Vision

Make difficult agent work durable, steerable, verifiable, and understandable without multiplying product modes or task-specific runtimes.

## Active roadmap

1. [Universal Cell](plans/1-universal-cell.md) — completed.
2. [Universal Organism](plans/2-universal-organism.md) — completed.
3. [Super DAN](plans/3-super-dan.md) — completed core runtime/TUI.
4. [Work and Notes GUI](plans/4-work-notes-gui.md) — active UX refinement.
5. [Universal Product Cutover](plans/5-universal-product-cutover.md) — completed; legacy code/docs are archived in Git and the boundary is locked.

## Near-term priorities

- Improve Work/Notes usability, accessibility, and responsive behavior.
- Tighten Super DAN efficiency without weakening evidence or validation.
- Complete the approved human-assist academic and market evaluations.
- Keep task blueprints protected while allowing execution attempts to adapt.
- Add capabilities only as tools, skills, briefs, or control-plane contracts inside the existing stack.

## Non-goals

- Restoring the visual graph builder or separate Code/Research/Content/Operations modes.
- Adding task-family organism classes.
- Reintroducing concierge, messaging, RAG, publishing, marketplace, or thin-client products without a new explicit roadmap decision.
- Treating more cells as automatically better; concurrency must be justified by dependency structure and expected value.

## Success criteria

- A user can start one Super DAN session, steer it, inspect live state, recover after restart, and verify the result.
- Work and Notes present the same durable task/run truth as the terminal UI.
- Every retained module has a direct dependency path to the four-layer product.
- Default deterministic tests and builds pass without live credentials.
