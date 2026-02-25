# 10-3: NL→Graph Mutation Engine

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** not-started
**Goal:** Enable the LLM to modify the workflow graph in response to natural language instructions. The core intelligence layer: user says "add a reviewer after the writer" and the system produces the correct sequence of graph operations (add node, add edges, set prompt) and applies them.

## Tasks

- [ ] 1. Graph operation primitives
  - [ ] 1-1. Define `GraphOperation` discriminated union (Pydantic models) in `src/dan/server/graph_mutator.py`:
    - `AddNode` — `{ op: "add_node", node_type, name, config: dict }` (config includes model, prompt, ports, etc.)
    - `RemoveNode` — `{ op: "remove_node", node_id }` (also removes connected edges)
    - `EditNode` — `{ op: "edit_node", node_id, updates: dict }` (partial update: name, prompt, model, ports, etc.)
    - `AddEdge` — `{ op: "add_edge", edge_type, source_id, source_port, target_id, target_port }`
    - `RemoveEdge` — `{ op: "remove_edge", source_id, source_port, target_id, target_port }` (or by edge id)
    - `EditEdge` — `{ op: "edit_edge", edge_id, updates: dict }` (change type, ports)
    - `SetNodePosition` — `{ op: "set_position", node_id, x, y }` (for layout after adding nodes)
    - `ReplaceSubgraph` — `{ op: "replace_subgraph", node_ids_to_remove: list, new_nodes: list, new_edges: list }` (atomic multi-node swap)
  - [ ] 1-2. `MutationPlan` model: `{ plan_id: str, base_graph_revision: str | None, base_graph_hash: str | None, apply_mode: "all_or_nothing" | "partial", operations: list[GraphOperation], description: str, reasoning: str }`
  - [ ] 1-3. `GraphMutator` class: `apply(graph_dict, current_revision, plan: MutationPlan) -> (new_graph_dict | None, applied_ops, errors, stale_plan: bool)`

- [ ] 2. Operation application engine
  - [ ] 2-1. `GraphMutator.apply()`: iterate operations in order, apply each to the graph dict. Track which operations succeeded vs. failed.
  - [ ] 2-2. `AddNode`: generate unique node_id (slugified name + counter), create node dict with correct schema for node_type (ports, defaults), assign position (auto-layout relative to neighbors or default grid position)
  - [ ] 2-3. `RemoveNode`: delete node, remove all edges where node is source or target, remove from sub_graphs if applicable
  - [ ] 2-4. `EditNode`: merge updates into existing node dict. Validate that updated fields are valid for the node type.
  - [ ] 2-5. `AddEdge`: create edge dict, validate source/target nodes and ports exist. Auto-create ports on target if needed (for convenience).
  - [ ] 2-6. `RemoveEdge`: find and remove matching edge. Handle "edge not found" gracefully.
  - [ ] 2-7. Position auto-assignment: when adding nodes, place them near their connected neighbors. Use a simple heuristic (right of source node for chains, below for branches). Full auto-layout (dagre) triggered after all operations complete.
  - [ ] 2-8. Transactional default: pre-validate entire plan, apply in-memory, run `validate_graph()`, then commit all changes only if plan is valid (`apply_mode="all_or_nothing"`).
  - [ ] 2-9. Concurrency guard: if `plan.base_graph_revision`/`base_graph_hash` mismatches current graph state, reject apply with `stale_plan=True` and require re-plan.

- [ ] 3. LLM function-calling schema
  - [ ] 3-1. Define a JSON Schema / OpenAI function-calling tool definition for `plan_graph_mutations`. The LLM returns a `MutationPlan` with a list of `GraphOperation` objects.
  - [ ] 3-2. Tool description: explains each operation type with examples. Includes the current graph context and available node types.
  - [ ] 3-3. System prompt additions: instruct the LLM to use the `plan_graph_mutations` tool when the user asks to modify the graph. For questions or explanations, respond in plain text.
  - [ ] 3-4. Multi-turn refinement: if the user says "no, make it a Code node instead", the LLM references the previous mutation plan and produces a corrective plan.
  - [ ] 3-5. Handle both function-call-capable models (OpenAI, Anthropic) and text-only models (fallback: parse structured JSON from text response)

