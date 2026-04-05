# 50-1: Key Script Inventory and Refactor Guardrails

**Parent:** [50-structural-consolidation-and-module-reduction](50-structural-consolidation-and-module-reduction.md)
**Status:** not-started
**Goal:** Pin down the real key scripts/modules, assign authoritative ownership, and define split/pruning/performance guardrails before larger structural refactors begin.

## Dependencies

- This is the first sub-plan in the 50-series and should land before any broad file-splitting work.

## Deliverable

Output: a new `docs/key-scripts.md` file (referenced by later 50-* plans) containing the inventory table, ownership map, split/pruning guardrails, validation basket, and pruning candidates. The format should use the same total-line-count convention as the parent plan hotspot table.

The inventory should be regenerable, not hand-maintained. Ship a small script (`scripts/inventory.py` or similar) that outputs:
- hotspot line counts for key scripts (backend + frontend)
- `extra_system_instructions` usage count by file
- `prompt_context` usage count by file
- import-boundary violations (Worker files importing `dan.server.*`, `dan.models.legacy`, `dan.executors.*`)
- legacy-bridge family inventory (`LegacyWorkerAdapterExecutor` registration sites)

The script does not need to be complex — simple `wc -l` / `grep -c` wrappers are fine — but the counts must be reproducible so the inventory does not rot as the codebase moves underneath it.

## Tasks

- [ ] 1. Build the key-script inventory
  - [ ] 1-1. Enumerate the highest-risk oversized files and classify them by role: lifecycle owner, control-plane owner, adapter bridge, authoring/runtime sink, or frontend state/UI sink.
  - [ ] 1-2. Record total-line-count baselines (matching the parent plan's hotspot table convention) and "why this file is large" notes so later splits are measured against real structural problems, not arbitrary size alone.
  - [ ] 1-3. Distinguish true key scripts (authoritative boundary owners) from false facades (files that only claim to be thin layers).
  - [ ] 1-4. Enumerate which compute families still depend on `LegacyWorkerAdapterExecutor` in the default path, so 50-7 can reference this inventory directly rather than repeating the audit.
- [ ] 2. Build the ownership map
  - [ ] 2-1. For each key script, document its intended responsibility, its actual responsibility today, and the adjacent files that should own moved behavior after refactor.
  - [ ] 2-2. Mark the top caller-owned invariants, duplicated launch flows, and compatibility bridges that later plans must collapse.
  - [ ] 2-3. Map the message -> queue -> task -> session -> executor -> result path, including which layers currently duplicate context resolution, queue ownership, or prompt/context injection. Specifically document authoritative ownership for: `SurfaceMessage` (ingress), `ResolvedContext` (turn context), `Task`/`Project` in `models.py` (project-level state), `ConciergeTask` in `task_registry.py` (concierge-level task lifecycle), `Session`/`SessionManager` in `session.py` (execution session), and the prompt fields (`prompt_context`, `extra_system_instructions`, metadata bag).
  - [ ] 2-4. Produce a duplication/deletion matrix: for each overlapping model/queue/prompt concept, mark which layer is authoritative, which is transitional, and which should be deleted or demoted to an adapter-only role.
- [ ] 3. Define split and pruning guardrails
  - [ ] 3-1. Define a soft/hard threshold policy for future split candidates, using separate expectations for backend control-plane files, runtime files, routers, and frontend UI components/stores.
  - [ ] 3-2. Define what qualifies as real subtraction: deleting dead helpers, removing repeated glue, or turning a compatibility file into a true shim.
  - [ ] 3-3. Define what does **not** count as success: moving code without reducing old-owner responsibility, or adding wrappers without deleting behavior.
- [ ] 4. Define validation and performance guardrails
  - [ ] 4-1. Choose the focused validation basket each later sub-plan must rerun (unit/integration slices plus `py_compile`/TypeScript/Vitest where relevant).
  - [ ] 4-2. Identify hot-path performance checks to protect: workflow run start latency (wall-clock from `start_run` call to first engine event), scheduler fire-time execution latency (cron fire to first node dispatch), chat stream/reconnect time-to-first-token, and frontend bundle-size/budget checks on splits. Methodology: manual `time.perf_counter()` or `pytest-benchmark` spot-checks on the focused paths, with pass/fail thresholds expressed as "no more than 20% regression vs. pre-split baseline."
- [ ] 5. Map the concierge prompt pipeline for 46-6
  - [ ] 5-1. Map the concierge prompt assembly chain: `_build_prompt()`, `_stage_prompt_overlay()`, `_extract_chat_params()`, `_apply_workflow_continuity_context()` in `tier_executors.py`; `extra_system_instructions` injection sites; `prompt_context` uses. Use the inventory script to get current counts rather than hardcoding numbers that drift.
  - [ ] 5-2. Note the `_STAGE_PROMPT_OVERLAYS` content quality for the 46-6 prompt quality pass.
- [ ] 6. Ship the inventory automation script
  - [ ] 6-1. Write `scripts/inventory.py` (or similar) that regenerates hotspot counts, prompt-field counts, import-boundary violations, and legacy-bridge family inventory from the live repo.
  - [ ] 6-2. Run the script to produce the initial `docs/key-scripts.md` baselines instead of hand-counting.
- [ ] 7. Identify pruning candidates
  - [ ] 7-1. Inventory compatibility shims, duplicate launch glue, repeated run-relay wiring, and old helpers that can be deleted as later plans land.
  - [ ] 7-2. Record "delete only after" dependencies so cleanup can happen in the same patch as the replacement seam.

## Success Criteria

- the repo has one explicit inventory of key scripts/modules with current size, role, and intended owner
- later 50-series plans can point to `docs/key-scripts.md` for "why this split" and "what gets deleted afterward"
- validation and performance guardrails are explicit enough that later concision/pruning work does not hide regressions
- the inventory identifies both backend and frontend key scripts, not just server hotspots
- the Worker/legacy bridge compute-family audit is included so 50-7 does not repeat it
- the concierge prompt pipeline is mapped well enough that 46-6 can clean it up without re-discovering the seams
- the duplication/deletion matrix explicitly marks which queue/task/session/prompt models are authoritative vs. transitional, so 50-5 and 46-6 can act on it directly
- the inventory is regenerable via a checked-in script, not hand-maintained numbers that rot

## Decisions

- Key scripts are the files that own architectural decisions, not merely the files with the highest line counts.
- File budgets are review aids, not rigid lint rules; boundary clarity is the primary criterion.
- A file split only counts when the old owner loses real responsibility in the same patch.

## Notes

- At plan kickoff, the biggest live backend files: `engine/scheduler.py` (4154), `concierge/runtime/__init__.py` (3999), `chat_manager.py` (3759), `builder/builder.py` (3053), `executors/control_flow.py` (2859), `routers/adapters.py` (2459), `meta/planner.py` (2384), `graph_mutator.py` (2259), `run_manager.py` (2155), `concierge/tier_executors.py` (2146), `workflow_generation.py` (1983), `concierge/scheduler.py` (1875).
- Biggest live frontend files: `ChatPanel.tsx` (5151), `ConfigPanel.tsx` (2982), `useGraphStore.ts` (2533), `ExtensionsPanel.tsx` (1915), `GlobalSettingsPanel.tsx` (1646), `useMessagingStore.ts` (1424).
- The inventory should also treat the concierge prompt/connection path as a first-class key-script seam even though it spans several files: `routers/chat.py` -> `concierge/dispatcher.py` -> `concierge/runtime/__init__.py` -> `concierge/tiered_dispatch.py` -> `concierge/tier_executors.py` -> `chat_manager.py` / `worker/executor.py`.
