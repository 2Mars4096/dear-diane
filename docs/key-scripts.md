# Key Scripts Inventory

Generated from live repo scans plus checked-in annotations in `scripts/inventory.py`.
Refresh with `python3 scripts/inventory.py --write docs/key-scripts.md`.

## Structural Hotspots

### Backend
| File | Lines |
| --- | --- |
| src/dan/engine/scheduler.py | 4154 |
| src/dan/server/chat_manager.py | 3398 |
| src/dan/server/concierge/runtime/__init__.py | 3355 |
| src/dan/executors/control_flow.py | 2859 |
| src/dan/server/routers/adapters.py | 2459 |
| src/dan/meta/planner.py | 2384 |
| src/dan/builder/builder.py | 2369 |
| src/dan/server/concierge/tier_executors.py | 2198 |
| src/dan/builder/decompiler.py | 1961 |
| src/dan/adapters/telegram_fleet.py | 1931 |
| src/dan/cli/chat.py | 1894 |
| src/dan/server/concierge/scheduler.py | 1875 |
| src/dan/loader/compiler.py | 1873 |
| src/dan/server/routers/furnace.py | 1839 |
| src/dan/server/startup/__init__.py | 1761 |
| src/dan/server/startup.py | 1701 |
| src/dan/server/agent_runtime/workflow_generation/runtime.py | 1647 |
| src/dan/server/run_manager.py | 1618 |
| src/dan/server/concierge/triage.py | 1531 |
| src/dan/engine/token_optimization.py | 1529 |

### Frontend
| File | Lines |
| --- | --- |
| editor/src/components/ChatPanel.tsx | 4362 |
| editor/src/components/ConfigPanel.tsx | 2982 |
| editor/src/store/useGraphStore.ts | 2235 |
| editor/src/components/code/ExtensionsPanel.tsx | 1915 |
| editor/src/components/shell/GlobalSettingsPanel.tsx | 1646 |
| editor/src/components/code/GitPanel.tsx | 1511 |
| editor/src/components/shared/ModeChatSidebar.tsx | 1488 |
| editor/src/components/code/MonacoTabs.tsx | 1440 |
| editor/src/store/useMessagingStore.ts | 1424 |
| editor/src/components/code/FileExplorer.tsx | 1177 |
| editor/src/lib/api.ts | 1134 |
| editor/src/components/ChatMessage.tsx | 1118 |
| editor/src/components/modes/ResearchFurnacePanel.tsx | 1092 |
| editor/src/components/research/DistillationTab.tsx | 1084 |
| editor/src/components/modes/ResearchModeShell.tsx | 1028 |

## Key Script Ownership Map

