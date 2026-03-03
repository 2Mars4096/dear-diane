# 16-1: Agent Teams

**Parent:** [16-execution-primitives](16-execution-primitives.md)
**Status:** completed
**Goal:** Implement group-chat style multi-agent coordination as a first-class node type — agents within a team can address each other via `@`-routing, explicitly hand off work, and share a conversational context that accumulates across turns, enabling collaborative problem-solving patterns that the unidirectional `OrchestratorNode` cannot express.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `OrchestratorNode` | `models/control_flow.py` | `teams` dict (name→sub_graph key), `orchestrator_prompt`, `completion_condition` (all_done/any_done/orchestrator_halt), `max_iterations`, `timeout_seconds`, `input_mappings`, `team_inputs` | Orchestrator monitors but teams can't talk to each other; no `@` routing; no handoff; no shared message history |
| `OrchestratorExecutor` | `executors/control_flow.py` | Spawns teams as `asyncio.Task`s, event queue processing loop, writes `__orchestrator__*` keys to shared context, done callbacks | Unidirectional: orchestrator reads events, writes context; teams don't read orchestrator directives or peer messages |
| `ParallelSubagentsNode` | `models/control_flow.py` | `branch_graphs`, `parallelism`, `merge_strategy`, `reducer`, `input_mappings`, `branch_inputs` | Fire-and-forget; no inter-branch communication |
| `SharedContextStore` | `engine/context_runtime.py` | KV blackboard with `read(key)`, `write(key, value)`, `append(key, value)`, `snapshot()`/`restore()` | Available for message passing but not structured as a conversation; no ordering guarantees |
| `BoundaryContract` (14-2) | `models/context.py` | `reads_global`/`writes_global` whitelists, `signals` list with sticky/non-sticky | Governs what crosses sub-graph boundaries; team agents could use signals for upward communication |
| `ScopedContextView` (14-2) | `engine/context_runtime.py` | Isolated read/write view of shared context within sub-graph boundaries | Available for team-member isolation if needed |
| `MemoryStore` (14-1) | `engine/memory_store.py` | `FileSystemMemoryStore` with session/workflow scoping, CRUD, APPEND/MERGE modes | Could persist team conversations across runs |
| `RouterNode` | `models/control_flow.py` | LLM-powered routing: model picks from `route_descriptions` | Routing logic exists; could be reused for turn-taking decisions |
| Engine event system | `engine/events.py` | 26 event types (incl. lifecycle, logging, gate, parallel, RAG, sandbox, validator), `event_callback` on `ExecutionContext` | Events are fire-and-forget notifications; not bidirectional messages |
| `context.run_subgraph()` | `engine/scheduler.py` `_run_subgraph()` | Executes a sub-graph with inputs, returns outputs | Blocking call; no mid-execution injection of new inputs |
| `HumanInTheLoopNode` | `models/control_flow.py` | Pause-resume via callback; single human response per invocation | Pattern for "wait for external input" reusable for "wait for peer message" |
| Builder sub-graph context managers | `builder/builder.py` | `wf.composite()`, `wf.while_loop()`, `wf.for_each()`, `wf.parallel_subagents()`, `wf.orchestrator()` | No `wf.team()` or `wf.group_chat()` |
| Markdown workflow format | `loader/compiler.py` | `## Agents` section for node references | No team/group-chat section |
| Node type union | `models/graph.py` | 16 node types in `Node` discriminated union | No `AgentTeamNode` |

## Tasks

- [x] 1. Define AgentTeamNode model
  - [x] 1-1. Create `AgentTeamNode` in `models/control_flow.py` with `node_type: Literal["agent_team"]`. Fields: `agents` (dict[str, str] — agent_name → sub_graph key, analogous to `OrchestratorNode.teams`), `moderator_prompt` (str — system prompt for the moderator LLM that manages turn order), `moderator_model` (str | None — LLM for turn decisions), `turn_strategy` (Literal: `"round_robin"`, `"moderator"`, `"free_form"`, `"sequential"` — how turns are assigned), `max_turns` (int, default 20 — safety bound), `completion_condition` (Literal: `"consensus"`, `"moderator_halt"`, `"max_turns"`, `"all_responded"` — when the team conversation ends), `timeout_seconds` (float | None).
  - [x] 1-2. Add `shared_context_keys` field (list[str]) — context keys visible to all team agents (e.g., `["conversation_history", "shared_artifacts"]`). Automatically provisioned in `SharedContextStore` at team start.
  - [x] 1-3. Add `handoff_policy` field (Literal: `"explicit"`, `"any"`, `"moderator_only"` — who can initiate handoffs). `"explicit"` = agent must use `@handoff(target)` in output; `"any"` = any agent can hand off to any other; `"moderator_only"` = only the moderator routes.
  - [ ] 1-4. Add `message_port` field (str, default `"message"`) — the expected output port from agent sub-graphs that contains the conversational text to be parsed for `@` mentions.
  - [x] 1-5. Add composite-node contract fields (matching `OrchestratorNode` pattern): `external_input_schema`, `external_output_schema`, `input_mappings`, `agent_inputs` (per-agent overrides), `read_set`/`write_set`, `compaction_rule`, `failure_policy`, `boundary_contract`.
  - [x] 1-6. Add `AgentTeamNode` to `Node` discriminated union in `models/graph.py`. Export from `models/control_flow.py`.

