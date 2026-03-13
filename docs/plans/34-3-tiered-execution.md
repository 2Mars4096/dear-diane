# 34-3: Tiered Execution

**Parent:** [34-tiered-async-dispatcher](34-tiered-async-dispatcher.md)
**Status:** in-progress
**Goal:** Three self-contained tier executors that own the full prompt → LLM → result flow. They call `chat_manager.send_message_with_tools()` directly — no handler wrappers, no solver, no executor indirection.

## Executor Protocol

```python
class TierExecutor(Protocol):
    async def execute(
        self,
        session: Session,
        manager: SessionManager,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Run the session and yield events. Update session state via manager."""
        ...
```

All three tiers implement this. The `TieredDispatcher` picks the right one based on `session.tier`.

## Tier 0 — Instant Executor

Handles: social turns, pending confirmations/clarifications, Tier-0 triaged messages.

No LLM call. No child sessions. Responds inline.

```python
class InstantExecutor:
    async def execute(self, session, manager):
        triage = session.triage

        if triage.is_social and triage.social_response:
            manager.update_state(session.id, COMPLETED)
            manager.set_result(session.id, SessionResult(content=triage.social_response))
            yield complete_event(triage.social_response)
            return

        if self._is_pending_response(session):
            result = self._resolve_pending(session)
            manager.update_state(session.id, COMPLETED)
            manager.set_result(session.id, result)
            yield complete_event(result.content)
            return
```

## Tier 1 — Single-Shot Executor

Handles: Q&A, file lookups, status checks, experience queries, simple direct tasks.

Builds a prompt from triage context, calls `chat_manager.send_message_with_tools()` once. No handler wrappers.

```python
class SingleShotExecutor:
    async def execute(self, session, manager):
        manager.update_state(session.id, RUNNING)

        # 1. Build system prompt from triage + gathered context
        system_prompt = self._build_prompt(session)
        messages = self._build_messages(session, system_prompt)

        # 2. Select tool set based on triage intent/route
        tools = self._select_tools(session)

        # 3. Call chat_manager directly
        async for event in self._concierge.chat_manager.send_message_with_tools(
            messages=messages,
            tools=tools,
            max_tool_turns=5,
        ):
            yield event

        # 4. Record turn + store memory
        self._record_result(session, manager)
```

### Prompt Building

The executor builds its own prompt from:
- Triage `goal` + `deliverable` (what the user wants)
- Gathered context (memory, files, domain — whatever `ContextGatherer` returned)
- Surface hints (from `msg.metadata.surface_context`)
- PII masking (applied before sending)
- Active project/task context

This replaces the scattered prompt assembly that was spread across `runtime.py`, `handlers.py`, `solver.py`, and `chat_manager.py`.

### Tool Selection

Based on `triage.intent` and `triage.route.action_hints`:
- `ask` intent: read-only tools (`file_read`, `web_search`, `web_fetch`, `list_directory`, `file_grep`)
- `agent` intent: all tools including `file_write`, `python_eval`, `shell_command`
- `plan` intent: planning tools + read-only tools

The tool set is built from the registered capability tools — not hardcoded.

## Tier 2 — Recursive Multi-Step Executor

Handles complex tasks by either executing directly (with the tool loop) or decomposing into child sessions.

### Decision: Decompose vs. Execute Directly

```python
class MultiStepExecutor:
    async def execute(self, session, manager):
        manager.update_state(session.id, RUNNING)

        if self._should_decompose(session):
            async for event in self._decompose_and_execute(session, manager):
                yield event
        else:
            async for event in self._execute_directly(session, manager):
                yield event
```

### When to Decompose

Decompose when:
- Triage provided `subtasks` (len > 1) and we haven't hit max depth
- The task requires heterogeneous actions (research + analysis + writing)
- Can spawn children (budget not exhausted)

Execute directly when:
- Single coherent task (even if multi-tool)
- Already at max depth
- Budget exhausted
- Task is a leaf action (search, read, compute, write)

### Decomposition Flow

```python
async def _decompose_and_execute(self, session, manager):
    subtasks = session.triage.subtasks if session.triage else []
    if not subtasks:
        subtasks = await self._plan_decomposition(session)

    children = []
    for task_desc in subtasks:
        child_tier = self._estimate_child_tier(task_desc)
        if not manager.can_spawn_child(session.id):
            break
        child = manager.create_child(
            parent_id=session.id,
            task=task_desc,
            tier=child_tier,
            task_context=self._build_child_context(session),
        )
        children.append(child)

    manager.update_state(session.id, WAITING)

    if session.child_execution == "parallel":
        async for event in self._run_children_parallel(children, manager):
            yield event
    else:
        async for event in self._run_children_serial(children, manager):
            yield event

    child_results = {c.id: manager.get(c.id).result for c in children}

    manager.update_state(session.id, RUNNING)
    final = await self._synthesize(session, child_results)
    manager.update_state(session.id, COMPLETED)
    manager.set_result(session.id, final)
    yield complete_event(final.content)
```

### Direct Execution (Leaf Node)

When a Tier 2 session executes directly, it's the same as Tier 1 but with a bigger tool budget:

```python
async def _execute_directly(self, session, manager):
    system_prompt = self._build_prompt(session)
    messages = self._build_messages(session, system_prompt)
    tools = self._all_tools()

    async for event in self._concierge.chat_manager.send_message_with_tools(
        messages=messages,
        tools=tools,
        max_tool_turns=20,
    ):
        yield event

    self._record_result(session, manager)
```

