# 41-2: Agent Runtime Extraction

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** not-started
**Goal:** Extract DAN's reusable single-agent loop from `ChatManager` into an explicit `agent_runtime` module so chat surfaces become adapters instead of the primary owners of agent behavior.

## Context

`ChatManager` already acts as the de facto agent runtime for several behaviors:

- prompt assembly
- history and memory handling
- tool-loop execution
- streaming and non-streaming agent turns
- workflow-build and intent-driven generation helpers

The problem is not that these capabilities exist; it is that they are still anchored to a server-facing chat manager module rather than a reusable runtime contract.

## Tasks

### 1. Define the agent-runtime contract
- [ ] 1-1. Define the minimal runtime/session types needed for one agent turn: request, state, events, tool budget, and result.
- [ ] 1-2. Separate pure agent concerns from transport concerns such as HTTP channel IDs, thread storage, or UI-specific event formatting.
- [ ] 1-3. Decide what belongs inside the base agent loop vs stage/profile-specific overlays.

### 2. Move reusable logic out of `ChatManager`
- [ ] 2-1. Extract prompt assembly, context loading, and memory injection into `agent_runtime`. This includes the prompt modules in `src/dan/server/chat/` (prompts, context, overlays) which are deeply intertwined with ChatManager but are really agent concerns.
- [ ] 2-2. Extract the generic streaming loop and non-streaming completion loop into reusable runtime methods.
- [ ] 2-3. Extract the generic capability-tool loop and post-tool follow-up behavior into runtime-owned helpers.
- [ ] 2-4. Keep workflow-build-specific helpers either behind explicit agent profiles or in a dedicated adapter layer, rather than leaving them mixed into the base runtime.
- [ ] 2-5. Decide the boundary for `capability_handlers.py` (~76KB): which capability-handler logic is reusable agent behavior (tool dispatch, execution helpers) vs surface-adapter logic (HTTP-specific, UI-specific formatting). Document the split explicitly.
- [ ] 2-6. Decide where `ChatManager`'s 21 `meta/` imports land. Currently `ChatManager` directly calls intent extraction, compilation, diagnosis, graph quality, planner, and codegen from `dan.meta`. These are workflow-generation concerns, not base agent-runtime behavior. Options: (a) move them into a "workflow-build" agent profile that the base runtime dispatches to, (b) keep them as surface-adapter calls in the thinned `ChatManager`, or (c) route them through a `meta/` facade that `agent_runtime` profiles can call without importing server. Document the decision.

### 3. Make `ChatManager` a surface adapter
- [ ] 3-1. Keep `ChatManager` responsible for chat-store integration, workflow/thread context loading, and event serialization.
- [ ] 3-2. Replace direct execution ownership with calls into the extracted runtime.
- [ ] 3-3. Preserve current external server/router contracts while the internal ownership shifts.

### 4. Define agent profiles / operating styles explicitly
- [ ] 4-1. Introduce explicit agent profiles or stage overlays for direct-task, build, planning, and debug behavior instead of encoding them as a growing set of special branches.
- [ ] 4-2. Keep the base runtime small: one loop, one model-call path, explicit hooks for memory/tools/prompt overlays.
- [ ] 4-3. Document what future specialized agents should extend vs what they must not reimplement.

### 5. Add compatibility and parity tests
- [ ] 5-1. Preserve existing `send_message()` and `send_message_with_tools()` behavior through regression coverage.
- [ ] 5-2. Add tests proving agent-runtime behavior can be exercised without booting the full server stack.
- [ ] 5-3. Add explicit regressions around workflow-build turns so the extraction does not degrade mutation/build behavior.

## Primary Files

- `src/dan/server/chat_manager.py` — the main extraction target (~5700 lines, ~270KB)
- `src/dan/server/chat/` — prompt assembly, context modules, overlays (agent concerns currently anchored to server)
- `src/dan/server/capability_registry.py` — tool/capability registration (~6.5KB)
- `src/dan/server/capability_handlers.py` — tool dispatch and execution helpers (~76KB); straddles agent-runtime/surface-adapter boundary
- `src/dan/server/chat_store.py` — chat persistence (~19KB); stays surface-side
- `src/dan/server/graph_mutator.py` — workflow-build mutation logic (~63KB); tightly coupled to ChatManager for build turns. Should be treated as a workflow-build agent profile/adapter, not base runtime.
- `src/dan/server/mention_resolver.py` — context mention resolution (~24KB); candidate for agent-runtime context injection

## Decisions

- `ChatManager` remains as a compatibility entry point, but should stop being the true owner of reusable agent behavior.
- Transport-specific concerns are adapters; the extracted runtime should stay testable without HTTP, websocket, or editor context.

## Notes

- This sub-plan should avoid rewriting the world at once. The first good milestone is simply: `ChatManager` delegates most of a turn to a dedicated runtime object.
- `graph_mutator.py` is the largest file that straddles the agent-runtime/surface boundary. Its coupling to ChatManager build turns makes it a natural candidate for a "workflow-build agent profile" rather than base runtime. The extraction plan should explicitly decide where mutation logic lands.
- `src/dan/server/chat/` prompt modules are the second-largest cluster of agent-specific logic outside ChatManager itself. They should move together with prompt assembly into `agent_runtime`.
