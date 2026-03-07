# 25-6: Concierge Runtime — Transitional Project/Task Foundation

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Replace the current "everything goes through the LLM tool-calling pipeline" path with a shared concierge foundation that infers project/task context, handles deterministic fast paths, and exposes stable execution backends for later solver-first planning. Heavy multi-step goals can still delegate to the meta-orchestrator.

## Problem

Today every incoming message — whether "send me the late payment doc", "what's running?", or "build a lit review workflow" — lands in `ChatManager` with 24 capability tools + mutation tool. The LLM decides ad-hoc what to call. This produces:

1. **Inconsistent routing.** File requests trigger `search_workflow_history`; status queries produce capability tool markup instead of answers.
2. **No task separation.** One long chat thread mixes unrelated tasks (file request, then a workflow build, then a run check). History grows, context degrades.
3. **No parallel tasks.** If a workflow is running and the user asks about something else, the new message waits or clobbers the previous context.
4. **Wasted tokens.** Every message carries the full 24-tool schema + long history regardless of intent.

This plan still solves those problems, but it is now explicitly the **transitional substrate** for the later solver-first runtime in `25-8+`, not the final top-level router.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Incoming message (dan-chat / editor / Telegram / WhatsApp)  │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
              ┌────────────────────────┐
              │  SurfaceMessage (norm) │
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │ ProjectContextResolver │  ← infer or create project/task
              │  (summaries, active    │     from conversation context
              │   runs, mentions, sim) │
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │ Concierge Foundation   │  ← deterministic fast paths,
              │  (classify → backend)  │     state, and shared backends
              └───────────┬────────────┘
                          │
     ┌─────────┬──────────┬───────┬─────────┬──────┬─────────┬────────────┬─────────────┐
     ▼         ▼          ▼       ▼         ▼      ▼         ▼            ▼             ▼