| File | Lines | Role | Authoritative Owner | Current Overreach | Target Boundary | Follow-on |
| --- | --- | --- | --- | --- | --- | --- |
| src/dan/server/concierge/runtime/__init__.py | 3355 | control-plane sink | orchestration and state progression | dispatch policy, schedule rewrite, progress UX, and control-plane glue still share one file | runtime/dispatch_policy.py, runtime/schedule_commands.py, runtime/progress_ux.py | 50-5 |
| src/dan/server/chat_manager.py | 3398 | facade sink | chat boundary and import shim only | still owns workflow build/apply/save, smoke-run glue, and prompt plumbing | src/dan/server/chat/* plus src/dan/server/agent_runtime/* | 50-3 |
| src/dan/server/run_manager.py | 1618 | lifecycle owner | run lifecycle, start/resume/cancel, relay seam | run guards are caller-owned today and the completion tail still mixes reflection, telemetry, and learning | workflow_guards.py in 50-2, run_finalization.py in 50-4 | 50-2, 50-4 |
| src/dan/server/capability_handlers.py | 879 | compatibility facade | registration and schema wiring only | still owns graph delete, mutation apply/save, and latest-mutation lookup | src/dan/server/capabilities/* | 50-3 |
| src/dan/server/concierge/tier_executors.py | 2198 | prompt and handoff sink | slot-based prompt assembly and child-handoff derivation | duplicates prompt build/extract/apply chains and mixes execution, events, and handoff text | shared executor base plus typed envelopes | 46-6 |
| src/dan/server/concierge/scheduler.py | 1875 | scheduler daemon owner | lease authority and fire-time execution only | still carries user-facing schedule-command semantics and workflow normalization | schedule surface helpers outside the daemon boundary | 50-5 |
| src/dan/server/concierge/dispatcher.py | 693 | ingress and queue adapter | ingress serialization and canonical queue lease model | shares queue and task ownership with tiered_dispatch, models.py, and task_registry.py | one documented queue owner with task/session ids demoted accordingly | 50-5 |
| src/dan/server/concierge/models.py | 183 | user-facing state truth | SurfaceMessage, Task, Project, and ResolvedContext truth | project/task truth overlaps with ConciergeTask and Session ids in adjacent layers | keep user-facing truth here; demote lifecycle and trace mirrors | 50-5 |
| src/dan/server/concierge/task_registry.py | 588 | task lifecycle tracker | concierge-level task state and persistence | overlaps with dispatcher queues and models.Task/Project on work identity | either canonical queue lease owner or thin state tracker, not both | 50-5 |
| src/dan/server/concierge/session.py | 530 | execution trace owner | runtime session tree and execution traces | session ids bleed into user-facing work-state reasoning | keep runtime-only trace ownership here | 50-5 |
| src/dan/server/agent_runtime/workflow_generation.py | 0 | authoring sink | workflow-generation phases with explicit package seams | planning, build, diagnosis, repair, and recovery still mix in one file | workflow_generation/ package split by phase | 50-6 |
| src/dan/server/graph_mutator.py | 1284 | mutation sink | apply engine separated from macro authoring and repair policy | apply, macros, migration policy, and repair logic still mix together | split by apply engine, macros, and repair helpers | 50-6 |
| src/dan/server/routers/adapters.py | 2459 | gateway sink | adapter protocol and streaming surface only | adapter state, routing, callback protocol, and streaming are still bundled together | smaller adapter-surface modules | 50-6 |
| src/dan/worker/executor.py | 1204 | worker adapter boundary | core worker compute dispatch behind adapter seams | still mixes engine-facing dispatch, cached legacy-template reuse, and DAN runtime compatibility modes beside the thinner worker-core membrane | dan/worker/core/ plus dan/worker/adapters/ | 46-7 |
| src/dan/executor_defaults.py | 90 | default runtime registry | default adapter registration only | ready llm/tool/code/input families now use dedicated worker-backed runtime executors, but default policy still mixes worker-backed paths with remaining explicit legacy registrations | keep only honest default policy wiring while any remaining compatibility projections shrink outward | 46-7 |
| editor/src/components/ChatPanel.tsx | 4362 | frontend shell sink | chat thread UI shell only | thread, stream, run, reconnect, persistence, and branch state all mix together | stream/reconnect/persistence hooks and message-list components | 50-8 |
| editor/src/components/ConfigPanel.tsx | 2982 | frontend config sink | node and edge config editing shell only | schema validation, editing logic, and form rendering still share one file | config subpanels and validation helpers | 50-8 |
| editor/src/store/useGraphStore.ts | 2235 | frontend state sink | graph-editor state only | workflow, chat-session, and broader workspace state still spill into the graph store | chat-session, run-history, and workspace-tab stores | 50-8 |
| editor/src/store/useMessagingStore.ts | 1424 | frontend messaging sink | messaging and transport state only | adapter and broader UI concerns still spill into one store | narrow messaging-only selectors and split stores if the inventory still justifies it | 50-8 |

## Authoritative State Flow

| Concept | Current Truth | What It Owns | Transitional Neighbors | Decision / Next Move |
| --- | --- | --- | --- | --- |
| SurfaceMessage | src/dan/server/concierge/models.py | authoritative ingress packet for surface text, ids, attachments, metadata | metadata copies inside dispatcher and tiered dispatch | keep here; delete duplicated prompt metadata copies during 50-5 and 46-6 |
| ResolvedContext | src/dan/server/concierge/models.py | turn-level resolved project, task, workflow, and follow-up context | runtime and tiered-dispatch re-resolution/materialization | 50-5 should reduce repeated resolution and make this the single materialized turn context |
| Task / Project | src/dan/server/concierge/models.py | user-facing unit of work and persisted project truth | ConciergeTask and Session ids mirror some of the same concepts | keep user-facing truth here; demote queue/trace layers to implementation detail |
| ConciergeTask | src/dan/server/concierge/task_registry.py | queue lease and concierge task lifecycle tracker | dispatcher queues and models.Task overlap on work identity | 50-5 must decide whether this is the canonical queue lease or only a state tracker |
| Session / SessionManager | src/dan/server/concierge/session.py | runtime execution trace and child-session tree | task ids and user-facing work state leak into prompt/log reasoning | keep runtime trace ownership here; do not let Session become user-facing truth |
| Prompt carriers | routers/chat.py, chat_manager.py, messages.py, tier_executors.py | future typed PromptEnvelope / TurnExecutionEnvelope contract | prompt_context, extra_system_instructions, metadata bag, attachment_prompt_context | 46-6 should retire free-form carriers and duplicate metadata strings |

## Duplication / Deletion Matrix

| Concept | Authoritative Owner | Transitional Owners | Delete / Demote Path |
| --- | --- | --- | --- |
| run-readiness guard | RunManager and workflow_guards | routers/runs.py, capabilities/runs.py, gateway/router.py, chat surfaces | 50-2 moves the guard to RunManager and deletes caller-side copies |
| run launch and relay hookup | RunManager launch seam | run_relay.py wrappers, routes, gateway, startup, chat local | 50-2 consolidates one launch helper and deletes wrapper glue in the same patch |
| workflow apply-save path | shared apply-ready helper beside workflow_guards.py | routers/graphs.py, capability_handlers.py, chat_manager.py | 50-2 routes all apply-save paths through one helper |
| attachment prompt context | single prompt slot in the 46-6 envelope | routers/chat.py currently passes it into both prompt_context and extra_system_instructions | 50-3 removes the double-pass before 46-6 retires the old carriers |
| prompt assembly | tier_executors.py shared prompt builder plus PromptEnvelope | chat_manager.py, messages.py, raw metadata strings | 46-6 removes ad-hoc concatenation and duplicate metadata extraction |
| queue authority | one concierge queue model | dispatcher queues plus tiered_dispatch background queue/cap | 50-5 chooses one queue lease owner and demotes the other |
| work-state identity | models.Task / Project for user truth | ConciergeTask and Session partial mirrors | 50-5 documents which ids are canonical in logs, prompts, and follow-up resolution |
| legacy worker bridge | Worker core plus DAN adapters | compatibility-only adapters plus the remaining projection seams | 50-7 landed the default-path split; 46-7 can keep shrinking compatibility-only seams if external bundle extraction becomes active |

## Prompt Carrier Inventory

- `prompt_context`: 59 occurrences across 13 files
- `extra_system_instructions`: 39 occurrences across 7 files
- 46-6 target: retire the free-form carriers in favor of typed prompt slots and drive `extra_system_instructions` down to <=8 uses.

| File | prompt_context | extra_system_instructions | Total |
| --- | --- | --- | --- |
| src/dan/server/chat_manager.py | 13 | 11 | 24 |
| src/dan/server/concierge/tier_executors.py | 9 | 10 | 19 |
| src/dan/agent_runtime/messages.py | 9 | 8 | 17 |
| src/dan/server/chat/prompt_builder.py | 5 | 3 | 8 |
| src/dan/server/routers/chat.py | 8 | 0 | 8 |
| src/dan/agent_runtime/text_runtime.py | 3 | 3 | 6 |
| src/dan/agent_runtime/synthesis_runtime.py | 1 | 2 | 3 |
| src/dan/server/concierge/pending_actions.py | 3 | 0 | 3 |
| src/dan/prompt_contracts.py | 0 | 2 | 2 |
| src/dan/meta/__init__.py | 2 | 0 | 2 |
| src/dan/meta/controller.py | 2 | 0 | 2 |
| src/dan/meta/planner.py | 2 | 0 | 2 |

### Concierge Prompt Pipeline

- `src/dan/server/routers/chat.py` builds attachment context and currently duplicates it into both prompt carriers.
- `src/dan/server/concierge/dispatcher.py` binds ingress work to queue/task state.
- `src/dan/server/concierge/runtime/__init__.py` decides dispatch mode and orchestration handoff.
- `src/dan/server/concierge/tiered_dispatch.py` materializes turn context and background or foreground execution.
- `src/dan/server/concierge/tier_executors.py` assembles prompt fragments, stage overlays, and child handoff text.
- `src/dan/server/chat_manager.py` and `src/dan/agent_runtime/messages.py` still carry the untyped prompt carriers into final message assembly.
- `src/dan/worker/executor.py` remains the workflow-node compute boundary and should consume the normalized minimum contract from 46-6, not invent a second prompt shape.

## Worker Import Boundary Violations

| File | Line | Forbidden Import | Import |
| --- | --- | --- | --- |
| src/dan/worker/adapters.py | 14 | dan.executors.* | from dan.executors.code import CodeExecutor |
| src/dan/worker/adapters.py | 15 | dan.executors.* | from dan.executors.control_flow import GateExecutor, HumanNodeExecutor, ReduceExecutor, RouterExecutor, VoteExecutor |
| src/dan/worker/adapters.py | 16 | dan.executors.* | from dan.executors.input import InputExecutor |
| src/dan/worker/adapters.py | 17 | dan.executors.* | from dan.executors.rag import RAGExecutor |
| src/dan/worker/adapters.py | 18 | dan.executors.* | from dan.executors.reflection import ReflectionExecutor |
| src/dan/worker/adapters.py | 19 | dan.executors.* | from dan.executors.tool import ToolExecutor |
| src/dan/worker/adapters.py | 20 | dan.executors.* | from dan.executors.validator import ValidatorExecutor |
| src/dan/worker/adapters.py | 26 | dan.executors.* | from dan.executors.llm import LLMExecutor |
| src/dan/worker/adapters.py | 39 | dan.executors.* | from dan.executors.llm import LLMExecutor |
| src/dan/worker/executor.py | 28 | dan.executors.* | from dan.executors.code import CodeExecutor |
| src/dan/worker/executor.py | 29 | dan.executors.* | from dan.executors.control_flow import GateExecutor, HumanNodeExecutor, ReduceExecutor, RouterExecutor, VoteExecutor |
| src/dan/worker/executor.py | 30 | dan.executors.* | from dan.executors.llm import LLMExecutor |
| src/dan/worker/executor.py | 31 | dan.executors.* | from dan.executors.rag import RAGExecutor |
| src/dan/worker/executor.py | 32 | dan.executors.* | from dan.executors.reflection import ReflectionExecutor |
| src/dan/worker/executor.py | 33 | dan.executors.* | from dan.executors.tool import ToolExecutor |
| src/dan/worker/executor.py | 34 | dan.executors.* | from dan.executors.validator import ValidatorExecutor |
| src/dan/worker/executor.py | 35 | dan.models.legacy | from dan.models.legacy import CodeOperator, LLMOperator, ToolOperator |
| src/dan/worker/executor.py | 169 | dan.executors.* | from dan.executors.llm import LLMExecutor |
| src/dan/worker/executor.py | 176 | dan.executors.* | from dan.executors.tool import ToolExecutor |
| src/dan/worker/executor.py | 184 | dan.executors.* | from dan.executors.code import CodeExecutor |
| src/dan/worker/executor.py | 191 | dan.executors.* | from dan.executors.control_flow import GateExecutor |
| src/dan/worker/executor.py | 198 | dan.executors.* | from dan.executors.control_flow import RouterExecutor |
| src/dan/worker/executor.py | 205 | dan.executors.* | from dan.executors.validator import ValidatorExecutor |
| src/dan/worker/executor.py | 212 | dan.executors.* | from dan.executors.reflection import ReflectionExecutor |
| src/dan/worker/executor.py | 219 | dan.executors.* | from dan.executors.rag import RAGExecutor |
| src/dan/worker/executor.py | 226 | dan.executors.* | from dan.executors.control_flow import HumanNodeExecutor |
| src/dan/worker/executor.py | 233 | dan.executors.* | from dan.executors.control_flow import VoteExecutor |
| src/dan/worker/executor.py | 240 | dan.executors.* | from dan.executors.control_flow import ReduceExecutor |
| src/dan/worker/executor.py | 649 | dan.models.legacy | from dan.models.legacy import LLMOperator |
| src/dan/worker/executor.py | 789 | dan.models.legacy | from dan.models.legacy import CodeOperator |
| src/dan/worker/executor.py | 827 | dan.models.legacy | from dan.models.legacy import ToolOperator |
| src/dan/worker/executor.py | 878 | dan.models.legacy | from dan.models.legacy import LLMOperator |
| src/dan/worker/model.py | 20 | dan.models.legacy | from dan.models.legacy import ValidationRule |
| src/dan/worker/presets.py | 9 | dan.models.legacy | from dan.models.legacy import ( |

## Legacy Adapter Inventory

- Default-path bridged node families: 0
| Node Type | executor_defaults.py Line |
| --- | --- |

### Default Registration Callers

| Caller | Line |
| --- | --- |
| src/dan/client/local.py | 204 |
| src/dan/engine/scheduler.py | 498 |
| src/dan/executor_defaults.py | 39 |
| src/dan/server/run_manager.py | 1301 |

## Split Guardrails

| Category | Soft Threshold | Hard Threshold |
| --- | --- | --- |
| backend control-plane files | soft 1500 | hard 2200 |
| runtime and scheduler sinks | soft 1800 | hard 2600 |
| routers and gateways | soft 900 | hard 1500 |
| frontend components | soft 1200 | hard 1800 |
| frontend stores | soft 1000 | hard 1600 |

- Real subtraction means the old owner loses responsibility in the same patch, not just that code moved to a neighbor.
- Wrappers and facades do not count as success unless they become thin import or registration shims.
- Oversize alone is not enough to justify a split; mixed ownership, duplicated invariants, or queue/prompt ambiguity must also be present.

## Validation Basket

| Plan | Minimum Focused Validation |
| --- | --- |
| 50-1 | `python3 scripts/inventory.py --write docs/key-scripts.md` and `python3 -m py_compile scripts/inventory.py` |
| 50-2 | `tests/test_server/test_run_manager.py`, `tests/test_server/test_run_manager_reflection.py`, `tests/test_server/test_capability_run_lifecycle.py`, `tests/test_concierge/test_live_data.py`, `tests/test_post_tool_followup_recovery.py`, `tests/test_concierge/test_chat_router.py` |
| 50-3 | `tests/test_server/test_chat_manager.py`, `tests/test_concierge/test_tiered_dispatch.py`, `tests/test_concierge/test_chat_router.py` |
| 50-4 | `tests/test_server/test_run_manager.py`, `tests/test_server/test_run_manager_reflection.py` |
| 50-5 | `tests/test_concierge/test_tiered_dispatch.py`, `tests/test_concierge/test_scheduler.py`, `tests/test_concierge/test_session_manager.py`, `tests/test_concierge/test_progress_ux.py` |
| 46-6 | `tests/test_concierge/test_tiered_dispatch.py`, `tests/test_concierge/test_fast_commands.py`, `tests/test_worker/test_executor.py` |
| 46-7 | `tests/test_executor_defaults.py`, `tests/test_worker/test_workflow.py` |
| 50-8 | `npm --prefix editor run test` plus `npm --prefix editor run build:verify` |

## Performance Guardrails

| Metric | How To Measure | Pass Threshold |
| --- | --- | --- |
| workflow run start latency | wall-clock from `start_run` call to first engine event | <= 20% regression vs. pre-split baseline |
| scheduler fire-time execution latency | cron fire to first node dispatch | <= 20% regression vs. baseline |
| chat time-to-first-token | foreground ask/agent/plan turn to first stream token | <= 20% regression and no ack-only regressions |
| frontend bundle budgets | `npm --prefix editor run build:verify` | stay within current bundle budgets |

## Pruning Candidates

| Delete Candidate | Delete After | Why |
| --- | --- | --- |
| caller-owned run guards and launch glue | 50-2 | delete route, capability, gateway, and chat-local wrappers once RunManager owns guard and launch |
| attachment prompt double-pass in routers/chat.py | 50-3 | remove the extra_system_instructions duplicate once the single-slot path is wired |
| dispatch-policy, schedule rewrite, and progress UX code in runtime/__init__.py | 50-5 | delete embedded special cases when extracted neighbors own them |
| free-form prompt carriers and duplicated metadata strings | 46-6 | retire prompt_context, extra_system_instructions, and metadata prompt copies in the same patch series |
| remaining Worker/legacy projection glue and compatibility-only bridge registrations | 46-7 | keep shrinking compatibility-only adapter surfaces once an external bundle consumer justifies the extra extraction work |
