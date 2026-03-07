# 25: Chat as Unified Control Plane

**Status:** completed
**Goal:** Collapse all existing capabilities behind the conversational interface so that chat is the single front door — whether the user is in `dan-chat`, the editor ChatPanel, Telegram, WhatsApp, or any future chat surface.

## Motivation

DAN today has 8 CLI commands (`dan-serve`, `dan-run`, `dan-chat`, `dan-status`, `dan-logs`, `dan-publish`, `dan-adapter`, `dan-blocks`), a visual editor with a chat panel, messaging adapters (email, Telegram, WhatsApp), and a meta-orchestrator — but they're separate surfaces with separate UX. Users must know which command or panel to use for which task.

Phase 15 makes chat the universal entry point. The meta-orchestrator routes intent to the right subsystem:

- "Have you done a lit review before?" → `ExperienceIndex` query
- "Adapt it for supply chain" → `WorkflowPlanner`
- "Run it" → `RunManager.start_run()`
- "Share it with John via Telegram" → `PublishRuntime` / adapter
- "What's still running?" → `ActivityTracker`

Same behavior across all chat surfaces. No new product features — just wiring every surface to what's already built.

## Existing Infrastructure

| Component | Location | What It Does Today |
|---|---|---|
| ChatManager | `server/chat_manager.py` | 5 modes (ask/plan/debug/agent/build), mutation tool schema, streaming, context window management |
| MetaController | `meta/controller.py` | Autonomous plan→execute→diagnose→repair loop, sessions, human override |
| DiscoveryService | `meta/discovery.py` | `discover_all()` → tools, skills, patterns, workflows, self-knowledge |
| ExperienceStore/Index | `engine/experience.py` | `save_experience()`, `search_similar()`, workflow history |
| RunManager | `server/run_manager.py` | `start_run()`, `resume_run()`, `cancel_run()`, `get_run()`, `list_runs()`, event pubsub, checkpoints |
| ActivityTracker | `gateway/activity.py` | Active/recent runs, connected surfaces, stale detection |
| PublishRuntime | `publish/runtime.py` | `GatewayRuntime` + `LocalRuntime`, publish/unpublish, MCP/HTTP serving |
| BlockRegistry | `blocks/registry.py` | Export/import workflow blocks, versioned packaging |
| Gateway Router | `gateway/router.py` | Dispatch, cancel, activity, surfaces, HumanNode, global event bus |
| CLI Chat | `cli/chat.py` | `ChatClient` (httpx/WS), REPL with `/run`, `/show`, `/save`, `/list`, `/open`, workflow management |
| Messaging Adapters | `adapters/` | Email, Telegram, WhatsApp — each renders HumanNode prompts and relays responses |
| GraphMutator | `server/graph_mutator.py` | 13 mutation ops, `apply()`/`dry_run()`, validation gate |
| PlanningPromptBuilder | `meta/planner.py` | Self-knowledge injection, few-shot examples, builder-code prompts |

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [25-1](25-1-capability-router.md) | Capability Router | Intent classification layer: detect message type (memory query, build/adapt, run control, publish/share, status, general Q&A), route to the correct subsystem. Unified tool schema for all capabilities. | ~3 days | None (foundational) |
| [25-2](25-2-experience-in-chat.md) | Experience in Chat | Surface `ExperienceStore`/`ExperienceIndex` and `DiscoveryService` as chat-accessible tools. "Have we done X?" / "Show similar workflows" / past run history. | ~2 days | 25-1 (router dispatches memory queries) |
| [25-3](25-3-publish-share-from-chat.md) | Publish & Share from Chat | "Share this" / "Publish as MCP" / "Export as block" — tool-call wrappers around `PublishRuntime`, `dan-blocks`, `generate_mcp_config()`. Returns inline results. | ~2 days | 25-1 (router dispatches publish intents) |
| [25-4](25-4-run-lifecycle-from-chat.md) | Run Lifecycle from Chat | Full run lifecycle without leaving chat: start, monitor, inspect status, resume from checkpoint, cancel, tail logs, view checkpoints, and resolve `HumanNode` prompts. Wraps `RunManager` + `ActivityTracker` + `RunStore`. | ~2 days | 25-1 (router dispatches run intents) |
| [25-5](25-5-auto-approve-undo.md) | Auto-Approve & Undo | Auto-apply mutations by default (no `Apply? [Y/n]`). `/undo` reverts last mutation. `--confirm` flag restores approval for cautious use. Trust-but-verify. | ~1 day | 25-1 (mutation flow change) |
| [25-6](25-6-intent-dispatcher.md) | Concierge Runtime | Transitional concierge foundation: durable `Project`/`Task` scope, deterministic fast paths, handler registry, summary-first context loading, and shared surface wiring above `ChatManager`. This gets DAN off the old "everything through one tool-calling prompt" path, but is no longer the final router architecture. | ~4 days | 25-1 through 25-5 (capabilities exist; concierge routes to them) |
| [25-7](25-7-action-autonomy-policy.md) | Behavior Policy | Shared execution-policy foundation for the concierge: one-round clarification, project-aware queue, progress, promotion, destructive/cost confirmation, and messaging-surface defaults. This remains the policy substrate for later solver-first work. | ~3 days | 25-6 (concierge dispatch loop) |
| [25-8](25-8-solver-runtime.md) | Solver Runtime | Solver-first control loop: `Understand -> Plan -> Act -> Reflect`. Introduces `GoalResolver`, `PlanBuilder`, `SolverDecision`, and a deliverable-first execution choice that demotes classifier/handler routing into an optimization instead of the top-level brain. | ~4 days | 25-6, 25-7 |
| [25-9](25-9-workflow-memory-and-reuse.md) | Workflow Memory & Reuse | Make saved workflows and experience a semantic knowledge base for planning: retrieve similar workflows, choose reuse vs. adapt vs. build, and learn from task corrections. | ~3 days | 25-8 |
| [25-10](25-10-execution-selector.md) | Execution Selector | Map `SolverDecision` outputs into concrete execution backends: direct actions, workflow reuse/adaptation/build, run/status/publish operations, and meta-orchestrator delegation. Preserve deterministic prechecks and shared backends. | ~3 days | 25-8, 25-9 |
| [25-11](25-11-fallback-and-completion-policy.md) | Fallback & Completion Policy | Encode the "never just stop" runtime contract: alternate-path fallback, useful-subset delivery, build-capability fallback, human-action scaffolding, and terminal outcomes that always move the user toward done. | ~2 days | 25-8, 25-10 |