FileHandler DirectTaskH  RunH   StatusH   ExpH  PublishH WorkflowBuildH ConversationH MetaGoalH
(local FS) (tools/LLM) (RunMgr)(Activity)(Experience)(Publish)(→ChatMgr) (→ChatMgr) (→MetaCtrl)
```

## Existing Infrastructure Used

| Component | How It's Used |
|---|---|
| `ChatManager` | Conversation, direct-task, and workflow-build handlers delegate to it when a plain answer is enough and no reusable workflow should be created |
| `CapabilityRegistry` / handlers | `StatusHandler`, `RunHandler`, `ExperienceHandler`, `PublishHandler` call the same 24 handler functions — they're already written |
| `_classify_adapter_intent()` | Promoted from adapter-local heuristic to shared concierge classifier |
| `_search_local_files()` | Promoted from adapter-local to `FileHandler` |
| `MetaController` | `MetaGoalHandler` delegates clearly open-ended multi-step goals to `MetaController.start_session()` |
| `ChatStore` / `ChatThread` | Existing threads remain for raw persistence; `ProjectStore` adds the project/task overlay for scoped context |
| `ConversationMemoryStore` | Project summaries written here for cross-session recall |
| `UserProfile` | User preferences consulted for default search dirs, autonomy overrides |
| `ActivityTracker` | `StatusHandler` reads active runs |
| `GlobalEventBus` | Progress events for active projects/tasks routed back to the originating surface |

## Key Decisions

- **2-layer scope model: Project + Task.** `Project` is the durable first-class scope — owns memory, summaries, history loading, linked workflows, and the reply label. `Task` is a lighter "current workstream inside the project" — just a label, status, and recent turns, without its own heavy persistence. This gives structure without making dynamic loading explode. (Decision from prior brainstorming chat.)
- **Reply label format:** `[DAN - <Project>]` by default. Expand to `[DAN - <Project> / <Task>]` only when the reply is clearly task-specific within a multi-task project. Omit the label entirely when only one project is active (avoid noise).
- **This plan is transitional, not the final router.** `25-6` establishes project/task state, deterministic fast paths, shared execution backends, and summary-scoped prompting. The later solver runtime in `25-8+` will replace classifier-first top-level control with `Understand -> Plan -> Act -> Reflect`.
- **Classifier stays as a fast-path optimization.** Heuristic rules and continuation matching remain useful here for status, file requests, run control, follow-ups, and obvious direct asks, but they are no longer the intended long-term "brain" of the system.
- **Project context is inferred, not explicit.** The user never needs to know project IDs. The concierge infers them from turn content, linked runs/workflows, and similarity to prior project summaries. When citing results, DAN uses the human-friendly project label, not a UUID.
- **One visible chat, many hidden project/task sessions.** `ProjectStore` keeps per-project state and summaries. When the user's message matches an existing project, the concierge loads that project's context (summary + recent task turns). New topics start a new project. Ambiguous? Ask once.
- **Summary-first memory loading.** For each project, load a compact summary + recent turns (not full history). The full thread stays on disk; the LLM only sees the summary + last N turns for the active task. This keeps context tight even when chat history is long. (User noted dynamic loading is hard; summaries solve this.)
- **Simple tasks should not build workflows by default.** The concierge should first try to solve a request as a direct task using existing tools and a light answer path: find a file, list PDFs in a folder, review/summarize one PDF, look up facts, get a stock price, or draft a short piece of text. Only route to `WorkflowBuildHandler` when the user explicitly asks for automation/reuse or the request clearly requires a reusable multi-step graph.
- **Heavy goals delegate to meta-orchestrator.** `MetaGoalHandler` owns clearly open-ended multi-step goals. `WorkflowBuildHandler` remains for workflow creation or mutation requests; if a request crosses into fully autonomous multi-stage work, routing should promote it into `meta_goal` and hand it to the meta-orchestrator.
- **Parallel project sessions allowed.** A running project/task doesn't block a new query. Status/progress checks on running work route through `RunHandler`/`StatusHandler` even mid-stream.
- **Adapter heuristic promoted.** `_classify_adapter_intent()` and `_search_local_files()` from `cli/adapter.py` become the shared `concierge.classifier` and `concierge.file_handler`, usable by all surfaces. *(Adapter-local versions already shipped as quick fixes: intent pre-routing, normalized filename matching, polite-send extraction, ask-mode tool-reference fix, docx MIME fix — see changelog 2026-03-06.)*
- **Execution backends should survive the migration.** `FileHandler`, `DirectTaskHandler`, `RunHandler`, `StatusHandler`, `ExperienceHandler`, `PublishHandler`, `WorkflowBuildHandler`, `ConversationHandler`, and `MetaGoalHandler` remain useful as shared actuators even after solver-first planning lands.

## Tasks

### 1. SurfaceMessage model + Project/Task store

- [x] 1-1. Create `src/dan/server/concierge/models.py`:
  - `SurfaceMessage(surface: str, external_id: str, text: str, attachments: list, timestamp: datetime, metadata: dict)` — normalized inbound envelope.
  - `Project(project_id: str, label: str, created_at: datetime, updated_at: datetime, status: Literal["active","paused","completed"], linked_workflow_ids: list[str], linked_run_ids: list[str], summary: str, tasks: list[Task])` — durable first-class scope that owns memory, summaries, and the reply label.
  - `Task(task_id: str, label: str, status: Literal["active","paused","completed"], turns: list[TaskTurn], created_at: datetime)` — lightweight current workstream inside a project. No separate heavy persistence.
  - `TaskTurn(role: str, content: str, timestamp: datetime, intent: str | None)`.
- [x] 1-2. Create `src/dan/server/concierge/project_store.py`:
  - `ProjectStore(base_dir)` — filesystem persistence (`~/.dan/projects/{surface_id}/{project_id}.json`).
  - `create_project(label, surface_id) → Project`.
  - `get_project(project_id) → Project | None`.
  - `list_active(surface_id) → list[Project]`.
  - `add_task(project_id, task_label) → Task`.
  - `get_current_task(project_id) → Task | None` — the most recently active task.
  - `append_turn(project_id, task_id, turn)`.
  - `update_project_status(project_id, status)`.
  - `update_project_summary(project_id, summary)`.
  - `search_projects(query, surface_id, limit) → list[Project]` — keyword search over project labels + summaries.
- [x] 1-3. Unit tests for models and store: create/read/append/search/status transitions, task lifecycle within project. 18+ tests.

### 2. ProjectContextResolver

- [x] 2-1. Create `src/dan/server/concierge/context_resolver.py`:
  - `ProjectContextResolver(project_store, activity_tracker, conversation_memory)`.
  - `resolve(msg: SurfaceMessage) → ResolvedContext(project: Project, task: Task, is_new_project: bool, is_new_task: bool, confidence: float)`.
  - Resolution strategy (in order):
    1. If msg contains explicit project reference (`/project <label>` or `[label]`), match by project label.
    2. If there's a pending `HumanNode` input, match to the project/task that owns that run.
    3. If msg is a continuation cue ("yes", "do it", "ok", number selection), match to the most recent active task in the most recent active project on that surface.
    4. If msg mentions a workflow name or run, match to the project linked to that workflow/run.
    5. Keyword similarity: compare msg against active project labels + summaries. Pick highest-scoring project if score > threshold. Within the matched project, pick the most relevant active task (or create a new one).
    6. Else: create a new project + task, auto-label from the first user turn (first 40 chars, slugified).
  - `_score_project_match(msg_text, project) → float` — token overlap on project label + summary + recent task labels, with recency bonus.
- [x] 2-2. Add `_generate_label(text) → str` — short, human-friendly label (e.g. `"lit-review"`, `"late-payment-file"`, `"vibe-research-run"`). Used for both project and task labels.
- [x] 2-3. Unit tests: continuation matching, explicit reference, workflow mention, new project creation, new task within existing project, ambiguous → new. 18+ tests.

### 3. Intent taxonomy + heuristic classifier

- [x] 3-1. Create `src/dan/server/concierge/classifier.py`:
  - `IntentCategory` enum: `file_request`, `direct_task`, `run_control`, `workflow_build`, `workflow_query`, `experience_query`, `publish_share`, `status_check`, `meta_goal`, `conversation`.
  - `ClassificationResult(intent: IntentCategory, confidence: float, param: str, raw_text: str)`.
  - `classify(text: str, context: ResolvedContext) → ClassificationResult`.
  - Promote and generalize `_classify_adapter_intent()` from `cli/adapter.py`.
  - Add `meta_goal` category: detected when text contains multi-step planning language ("build me a workflow that...", "create an end-to-end pipeline for...", "I want a system that...").
- [x] 3-2. Define keyword/pattern rules for each category:
  - `file_request`: "send me", "find", "do you have", "the ... doc/file/pdf", path-like tokens.
  - `direct_task`: "review this paper", "summarize this pdf", "list all pdfs starting with", "what's the stock price", "look up this fact", "draft a short email", "write a summary".
  - `run_control`: "run it", "cancel", "resume", "stop", "pause".
  - `status_check`: "what's running", "status", "progress", "how's it going".
  - `workflow_query`: "show me the workflow", "what does it do", "list workflows".
  - `experience_query`: "have we done", "similar to", "past work", "what did we learn".
  - `publish_share`: "publish", "share", "export", "send to".
  - `workflow_build`: "add a node", "change the prompt", "wire X to Y", simple mutations.
  - `meta_goal`: "build me", "create a workflow for", "I need a pipeline that", open-ended multi-step.
  - `conversation`: fallback when nothing else matches.
- [x] 3-3. LLM fallback: when confidence < 0.6, call LLM with a short intent prompt (intent list + user text) to pick one intent. No tool schema. Bias toward `file_request` / `direct_task` over `workflow_build` when the request is plausibly solvable in one shot.
- [x] 3-4. Unit tests: 40+ example messages across all categories, including edge cases (polite requests, follow-ups, ambiguous). Verify no file request ever routes to `workflow_build`.

### 4. Handler registry + handlers

- [x] 4-1. Create `src/dan/server/concierge/handlers.py`:
  - `HandlerResult(content: str, attachments: list[Path], project_update: dict | None, task_update: dict | None, stream_channel_id: str | None)`.
  - `Handler` protocol: `async def handle(msg: SurfaceMessage, context: ResolvedContext, classification: ClassificationResult) → HandlerResult`.
  - `HandlerRegistry` — maps `IntentCategory → Handler`.
- [x] 4-2. `FileHandler`:
  - Promote `_search_local_files()` and `_handle_find_command()` / `_handle_send_command()` from `cli/adapter.py`.
  - Search dirs configurable via `UserProfile.search_dirs` (default: `~/Dropbox`, `~/Documents`, `~/Desktop`).
  - Single match → auto-send. Multiple → list + "which one?". Zero → "not found".
  - Returns `HandlerResult` with `attachments=[path]` for single match.
- [x] 4-3. `DirectTaskHandler`:
  - Handles one-shot useful work without creating a workflow: review/summarize a resolved PDF, list files in a folder, answer factual lookup requests, quick stock-price/fact queries, short drafting tasks.
  - Uses existing tools first (`pdf_read`, file lookup, web/fact tools, simple `ChatManager.send_message()` in ask mode).
  - Explicit rule: do **not** propose workflow mutation unless the user asks for automation/reuse or the request becomes clearly multi-step.
- [x] 4-4. `RunHandler`:
  - Wraps `handle_start_run`, `handle_cancel_run`, `handle_resume_run`, `handle_submit_human_input` from `capability_handlers.py`.
  - Links the run to the current project and current task.
  - Returns `stream_channel_id` for run-event streaming.
- [x] 4-5. `StatusHandler`:
  - Wraps `handle_get_run_status`, `handle_list_active_runs`, `handle_get_activity`.
  - If asked about a specific project, scopes to that project's linked runs; if the user is clearly referring to a task inside that project, include task-specific progress details.
  - Compact format for messaging surfaces.
- [x] 4-6. `ExperienceHandler`:
  - Wraps `handle_search_workflow_history`, `handle_get_learned_principles`, `handle_discover_capabilities`.
- [x] 4-7. `PublishHandler`:
  - Wraps `handle_publish_workflow`, `handle_export_workflow`, `handle_share_workflow`.
- [x] 4-8. `WorkflowBuildHandler`:
  - For simple mutations: delegates to `ChatManager.send_message_with_tools()` with project summary + task-scoped history (not full thread).
  - For complex goals (classifier returned `meta_goal`): opens a `MetaSession` via `MetaController.create_session()` / `run_session()` and links the session to the project.
  - Links created/modified workflows to the current project and marks the task as the active workstream inside that project.
- [x] 4-9. `ConversationHandler`:
  - Delegates to `ChatManager.send_message()` (text-only path) with project summary + task-scoped history.
  - Minimal system prompt: "You are DAN. Answer the user's question about their workflows. Be concise."
- [x] 4-10. Unit tests for each handler (mock subsystem calls): 25+ tests total.

### 5. Concierge dispatch loop

- [x] 5-1. Create `src/dan/server/concierge/__init__.py`:
  - `Concierge(project_store, context_resolver, classifier, handler_registry, ...)`
  - `async def process(msg: SurfaceMessage) → AsyncIterator[HandlerResult | StreamEvent]`:
    1. `context = context_resolver.resolve(msg)` — returns `project` + `task`.
    2. `classification = classifier.classify(msg.text, context)`.
    3. `handler = handler_registry.get(classification.intent)`.
    4. `result = await handler.handle(msg, context, classification)`.
    5. Record turn in `project_store` under `project.task`.
    6. If task completed, update project summary and write to `ConversationMemoryStore`.
    7. Yield result (or stream events for long-running).
  - DAN cites project/task label per the 2-layer format.
- [x] 5-2. Reply label citation rules:
  - Multiple active projects → `[DAN - <Project>]` prefix on every response.
  - Single active project, multiple tasks → `[DAN - <Project> / <Task>]` only when task-specific.
  - Single active project, single task → no prefix (avoid noise).
- [x] 5-3. Integration tests: message → classify → dispatch → result, including (a) new project creation, (b) new task within existing project, and (c) cross-project switching. 12+ tests.

### 6. Wire into surfaces

- [x] 6-1. **`/api/chat/message` endpoint** — add an optional `concierge=true` query param (default `true`). When enabled, route through `Concierge.process()` instead of directly to `ChatManager`. When `concierge=false`, bypass for backward compatibility.
- [x] 6-2. **`dan-chat` CLI** — pass `thread_id=workflow_id` so server concierge maintains project context; local mode already routes through concierge via `LocalChatRuntime`.
- [x] 6-3. **Adapter chat mode (`cli/adapter.py`)** — route natural-language messages through server concierge (POST with `thread_id=external_id`); detect "Found file:" responses and send files; keep local handling only for explicit `/find`/`/send` and their number-reply follow-ups.
- [x] 6-4. **Editor ChatPanel** — no change to the WebSocket protocol; the server-side `/api/chat/message` change handles it transparently. Editor still sends messages to the same endpoint.
- [x] 6-5. Integration tests: verify `dan-chat`, adapter, and editor paths all route through concierge. 6+ tests.

### 7. Summary-first context loading

- [x] 7-1. When `ConversationHandler` or `WorkflowBuildHandler` delegates to `ChatManager`, pass **project summary + current task's recent turns (last 20)** as `history`, not the full thread history. The project summary is a 1-3 sentence synopsis maintained by `ProjectStore.update_project_summary()`.
- [x] 7-2. Inject project context as a system-level context block:
  ```
  Current project: {project.label}. Summary: {project.summary}
  Current task: {task.label}. Linked workflows: {project.linked_workflow_ids}
  ```
- [x] 7-3. For `meta_goal` handler: inject relevant experience (via `ExperienceIndex.search_similar(goal)`) and learned principles into the meta-orchestrator's planning context, scoped to the project.
- [x] 7-4. Auto-summarize: after every N turns (default 10) or on task completion, generate a 1-2 sentence task summary and fold it into the project summary. Use a lightweight LLM call with a 3-line prompt (not the full tool pipeline).
- [x] 7-5. Verify that `ChatManager._build_messages()` respects the reduced history without breaking existing context-window management.
- [x] 7-6. Unit tests: verify history is project/task-scoped, auto-summarization trigger, summary injection, and that cross-project history is never mixed into the active prompt. 10+ tests.

## Files

| File | Action |
|---|---|
| `src/dan/server/concierge/__init__.py` | Create — `Concierge` dispatch loop |
| `src/dan/server/concierge/models.py` | Create — `SurfaceMessage`, `Project`, `Task`, `TaskTurn` |
| `src/dan/server/concierge/project_store.py` | Create — `ProjectStore` |
| `src/dan/server/concierge/context_resolver.py` | Create — `ProjectContextResolver` |
| `src/dan/server/concierge/classifier.py` | Create — transitional fast-path classifier and continuation recognizer |
| `src/dan/server/concierge/handlers.py` | Create — handler protocol, registry, and shared execution backends |
| `src/dan/cli/adapter.py` | Modify — replace local heuristic with `Concierge.process()` |
| `src/dan/cli/chat.py` | Modify — route through concierge |
| `src/dan/server/app.py` | Modify — wire `Concierge` into `/api/chat/message` |
| `src/dan/server/chat_manager.py` | Modify — accept project/task-scoped history |
| `tests/test_concierge/` | Create — all test files |

## Already-Shipped Quick Fixes (from prior chat, 2026-03-06)

These adapter-local patches are the prototype for the shared concierge. They stay in `cli/adapter.py` until 25-6 replaces them:

- [x] `_classify_adapter_intent()` — heuristic pre-routing (file, continuation, number, workflow-build, conversation)
- [x] `_extract_search_query_from_send_request()` — polite file-send normalization
- [x] `_search_local_files()` — token-normalized filename matching (space/underscore/hyphen)
- [x] Ask-mode tool-reference injection fix (ChatManager no longer advertises 24 tools in ask/plan modes)
- [x] `.docx` MIME type fix in WhatsApp Web adapter

## Acceptance Criteria

- "Send me the late payment doc" **always** routes to `FileHandler`, never to `ChatManager` mutation tools.
- "Review the crogi pdf in that folder" routes to `FileHandler` or `DirectTaskHandler`, not `WorkflowBuildHandler`.
- "What's the stock price of NVDA?" or "draft a short reply to this email" routes to `DirectTaskHandler`, not workflow build.
- "What's running?" routes to `StatusHandler` and returns a compact answer in < 200ms (no LLM call).
- "Build me an end-to-end lit review pipeline" routes to `WorkflowBuildHandler` → `MetaController`.
- Two interleaved projects (a file request mid-workflow-build) resolve to separate project contexts and don't pollute each other's history.
- "Yes" / "do it" / "3" (number selection) route to the correct pending project/task's continuation.
- DAN cites `[DAN - <Project>]` when multiple projects are active; `[DAN - <Project> / <Task>]` when task-specific within a multi-task project.
- The LLM only sees the project summary + recent task turns, not the full chat history.
- Adapter, `dan-chat`, and editor all route through the same concierge — no surface-specific routing logic.

## Notes

- 2026-03-06 implementation snapshot: core concierge package landed (`models`, `project_store`, `context_resolver`, `classifier`, `handlers`, `runtime`, `policy`, `queue`, `progress`, `promotion`) with server/local wiring, shared adapter file-search helpers, persisted confirmation/clarification follow-up state, and `meta_goal` delegation through a dedicated `MetaGoalHandler`.
- As of the solver-first redesign discussion, this plan is now the **foundation plan** rather than the final router spec. The new top-level decision loop and workflow-memory-first planning move into `25-8` onward.
- Remaining gaps before this plan can be marked complete: LLM fallback classification, the dedicated `DirectTaskHandler` lane, richer meta-session event/progress streaming, true file attachment delivery, full reply-label policy, and full `dan-chat` / adapter routing through `Concierge.process()`.
- The existing 24 capability tools in `ChatCapabilityRegistry` remain available. `RunHandler`, `StatusHandler`, `ExperienceHandler`, and `PublishHandler` call the same underlying handler functions. The concierge foundation provides shared execution backends; later solver logic chooses when and why to invoke them.
- `_classify_adapter_intent()` in `cli/adapter.py` is the proven prototype. It handles file requests, continuations, and number selections correctly for WhatsApp. Promoting it to shared code is a refactor, not a rewrite.
- Project labels are auto-generated and short (`"late-payment-file"`, `"vibe-run-3"`). The user never needs to type them; they're for DAN to cite.
- The 2-layer model (Project + Task) is asymmetric on purpose: `Project` is the heavy durable scope with memory and summaries. `Task` is light — just a label, status, and recent turns inside the project. This avoids building two full persistence systems while still supporting "I'm working on the lit review" (project) and "now write the intro" (task within that project).
