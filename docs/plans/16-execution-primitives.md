# 16: Phase 9C — Execution Primitives

**Status:** completed
**Goal:** Add five new execution primitives — agent teams (group-chat multi-agent coordination), voting/ensemble (redundancy for quality), generalized HumanNode (human as first-class graph participant), loop context managers (selective feedback control), and async loop design (event-driven LLM orchestration) — completing the Deep Systems layer.

## Motivation

Phase 9C is the third and final Deep Systems cluster. It introduces execution-level capabilities that expand the types of workflows DAN can express:

- **Agent teams don't exist.** `OrchestratorNode` runs subgraph teams concurrently with an event-processing loop, but communication is unidirectional (orchestrator monitors events, writes to shared context). There is no peer-to-peer `@`-routing between agents, no explicit handoff protocol, and no shared conversational context. Group-chat style coordination — where agents address each other, hand off work, and build on each other's contributions — requires a new execution pattern.
- **No voting or ensemble mechanism.** Running the same task through multiple models (or the same model N times) and selecting the best answer is a proven quality technique (self-consistency, majority voting, cross-model ensemble). This currently requires manual fan-out + reduce wiring. A dedicated `VoteNode` with built-in voting strategies would make this a one-step primitive.
- **HumanNode is underspecified.** `HumanInTheLoopNode` exists as a control-flow primitive with a simple prompt/response callback. Architecture docs describe a richer vision: typed I/O schemas, chat-as-renderer, adjustable autonomy via topology, rendering surface decoupling. The gap between the vision and the implementation prevents real human-in-the-loop workflows beyond simple approval gates.
- **Loop feedback is all-or-nothing.** While-gate loops and `WhileLoopNode` feed the entire body output back. There is no way to declare which outputs feed back to the condition vs. which are intermediate artifacts. `CompactionRule` reduces history size but doesn't filter *what* flows between iterations. This forces workaround edges and extra code nodes to extract feedback signals.

- **Async loop design.** Currently, `OrchestratorNode` defines an `orchestrator_prompt` and `orchestrator_model`, but the executor doesn't actually invoke the LLM. It simply spawns all teams as background tasks. The orchestrator must be brought into the event loop: receiving team completion events and dynamically deciding when to dispatch work, wait, or halt.

These primitives are independent of each other and can be built in parallel. They depend on stable memory semantics (9A, done) and benefit from behavior modifiers (9B) but don't require them.

## Existing Infrastructure (baseline)

| Component | Location | What exists | Gap |
|---|---|---|---|
| `OrchestratorNode` | `models/control_flow.py` | Teams, orchestrator prompt, completion conditions, event-driven loop, input/team_inputs mappings | Unidirectional monitoring; no peer-to-peer routing; no handoff protocol; no shared conversation |
| `OrchestratorExecutor` | `executors/control_flow.py` | Spawns teams as background tasks, processes event queue, writes to shared context | Orchestrator→team only; teams can't address each other; no conversational history |
| `ParallelSubagentsNode` | `models/control_flow.py` | Fire-and-forget concurrent branches with merge strategies | No interaction between branches; no voting |
| `ReduceNode` | `models/control_flow.py` | Expression-based aggregation of upstream results | Generic reducer; no voting/consensus semantics |
| `MergeStrategy` | `models/context.py` | APPEND, LAST_WRITE_WINS, REDUCER | No majority, weighted, or quality-ranked strategies |
| `HumanInTheLoopNode` | `models/control_flow.py` | `prompt`, `timeout_seconds`, `default_action` | No typed I/O schema; no rendering mode; no multi-point interaction support |
| `HumanInTheLoopExecutor` | `executors/control_flow.py` | Callback-based pause/resume with event emission | Single callback pattern; no rendering surface abstraction |
| `HumanInputDialog.tsx` | `editor/src/components/` | Modal popup for mid-run human input | Simple text input; no form mode, no approval mode, no file upload |
| `GateNode(while)` | `models/control_flow.py` | Condition-based loop with `state_schema`/`state_defaults` | Entire body output feeds back; no selective feedback |
| `WhileLoopNode` | `models/control_flow.py` | Sub-graph iteration with compaction rule | `CompactionRule` reduces history size but doesn't filter feedback content |
| `CompactionRule` | `models/context.py` | `sliding_window`, `keep_last`, `diff_based`, `summarize` strategies | Compacts history; no per-field feedback selection |
| `LocalStateManager` | `engine/context_runtime.py` | Node-scoped working memory for loop state bags | Scope is per-node; no explicit feedback vs. artifact distinction |
| `SharedContextStore` | `engine/context_runtime.py` | Graph-wide KV blackboard with `read`/`write`/`append` | Available for inter-agent communication but not structured for conversations |
| `BoundaryContract` (14-2) | `models/context.py` | `reads_global`/`writes_global` whitelists, signal specs | Governs sub-graph isolation; relevant for team boundary design |
| `MemoryStore` (14-1) | `engine/memory_store.py` | Cross-run persistent KV store | Available for persisting team conversations and vote histories |

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [16-1](16-1-agent-teams.md) | Agent Teams | Group-chat multi-agent coordination, `@`-routing between agents, handoff protocol, shared conversational context, turn strategies | `models/control_flow.py`, `executors/control_flow.py`, `engine/scheduler.py`, `engine/context_runtime.py` |
| [16-2](16-2-voting-ensemble.md) | Voting / Ensemble | `VoteNode`, voting strategies (majority, weighted, quality-ranked), cross-model ensemble, `wf.vote()` builder sugar | `models/control_flow.py`, `executors/control_flow.py`, `builder/`, `loader/` |
| [16-3](16-3-human-node-generalization.md) | HumanNode Generalization | Typed I/O schemas, rendering modes (approval, form, free-text), chat-as-renderer, rendering surface abstraction, adjustable autonomy via topology | `models/control_flow.py`, `executors/control_flow.py`, `engine/executor.py`, `editor/src/components/` |
| [16-4](16-4-loop-context-manager.md) | Loop as Context Manager | New `FeedbackSelector` model, `feedback_selector`/`artifact_ports` on loop nodes, selective feedback filtering in scheduler and executor, builder API for feedback-aware loops | `models/context.py`, `models/control_flow.py`, `executors/control_flow.py`, `engine/scheduler.py`, `builder/builder.py` |
| [16-5](16-5-async-loop-design.md) | Async Loop Design | Event-driven LLM orchestration, `dispatch_to_team` tool calling, dynamic sub-graph restarts | `models/control_flow.py`, `executors/control_flow.py`, `engine/scheduler.py` |