## Dependencies / Sequencing

```
25-1 (Capability Router) ← foundational, start here
  ├→ 25-2 (Experience in Chat) ← needs router to dispatch memory/discovery queries
  ├→ 25-3 (Publish & Share) ← needs router to dispatch publish/share intents
  ├→ 25-4 (Run Lifecycle) ← needs router to dispatch run control intents
  ├→ 25-5 (Auto-Approve & Undo) ← can start early, but router determines mutation flow
  ├→ 25-6 (Concierge Runtime) ← transitional foundation above all of the above
  ├→ 25-7 (Behavior Policy) ← policy substrate for concierge execution
  ├→ 25-8 (Solver Runtime) ← solver-first brain above the concierge foundation
  ├→ 25-9 (Workflow Memory & Reuse) ← semantic reuse flywheel for solver planning
  ├→ 25-10 (Execution Selector) ← turns solver decisions into concrete execution
  └→ 25-11 (Fallback & Completion Policy) ← "never just stop" runtime contract
```

**Recommended sequence:**
1. **25-1** first — defines the intent classification and tool registry that everything else uses.
2. **25-2, 25-3, 25-4** in parallel — each wraps an independent subsystem as chat tools.
3. **25-5** can start any time (it's a UX change to the mutation approval flow), but should finalize after 25-1 establishes the tool routing pattern.
4. **25-6** after 25-1–25-5 — establish the shared concierge foundation: project/task scope, fast-path routing, handler backends, and common surface wiring.
5. **25-7** after 25-6 — add shared policy, queue, progress, and promotion infrastructure.
6. **25-8** after 25-6/25-7 — replace classifier-first top-level routing with the solver-first `Understand -> Plan -> Act -> Reflect` loop.
7. **25-9, 25-10, 25-11** after 25-8 — make workflow memory first-class in planning, route solver decisions into concrete execution, and enforce productive fallback/completion behavior.

## Architecture: Capability Tool Registry + Solver Runtime

The Phase 15 architecture now has **2 active layers**, with the later solver runtime building on the concierge foundation rather than adding a third product tier:

1. **Capability layer** (25-1 through 25-5): `ChatCapabilityRegistry` + capability handlers wrap existing subsystems.
2. **Concierge + solver layer** (25-6 through 25-11): `25-6`/`25-7` establish shared project/task state, deterministic fast paths, and policy; `25-8+` replace classifier-first top-level routing with a solver-first loop that understands the goal, builds a plan, acts, and reflects.

The capability layer remains the foundation, but the target runtime for user-facing chat surfaces becomes solver-first while preserving the concierge foundation underneath:

```
┌─────────────────────────────────────────────────────────┐
│                    Chat Message                          │
│         (dan-chat / editor / Telegram / ...)            │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
           ┌──────────────────────────────┐
          │ Solver Runtime (25-8+)      │
          │ understand/plan/act/reflect │
           └──────────────┬───────────────┘
                          ▼
       ┌────────────────────────────────────────────────────┐
       │ Concierge Foundation (25-6 / 25-7)                │
       │ project/task state, fast prechecks, queue, policy │
       └──────────────┬─────────────────────────────────────┘
                      ▼
       ┌──────────┬──────────────┬──────────┬──────────────┐
       ▼          ▼              ▼          ▼              ▼
  File/Direct  Run/Status   Experience   Publish     ChatManager /
   backends      backends    retrieval    backends   GraphMutator /
                                                  MetaController
```

Within the capability layer, the LLM can still use function calling to invoke tools. But after `25-8`, the top-level question is no longer "which handler matches this utterance?" The solver runtime decides what the user actually needs, what deliverable to produce, whether to reuse/adapt/build a workflow, and which backend should execute the plan.

## Key Decisions

- **Capability layer stays thin; solver runtime becomes the top-level brain.** The capability handlers from 25-1 through 25-5 remain thin wrappers around existing subsystems. `25-6`/`25-7` keep shared state and policy, but `25-8+` shift top-level control to a solver-first loop: understand the goal, produce a plan, act, and reflect.
- **Simple direct tasks stay first-class.** DAN should still solve one-shot useful tasks directly before workflow build: file lookup, PDF review/summarization, quick fact lookup, stock price, short drafting, and similar requests. The difference is that `25-8+` make this a planning decision rather than just a classifier branch.
- **Workflow memory is a planning input, not just history.** Saved workflows and past experiences should be retrieved semantically and used to choose reuse, adaptation, or new build before reasoning from scratch.
- **`25-6` and `25-7` are transitional, not throwaway.** They remain the runtime substrate for state, queueing, progress, safety, and shared surface behavior. `25-8+` build on that substrate instead of replacing it wholesale.
- **Thin wrappers, not new logic.** Each chat tool is a 20-50 line wrapper that calls an existing subsystem method and formats the result for chat display. If a capability requires >100 lines of new logic, it's scope creep.
- **Shared capability layer, thin surface adapters.** Capability handlers stay surface-agnostic, but CLI/editor/messaging clients still need small integration work to render tool results, subscribe to run streams, and relay chat replies into `ChatManager`.
- **Mutation tools unchanged.** The existing `plan_graph_mutations` tool and `GraphMutator` pipeline continue to handle graph editing. New tools handle non-mutation capabilities.
- **No new public chat API surface.** Capabilities and concierge both stay behind the existing `/api/chat/message` flow. The server-side implementations call existing internal APIs (RunManager, ExperienceStore, etc.) directly — no HTTP hop.
- **Progressive rollout.** 25-1 through 25-5 can operate as the capability foundation; 25-6 and 25-7 then change the routing behavior above them. During rollout, disabling concierge should fall back to the old ChatManager-first path for compatibility.
- **Auto-approve stays client-driven.** The server should keep returning `ChatMutationEvent` proposals so clients can preserve local undo/history invariants; CLI/editor can auto-apply by default, while messaging surfaces stay confirm-first.
- **Never just stop.** The final runtime contract should always attempt alternate paths, useful subsets, capability-building, or human-action scaffolding before asking for help. The user should always get a result or the smallest possible unblock, not a generic limitation statement.

## Success Criteria

- **Workflow-facing CLI capabilities accessible from chat.** User can accomplish the tasks currently handled by `dan-run`, `dan-status`, `dan-logs`, `dan-publish`, `dan-blocks`, and workflow-management portions of `dan-chat` without leaving the chat session.
- **Cross-surface consistency.** Same intent in `dan-chat`, editor ChatPanel, and Telegram produces the same result (modulo rendering differences).
- **No new subsystems.** Every capability tool is a wrapper around an existing API. Total new code < 1000 lines across all sub-plans.
- **Latency acceptable.** Tool-call routing adds < 500ms over direct subsystem access (dominated by LLM inference, not wrapper overhead).
- **Existing behavior preserved.** Current mutation flow, run execution, and chat modes continue to work. New capabilities are additive.

## Notes

- The `ChatManager.send_message_with_tools()` already supports multiple tools via `MUTATION_TOOL_SCHEMA`. Adding more tools is a schema extension, not an architecture change.
- `DiscoveryService.discover_all()` already aggregates tools, skills, patterns, and workflows. The experience-in-chat tool (25-2) is primarily about making this queryable from chat, not building new discovery logic.
- The `DanClient` (23-2) provides the server abstraction layer. CLI and adapters that use `DanClient` will automatically gain chat capabilities if they route through the chat API — but direct `DanClient` methods (dispatch, cancel) remain available for non-chat use cases.
- Messaging adapters currently only render HumanNode prompts. Phase 15 makes them full chat clients: incoming adapter messages need session/thread mapping, chat API submission, and run-event forwarding. The capability handlers remain shared, but the adapter bridge work is real and belongs explicitly in the Phase 15 implementation.
