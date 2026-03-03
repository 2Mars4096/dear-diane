# 16-3: HumanNode Generalization

**Parent:** [16-execution-primitives](16-execution-primitives.md)
**Status:** completed
**Goal:** Promote `HumanInTheLoopNode` to a first-class `HumanNode` with typed input/output schemas, multiple rendering modes (approval, form, free-text, selection, file-upload), a rendering surface abstraction that decouples the engine from the UI, and support for the "chat as renderer" pattern where the chat panel renders whichever HumanNode is active — enabling adjustable autonomy via graph topology rather than mode switches.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `HumanInTheLoopNode` | `models/control_flow.py` | `node_type: "human_in_the_loop"`, `prompt` (str), `timeout_seconds` (float\|None), `default_action` (str\|None) | No typed input/output schema; no rendering mode; single text prompt; no multi-field forms; no approval semantics |
| `HumanInTheLoopExecutor` | `executors/control_flow.py` | Awaits `context.human_input_callback(request_meta)` with prompt; handles timeout + default; merges response into outputs | Single callback interface; no rendering hint; no schema validation of human response; no multi-point coordination |
| `human_input_callback` | `engine/executor.py` `ExecutionContext` | `Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] \| None` — optional async callback | Return type is dict, but no schema enforcement; callback contract has no rendering mode dispatch |
| `HumanInputDialog.tsx` | `editor/src/components/` | Modal popup: shows prompt text, single textarea input, submit button | Text-only; no form fields; no approval buttons; no file upload; no selection UI |
| `RunManager` human input | `server/run_manager.py` | `_make_human_input_callback()` (private — creates per-run async callback), `submit_human_input()` (public — resolves pending request by `request_id`), `get_pending_human_inputs()` (public — lists unresolved requests), `_pending_human_inputs` dict mapping `request_id` → `asyncio.Event` | Functional but minimal; no rendering metadata or explicit prioritization for concurrent pending requests; callback uses `asyncio.Event` wait/set pattern |
| Architecture docs | `docs/architecture.md` §HumanNode | Full vision: typed I/O, chat-as-renderer, adjustable autonomy via topology, background mode = zero HumanNodes, multi-point interaction, rendering decoupling | **Prose-only** — none of the advanced features are implemented |
| `ChatPanel.tsx` | `editor/src/components/` | Chat sidebar with message send/stream, `@` mentions, mutation event handling | Not connected to HumanNode rendering; runs independently of graph execution |
| `OutputPreview.tsx` | `editor/src/components/` | Per-node output viewer with streaming text | Could display HumanNode prompts but currently only shows post-execution output |
| Engine event system | `engine/events.py` | `human_input_needed` event emitted by executor | Contains `prompt` and `request_id`; no rendering mode or schema info |
| WebSocket events | `server/run_manager.py` | Events multiplexed to WS subscribers; frontend receives and dispatches | HumanNode events reach frontend but only trigger basic modal |

## Tasks

- [x] 1. Generalize HumanNode model
  - [x] 1-1. Rename `HumanInTheLoopNode` to `HumanNode` with `node_type: Literal["human"]`. `HumanInTheLoopNode` kept as subclass with its own discriminator.
  - [x] 1-2. Add `input_schema` field (dict[str, Any] | None — JSON Schema for what the human receives).
  - [x] 1-3. Add `output_schema` field (dict[str, Any] | None — JSON Schema for what the human must provide).
  - [x] 1-4. Add `render_mode` field (Literal: `"text"`, `"approval"`, `"form"`, `"selection"`, `"file_upload"`, `"rich"`).
  - [x] 1-5. Add `options` field (list[str] | None) — for `"selection"` mode, the choices to present.
  - [x] 1-6. Add `instructions` field (str, default "") — guidance text shown above the input area.
  - [x] 1-7. Keep `prompt`, `timeout_seconds`, `default_action` for backward compatibility.

