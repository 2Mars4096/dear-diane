# 29-4: Experience-Driven Reuse

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** completed
**Goal:** Wire workflow experience retrieval into the primary build path so the concierge checks memory before generating, making reuse the default rather than an exception.

## Context

The MetaController's `WorkflowPlanner` already has REUSE/ADAPT/GENERATE logic with scoring thresholds (≥0.8 → REUSE, 0.4-0.8 → ADAPT, <0.4 → GENERATE). The `ExperienceIndex` does semantic search over past workflows. The `WorkflowMemoryIndex` (memory_bridge) adds reuse recommendations.

But none of this is wired to the primary build path. When a user says "build me a data analysis pipeline" in chat, `ChatManager._generate_workflow_from_intent()` goes straight to intent extraction → compile → codegen. It never checks if a similar workflow already exists.

After 29-1 (memory kernel) and 29-2 (concierge orchestrator), experience data lives in the unified memory as `WORKFLOW_ASSET` and `WORKFLOW_PATTERN` items. This plan wires that into every build decision.

## Tasks

### 1. Reuse-first build decision
- [x] 1-1. Before any workflow generation, concierge queries memory kernel with `WORKFLOW_BUILD` policy
- [x] 1-2. If `WORKFLOW_ASSET` items return with similarity ≥ 0.8 and success_rate ≥ 0.5 → propose REUSE
- [x] 1-3. If similarity 0.4-0.8 → propose ADAPT (show what would change)
- [x] 1-4. If similarity < 0.4 or no matches → proceed to GENERATE (current build path)
- [x] 1-5. Present reuse/adapt proposal to user: "I found a similar workflow from last week. Want me to adapt it or start fresh?"

### 2. Reuse path
- [x] 2-1. Load existing graph from `GraphStore` by workflow_id
- [x] 2-2. Apply input mapping (map user's current inputs to existing workflow's input variables)
- [x] 2-3. Validate the reused graph still works (schema check, tool availability)
- [x] 2-4. Run via build session's smoke test (29-3)
- [x] 2-5. On success: present result to user without modification

### 3. Adapt path
- [x] 3-1. Load existing graph as starting point
- [x] 3-2. Compute diff between user's intent and existing workflow's capabilities
- [x] 3-3. Generate targeted mutations to bridge the diff (using ChatManager mutation path)
- [x] 3-4. Feed the diff context + existing graph to the LLM as "adapt this workflow for: [new intent]"
- [x] 3-5. Validate and smoke-test the adapted version via build session

### 4. Workflow catalog in chat
- [x] 4-1. `list_my_workflows` capability tool: show user's saved workflows with names, descriptions, node/edge counts
- [x] 4-2. `search_workflows(query)` capability tool: keyword search over saved workflow names/descriptions in GraphStore
- [x] 4-3. `show_workflow(id)` capability tool: display workflow structure (ASCII DAG via `render_dag()`)
- [x] 4-4. `fork_workflow(id, new_name)` capability tool: duplicate a workflow as starting point for adaptation
- [x] 4-5. These tools are available in all chat modes, not just build mode

### 5. Adapter parity
- [x] 5-1. Server-started adapters (`/api/adapters/start`) use concierge path instead of raw `Engine.run()`
- [x] 5-2. WhatsApp/Telegram users can trigger reuse-first builds through conversation
- [x] 5-3. Adapter `_on_new_message` callback routes through `ConcurrentDispatcher.dispatch()` (not direct engine)
- [x] 5-4. `/workflows` adapter command: list available workflows (formatted for messaging surface)

### 6. Experience feedback loop
- [x] 6-1. After successful reuse: increment `access_count` and `success_count` on the WORKFLOW_ASSET memory item
- [x] 6-2. After failed reuse: store failure context as `FAILURE_PATTERN` linked to the workflow asset
- [x] 6-3. After successful adaptation: store the adapted version as a new `WORKFLOW_ASSET` (linked to parent via `related_ids`)
- [x] 6-4. Track reuse/adapt/generate ratio in logs for monitoring

### 7. Tests
- [x] 7-1. Unit tests for reuse-first decision logic (similarity thresholds)
- [x] 7-2. Unit tests for reuse path (load, validate, input mapping)
- [x] 7-3. Unit tests for adapt path (diff computation, targeted mutations)
- [x] 7-4. Integration test: user describes task → system finds similar workflow → proposes reuse → user accepts → runs successfully
- [x] 7-5. Integration test: adapter surface gets same reuse behavior as CLI
- [x] 7-6. Integration test: experience feedback updates memory after reuse/adapt

## Decisions

- (to be filled during execution)

## Notes

- 2026-03-09 reconciliation: the reuse/adapt/generate path is partially wired in the concierge and memory kernel, including reuse proposals, goal-context propagation, smoke-test validation, and reuse usage counters. The biggest remaining gaps are explicit input mapping, workflow catalog tools, adapter parity, full feedback-loop persistence, and broader scenario coverage.
- 2026-03-09 final reconciliation: All 7 task groups complete. Input mapping (§2-2), intent diff (§3-2), adapter parity (§5), workflow catalog (§4), and feedback loop (§6) all shipped. Status → completed.
- 2026-03-09 (d): Implemented `_map_reuse_inputs()` (§2-2) and `_compute_intent_diff()` (§3-2) in `reuse_decision.py`. Input mapping extracts file paths, URLs, and quoted values from user intent and matches to workflow variables by keyword category. Intent diff identifies additions/modifications/removals between user intent and existing workflow. Both heuristic-based. 13 tests added.
- Practical daily-task acceptance and the audit trail for reuse decisions are tracked in [29-8](29-8-practical-research-quality-and-audit.md), since that is where reuse quality is evaluated against real research/report asks.
- The reuse scoring thresholds (0.8/0.4) are inherited from the existing `WorkflowPlanner`. They can be tuned based on real-world reuse success rates.
- Adapter parity (task 5) is important: WhatsApp/Telegram users currently get a degraded experience because server-started adapters bypass the concierge entirely.
- The workflow catalog tools (task 4) complement the existing `search_workflow_history` capability but are oriented toward reuse rather than just browsing.