- [x] 2. Define team message and handoff protocol
  - [x] 2-1. Create `TeamMessage` model in `models/control_flow.py` (or a new `models/team.py`): `sender` (str — agent name), `recipients` (list[str] — `@`-addressed targets, or `["all"]` for broadcast), `content` (str), `message_type` (Literal: `"message"`, `"handoff"`, `"result"`, `"question"`), `metadata` (dict — optional structured data alongside text), `turn_number` (int).
  - [x] 2-2. Define `HandoffRequest` model: `source_agent` (str), `target_agent` (str), `reason` (str), `context` (dict — data to pass to target), `handoff_type` (Literal: `"transfer"`, `"consult"` — transfer = source is done; consult = source expects a reply back).
  - [x] 2-3. Define `TeamConversation` model: `messages` (list[TeamMessage]), `active_agent` (str | None), `turn_count` (int), `handoff_log` (list[HandoffRequest]). Stored in `SharedContextStore` under a well-known key (`__team__{node_id}__conversation`).
  - [x] 2-4. Define `@`-routing syntax for agent outputs: when an LLM agent's response contains `@agent_name`, the moderator routes the next turn to that agent. Parsing via regex; validated against known agent names in the team.

- [x] 3. Build AgentTeamExecutor
  - [x] 3-1. Create `AgentTeamExecutor` in `executors/control_flow.py`. Core loop: initialize `TeamConversation` → select first agent (by strategy) → run agent's sub-graph with conversation context as input → parse output for `@` mentions and handoff requests → update conversation → select next agent → repeat until completion condition met.
  - [x] 3-2. Implement turn strategies:
    - `round_robin`: cycle through agents in declaration order.
    - `sequential`: each agent runs once in order, no repeats.
    - `moderator`: after each agent turn, call moderator LLM with conversation so far + agent roster → moderator returns next agent name.
    - `free_form`: agent's `@` mentions determine next speaker; if no mention, moderator decides; if no moderator, round-robin fallback.
  - [x] 3-3. Implement agent sub-graph execution: for each agent turn, call `context.run_subgraph(agent_key, agent_inputs)` where `agent_inputs` includes `conversation_history` (compacted), `current_turn` (the message addressed to this agent), `team_roster` (names + descriptions), `handoff_context` (if this turn resulted from a handoff).
  - [x] 3-4. Implement handoff processing: when an agent's output is parsed as a `HandoffRequest`, validate target exists, update `TeamConversation.handoff_log`, set `active_agent` to target, pass handoff context as next-turn input. For `"consult"` type, push a return-to-source marker so the source resumes after the consulted agent responds.
  - [x] 3-5. Implement completion detection: `"consensus"` = keyword-based check for CONSENSUS in output (LLM call deferred to integration phase); `"moderator_halt"` = HALT keyword in last message; `"max_turns"` = hit `max_turns` limit; `"all_responded"` = every agent has had at least one turn. Added sequential early-exit: `sequential` strategy stops when all agents have spoken once, regardless of `completion_condition`. Added `handoff_policy` enforcement: `moderator_only` suppresses agent-initiated handoffs.
  - [x] 3-6. Implement conversation compaction: before injecting conversation history into agent inputs, apply `CompactionRule` if set on the node. Default: `sliding_window` with window size proportional to agent count × 3 turns.
  - [x] 3-7. Collect final result: when conversation ends, use last agent's output as `result`, include `agent_contributions` (dict[str, str]), `consensus_reached` (bool), `total_turns` (int), and full `conversation` log.