- [x] 2. Define rendering surface protocol
  - [x] 2-1. Create `HumanRenderRequest` dataclass in `engine/executor.py`.
  - [x] 2-2. Create `HumanRenderResponse` dataclass in `engine/executor.py`.
  - [x] 2-3. Define `HumanRenderer` protocol in `engine/executor.py`.
  - [x] 2-4. Add `human_renderer` to `ExecutionContext` with auto-wrap of legacy callback via `LegacyCallbackRenderer`.

- [x] 3. Update HumanNode executor
  - [x] 3-1. Renamed to `HumanNodeExecutor`; `HumanInTheLoopExecutor = HumanNodeExecutor` alias.
  - [x] 3-2. Builds `HumanRenderRequest` from node fields + incoming data.
  - [x] 3-3. Uses `context.human_renderer.render(request)` with legacy callback fallback.
  - [x] 3-4. Validates response against `output_schema` with up to 2 retries, then default/fail.
  - [x] 3-5. Emits enriched `human_input_needed` event with render_mode, schema, options.
  - [x] 3-6. Emits `human_input_received` event with source for audit.

- [ ] 4. Implement web UI renderers
  - [ ] 4-1. Refactor `HumanInputDialog.tsx` to dispatch on `render_mode`:
    - `text`: current textarea (preserve existing behavior).
    - `approval`: content display area + "Approve" (green) / "Reject" (red) buttons + optional comment field.
    - `form`: dynamically generate form fields from `output_schema` (text input for strings, number input for numbers, checkbox for booleans, dropdown for enums). Use schema `title`/`description` for labels/help text.
    - `selection`: radio buttons (single select) or checkboxes (multi-select based on schema) for `options` list.
    - `file_upload`: file drop zone + browse button; read file content and include in response.
    - `rich`: multi-message interface within the dialog (mini-chat).
  - [ ] 4-2. Show `instructions` as markdown-rendered body above the input area.
  - [ ] 4-3. Show `input_data` as formatted preview (collapsible JSON or rendered template) so the human can see what the graph is presenting.
  - [ ] 4-4. Add validation feedback: if `output_schema` validation fails on submit, show field-level error messages and prevent submission.

- [ ] 5. Implement chat-as-renderer pattern
  - [ ] 5-1. When a `HumanNode` with `render_mode="rich"` (or any mode) fires during execution, route the render request to the `ChatPanel` instead of (or alongside) `HumanInputDialog`. Display the prompt as a system message in the chat thread; the human's chat response becomes the HumanNode output.
  - [ ] 5-2. Add `render_target` field to `HumanNode`: `"dialog"` (default — popup modal), `"chat"` (render in chat panel), `"both"` (show in both). This controls where the interaction appears.
  - [ ] 5-3. In `ChatPanel`, detect incoming `human_input_needed` events. When `render_target` includes `"chat"`, insert a special `HumanInputChatMessage` component that shows the prompt, input data, and an inline response area matching the `render_mode`.
  - [ ] 5-4. Wire chat response submission through `RunManager.submit_human_input()` to resolve the pending Future.
  - [ ] 5-5. Support multi-point interaction: when multiple `HumanNode`s fire during a single run (possibly at different times), queue them in the chat panel as sequential interaction points. Show progress indicator between interactions.

- [ ] 6. Implement CLI and programmatic renderers
  - [ ] 6-1. Create `CLIHumanRenderer` implementing `HumanRenderer` protocol (deferred — CLI not yet needed).
  - [x] 6-2. Created `AutoRenderer` in `engine/executor.py`.
  - [x] 6-3. Created `ProgrammaticRenderer` in `engine/executor.py`.
  - [ ] 6-4. Add `human_renderer` parameter to `Engine.__init__()` and `EngineConfig` (deferred — context-level support landed).

- [ ] 7. Extend builder API
  - [ ] 7-1. Add `wf.human(name, render_mode="text", prompt="", instructions="", output_schema=None, ...)` to `WorkflowBuilder`. Returns `NodeRef`.
  - [ ] 7-2. Add `wf.approval(name, prompt="Review and approve:", ...)` as shorthand for `wf.human(render_mode="approval")`.
  - [ ] 7-3. Add `wf.form(name, schema={...}, ...)` as shorthand for `wf.human(render_mode="form", output_schema=schema)`.
  - [ ] 7-4. Extend builder compiler/decompiler for `HumanNode`.