- [ ] 4. Multi-step mutation planning
  - [ ] 4-1. Complex requests may require multiple coordinated operations. Example: "Create a review loop where the writer drafts, the reviewer critiques, and the writer revises until approved" → add writer node, add reviewer node, add gate node, add edges (chain + feedback), set gate condition, set max iterations.
  - [ ] 4-2. The LLM produces the full operation sequence in a single `MutationPlan`. The `reasoning` field explains the plan in natural language (shown to user alongside the diff).
  - [ ] 4-3. Operation ordering: AddNode before AddEdge (can't wire nonexistent nodes). The mutation engine auto-sorts if the LLM produces out-of-order operations.
  - [ ] 4-4. Template-based shortcuts: for common patterns (chain, review loop, fan-out), the LLM can reference known templates. The mutation engine expands templates into operations.

- [ ] 5. Validation and error recovery
  - [ ] 5-1. Pre-validation: before applying, check referenced node_ids, ports, and node_types. In default `all_or_nothing` mode, any invalid operation blocks commit and returns structured errors.
  - [ ] 5-2. Post-validation: run `validate_graph()` on the mutated graph. Return warnings/errors to the LLM.
  - [ ] 5-3. Error recovery: if validation finds issues, send the errors back to the LLM in a follow-up prompt ("The mutation produced these validation errors: [...]. Please fix them."). Auto-retry once.
  - [ ] 5-4. Partial apply is opt-in only: allow partial execution only when user-selected subset is applied from diff UI (`apply_mode="partial"`). Default path remains all-or-nothing.
  - [ ] 5-5. Dry-run mode: `GraphMutator.dry_run(graph_dict, current_revision, plan)` returns diff + validation + stale-plan signal without applying. Used by diff preview (10-4).

- [ ] 6. Chat integration
  - [ ] 6-1. `ChatManager` extended: when LLM response includes a `plan_graph_mutations` tool call, extract the `MutationPlan`, run dry-run, return both the text response and the mutation plan to the frontend.
  - [ ] 6-2. Frontend: when a chat response includes a mutation plan, show it as a special "proposed changes" block in the chat message (collapsed by default, expandable to see operations). Wire to diff preview (10-4).
  - [ ] 6-3. Quick-apply button: optional explicit action for simple low-risk plans; still runs stale-plan check and transactional validation before commit.
  - [ ] 6-4. Rejection flow: if user says "no" or "undo that", the LLM understands and can produce a reversal plan or try a different approach.

- [ ] 7. Tests
  - [ ] 7-1. `GraphOperation` model tests: serialization, validation, all operation types
  - [ ] 7-2. `GraphMutator` unit tests: apply single operations (add, remove, edit for nodes and edges), multi-operation plans, out-of-order auto-sort
  - [ ] 7-3. Position auto-assignment: test neighbor-relative placement
  - [ ] 7-4. Validation integration: test pre-validation catches (missing node, invalid port), post-validation catches (disconnected graph, type mismatch)
  - [ ] 7-5. Error recovery: test dry-run mode, all-or-nothing rollback on validation failure, and opt-in partial apply behavior
  - [ ] 7-6. End-to-end: mock LLM returns mutation plan, ChatManager applies it, verify graph state
  - [ ] 7-7. Complex scenarios: "create a review loop", "add fan-out with 3 parallel branches", "replace node X with a composite"
  - [ ] 7-8. Concurrency: stale-plan rejection when base revision/hash mismatches current graph

- [ ] 8. Docs sync
  - [ ] 8-1. `architecture.md`: document GraphMutator, GraphOperation types, mutation flow
  - [ ] 8-2. `llm-api-guide.md`: document graph mutation tool schema (for LLM callers)
  - [ ] 8-3. `changelog.md`: implementation entry

## Decisions

- Function-calling is default for mutation plans; structured-text JSON parse remains fallback.
- Plan application is transactional by default (`all_or_nothing`); partial apply is explicit user opt-in.
- Optimistic concurrency is required via `base_graph_revision`/`base_graph_hash`.

## Notes

- The mutation engine is intentionally decoupled from the LLM. `GraphMutator` takes a `MutationPlan` and applies it — the plan could come from the LLM, from a script, or from a future AI agent. This makes it testable and reusable.
- Operation granularity matters. Too fine-grained (e.g., "set node.prompt character 5 to 'x'") makes LLM output verbose and error-prone. Too coarse (e.g., "replace entire graph") loses diff granularity. The current set (add/remove/edit at node and edge level) hits the sweet spot.
- The `ReplaceSubgraph` operation is an escape hatch for complex transformations where individual operations would be cumbersome. Use sparingly.
- Function calling is preferred over text-based JSON parsing for mutation plans. It's more reliable, validates against schema, and the LLM is trained for it. Text fallback exists for models without function-calling support.
- Auto-layout after mutation is important. Adding nodes without positioning creates overlapping chaos. The dagre pass after applying operations ensures the graph stays readable.
- Concurrency guard is non-negotiable for correctness: applying a plan generated from stale graph state should fail fast and prompt re-planning.