- [x] 4. Integrate with scheduler and engine
  - [x] 4-1. Register `AgentTeamExecutor` in scheduler `_register_defaults()` for `node_type="agent_team"`.
  - [x] 4-2. In `_execute_node` (scheduler.py), handle `agent_team` the same as other composite nodes — resolve inputs, call executor, store outputs. (Already handled by generic executor dispatch.)
  - [ ] 4-3. In `_run_subgraph`, propagate team-level `SharedContextStore` keys to child sub-graphs so agents can read shared context. Respect `BoundaryContract` if present.
  - [x] 4-4. Emit engine events: `TEAM_TURN_STARTED` (agent_name, turn_number), `TEAM_TURN_COMPLETED` (agent_name, turn_number, message summary), `TEAM_HANDOFF` (source, target, reason), `TEAM_COMPLETED` (consensus_reached, total_turns).
  - [x] 4-5. Add event types to `engine/events.py` (already present).

- [ ] 5. Extend builder API
  - [ ] 5-1. Add `wf.team(name, agents: dict[str, NodeRef], turn_strategy, moderator_prompt, ...)` to `WorkflowBuilder`. Returns a `NodeRef` wrapping the `AgentTeamNode`. Each agent value is a `NodeRef` to a composite sub-graph.
  - [ ] 5-2. Add `wf.group_chat(name, agents, ...)` as an alias for `wf.team()` with `turn_strategy="free_form"` default.
  - [ ] 5-3. Extend builder compiler to generate `AgentTeamNode` + sub-graph wiring from team declarations.
  - [ ] 5-4. Extend builder decompiler to emit `wf.team()` calls for `AgentTeamNode` instances.

- [ ] 6. Extend markdown loader
  - [ ] 6-1. Support `type: agent_team` in workflow markdown frontmatter (alternative to declaring as a node in `## Flow`).
  - [ ] 6-2. Add `## Team` section to workflow format: declares agent members, turn strategy, moderator config. Example:
    ```
    ## Team
    strategy: moderator
    moderator_model: claude-sonnet-4-6
    max_turns: 15

    - researcher.md -> researches topics
    - writer.md -> drafts content
    - reviewer.md -> reviews and critiques
    ```
  - [ ] 6-3. Extend `loader/compiler.py` to parse `## Team` section → `AgentTeamNode` with sub-graph wiring.
  - [ ] 6-4. Extend `loader/decompiler.py` to emit `## Team` section for `AgentTeamNode`.

- [ ] 7. Extend visual editor
  - [ ] 7-1. Add `agent_team` to `NODE_TYPE_CATALOG` in `graph.ts` with description and port info.
  - [ ] 7-2. Add node icon for `agent_team` in `nodeIcons.tsx`.
  - [ ] 7-3. Add `AgentTeamNode` config panel fields: agent list (editable), turn strategy dropdown, moderator prompt textarea, max turns, completion condition.
  - [ ] 7-4. Show team conversation in `OutputPreview` or a dedicated `TeamConversationView` component: message bubbles with agent avatars/colors, `@` mentions highlighted, handoff indicators.
  - [ ] 7-5. Support drill-in to individual agent sub-graphs (reuse composite drill-in infrastructure).
  - [ ] 7-6. Palette entry under "Control Flow" or new "Teams" category.

- [ ] 8. Validation
  - [ ] 8-1. Extend `validate_graph()`: `AgentTeamNode.agents` values must reference existing sub-graph keys. At least 2 agents required. Agent names must be unique. `moderator_model` required when `turn_strategy="moderator"` or `completion_condition="consensus"`.
  - [ ] 8-2. Validate that each agent sub-graph has at least one entry node that accepts `conversation_history` (or a compatible port name) as input.
  - [ ] 8-3. Warn if `max_turns` is very high (>50) — likely misconfigured.

- [ ] 9. Tests and documentation
  - [x] 9-1. Unit tests: `AgentTeamNode` serialization, `TeamMessage`/`HandoffRequest` models, `@`-routing regex parsing, turn strategy selection logic.
  - [x] 9-2. Integration tests (41 total): round-robin 3-agent turn order + conversation accumulation + contributions (3), sequential one-pass + max_turns boundary (2), handoff routing + moderator_only blocking + handoff context passthrough + self-handoff rejection (4), max_turns safety (2), all_responded early-stop (2), event data verification (3), edge cases — <2 agents, error recovery, shared context persistence, conversation output (4), static helpers for _parse_handoff/_extract_content/_parse_mentions (7), model serialization round-trips (4), @-mention regex (10).
  - [ ] 9-3. Backward compat tests: existing `OrchestratorNode` workflows unchanged, `ParallelSubagentsNode` workflows unchanged.
  - [ ] 9-4. Builder round-trip tests: `wf.team()` → build → decompile → compare.
  - [ ] 9-5. Markdown round-trip tests: `## Team` section → compile → decompile → compare.
  - [ ] 9-6. Update `docs/architecture.md`: add `AgentTeamNode` to control-flow primitives table, document turn strategies, `@`-routing syntax, handoff protocol.
  - [ ] 9-7. Update `docs/llm-api-guide.md`: agent team builder API, markdown syntax, node type reference.
  - [ ] 9-8. Update `docs/changelog.md`, `docs/todo.md`, and this plan as implementation progresses.

