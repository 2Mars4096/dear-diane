# 51-2: Capability Manifest And Selective Tool Loading

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** not-started
**Goal:** Replace thin tool identifiers with richer capability manifests so a worker can discover, understand, and selectively load only the tools it actually needs.

## Tasks
- [ ] 1. Define the capability manifest shape
  - [ ] 1-1. Include identity, purpose, input/output contract, side effects, permissions, cost hints, and retry/idempotency expectations
  - [ ] 1-2. Separate compact discovery metadata from full detail so the worker can inspect progressively
- [ ] 2. Align manifests with the worker core
  - [ ] 2-1. Extend the reusable core interfaces so the cell sees typed capability metadata instead of bare tool ids
  - [ ] 2-2. Keep the core DAN-independent where possible
- [ ] 3. Add selective loading behavior
  - [ ] 3-1. Support listing available capabilities before reading their full specs
  - [ ] 3-2. Support loading detailed capability information only for the shortlisted set
- [ ] 4. Add governance boundaries
  - [ ] 4-1. Express capability allowlists, disallowed side effects, and tier/budget gating through the same manifest surface
- [ ] 5. Validate on a minimal initial tool family
  - [ ] 5-1. Pick one or two high-value capability families and prove that the worker can choose intelligently without eager full-manifest injection

## Decisions
- Tool selection should be capability-driven and manifest-first, not prompt-lore-first.
- Discovery metadata should be cheap enough to expose broadly; detailed specs should be loaded lazily.

## Notes
- This slice turns tools into real organelles instead of opaque names.
