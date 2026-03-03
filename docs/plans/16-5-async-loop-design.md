# 16-5: Async Loop Design

**Parent:** [16-execution-primitives](16-execution-primitives.md)
**Status:** completed
**Goal:** Upgrade the `OrchestratorNode` and `OrchestratorExecutor` into a true event-driven, long-running process where the orchestrator LLM runs independently, monitors progress, and pushes commands/work to teams asynchronously as they become free — replacing the current static one-shot team fan-out behavior.

## Motivation

This plan addresses a long-standing backlog item discovered during vibe-research workflow development:
> **Async loop design** — (historical backlog wording) orchestrator runs independently with access to current progress and can emit commands anytime; departments/teams work in tandem (parallel, no cross-deps). Target: orchestrator as long-running/streaming process that pushes work as teams become free, or event-driven model where orchestrator and teams overlap.

Currently, `OrchestratorNode` defines an `orchestrator_prompt` and `orchestrator_model`, but the `OrchestratorExecutor` **does not actually invoke the LLM**. It simply spawns all teams as background tasks, runs an event-polling loop, and stops when completion conditions are met. It is effectively a `ParallelSubagentsNode`-style fan-out with an event queue, not a dynamic coordinator.

To realize the async loop design, the orchestrator LLM must be brought into the event loop: receiving team completion events, evaluating the current state, and deciding whether to dispatch new inputs to teams, wait, or halt.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `OrchestratorNode` | `models/control_flow.py` | `orchestrator_prompt`, `orchestrator_model`, `completion_condition`, `teams`, `input_mappings`, `team_inputs` | Model fields exist but are currently unused by the executor |
| `OrchestratorExecutor` | `executors/control_flow.py` | Spawns teams via `asyncio.create_task`; loops over `event_queue.get_nowait()`; checks `all_done`/`any_done` | Never calls the LLM; no mid-execution dispatch of new inputs to teams; iterations are just event-polling cycles |
| `ExecutionContext._run_subgraph` | `engine/executor.py` | Runs a named sub-graph with given inputs | Executor currently calls it once per team at startup; no orchestrator-level redispatch loop exists yet |
| `RouterExecutor` LLM call pattern | `executors/control_flow.py` | `_call_router_llm()` demonstrates provider-registry-backed LLM invocation from control-flow executor code | Orchestrator can reuse a similar call pattern but currently has no LLM call path |
| Event routing | `executors/control_flow.py` | Overrides `context._event_callback` to capture team events | Captures events, but only writes `__orchestrator__*` to shared context without reacting |
| Tool calling | `executors/tool.py` | Standard `ToolExecutor` | The orchestrator LLM needs a way to "dispatch" work to teams — likely via a tool call |

## Tasks

- [x] 1. Define the orchestrator interaction model
- [x] 1-1. Define how the orchestrator LLM interacts with teams. Instead of normal text output, the orchestrator LLM should be provided with a built-in tool: `dispatch_to_team(team_name: str, inputs: dict) -> dict`. This tool returns a `job_id` so the orchestrator can track it.
  - [x] 1-2. Define the orchestrator's observation model: when a team finishes (or emits a sticky signal), the event is formatted into a natural language observation (e.g. "Job <job_id> (team: <team_name>) completed with result...") and appended to the orchestrator's conversation history.
  - [x] 1-3. Define the halting condition: the orchestrator LLM decides to stop by calling a `halt_orchestrator(reason: str, final_result: dict)` tool, or by outputting a specific stop sequence.

- [x] 2. Refactor OrchestratorExecutor core loop
  - [x] 2-1. Rewrite `OrchestratorExecutor.execute()`: initialize an `orchestrator_messages` list with the `orchestrator_prompt` as the system message.
  - [x] 2-2. Initial dispatch: call the orchestrator LLM with the initial node `inputs`. The LLM should call `dispatch_to_team` for one or more teams.
  - [x] 2-3. Event loop: `await` on the `event_queue`. When a significant event occurs (e.g., a team finishes and returns its `NodeResult`, or a team emits a sticky signal), append a user message to `orchestrator_messages` detailing the event.
  - [x] 2-4. Re-evaluation: after receiving events, call the orchestrator LLM again. It can process the new information and either dispatch more work, wait for other teams, or halt.
  - [x] 2-5. Concurrency management: keep track of which *jobs* are currently running. The orchestrator can dispatch to the same team multiple times concurrently (e.g., spinning up 3 "researcher" jobs with different inputs). The `job_id` enables disambiguation of their results.