### Running Children

Children are full sessions. The dispatcher recursively calls the appropriate tier executor:

```python
async def _run_child(self, child, manager):
    executor = self._dispatcher.get_executor(child.tier)
    async for event in executor.execute(child, manager):
        if self._should_surface_to_parent(event):
            yield event

async def _run_children_parallel(self, children, manager):
    tasks = [self._run_child(child, manager) for child in children]
    async for event in merge_async_iterators(*tasks):
        yield event

async def _run_children_serial(self, children, manager):
    for i, child in enumerate(children):
        if i > 0:
            prev_result = manager.get(children[i-1].id).result
            child.task_context["previous_result"] = prev_result.content if prev_result else ""
        async for event in self._run_child(child, manager):
            yield event
```

### Synthesis

LLM call to combine child results into a final answer:

```python
async def _synthesize(self, session, child_results):
    results_text = "\n\n".join(
        f"## {cid}\n{r.content}" for cid, r in child_results.items() if r
    )
    messages = [
        {"role": "system", "content": f"Combine these results into a final answer for: {session.task}"},
        {"role": "user", "content": results_text},
    ]
    response = await self._concierge.chat_manager.provider.complete(messages)
    return SessionResult(content=response.text)
```

## Context Flow

Context flows **down** via `task_context`:
```
Root session: original message, full triage, user profile, memory context
  ├── Child: task description, relevant subset of memory, parent's partial results
  │     └── Grandchild: narrower task, parent's artifacts
  └── Child: different task, same memory subset, sibling's results (if serial)
```

Results flow **up** via `SessionResult.child_results`:
```
Grandchild completes → result stored
  ▲ Parent reads child results, synthesizes
Root reads all child results, produces final answer
```

## Current Entanglements in `tier_executors.py`

These imports from deleted modules must be removed during the rewrite:

| Line | Import | Resolution |
|------|--------|------------|
| 19 | `from .classifier import ClassificationResult, IntentCategory, RouteDecision` | Delete `ClassificationResult`. Import `IntentCategory`, `RouteDecision` from `.models` (relocated). |
| 20 | `from .handlers import HandlerRegistry, HandlerResult` | Delete entirely. The rewrite removes handler delegation — executors call `chat_manager.send_message_with_tools()` directly. |

After the rewrite, `tier_executors.py` should have **zero** imports from any deleted module. It imports from:
- `.models` — `SurfaceMessage`, `IntentCategory`, `RouteDecision`, `ResolvedContext`
- `.session` — `Session`, `SessionManager`, `SessionState`, `SessionResult`, `SessionTier`
- `.triage` — `TriageResult`
- No other concierge modules.

## Tasks

- [x] 1. `TierExecutor` protocol *(exists)*
- [x] 2. `InstantExecutor` (Tier 0): social response, pending follow-up *(exists)*
- [ ] 3. **Rewrite `SingleShotExecutor`** — call `chat_manager.send_message_with_tools()` directly, build prompt from triage context, select tools from registry. Remove ALL handler delegation (`HandlerRegistry`, `HandlerResult`).
- [ ] 4. **Rewrite `MultiStepExecutor`** — same direct-call pattern for leaf execution. Remove `ClassificationResult` usage. Decomposition and child management stays similar.
- [ ] 5. **Prompt builder** — shared utility for building system prompts from triage + gathered context + surface hints. Replaces the scattered prompt assembly across 4 files.
- [ ] 6. **Tool selector** — build tool set from registered capabilities + triage intent. Replaces hardcoded tool lists.
- [ ] 7. `_plan_decomposition` — real LLM call (currently a stub)
- [ ] 8. `_synthesize` — real LLM call (currently a stub)
- [x] 9. `_run_children_parallel` / `_run_children_serial` *(exists)*
- [x] 10. `_estimate_child_tier` *(exists)*
- [x] 11. Progress event emission *(exists)*
- [x] 12. Error handling: child failure → parent decides *(exists)*
- [x] 13. Cancellation propagation *(exists)*
- [ ] 14. Terminal event guarantee — every executor path must produce exactly one terminal `chat_complete` or `chat_interrupted`
- [ ] 15. **Clean imports** — remove ALL imports from `classifier.py` and `handlers.py`. After rewrite, no deleted module appears in the import list.
- [ ] 16. Update tests for direct-call pattern

## Decisions

- **No handler wrappers.** Tier 1/2 executors build prompts and call `chat_manager` directly. `handlers.py` is deleted.
- **No solver.** Tier 2 decomposition replaces the solver's goal/plan extraction. `solver.py` is deleted.
- **No executor.py.** The tier executors ARE the executors. `executor.py` is deleted.
- Decomposition planning uses a simple structured-output LLM call: "break this into subtasks".
- Synthesis is a lightweight LLM call: "combine these results".
- Tool selection is registry-driven, not hardcoded.
- Guard checks (if any) are the Tier 2 executor's responsibility — but we start without guards and add only if needed.
- Serial children are created lazily so cancellation stops additional fan-out.

## Notes

- `merge_async_iterators` interleaves events from multiple async generators. `fan_out.py` has the parallel utility.
- The recursive structure is self-similar: a Tier 2 executor at depth 3 works exactly the same as at depth 0.
- Tier 1 and Tier 2 leaf execution must guarantee a terminal event even if the underlying `send_message_with_tools()` stream ends silently.
