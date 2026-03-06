# 25-1: Capability Router

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Build the intent classification and capability routing layer so the LLM can route user intents to the correct subsystem via function-calling tools, enabling chat as the unified control plane.

## Design

### Routing Architecture

No separate intent classifier model. The LLM that handles conversation also routes to capabilities via **function-calling**. The ChatManager exposes multiple tools alongside the existing `plan_graph_mutations` tool. The LLM picks the right tool based on user message and context.

```
User message → ChatManager.send_message_with_tools()
  → provider.complete(messages, tools=[...all schemas...], tool_choice="auto")
  → LLM returns tool_call(name, args) or text
  → Dispatcher routes tool_name → handler(args, context)
  → Handler calls subsystem (RunManager, ExperienceStore, etc.) and formats result
  → Result fed back to LLM or streamed to user
```

### Tool Schema Design

Each capability is a **thin wrapper** with a JSON schema matching the existing `MUTATION_TOOL_SCHEMA` pattern in `chat_manager.py`:

```python
{
    "type": "function",
    "function": {
        "name": "search_workflow_history",
        "description": "Search past workflows and related experience. Use when the user asks 'have we done X?', 'similar workflows', or 'past work'.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "description": "Max results", "default": 5}
            },
            "required": ["query"]
        }
    }
}
```

Tools are registered in a **ChatCapabilityRegistry** that:
- Holds `{tool_name: (schema, handler_fn)}` mappings
- Exposes `get_tools(mode: str) -> list[dict]` for mode-aware filtering
- Returns schemas in OpenAI/Anthropic function-calling format

### Mode-Aware Tool Availability

| Mode | mutation tools | experience/discovery | run read | run write | publish read | publish/write/export | graph read |
|------|----------------|----------------------|----------|-----------|--------------|----------------------|------------|
| ask  | no             | yes                  | yes      | no        | yes          | no                   | yes        |
| plan | no             | yes                  | yes      | no        | yes          | no                   | yes        |
| debug| yes            | yes                  | yes      | limited   | yes          | no                   | yes        |
| agent| yes            | yes                  | yes      | yes       | yes          | yes                  | yes        |
| build| yes            | yes                  | yes      | yes       | yes          | yes                  | yes        |

`ask` and `plan` stay read-only. `debug` may allow selected write actions such as retrying or rerunning, but should not publish/export by default.

## Tasks

### 1. Define ChatCapabilityRegistry and tool schemas

- [x] 1-1. Create `src/dan/server/capability_registry.py` with `ChatCapabilityRegistry` class
  - `register(name, schema, handler, modes: list[str])` — modes = which chat modes get this tool
  - `get_tools(mode: str) -> list[dict]` — returns schemas for the given mode
  - `get_handler(name: str) -> Callable | None`
- [x] 1-2. Define registry primitives in `capability_registry.py` (or `capability_schemas.py`):
  - Shared schema builder helpers so each sub-plan can register tools consistently
  - Category-aware registration (`experience`, `run`, `publish`, `mutation`, `graph`)
  - Mode filters per tool (`ask`, `plan`, `debug`, `agent`, `build`)
  - Base read-only utility tools needed immediately for integration smoke tests:
    - `list_graphs` — → `GraphStore.list_graphs`
    - `get_activity` — → `ActivityTracker.get_activity`
  - Leave domain-specific tool definitions to 25-2 / 25-3 / 25-4 so names and params stay aligned with those plans
- [x] 1-3. Use JSON schema format matching `MUTATION_TOOL_SCHEMA` in `chat_manager.py` (lines 95–265): `type: function`, `function.name`, `function.description`, `function.parameters`

### 2. Extend ChatManager to support multiple tool schemas

- [x] 2-1. In `send_message_with_tools()`, replace `tools=[MUTATION_TOOL_SCHEMA]` with `tools=registry.get_tools(mode)` (include MUTATION_TOOL_SCHEMA when mode allows mutations)
- [x] 2-2. Add tool execution dispatcher: when `result.tool_calls` contains any tool:
  - For `plan_graph_mutations`: keep existing flow (dry_run, ChatMutationEvent, etc.)
  - For other tools: call `registry.get_handler(tool_name)(workflow_id, args, context)` and format result
- [x] 2-3. Handle multiple tool calls in one response: iterate `result.tool_calls`, execute each, append tool results to messages, optionally re-call LLM if needed (or return combined result)
- [x] 2-4. Inject `ChatManager` dependencies (GraphStore, RunManager, etc.) into handlers via a `CapabilityContext` object passed at call time, not stored globally in the registry
- [x] 2-5. Update chat API mode gating in `app.py`: replace the current hard block (`use_tools=False` for `ask`/`plan`) with mode-aware read-only tool availability so `ask`/`plan` can call safe capability tools while still blocking mutation/publish-write actions

### 3. Implement the shared handler contract

