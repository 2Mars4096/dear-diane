# 48: Validator Input-Port Contract And Live Codegen

**Status:** completed
**Goal:** Keep generated validator nodes aligned with the runtime validator contract so codegen-enriched workflows validate and execute without hidden port mismatches.

## Tasks
- [x] 1. Canonicalize validator ports at the model boundary
  - [x] 1-1. Seed `ValidatorNode` with the canonical `data` input port when ports are omitted.
  - [x] 1-2. Preserve the existing `valid` / `invalid` output ports as the canonical validator branches.
- [x] 2. Align raw graph-dict generation paths with the same contract
  - [x] 2-1. Make `DefaultsEnricher` emit explicit validator `input_ports` / `output_ports`.
  - [x] 2-2. Make structural `insert_validator()` emit the same explicit ports.
- [x] 3. Lock the behavior with regression coverage
  - [x] 3-1. Extend generation-default and structural-mutation tests to assert validator ports.
  - [x] 3-2. Update the live builder-code integration expectation to match the intentional enriched graph shape.
  - [x] 3-3. Re-run the focused local validator slice and the live `.env` integration subset.

## Decisions
- The canonical validator contract is `input_ports=["data"]` with `output_ports=["valid", "invalid"]`.
- The executor may remain lenient about legacy inputs for compatibility, but generated and mutated graphs should emit the canonical contract explicitly.
- Repo `.env` files containing JSON literals should be loaded with `dotenv -f .env run -- ...` rather than `source .env`.

## Notes
- The previous live failure moved from a real validator-port mismatch to a stale integration assertion once the validator node was emitted correctly; the test now asserts the intended 3-node enriched graph.
