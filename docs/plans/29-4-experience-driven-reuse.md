# 29-4: Experience-Driven Reuse

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** not-started
**Goal:** Wire workflow experience retrieval into the primary build path so the concierge checks memory before generating, making reuse the default rather than an exception.

## Context

The MetaController's `WorkflowPlanner` already has REUSE/ADAPT/GENERATE logic with scoring thresholds (≥0.8 → REUSE, 0.4-0.8 → ADAPT, <0.4 → GENERATE). The `ExperienceIndex` does semantic search over past workflows. The `WorkflowMemoryIndex` (memory_bridge) adds reuse recommendations.

But none of this is wired to the primary build path. When a user says "build me a data analysis pipeline" in chat, `ChatManager._generate_workflow_from_intent()` goes straight to intent extraction → compile → codegen. It never checks if a similar workflow already exists.

After 29-1 (memory kernel) and 29-2 (concierge orchestrator), experience data lives in the unified memory as `WORKFLOW_ASSET` and `WORKFLOW_PATTERN` items. This plan wires that into every build decision.

## Tasks

### 1. Reuse-first build decision
- [ ] 1-1. Before any workflow generation, concierge queries memory kernel with `WORKFLOW_BUILD` policy
- [ ] 1-2. If `WORKFLOW_ASSET` items return with similarity ≥ 0.8 and success_rate ≥ 0.5 → propose REUSE
- [ ] 1-3. If similarity 0.4-0.8 → propose ADAPT (show what would change)
- [ ] 1-4. If similarity < 0.4 or no matches → proceed to GENERATE (current build path)
- [ ] 1-5. Present reuse/adapt proposal to user: "I found a similar workflow from last week. Want me to adapt it or start fresh?"

### 2. Reuse path
- [ ] 2-1. Load existing graph from `GraphStore` by workflow_id
- [ ] 2-2. Apply input mapping (map user's current inputs to existing workflow's input variables)
- [ ] 2-3. Validate the reused graph still works (schema check, tool availability)
- [ ] 2-4. Run via build session's smoke test (29-3)
- [ ] 2-5. On success: present result to user without modification

### 3. Adapt path
- [ ] 3-1. Load existing graph as starting point
- [ ] 3-2. Compute diff between user's intent and existing workflow's capabilities
- [ ] 3-3. Generate targeted mutations to bridge the diff (using ChatManager mutation path)
- [ ] 3-4. Feed the diff context + existing graph to the LLM as "adapt this workflow for: [new intent]"
- [ ] 3-5. Validate and smoke-test the adapted version via build session

### 4. Workflow catalog in chat
- [ ] 4-1. `list_my_workflows` capability tool: show user's saved workflows with names, descriptions, last-run dates, success rates
- [ ] 4-2. `search_workflows(query)` capability tool: semantic search over workflow assets in memory
- [ ] 4-3. `show_workflow(id)` capability tool: display workflow structure (ASCII DAG via `render_dag()`)
- [ ] 4-4. `fork_workflow(id, new_name)` capability tool: duplicate a workflow as starting point for adaptation
- [ ] 4-5. These tools are available in all chat modes, not just build mode

### 5. Adapter parity
- [ ] 5-1. Server-started adapters (`/api/adapters/start`) use concierge path instead of raw `Engine.run()`
- [ ] 5-2. WhatsApp/Telegram users can trigger reuse-first builds through conversation
- [ ] 5-3. Adapter `_on_new_message` callback routes through `ConcurrentDispatcher.dispatch()` (not direct engine)
- [ ] 5-4. `/workflows` adapter command: list available workflows (formatted for messaging surface)

### 6. Experience feedback loop
- [ ] 6-1. After successful reuse: increment `access_count` and `success_count` on the WORKFLOW_ASSET memory item
- [ ] 6-2. After failed reuse: store failure context as `FAILURE_PATTERN` linked to the workflow asset
- [ ] 6-3. After successful adaptation: store the adapted version as a new `WORKFLOW_ASSET` (linked to parent via `related_ids`)
- [ ] 6-4. Track reuse/adapt/generate ratio in logs for monitoring

### 7. Tests
- [ ] 7-1. Unit tests for reuse-first decision logic (similarity thresholds)
- [ ] 7-2. Unit tests for reuse path (load, validate, input mapping)
- [ ] 7-3. Unit tests for adapt path (diff computation, targeted mutations)
- [ ] 7-4. Integration test: user describes task → system finds similar workflow → proposes reuse → user accepts → runs successfully
- [ ] 7-5. Integration test: adapter surface gets same reuse behavior as CLI
- [ ] 7-6. Integration test: experience feedback updates memory after reuse/adapt

## Decisions

- (to be filled during execution)

## Notes

- The reuse scoring thresholds (0.8/0.4) are inherited from the existing `WorkflowPlanner`. They can be tuned based on real-world reuse success rates.
- Adapter parity (task 5) is important: WhatsApp/Telegram users currently get a degraded experience because server-started adapters bypass the concierge entirely.
- The workflow catalog tools (task 4) complement the existing `search_workflow_history` capability but are oriented toward reuse rather than just browsing.