## Primary Files

- `src/dan/models/control_flow.py` — `AgentTeamNode`, `TeamMessage`, `HandoffRequest`, `TeamConversation`
- `src/dan/models/graph.py` — add `AgentTeamNode` to `Node` union
- `src/dan/executors/control_flow.py` — `AgentTeamExecutor`
- `src/dan/executors/__init__.py` — register `AgentTeamExecutor`
- `src/dan/engine/events.py` — `TEAM_TURN_STARTED`, `TEAM_TURN_COMPLETED`, `TEAM_HANDOFF`, `TEAM_COMPLETED`
- `src/dan/engine/scheduler.py` — no structural changes (executor handles team logic)
- `src/dan/builder/builder.py` — `wf.team()`, `wf.group_chat()`
- `src/dan/builder/compiler.py` — compile team declarations
- `src/dan/builder/decompiler.py` — decompile `AgentTeamNode`
- `src/dan/loader/compiler.py` — parse `## Team` section
- `src/dan/loader/decompiler.py` — emit `## Team` section
- `editor/src/types/graph.ts` — `AgentTeamNode` TypeScript type
- `editor/src/lib/nodeIcons.tsx` — team icon
- `editor/src/components/ConfigPanel.tsx` — team config fields
- `editor/src/components/OutputPreview.tsx` — team conversation view
- `tests/test_models/` — team model serialization
- `tests/test_engine/` — team execution integration tests
- `tests/test_builder/` — team builder round-trip
- `tests/test_loader/` — team markdown round-trip

## Decisions

- **New node type, not OrchestratorNode extension.** `OrchestratorNode` is a monitor-and-route pattern (one-to-many). `AgentTeamNode` is a peer-to-peer conversation pattern (many-to-many). The execution models are different enough to warrant separate types. The orchestrator remains for hierarchical coordination; teams handle collaborative coordination.
- **Moderator as optional LLM.** The moderator is not a separate node in the graph — it's an LLM call inside the `AgentTeamExecutor` that decides turn order. This keeps the team node self-contained. For complex moderator logic, users can make the moderator itself an agent in the team.
- **Conversation history as structured input.** Each agent receives the full (compacted) conversation as a typed input, not as a hidden context injection. This makes the data flow visible in the graph and compatible with boundary contracts.
- **Message extraction via `message_port`.** Since sub-graphs can produce arbitrary output schemas (dict of ports), the team executor needs to know which string contains the text to parse for mentions. The `message_port` (default `"message"`) provides this contract.
- **`@`-routing is parsed from LLM output.** Agents address each other by including `@agent_name` in their natural language response. The executor parses these mentions and routes accordingly. This is the most LLM-natural pattern (matches how humans use `@` in group chats).
- **Handoff is a first-class action.** Not just a side-effect of `@`-mentioning. Agents can produce structured `HandoffRequest` outputs (via output schema or a `handoff` output port) for explicit work transfer with context.
- **Shared conversation is append-only.** `TeamConversation` accumulates messages. Agents can't delete or edit previous messages. Compaction reduces what's sent as input but preserves the full log.

## Notes

- Design draws from AutoGen (group chat manager, speaker selection), CrewAI (task delegation, role-based agents), and MetaGPT (structured message routing). DAN's differentiator: teams are graph-level objects with explicit topology, typed boundaries, and all three authoring surfaces.
- The coding assistant PoC (backlog) maps directly: Ask/Agent/Debug/Plan agents as team members, with a moderator that routes based on user intent. 16-1 is a direct enabler.
- Team conversations can be persisted across runs via 14-1 session memory — the `TeamConversation` object can be written to `MemoryStore` at checkpoints.
- 15-1 hyperedge propagation into agent team sub-graphs should work via the standard `propagate=True` mechanism once 15-1 lands.
- The `free_form` turn strategy is the most powerful but least predictable. The moderator strategy provides more control. Round-robin is safest for deterministic testing.
