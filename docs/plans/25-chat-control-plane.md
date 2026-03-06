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

## Dependencies / Sequencing

```
25-1 (Capability Router) ← foundational, start here
  ├→ 25-2 (Experience in Chat) ← needs router to dispatch memory/discovery queries
  ├→ 25-3 (Publish & Share) ← needs router to dispatch publish/share intents
  ├→ 25-4 (Run Lifecycle) ← needs router to dispatch run control intents
  └→ 25-5 (Auto-Approve & Undo) ← can start early, but router determines mutation flow
```

**Recommended sequence:**
1. **25-1** first — defines the intent classification and tool registry that everything else uses.
2. **25-2, 25-3, 25-4** in parallel — each wraps an independent subsystem as chat tools.
3. **25-5** can start any time (it's a UX change to the mutation approval flow), but should finalize after 25-1 establishes the tool routing pattern.

## Architecture: Capability Tool Registry

The core mechanism is a **ChatCapabilityRegistry** — a set of LLM function-calling tools that the ChatManager exposes alongside the existing `plan_graph_mutations` tool. Each capability is a thin wrapper around an existing subsystem:

```
┌─────────────────────────────────────────────────────────┐
│                    Chat Message                          │
│         (dan-chat / editor / Telegram / ...)            │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
              ┌────────────────┐
              │  ChatManager   │
              │  (LLM + tools) │
              └───────┬────────┘
                      │ function-calling
        ┌─────────────┼─────────────────────────┐
        ▼             ▼             ▼            ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ mutation  │ │ run_     │ │ search_  │ │ publish_ │
  │ _tools   │ │ workflow │ │ history  │ │ workflow │
  │ (exist.) │ │ (25-4)   │ │ (25-2)   │ │ (25-3)   │
  └──────────┘ └──────────┘ └──────────┘ └──────────┘
        │             │             │            │
        ▼             ▼             ▼            ▼
  GraphMutator  RunManager    Experience   PublishRuntime
                              Store/Index  BlockRegistry
```

The LLM decides which tool to call based on user intent. No separate "intent classifier" model — the same LLM that handles conversation also routes to capabilities via function calling. This is the pattern already proven by the mutation tool: ChatManager gives the LLM a tool schema, the LLM calls it when appropriate.

## Key Decisions

- **Function-calling routing, not a separate classifier.** The LLM already routes to `plan_graph_mutations` via function calling. We add more tools (run, experience, publish) to the same schema. The LLM picks the right tool based on context. This is simpler and more flexible than a separate intent classification model.
- **Thin wrappers, not new logic.** Each chat tool is a 20-50 line wrapper that calls an existing subsystem method and formats the result for chat display. If a capability requires >100 lines of new logic, it's scope creep.
- **Shared capability layer, thin surface adapters.** Capability handlers stay surface-agnostic, but CLI/editor/messaging clients still need small integration work to render tool results, subscribe to run streams, and relay chat replies into `ChatManager`.
- **Mutation tools unchanged.** The existing `plan_graph_mutations` tool and `GraphMutator` pipeline continue to handle graph editing. New tools handle non-mutation capabilities.
- **No new API endpoints for chat capabilities.** Capabilities are exposed as LLM tool calls within the existing `/api/chat/message` flow. The server-side implementations call existing internal APIs (RunManager, ExperienceStore, etc.) directly — no HTTP hop.
- **Progressive rollout.** Each sub-plan adds tools independently. The system works with any subset enabled. Disable a tool = the LLM falls back to text-only responses for that capability.
- **Auto-approve stays client-driven.** The server should keep returning `ChatMutationEvent` proposals so clients can preserve local undo/history invariants; CLI/editor can auto-apply by default, while messaging surfaces stay confirm-first.

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
