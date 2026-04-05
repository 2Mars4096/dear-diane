# 46-7: Reusable Universal-Agent Bundle Extraction

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started
**Goal:** Extract a DAN-independent universal-agent core that other projects can embed, while DAN keeps its workflow/concierge/runtime specifics behind adapter boundaries.

## Dependencies

- **46-6** must land first. The bundle boundary should be extracted from standardized contracts, not from today's mixed queue/prompt/runtime seams.
- Coordinate with **50-7** because Worker/legacy bridge retirement and globals cleanup reduce DAN-specific coupling in the runtime-core path.
- Coordinate with **50-5** because concierge runtime narrowing should leave a cleaner adapter surface instead of one giant control-plane sink.

## Tasks

- [ ] 1. Define the target bundle boundary
  - [ ] 1-1. Separate the reusable core from DAN-specific adapter/runtime surfaces. Candidate split: universal-agent core, DAN adapter layer, concierge application layer.
  - [ ] 1-2. Set a dependency ceiling for the reusable core: no imports from `dan.server.*`, `dan.models.legacy`, `dan.executors.*`, or DAN-global tool registries.
  - [ ] 1-3. Decide the extraction sequence explicitly: first an in-repo clean package with enforced import boundaries, then optional external publication.
- [ ] 2. Introduce explicit runtime interfaces
  - [ ] 2-1. Model/completion interface
  - [ ] 2-2. Tool capability registry interface
  - [ ] 2-3. Memory client interface
  - [ ] 2-4. Child-work dispatcher / subgraph runner interface
  - [ ] 2-5. Event stream and result interface
- [ ] 3. Carve the reusable core
  - [ ] 3-1. Move the standardized agent/session/handoff contracts and the minimal core executor/orchestrator logic behind those interfaces.
  - [ ] 3-2. Keep DAN-specific workflow identity, graph store, capability registry, schedule/run services, and legacy bridge logic in adapter modules.
  - [ ] 3-3. Remove import-time DAN/provider coupling from the extracted core so importing the core does not drag in unnecessary runtime stacks.
- [ ] 4. Prove reuse outside DAN
  - [ ] 4-1. Add a minimal non-DAN fixture or example proving the bundle runs with stub model/tool/memory adapters.
  - [ ] 4-2. Add import-boundary tests that fail if DAN-specific modules leak back into the reusable core.
  - [ ] 4-3. Document the embedding contract for outside projects.
- [ ] 5. Rollout and compatibility
  - [ ] 5-1. Keep DAN runtime behavior stable while moving DAN onto the adapter-backed core.
  - [ ] 5-2. Only publish/externalize the bundle after in-repo adapter parity and focused benchmarks are green.

## Success Criteria

- the reusable core imports cleanly without DAN server/legacy/executor modules
- DAN consumes that core through adapters instead of hard-coded runtime imports
- a non-DAN harness or fixture proves the bundle can run outside this repo
- import-time weight and DAN-specific coupling are materially reduced from the current `src/dan/worker/*` boundary

## Decisions

- Internal extraction comes before external publication.
- No "bundle" claim is honest until adapter boundaries are proven by tests.
- The reusable core should consume interfaces, not DAN globals or legacy compatibility helpers.

## Notes

- Today `src/dan/worker/executor.py`, `src/dan/worker/model.py`, and `src/dan/worker/presets.py` still import DAN internals directly. This plan exists to reverse that layering rather than publish the current coupling as-is.
- The likely end state is not "everything becomes Worker." The reusable core should own the universal agent/session/handoff contracts; DAN should own workflow identity, graph/runtime orchestration, and product-specific control-plane behavior.