- [ ] 8. Extend markdown loader
  - [ ] 8-1. Support `type: human` in agent markdown. Frontmatter fields: `render_mode`, `output_schema`, `options`, `timeout_seconds`, `default_action`, `instructions` (or body as instructions). Example:
    ```
    ---
    type: human
    name: Review Draft
    render_mode: approval
    timeout_seconds: 300
    default_action: '{"approved": true}'
    ---
    Please review the following draft and approve or reject it.
    Pay attention to factual accuracy and citation completeness.
    ```
  - [ ] 8-2. Extend loader/compiler and decompiler for `HumanNode` markdown format.

- [ ] 9. Migration and backward compatibility
  - [ ] 9-1. `node_type: "human_in_the_loop"` in existing graph JSON auto-migrates to `"human"` on load. Emit deprecation warning. *(Deferred — both types coexist in Node union for now.)*
  - [x] 9-2. `HumanInTheLoopNode` class remains as subclass of `HumanNode`. Existing code continues to work.
  - [x] 9-3. Existing `human_input_callback` on `ExecutionContext` continues to work via `LegacyCallbackRenderer` adapter.
  - [ ] 9-4. Add migration helper in `dan.migration` that converts `"human_in_the_loop"` → `"human"` in graph dicts. *(Deferred.)*

- [ ] 10. Validation
  - [ ] 10-1. Extend `validate_graph()`: `HumanNode.render_mode="selection"` requires non-empty `options`. `render_mode="form"` requires `output_schema`. `render_mode="approval"` should have `output_schema` with `approved` boolean field (warn if missing).
  - [ ] 10-2. Validate `output_schema` is valid JSON Schema.
  - [ ] 10-3. Warn if HumanNode has no `timeout_seconds` and no `default_action` (execution could hang indefinitely in headless mode).

- [ ] 11. Tests and documentation
  - [x] 11-1. Unit tests: 40 tests in `tests/test_engine/test_human_node.py` — model serialization (4), backward compat (7), render models (4), LegacyCallbackRenderer (2), AutoRenderer (2), ProgrammaticRenderer (3), executor integration (14), scheduler registration (1), context auto-wrap (3).
  - [x] 11-2. Integration tests: text mode, approval (approve + reject), form (valid + invalid + no-default), selection, timeout (with/without default), no-renderer fallback, legacy callback, events, dynamic prompt.
  - [x] 11-3. Migration tests: discriminated union for both `"human"` and `"human_in_the_loop"`, `HumanInTheLoopNode` isinstance check, executor alias.
  - [ ] 11-4. Builder round-trip tests: `wf.human()` / `wf.approval()` / `wf.form()` → build → decompile → compare. *(Deferred — builder API not yet extended.)*
  - [ ] 11-5. Markdown round-trip tests: human agent `.md` → compile → decompile → compare. *(Deferred — loader not yet extended.)*
  - [ ] 11-6. Update `docs/architecture.md` §HumanNode. *(Deferred.)*
  - [ ] 11-7. Update `docs/llm-api-guide.md`. *(Deferred.)*
  - [x] 11-8. Update `docs/changelog.md`, `docs/todo.md`, and this plan.

## Primary Files