- [x] 3-1. Create `src/dan/server/capability_handlers.py` with the common handler signature: `async def handler(args: dict[str, Any], context: CapabilityContext) -> CapabilityResult`
- [x] 3-2. Define `CapabilityResult` shape (`success`, `message`, `data`, `output_preview`, `stream_channel_id?`) so all sub-plans return a consistent payload to `ChatManager`
- [x] 3-3. Implement only the base smoke-test handlers here:
  - `list_graphs` → `GraphStore.list_graphs()`
  - `get_activity` → `ActivityTracker.get_activity()`
- [x] 3-4. Leave experience/search, publish/export, and run-control handlers to 25-2 / 25-3 / 25-4
- [x] 3-5. Each handler should parse args, call one subsystem, catch exceptions, and return a `CapabilityResult` without surface-specific formatting

### 4. Intent-aware system prompt

- [x] 4-1. Extend `SYSTEM_PROMPT_TEMPLATE` and mode prompts to describe all available tools (not just plan_graph_mutations)
- [x] 4-2. Add a "## Available tools" section that lists tool names and when to use each (e.g. "search_workflow_history: when user asks about past workflows, similar runs, 'have we done X?'")
- [x] 4-3. Mode-specific tool descriptions: in `ask`/`plan`, state that only read-only tools are available

### 5. Surface-agnostic tool execution

- [x] 5-1. Ensure handlers take only `CapabilityContext` (workflow_id, graph_store, run_manager, etc.) — no request, session, or surface-specific params
- [x] 5-2. Handlers return `CapabilityResult` (or equivalent typed dict) — no HTTP or WebSocket-specific types
- [x] 5-3. ChatManager maps handler results to existing `ChatStreamEvent` types (e.g. `ChatToolCallResultEvent`, `ChatCompleteEvent`)

### 6. Streaming tool results

- [x] 6-1. For now, base capability tools return synchronously — no new streaming behavior in 25-1
- [x] 6-2. Define the contract for later streaming tools: handlers may return `stream_channel_id`, which `ChatManager` forwards to clients; 25-4 implements the first real use of this contract
- [x] 6-3. If a tool returns large payloads, truncate `output_preview` to ~500 chars and include full data in a follow-up message or `ChatCompleteEvent`

### 7. Error handling and fallbacks

- [x] 7-1. Wrap handler execution in try/except; on exception, return `{"success": false, "message": "User-friendly error: ..."}` (strip stack traces)
- [x] 7-2. Unknown tool name: log warning, return "I don't have that capability yet" — LLM can respond in text
- [x] 7-3. When LLM returns text only (no tool calls): existing flow unchanged — stream text, emit `ChatCompleteEvent`
- [x] 7-4. When LLM returns tool call for disabled tool (e.g. plan_graph_mutations in ask mode): return error message, let LLM retry or explain

### 8. Tests

- [x] 8-1. Unit tests for `ChatCapabilityRegistry`: register, get_tools(mode), get_handler
- [x] 8-2. Unit tests for base handlers in 25-1 (`list_graphs`, `get_activity`); domain-specific handler tests live in 25-2/25-3/25-4
- [x] 8-3. Integration test: ChatManager.send_message_with_tools with a message that should trigger `list_graphs`; assert tool call occurs and result is streamed
- [x] 8-4. Test mode filtering: ask mode does not receive plan_graph_mutations

## Files to Touch

| File | Action |
|------|--------|
| `src/dan/server/capability_registry.py` | Create — ChatCapabilityRegistry and schema helpers |
| `src/dan/server/capability_handlers.py` | Create — `CapabilityContext`, `CapabilityResult`, base handlers |
| `src/dan/server/chat_manager.py` | Modify — inject registry, multi-tool dispatch, extend prompts |
| `src/dan/server/app.py` | Modify — construct CapabilityContext with RunManager, ActivityTracker, ExperienceStore, DiscoveryService, GraphStore, BlockRegistry; pass to ChatManager |
| `tests/test_server/test_capability_registry.py` | Create — unit tests for registry and handlers |
| `tests/test_server/test_chat_capabilities.py` | Create — integration tests for tool routing |
| `docs/architecture.md` | Update — add CapabilityRegistry, CapabilityContext |
| `docs/cli.md` | Update — document chat behavior changes that are user-visible in `dan-chat` |

## Decisions

- (filled in during execution)

## Notes

- Sub-plans 25-2, 25-3, 25-4 add the domain-specific tool definitions and handlers. This plan establishes the registry, handler contract, mode policy, and dispatch pattern they plug into.
- `provider.complete(tools=[...])` accepts a list of schemas. OpenAI/Anthropic both support multiple tools; the LLM chooses which to call.
- `CapabilityContext` should be a dataclass or Pydantic model holding refs to subsystems. Constructed in `app.py` where all services are wired.