- [x] 3. Implement orchestrator tool dispatch
  - [x] 3-1. Expose `dispatch_to_team(team_name, inputs)` to the orchestrator LLM via function calling (using the existing `ToolExecutor` / JSON Schema logic).
  - [x] 3-2. When the tool is called, use `asyncio.create_task(context.run_subgraph(...))` to start the team. Add the task to the running tasks dict and attach a done callback that pushes a `TeamCompletedEvent` into the `event_queue`.
  - [x] 3-3. Expose `halt_orchestrator(result)` to the LLM to cleanly exit the `OrchestratorNode` and produce the final output.

- [x] 4. Manage sub-graph isolation and restarts
  - [x] 4-1. Ensure repeated dispatches to the same team key are isolated by execution state. Validate that each `run_subgraph` dispatch gets independent `ExecutionState` and that loop/local scopes from prior dispatches do not leak into subsequent dispatches.
  - [x] 4-2. Map `input_mappings` and `team_inputs` into the dynamic dispatch arguments transparently so the LLM doesn't have to know the low-level port names.

- [ ] 5. Extend Visual Editor and Output Validation
  - [ ] 5-1. Update `OrchestratorNode` UI in the visual editor to reflect the new dynamic nature. Show an "Orchestrator Log" that traces the LLM's decisions and dispatches over time.
  - [x] 5-2. Add a `max_llm_calls` safety bound (default 50) to prevent infinite dispatch loops.

- [ ] 6. Tests and Documentation
  - [x] 6-1. Unit tests: 21 tests in `tests/test_engine/test_orchestrator_async.py` — static fan-out backward compat (5), LLM-driven dispatch sequential/parallel/halt/unknown-team/duplicate/nudge (6), safety bounds max_llm_calls+max_iterations (2), timeout LLM+static (2), conversation flow system-msg/user-msg/tools/events (4), input mappings (1), orchestrator log (1). All 11 existing tests in `test_orchestrator.py` pass unchanged.
  - [ ] 6-2. Integration test: implement a simplified vibe-research workflow where the orchestrator manages a `research` team and a `writer` team, dispatching sequentially based on intermediate results.
  - [ ] 6-3. Update `docs/architecture.md` to reflect the active role of the orchestrator LLM.
  - [ ] 6-4. Update `docs/llm-api-guide.md` with instructions on how to prompt the orchestrator node.

## Primary Files

- `src/dan/models/control_flow.py` — adjust `OrchestratorNode` config (e.g., adding `max_llm_calls`)
- `src/dan/executors/control_flow.py` — completely rewrite `OrchestratorExecutor.execute()` to use the LLM
- `src/dan/engine/scheduler.py` — verify and harden repeated `_run_subgraph` dispatch isolation
- `editor/src/components/ConfigPanel.tsx` — orchestrator config updates
- `editor/src/components/LogPanel.tsx` — ensure dynamic dispatches are clearly logged
- `tests/test_engine/` — async loop integration tests

## Decisions

- **Event-driven LLM invocation.** The orchestrator LLM doesn't run continuously; it is invoked reactively whenever a team completes its work or emits a signal. This preserves tokens while achieving the async overlap.
- **Dispatch via Tool Calling.** The cleanest way for an LLM to initiate concurrent tasks is via structured tool calls. The orchestrator LLM is presented as an agent whose tools are `dispatch_to_team` and `halt`.
- **Replacing static parallel execution.** The current `OrchestratorExecutor` is just a parallel fan-out. This refactor makes it a true dynamic coordinator. If users just want static parallel fan-out without an LLM, they should use `ParallelSubagentsNode`.

## Notes

- This directly resolves the "Async loop design" backlog item.
- It differentiates `OrchestratorNode` from `AgentTeamNode` (16-1):
  - `AgentTeamNode` is peer-to-peer (agents talk to each other directly via `@`).
  - `OrchestratorNode` is strictly hierarchical (orchestrator talks to teams, teams only return results to orchestrator).
- It also differentiates from `ParallelSubagentsNode` which is static, fire-and-forget fan-out.