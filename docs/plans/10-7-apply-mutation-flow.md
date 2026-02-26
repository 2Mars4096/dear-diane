# 10-7: Apply Mutation Flow

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** completed
**Goal:** Wire the chat mutation proposal → diff preview → apply pipeline so users can actually apply LLM-generated graph changes. GraphDiffPreview and GraphMutator exist but are not connected; this plan closes the loop.

## Tasks

- [x] 1. Backend apply endpoint
  - [x] 1-1. `POST /api/graphs/{graph_id}/apply-mutation` — accepts `{ mutation_plan: MutationPlan }`, calls `GraphMutator.apply()`, persists via GraphStore, returns `{ success, new_graph?, errors? }`
  - [x] 1-2. Concurrency check: reject if `base_graph_revision` != current revision (existing in GraphMutator)
  - [x] 1-3. On success: save new graph, return full graph dict for frontend sync

- [x] 2. Frontend API + wiring
  - [x] 2-1. `api.applyMutation(graphId, mutationPlan)` → calls new endpoint
  - [x] 2-2. ChatMessage: make "Proposed changes" badge clickable — opens GraphDiffPreview modal
  - [x] 2-3. Diff source: `computeGraphDiff(currentGraph, dry_run_result.new_graph)` from message's mutation data
  - [x] 2-4. GraphDiffPreview receives diff, `onApplyAll` → call `applyMutation`, on success update store + message status
  - [x] 2-5. `onReject` → close modal, set message `mutationStatus: "rejected"`
  - [x] 2-6. Apply Selected: defer to follow-up; for now Apply All and Reject only (partial apply requires op-index mapping)

- [x] 3. Store + undo integration
  - [x] 3-1. After successful apply: `loadGraph` with new data (or equivalent store update), push undo snapshot
  - [x] 3-2. Record session marker for "Revert to here" (reuse existing `_recordMutationMarker` in ChatPanel)
  - [x] 3-3. Update message `mutationStatus: "applied"` and persist thread

- [x] 4. Tests + docs
  - [x] 4-1. Backend: test apply-mutation endpoint (success, stale revision rejection, invalid plan)
  - [x] 4-2. Docs: changelog, architecture.md apply-mutation endpoint

## Decisions

- Apply Selected (partial accept) deferred — requires mapping diff items to operation indices; ship Apply All first.
- Diff computed client-side from `dry_run_result.new_graph`; backend does not re-compute diff.

## Notes

- The mutation plan in the message already has `dry_run_result` from the backend. We use `dry_run_result.new_graph` for the diff. If the graph changed since (revision mismatch), apply will fail server-side.
- Session marker records undo stack cursor so "Revert to here" works within the session.