## Dependencies / Sequencing

1. **All five sub-plans are independent.** They touch different node types and execution patterns. Can be built in parallel.
2. **9A (14-*) is complete** and provides memory/boundary infrastructure that 16-1 (agent teams) benefits from: session memory for persisting team conversations, boundary contracts for team isolation.
3. **9B (15-*) is not a hard dependency** but coordinates with 16-1 and 16-3:
   - Skill hyperedges (15-1) could attach to agents within a team — 16-1 should design team boundaries to be hyperedge-propagation-compatible.
   - Dynamic model selection (15-3) benefits 16-2 (voting/ensemble) — vote nodes could use model policies for candidate selection.
4. **16-1 (agent teams) is the largest.** Recommended to start here as it introduces the most new abstractions.
5. **16-2 (voting/ensemble) is the most self-contained.** Good candidate for parallel work.
6. **16-3 (HumanNode) touches frontend.** Schedule after or alongside 16-1 to avoid merge conflicts in control-flow executors.
7. **16-4 (loop context manager) is smallest.** Extends existing loop mechanics with minimal new models.
8. **16-5 (async loop design) is high-risk runtime work.** Start after baseline executor regression coverage is in place, because it changes `OrchestratorExecutor` semantics and async scheduling behavior.

## Success Criteria

- Agent teams can coordinate via `@`-addressed messages, hand off work explicitly, and maintain shared conversational context across turns.
- Voting/ensemble runs the same task through N model instances and selects the best answer via configurable strategies.
- HumanNode is a first-class node type with typed I/O schemas, multiple rendering modes, and rendering-surface independence.
- Loop nodes support selective feedback — declaring which body outputs feed back to the next iteration vs. which are side-effect artifacts.
- Async loop design transforms `OrchestratorNode` into a true event-driven LLM process that overlaps its evaluation with team execution.
- All primitives work across all three authoring surfaces (builder DSL, markdown, visual editor).
- Existing workflows (OrchestratorNode, HumanInTheLoopNode, while-gate loops) continue to work unchanged.

## Decisions

- **Extend, don't replace.** `OrchestratorNode` remains as-is. Agent teams are a new node type (`AgentTeamNode`) that shares the concurrent-subgraph pattern but adds peer-to-peer communication. `HumanInTheLoopNode` is generalized in place with backward-compatible additions.
- **Composition over special-casing.** `VoteNode` is built on top of parallel fan-out + reduce, not as a completely separate execution path. The voting logic is the new part; the fan-out infrastructure is reused.
- **Rendering is the frontend's concern.** The engine produces structured events for HumanNode interactions. How those events are rendered (modal, chat bubble, CLI prompt) is a frontend decision. The engine provides the contract; the rendering layer fulfills it.
- **Feedback selectors are declarative.** Loop feedback configuration is a model-level declaration (`FeedbackSelector` on loop nodes), not runtime code. The scheduler interprets the declaration. NOTE: `ContextProjection` (dead code, `models/context.py`) has different fields (`context_keys`/`local_state_keys`/`artifact_uris`) designed for scope-boundary context views — `FeedbackSelector` is a new model with `include`/`exclude`/`rename`/`transform` on output ports.

## Notes

- Agent teams draw from group-chat patterns in AutoGen, CrewAI, and MetaGPT. The key differentiator is that DAN teams are *graph-level objects* with explicit topology, not imperative chat loops.
- The coding assistant proof-of-concept (backlog) needs both agent teams (Ask/Agent/Debug/Plan as team members) and generalized HumanNode (chat as rendering surface). 16-1 and 16-3 are direct enablers.
- Voting/ensemble is a quality primitive — it trades cost for reliability. Pairs naturally with 15-3 (dynamic model selection) for cost-aware voting.
- Loop context managers close a gap identified during vibe-research development and complement 16-5 by controlling what information cycles between orchestrator/loop iterations.