- `src/dan/models/control_flow.py` — `HumanNode` (generalized), backward-compat aliases
- `src/dan/models/graph.py` — update `Node` union (replace `HumanInTheLoopNode` with `HumanNode`)
- `src/dan/engine/executor.py` — `HumanRenderRequest`, `HumanRenderResponse`, `HumanRenderer` protocol, `LegacyCallbackRenderer`, `human_renderer` on `ExecutionContext`/`EngineConfig`
- `src/dan/executors/control_flow.py` — `HumanNodeExecutor` (generalized), backward-compat alias
- `src/dan/executors/__init__.py` — register for both `"human"` and `"human_in_the_loop"` node types
- `src/dan/engine/events.py` — enriched `human_input_needed` event data, `human_input_received` event
- `src/dan/migration/` — `"human_in_the_loop"` → `"human"` graph dict migration
- `src/dan/builder/builder.py` — `wf.human()`, `wf.approval()`, `wf.form()`
- `src/dan/builder/compiler.py` — compile human node declarations
- `src/dan/builder/decompiler.py` — decompile `HumanNode`
- `src/dan/loader/parser.py` — parse `type: human` frontmatter
- `src/dan/loader/compiler.py` — compile human agent to `HumanNode`
- `src/dan/loader/decompiler.py` — decompile `HumanNode` to markdown
- `editor/src/types/graph.ts` — update `HumanNode` TS type with new fields
- `editor/src/components/HumanInputDialog.tsx` — multi-mode rendering (approval, form, selection, etc.)
- `editor/src/components/ChatPanel.tsx` — chat-as-renderer integration
- `editor/src/components/ConfigPanel.tsx` — HumanNode config fields (render_mode, schema, options)
- `tests/test_models/` — HumanNode serialization + migration
- `tests/test_engine/` — HumanNode execution integration tests
- `tests/test_builder/` — human builder round-trip
- `tests/test_loader/` — human markdown round-trip

## Decisions

- **Subclass, not alias.** `HumanInTheLoopNode` is a subclass of `HumanNode` with its own `node_type: Literal["human_in_the_loop"]` discriminator. This allows both types in the Pydantic discriminated union without conflicting literals. Simpler than a validator that rewrites the type string.
- **Renderers in executor.py, not a separate module.** `HumanRenderRequest`, `HumanRenderResponse`, `HumanRenderer` protocol, and all renderer implementations live in `engine/executor.py` alongside `ExecutionContext`. Avoids circular imports and keeps the rendering surface protocol co-located with the context that uses it.
- **Schema validation with lightweight fallback.** `_validate_against_schema()` uses `jsonschema` when available, falls back to a simple required-key + type check. This avoids a hard dependency for a feature that many workflows won't use.
- **Rename with alias, don't deprecate.** `HumanInTheLoopNode` is a mouthful and doesn't match the architecture vision. `HumanNode` with `node_type: "human"` is cleaner. The old name remains as an alias for zero-breakage migration.
- **Rendering surface is a protocol, not a framework.** `HumanRenderer` is a single async method: `render(request) -> response`. Any surface that implements this contract works. The engine doesn't import UI code.
- **`render_mode` is a hint, not a contract.** The rendering surface should respect it but may fall back gracefully (e.g., CLI renders `"form"` as sequential prompts). The engine doesn't validate that the renderer matches the mode.
- **`output_schema` enables validation.** Unlike the current untyped callback, the generalized HumanNode can validate human responses against a JSON Schema — catching errors at input time rather than downstream. This is the same output normalization principle applied to human responses.
- **Chat-as-renderer is opt-in.** `render_target: "dialog"` preserves the current modal behavior. Users explicitly choose `"chat"` for the in-chat interaction pattern. This avoids surprising UX changes.
- **Background mode is zero HumanNodes.** Not a special engine mode — just a graph that has no HumanNode instances. `AutoRenderer` + `default_action` enables "semi-background" where human checkpoints have safe defaults.

## Notes

- Architecture docs (§HumanNode) already describe this vision in detail: "the human is a node in the graph", "chat is rendering", "adjustable autonomy is topology". This plan converts that prose into implementation tasks.
- The coding assistant PoC needs chat-as-renderer: Ask mode = one HumanNode for the question; Agent mode = periodic HumanNodes for approval; Debug mode = HumanNode for hypothesis confirmation. 16-3 is a direct enabler.
- Multi-point interaction (task 5-5) is a stretch goal — the core value is in typed schemas and render modes. Prioritize tasks 1-4 and 9, then 5-8.
- `ProgrammaticRenderer` (task 6-3) is essential for testing. Without it, every test that touches HumanNode needs mock callbacks. With it, tests declare responses upfront.
