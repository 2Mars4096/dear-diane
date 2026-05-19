# Deep Agent Network (DAN)

Typed graph orchestration for the hard 5% of long-running agentic tasks. Design persistent agent networks as directed graphs with typed edges, control-flow primitives, and heterogeneous models — then run them via Python, a visual editor, or (soon) markdown files.

DAN is aimed at deep work that ordinary single-agent copilots handle poorly: multi-stage research, long-running builds, high-trust workflows, and tasks that may genuinely benefit from hierarchical swarms of specialized workers. The goal is not to make everyday chat heavier. The goal is to make rare, high-value tasks tractable, inspectable, and repeatable.

## Who DAN Is For

- Long-running tasks that need planning, decomposition, tool use, checkpoints, and recovery over hours or days
- High-trust research and operational workflows where provenance, reviewability, and explicit control matter
- Problems that can be broken into many bounded workers, from a few specialists up to large hierarchical swarms when the task justifies it

## Who DAN Is Not For

- Everyday chat, shallow one-shot requests, or simple tasks a normal copilot can finish faster
- Flat “more agents = better” swarm setups without bounded roles, aggregation, or operator control
- Teams looking for the lightest possible AI wrapper rather than a durable workflow and execution system

## Key Concepts

- **Two-level nodes** — atomic operators (LLM call, tool call, code execution) and composite agents (sub-graphs that behave as single nodes with typed interfaces)
- **Worker-first compute surface** — new compute stages can be authored as `wf.worker(...)` with shared context/tool/memory refs, while pure control primitives stay explicit instead of being forced into one opaque super-node
- **Typed edges** — data (schema-validated), control (conditionals, loops, routing), and context (shared state)
- **Tiered handoff lint** — optional per-edge structural → semantic → intent validation with autofix blocks bad handoffs before downstream nodes consume them
- **Control-flow primitives** — GateNode (if/else + while loop), ForEach, Reduce, Router, Human-in-the-Loop
- **Hierarchical swarms** — scale from one agent to many bounded specialists when decomposition pays off; large swarms are useful only with supervision, aggregation, and recoverability
- **Model heterogeneity** — each operator independently specifies its model (cheap for classification, strong for reasoning)
- **Output normalization** — built-in parse → validate → re-prompt → retry on every LLM operator
- **Retry & fallback** — per-node `RetryPolicy` with exponential backoff, fallback models, and halt/skip/error failure modes
- **Multi-provider LLM** — built-in support for OpenAI, Anthropic, and Google; prefix-based routing (`gpt-*`, `claude-*`, `gemini-*`) with per-node model override, plus a shared provider-layer retry wrapper for transient API failures so agent surfaces do not each reinvent recovery logic
- **Built-in tools** — file I/O, deterministic workspace checks, web search/fetch, HTTP, shell commands, PDF reading, text chunking, JSON extraction, regex. Web fetch can optionally recover through DAN's persistent browser for JS-heavy or auth-gated pages; relative paths stay sandboxed to the workspace root, while explicit absolute paths are trusted and allowed. For untrusted LLM callers, keep inputs relative or add an approval layer
- **Checkpointing** — resume long-running workflows from the last completed level

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+ (for the visual editor)

### Install

```bash
git clone https://github.com/your-org/deep-agent-network.git
cd deep-agent-network

# Python package (editable)
pip install -e ".[dev]"

# Visual editor
cd editor && npm install && cd ..
```

### Configure

DAN is highly configurable via environment variables.

**Fastest path** — copy the minimal config (just API key + model):
```bash
cp .env.minimal .env
# Edit .env and fill in your API key
```

**Full config** — copy `.env.example` for all available options (model tiering, learning features, cost controls, MCP servers, sandbox settings, and more):
```bash
cp .env.example .env
```

**Quick Start Setup:**
To enable the best daily-use experience, uncomment these bundles in your `.env`:
```env
DAN_LEARNING_MODE=1      # Turn on all safe learning features (prompt optimization, memory, etc.)
DAN_FULL_TOOLS=1         # Expose the full built-in tool basket in chat (file ops, git, system)
DAN_ENABLE_TIER_POLICY=1 # Auto-assign models by task difficulty
```

For DAN control-plane migration testing, you can also set `DAN_CONTROL_PLANE=v1` or `DAN_CONTROL_PLANE=v2`. `v2` switches the server chat surface onto the new DAN-v2 top-level controller with Code, Research, and Incident Commander lanes. Inside DAN-v2, code turns and Incident Commander repair turns now use the dedicated coding organism directly by default instead of always falling through to the shared legacy substrate; if that direct runtime raises, the request falls back to the old handoff path. Use `DAN_V2_DIRECT_CODE_RUNTIME=0` to force the legacy handoff path globally, `surface_context["direct_code_execution"]=false` to disable the direct runtime for one surface/request, or `/api/chat/message` `control_plane_mode=v1|v2` to override the DAN-v1 vs DAN-v2 selector per request during migration testing. For staged rollout, remote/local chat clients now honor `DAN_CLI_CONTROL_PLANE` / `DAN_CHAT_CONTROL_PLANE`, adapter-originated chat now honors `DAN_ADAPTERS_CONTROL_PLANE` plus surface-specific env keys such as `DAN_TELEGRAM_CONTROL_PLANE` or `DAN_WECHAT_CONTROL_PLANE` whether it comes through the in-process bridge or a direct adapter POST path, the local in-process chat runtime now routes both chat and `/run` requests through the same shared router/stream seam instead of keeping its own legacy branch, and the server router itself now supports `DAN_<SURFACE_TYPE>_CONTROL_PLANE` plus `DAN_INTERNAL_CONTROL_PLANE` / `DAN_EXTERNAL_CONTROL_PLANE` so editor/browser-style chat can move ahead of messaging transports without per-caller patching.

Gateway-backed live/product surfaces now also share one in-process API dispatch broker. `dan code`, `dan research`, `dan reader`, `dan organism`, and live Super DAN turns all enter that same gateway-backed provider seam, so you can limit queue depth, in-flight calls, and optional request pacing centrally instead of patching each organism separately:

```env
DAN_LLM_GATEWAY_DISPATCH_ENABLED=1
DAN_LLM_GATEWAY_MAX_IN_FLIGHT=4
DAN_LLM_GATEWAY_MAX_QUEUE_SIZE=32
DAN_LLM_GATEWAY_MAX_REQUESTS_PER_SECOND=0
DAN_LLM_GATEWAY_QUEUE_TIMEOUT_SECONDS=30
# Optional: isolate one runtime onto its own broker group
# DAN_LLM_GATEWAY_DISPATCH_GROUP=default
```

Set `DAN_LLM_GATEWAY_MAX_REQUESTS_PER_SECOND` only when you want explicit pacing; `0` leaves pacing off while keeping the shared queue and in-flight cap.

### Run the Visual Editor

```bash
# Terminal 1 — backend
dan-serve
# or: python -m dan.server

# Terminal 2 — frontend (dev mode)
cd editor && npm run dev
```

Open `http://localhost:5173`. The editor connects to the backend at `localhost:8000`.

For the lighter Chat/Agent V2 frontend, open `http://localhost:5173/#v2`. It shows only `Chat` and `Agent`: Chat uses the V2 chat ingress, and Agent starts durable V2 Agent runs with live progress, `Fast` / `Balanced` / `Deep` / `Max` profiles, file/image/PDF attachment chips, explicit active-run queue gestures (`Enter` appends at the next checkpoint, `Cmd/Ctrl+Enter` queues after the current run), retry/open-log controls, active-run reconnect, and branch creation/lineage in the thread rail. The existing editor stays available at `#chat`.

**Chat: build + run a workflow** — Open a workflow tab, set chat to **Agent**, use `plan_graph_mutations` to edit, then ask the model to **`start_run`** with any `inputs` (e.g. `watchlist_path`). **Author equity/watchlist workflows in DAN (Agent chat), not by pasting large generated graphs/code from outside.** Prompts: [`docs/chat-equity-workflow-cookbook.md`](docs/chat-equity-workflow-cookbook.md).

### Run the Super DAN Organism

This is a Super DAN CLI for the "organized organism" story. In a terminal, omitting the objective starts a small interactive shell, similar to `dan code`; in non-interactive mode, live work requires an explicit objective instead of silently running the default target. With an objective and configured live model, Super DAN now enters the native model/tool execution lane automatically; `--live` remains available when you want to force that path, and `--plan-only` gives the deterministic coordination contract/showcase without live execution. Use `--cell-count 100 --organism-id super-dan-100` only when you want the larger showcase; scheduler wave sizing is internal and automatic.

Live Super DAN runs mutable workspace objectives through one generic workspace-deliverable lane in the current workspace, uses the shared `WorkerCoreExecutor` plus local tools to inspect/mutate files, passes the same ticket/packet/audit contract into the live worker and validator lanes, streams timestamped compact progress lines while the run is active, and persists a replayable `.dan-super/runs/turn-XX/events.jsonl` trace. The shared local runtime gives workers platform context and treats `shell_command` as the local command-line toolbox for terminal-native work such as tests, builds, project CLIs, filesystem transfers, checksums, archives, and large directory enumeration, while structured file tools remain preferred for small precise reads and edits. Shell commands now report workspace-change diffs, so successful terminal-native moves/removals/copies count as material mutations without parsing command text. Operator-supplied `--validation-command` checks run from the workspace before the model-only validator; nonzero exits and runtime/compiler error lines fail the run and feed the repair/final outcome even when the checked process exits 0. For broad objectives, Super DAN can first write temporary numeric plan files under `.dan-super/runs/turn-XX/plans/`, validate that plan, pass the first executable slice to the builder, and then audit any ticked plan checkboxes against actual deliverable changes; narrow edits and source-restricted requests still go straight to the builder. Live events now also feed an internal hook/inbox state projection under `.dan-super/state/`, where material writes, validation results, first-write recovery, repair completions, reader/scout completions, and stale heartbeats become durable packets with queue policy, lease, and owner-lock metadata. Before lane selection, Super DAN resolves an inspectable `IntentSignal` with operation, artifact target, mutation permission, confidence, rationale, and evidence. Hard operator workspace-access constraints such as "do not read other files" are carried as an operator-intent policy into worker/recovery/validator prompts and runtime tool approval, so lower layers cannot list, read, use git context, or reuse existing artifacts when that would violate the request. If that policy forbids other workspace inputs and the named target is missing, the generic lane starts from a prompt-only creation packet and a mutation-only first tool basket instead of spending its first response on workspace checks. Website objectives still default to an artifact directory named `website`; when you run in code-like mode with `--workspace ./website` and do not pass `--artifact-dir`, that workspace is treated as the website root instead of writing `website/website`. Existing artifact context participates as routing evidence, while explicit research-style requests still stay read-only. For explicit `--live` and interactive turns, the live context itself can supply mutation permission for ambiguous non-research artifact requests, so creation prompts such as `generate a playable animation` can route to the generic workspace lane without needing website/build wording. Vague continuation turns such as `keep patching this website` are expanded into a default maintainability patch brief for existing sites, with coordinated HTML/CSS/JS/README edits preferred for broad maintainability work and any prior validation feedback from `.dan-super` logs. Focused single-file website patches can pass when they materially satisfy the objective; final validation no longer uses a fixed two-file gate. Static anti-template failures name the exact matched phrases and feed those hits into the bounded repair prompt; if the one repair pass still fails, the final event is marked repair-exhausted so hook state does not advertise another repair that will not run.

```bash
dan super-organism
# or
dan super-organism "please build our product website with cool animation"
# or
dan super-organism --workspace ./website
# then type the objective at the super-dan> prompt
# or
dan super-organism "please build our product website with cool animation" --workspace ./website
# writes directly into ./website when a live model is configured
# or
dan super-organism "please build our product website with cool animation" --plan-only
# or
dan super-organism "please build our product website with cool animation" --verbose
# or
dan super-organism "please build our product website with cool animation" --live --model "$DAN_LLM_MODEL"
# or
dan super-organism "implement a small feature for this project" --live --model "$DAN_LLM_MODEL"
# or
dan super-organism "repair this project until tests pass" --live --validation-command "pytest -q"
# or
dan super-organism "please build our product website" --live --model "$DAN_LLM_MODEL" --quiet-progress
# or
dan super-organism "please build our product website" --live --model "$DAN_LLM_MODEL" --reactivity immediate --queue-status
# or
dan super-organism --workspace ./website --queue-status
# or
dan super-tui "please build our product website with cool animation" --workspace ./website
# or
dan super-tui "tell me current progress?" --workspace ./website
# or
dan super-tui --event-log .dan-super/runs/turn-01/events.jsonl --plain
dan super-tui --event-log .dan-super/runs/turn-01/events.jsonl --raw-events
# or
dan super-tui --workspace ./website --status
# or
dan super-tui --workspace ./website --server http://127.0.0.1:8000
# or
dan super-organism "build a cool website for this product" --cell-count 100 --organism-id super-dan-100
# or
dan-super-organism "LangGraph" --json
```

In interactive `dan super-tui`, Ctrl-V screenshot paste saves the clipboard image under `.dan-super/tui/attachments/`, inserts a workspace-relative `@...` mention, and sends it as structured image attachment metadata when the terminal exposes the key.

`dan super-tui` / `dan-super-tui` is the terminal-first surface for the same local Super DAN runner. Idle, composer, and event-log replay views can use Rich panels/colors when available, while active read-only/write turns stream progress directly into terminal history and render every completed lane with a consistent `DAN · <Section>:` vocabulary such as `DAN · Answer:`, `DAN · Outcome:`, `DAN · Tasks:`, and `DAN · Message:`. Rich panels keep titles in the top border and add a narrow vertical side-rail ornament inside the body, while plain output keeps the direct text labels. Answer and outcome sections preserve paragraph and bullet shape without repeating the full TUI header on every completed turn, and ordinary live turns do not auto-print session/board panels before the user asks for them. In prompt-toolkit terminals, the startup instruction panel is replaced by a compact rounded framed `DAN · Chat` composer with the title in the top border, a real bottom edge, and small inner margin; slash commands, skill mentions, path suggestions, screenshot paste, and history still work through keybindings and completions without a permanent bottom menu. Background progress and narrator updates in this chatbox path reuse boxed `DAN · Chat -> Thinking:` blocks with elapsed text instead of loose `DAN · ...` progress rows or a separate prompt-adjacent clock. It semantically highlights paths, model names, selected `$skill-name` mentions, and status terms; progress narration uses dimmer text while answer/result content uses brighter white text. It falls back to deterministic plain output, can replay an existing `.dan-super` event log, and has its own interactive `super-tui>` shell with a boxed message composer.

Explicit slash commands such as `/progress` route deterministically, but free-text input is routed by a model-authored structured decision rather than keyword or regex matching: `narrator read-only`, `executor read-only`, `executor write`, `plan mode`, or `clarification`. If the model-router reply is malformed prose or names an unsupported lane, the TUI asks the same model once to repair it into the strict route schema before clarifying. If the model chooses clarification, it gets one model-only re-review so ordinary workspace-changing requests are not blocked merely for extra write authorization. If no route model is configured for free text, the TUI asks for clarification instead of guessing. Progress/status questions use the snapshot-only narrator lane once routed there: the TUI first reuses the latest persisted narrator answer when it matches the current visible run context, then falls back to a current or latest-run snapshot from `.dan-super` event logs plus the visible transcript, renders compact `Answer:` lines first, and can optionally ask a model for a no-tool snapshot narration. Planning/refinement requests can route to `plan mode`, which asks planning questions without changing files or starting background execution. Workspace inspection stays in executor read-only lanes, which answer from bounded workspace inspection/search without creating run-local plans; complex read-only can use a live/model-backed read-only loop with `list_directory`, `file_read`, `workspace_check`, and read-only git tools when the TUI is running live or a model is explicitly supplied. If the read-only model loop returns no usable prose, the TUI falls back to bounded workspace inspection instead of ending with a dead "no usable answer" message.

After the model router selects an exact simple-write lane, operations such as `copy src.md to docs/src.md`, `rename old.md to new.md`, and `touch notes/new.md` can run directly inside the workspace. In real interactive terminals, submitted turns run through a single-lane UI worker so the `super-tui>` composer returns immediately, like a chat box; extra ordinary turns queue behind the active turn with a visible position notice. True V2 async Agent admission is still reserved for explicit `--async-agent` / `--server` sessions or when visible active/queued Agent work already exists. One-shot/noninteractive TUI runs still use the normal Super DAN execution path. Executor runs now have a passive narrator sidecar: the default stream can show concise progress reports for meaningful progress, checkpoints, 10-second quiet heartbeats, and blockers, while canned pre-router/read-only/opening phrases, repeated user-objective echoes, and semantically redundant progress lines are suppressed. Detailed `Activity:` / `Result:` telemetry is hidden unless no narrator report exists or `--raw-events` is enabled. For model-routed executor turns, a TUI-local no-tool narrator LLM sidecar can run in parallel from sanitized snapshots and print model-authored movement/progress prose while the executor continues; the renderer clock also triggers throttled narrator sidecar heartbeats during quiet model/executor waits, so the terminal can show intermediate narrator feedback even when no new tool event has arrived. Quiet heartbeat narration skips unchanged snapshots, sees the last few narrator messages to avoid repeating stable facts, and asks for one short natural sentence. If newer executor state arrives before a sidecar response renders, that stale response is discarded instead of printed. Direct foreground `Working: ...` elapsed time refreshes in place once per second across blocking model-router, narrator, executor, and final-answer waits; UI-worker/background waits suppress per-second clock spam above the live prompt and route progress through the boxed chat-thinking lane. Read-only fallback answers also use recent visible user context, so follow-ups such as "I mean the open report" can preserve the earlier requested review/takeaway shape instead of falling back to a raw file preview. Final `Answer` text is also model-written from sanitized run state instead of deterministic `Completed <request>` wording; fallback text is kept minimal and does not repeat the whole original request as the answer. Important answer bodies use generous display/transcript limits, while progress/status telemetry stays compact. `--raw-events` shows raw event names/tool metadata for debugging.

Interactive TUI sessions also keep a visible transcript under `.dan-super/tui/transcript.jsonl`; history is replayed before the composer on startup as role-separated User/Narrator/Executor/Assistant rows with bounded previews, each completed run records the full assistant answer with trace refs, narrator answers persist as `assistant_narrator` turns, `/reset` archives the visible transcript with the `.dan-super` context, and `/reset state` preserves it while clearing only hook/queue state. Recent visible user/assistant turns are also propagated into local/server async admission and Super DAN live briefs as bounded surface conversation context, so follow-up requests such as "review it" or "combine those two" can resolve against the current TUI conversation without repeating the whole objective. Submitted composer input is recorded immediately in `.dan-super/tui/outbox.jsonl` before routing, prompt-toolkit draft text is mirrored to `.dan-super/tui/draft.json` while typing, and prompt-toolkit message history is stored in `.dan-super/tui/prompt-history.txt` so Up/Down can recall prior submitted messages. Chatbox worker submissions print a compact `DAN · Chat -> Thinking:` acknowledgement before the composer returns; bounded refresh blocks then keep elapsed `Thinking Ns...` moving in that same boxed lane, and later renderer/narrator progress stays there instead of printing a loose thinking row. `/plan <objective>` is a no-execution planning mode: it asks project-specific decision questions with recommended choices plus a custom option, renders them under `DAN · Plan:`, records the exchange in the transcript, and leaves files untouched until the user chooses a direction. Natural-language planning requests can enter the same mode through model-routed `plan_mode`, so the slash command is an explicit shortcut rather than the only entry point. Plan prompts persist pending question state in `.dan-super/tui/plan-state.json`, so the next ordinary reply such as `1A, 2D...` is summarized as the answer to those planning questions instead of being sent through the front-door router; `/new <objective>` clears that pending state and intentionally starts separate work. A plan reply can also let the plan-reply model decide to start execution immediately with a concrete executor objective, so an explicit user approval does not require a second confirmation; record-only replies still leave files untouched. The TUI has a compact task board projection for async runs: `/tasks` shows active, queued, and recent work as human task overviews with `What`, `Latest`, `Changed`, `Next`, and `Commands` lines; `/status [task|run]` focuses one row; `/inside [task|run]` explicitly opens recent internal activity for a visible task; `/new <objective>` starts a separate turn; and `/focus`, `/append`, `/stop`, Esc, and Ctrl-C use the async control path when active work is visible. Ordinary natural-language turns stay as prose for model/executor routing even when a background Agent task is visible; use explicit `/append <text>` when you want to steer active work, or `/new <objective>` when you want a separate task. When a local background run reaches a terminal state, the TUI emits a compact `DAN · Answer:` completion notice with humanized summary text and records it in the visible transcript. `/tasks` and `/status` are the dashboard views; `/inside` is the opt-in diagnostic view; ordinary conversation turns do not dump the board inline. Passing `--async-agent` opts the interactive shell into local background Agent admission, and passing `--server http://...` switches the transport to the shared server-backed `/api/v2/agent-runs/admit` path. `/append`, `/pause`, `/resume`, and `/stop` use the same async control path when an async run is active. The board uses stable Admission / Board / Progress / Intervene sections internally, feeds narrator snapshots lane-separated task rows, and attaches final answers to the correct task row; the default TUI converts those internals into readable task state and hides low-signal `.dan-super` packets. Final outcomes prefer workspace-relative user-facing artifacts and group figures/tables/data/reports. When `prompt_toolkit` is installed, typing `/` opens a command dropdown, typing a bare `$` opens a skill dropdown from the loaded DAN/Codex/Claude/Cursor skill catalog, and typing `@` opens workspace path suggestions; skill matching is substring-aware, so `$dev` can surface `$scaffold-dev`. Pressing Enter accepts the highlighted or first suggestion before submission, so partial commands like `/ta` complete to `/tasks`. `/skills [filter]` prints the available `$skill-name` mentions, and readline fallback keeps Tab completion. A bare `$skill-name` selects or disambiguates a skill without starting a run; add objective text after the mention to run with it. The TUI only helps selection/autocomplete. The shared skill invocation layer parses `$skill-name`, and the Super DAN runner injects selected skills into the same skill-packet path as passive auto-selection. Selected-skill objectives stay on the skill-aware executor path rather than the TUI-only exact copy/move/touch helper. Explicit selections also add required selected-skill constraints and bounded companion reference excerpts when available. In live Super DAN runs, file-backed skills may run a conventional local preflight hook first, using script names such as `scripts/<skill_id>_preflight.py`, `scripts/<skill_id>_docs.py`, or `scripts/<skill_id>_scaffold.py`; skills without hooks still activate as prompt contracts.

The default text output is compact and build-first for terminal readability. Live runs print concise timestamped `[run]`, `[hooks]`, `[model]`, `[planning]`, `[worktree]`, `[build]`, `[tool]`, `[validation]`, `[retry]`, and `[repair]` progress lines before the final summary; use `--quiet-progress` to suppress those lines, and `--json` stays machine-readable. `--reactivity immediate|balanced|batch` controls hook/inbox queue behavior, `--queue-status` prints inbox depth/lease/owner-lock summaries, and the interactive shell supports `/status`, `/reset`, and `/reset state`. `/reset` archives the workspace `.dan-super` context directory, while `/reset state` archives only `.dan-super/state/` queues so run traces remain in place. Hook state is replayable from the `.dan-super/runs/turn-XX/events.jsonl` trace, so the `.dan-super/state/` projection can be rebuilt without duplicating packets. `--worktree-parallelism N` admits extra non-conflicting ready-frontier plan tasks into isolated `.dan-super/worktrees/...` workers, records their diff packets in hook state, and copies only admitted owned files back to the main workspace before final validation. Use `--verbose` or `--json` for the full deterministic report, which is explicitly labeled `mode="deterministic_demo"` and exposes the board/ticket/handoff/audit contract directly. Live mode requires normal DAN LLM configuration (`DAN_LLM_API_KEY` / base URL / model, or matching CLI overrides). For website objectives it writes `index.html`, `styles.css`, `app.js`, and `README.md`, then requires actual file changes plus a passing read-only validation round before success; if the builder produces no required-file edits, Super DAN runs one bounded first-write recovery pass, and if validation rejects a concrete website patch, Super DAN runs one bounded repair pass before the final verdict. For general coding/build objectives it reports success only after the native tool loop makes real workspace file mutations and the validator approves the result. When the provider reports usage, the live CLI shows aggregate prompt/completion/total tokens for the full build plus validator pass.

### Run the Reference Coding Organism

This is a local, no-server CLI around the bounded `research -> coding -> validation -> synthesis` reference organism. Demo mode stays deterministic; live mode uses the same organism with a local tool basket:

```bash
dan organism --json
# or
dan-organism --json
```

You can override the bounded coding objective directly:

```bash
dan organism "Repair the validator path and keep the delivery summary explicit."
```

Live local coding mode:

```bash
dan organism --live --model gpt-4.1
```

If you want only the research organ, use the thinner research-only surface:

```bash
dan organism --research-only --json
dan organism --research-only --live --model gpt-4.1
```

The research surface now auto-sizes reader fan-out from task breadth up to `8` concurrent readers. If you want to pin it explicitly for a run, use:

```bash
dan organism --research-only --research-readers 8 --json
```

The default live tool basket is:
- `list_directory`
- `file_read`
- `workspace_check`
- `file_edit`
- `file_write`
- `shell_command`
- `web_search`
- `git_status`
- `git_diff`
- `git_log`

Live research keeps the same runtime but uses the read-only role split, so the deep-research organ still sees `web_search` and file-read tooling while write-capable tools stay stripped from the research cells. The top-level connector now chooses the research reader pool ad hoc from the task and caps it at `8` concurrent readers unless you override it with `--research-readers`. Its public report contract is now:
- `findings`
- `evidence_summary`
- `evidence_refs`
- `contradictions`
- `open_questions`
- `verification_facts`
- `audit_issues`
- `evidence_integrity`
- `quality_gates`
- `report_readiness`
- `readiness_note`
- `artifact_mode`
- `confidence`
- `recommended_change`

### Run DAN Research

If you want the deep-research surface as a chat-first product instead of the thin organism harness, use `dan research`:

```bash
dan research --workspace .
# or
dan-research --workspace .
# or
danresearch --workspace .
```

It uses `.env` / environment settings by default for the provider and model, keeps product state under `./.dan-research/` inside the workspace, and keeps the stack intentionally thin:

- provider wrapper
- durable research orchestrator
- bounded deep-research organ

The workspace-local product directory now includes:

- `config.json` — workspace-local DAN Research defaults
- `session.json` — resumable session state
- `transcript.jsonl` — compact turn history with `event_log_path`, `markdown_report_path`, and `control_log_path` pointers
- `control-plane-events.jsonl` — workspace-level timestamped CLI/session/orchestrator log
- `runs/` — bounded run folders `turn-XX/` with `events.jsonl` plus a memo-style `report.md`, and orchestrator-only report replies under `reply-XX/report.md` when you ask DAN Research to synthesize a final report without launching another bounded run

Only concrete investigation/verification/comparison requests launch the bounded research organ; ordinary chat turns, clarifications, and post-run review decisions stay at the durable orchestrator layer.

Before the first bounded run starts, DAN Research inserts one thin intention-breaker step at the same product seam. That planner decomposes the user ask into small concrete subproblems, records why each subproblem matters, states what evidence would resolve it, groups related subproblems into explicit parallel workstreams with aggregation hints, and emits typed `evidence_targets` for exact unresolved facts with aliases, preferred sites/source families, acceptable proxy rules, explicit stop conditions, separate `not_found` vs `not_available` guidance, and the newer `entity_type` / `metric_kind` / `series_kind` / `comparison_basis` hints that help the model keep futures vs spot series, AUM vs market-cap-style fields, and thesis-to-vehicle fit separate. That same planner now also asks, explicitly, what answer format best serves the query and what coverage is actually needed, so DAN Research can choose a comparison brief, trend snapshot, verification note, buyer's guide, investment memo, or blocker note from the ask itself instead of inheriting one visible house template. The bounded deep-research organ still does the actual search/synthesis work; the thin planner just gives it a sharper first-pass map. Follow-up passes are now intentionally simpler: they narrow deterministically from the latest report/review context instead of paying for another heavyweight continuation-planner LLM call every turn, and a session-level frozen-claim ledger keeps already-settled facts from being reopened casually in later retries.

Inside the bounded organ, reader cells no longer inherit the full final-report contract. Each deep-research reader now returns one compact evidence note for its own lane, and the read-only local runtime will nudge that reader to stop searching and finalize once it already has enough bounded grounding. That keeps successful search/tool rounds from escalating away before the lead synthesis cell can assemble the final report.

Research width and depth are now explicit operator controls:

```bash
dan research --workspace . "Compare our local runtime limits with the docs"
dan research --workspace . --research-readers 8 --depth deep
dan research --workspace . --tool pdf_read --task-lane fast "Review this PDF and summarize the main arguments by section"
dan research --workspace . --tool pdf_read --task-lane deep "Review this PDF and summarize the main arguments by section"
dan research --workspace . --max-supervision-loops 3
dan research --workspace . --show-config
```

- width: auto-sized reader fan-out from task breadth, capped at `8` concurrent readers, with `--research-readers N` as an override
- depth: `--depth shallow|standard|deep`, which maps to per-worker tool-loop budgets and can still be overridden directly with `--max-tool-rounds` and `--max-tool-calls`
- task lane: `--task-lane auto|fast|deep`, which controls whether DAN Research uses the deterministic planner/review bypass (`fast`) or keeps the full control shell (`deep`) without abusing `--depth` just to force the old serial path
- continuation loop: capped at `3` bounded passes by default so bad follow-up framing cannot loop forever; use `--max-supervision-loops N` or `DAN_RESEARCH_MAX_SUPERVISION_LOOPS` to tune it, and set the value to `0` / `unbounded` only when you explicitly want no cap
- per-cell runtime budget: the research CLI now also resolves a default wall-clock ceiling per bounded cell (`60s` shallow, `120s` standard, `180s` deep) and passes it through the existing cell-budget membrane; override with `DAN_RESEARCH_MAX_RUNTIME_SECONDS`, or set it to `0`/negative to leave runtime unbounded
- default research tool basket: `list_directory`, `file_read`, `web_search`

For local PDF review, add `--tool pdf_read` explicitly. The default DAN Research tool basket does not include `pdf_read` unless you opt into it.

### Run DAN Reader

If the job is document understanding rather than open-ended research, use the flatter `DAN Reader` surface instead of the deep-research product shell:

```bash
dan read summarize /abs/path/to/paper.pdf
dan read extract /abs/path/to/paper.pdf --format json
dan reader convert /abs/path/to/paper.pdf --format latex --output paper.tex

# direct console script aliases
dan-read summarize /abs/path/to/paper.pdf
dan-reader summarize /abs/path/to/paper.pdf
danread summarize /abs/path/to/paper.pdf
danreader summarize /abs/path/to/paper.pdf
```

`DAN Reader` is intentionally stateless in this first pass. It does not create a `.dan-reader/` workspace directory or run a planner/reviewer loop. The pipeline is:

- direct text-first PDF extraction by default, with optional shared `pdf_read` reuse through `--pdf-mode hybrid|vision`
- lightweight section splitting from paper-style headings, with parent/child path preservation on real journal PDFs
- cleaned section text that strips common download/header boilerplate and repairs line-break hyphenation before summaries run
- optional parallel section summarization when a model is configured, with one reusable section packet contract (`span_id`, `section_path`, `quotes`, `claims`, `confidence`, follow-up flags)
- direct Markdown / LaTeX / JSON rendering

Supported actions:

- `summarize` — section-by-section summaries with page refs
- `extract` — metadata, abstract, and heading/page-range extraction
- `convert` — structured PDF-to-Markdown or PDF-to-LaTeX export

Useful flags:

- `--format md|latex|json`
- `--pdf-mode text|hybrid|vision`
- `--start-page N --end-page M` to bound extraction to a page window
- `--vision-model MODEL` and `--vision-prompt ...` when using `hybrid` or `vision`
- `--no-llm` to force deterministic snippet summaries
- `--parallelism N` to control concurrent section summaries
- `--include-backmatter` to keep sections like `References` in summarize output

Examples:

```bash
dan read extract /abs/path/to/paper.pdf --pdf-mode hybrid --format json
dan read summarize /abs/path/to/paper.pdf --pdf-mode hybrid --vision-model openai/gpt-4o-mini
dan read extract /abs/path/to/paper.pdf --pdf-mode vision --start-page 20 --end-page 22 --format json
```

For journal-style PDFs, `--pdf-mode hybrid` is currently the best default. It keeps the stronger text-derived outline and uses the shared vision-backed path where needed. Pure `--pdf-mode vision` is usable for bounded page slices, especially on image-heavy content, but its heading recovery is currently weaker on papers.

Like the thinner `--research-only` organism surface, DAN Research keeps the read-only runtime split for research workers, so it can use `web_search` plus local read tools but not write-capable tools. `web_search` now covers both discovery and grounded page reads on this surface: `search_depth="thorough"` or `fetch_content=true` fetches the top authoritative result pages internally, and a direct `url=` can be passed through the same tool name when the worker already knows the page it needs. Grounded page reads now also run concurrently with bounded fetch deadlines and reuse cache/inflight work on repeated verification-style queries, which cuts down the long quiet stretches that used to happen during current-fact rechecks. The generic capability-layer `web_search(fetch_content=true)` path now batches those top-result fetches in parallel too, so chat/capability callers and DAN Research both get the same concurrent grounding behavior instead of one surface staying serial. Behind that unchanged tool surface, Beacon Search now provides the internal broker/corpus/search-eval subsystem DAN uses for corpus-first retrieval, chunk-level evidence IDs, legacy shadow comparisons, and first-party search-state accumulation.

For the same reason, wide first-pass fan-out no longer requires every reader to succeed before the retrieval tissue can continue. The deep-research organ now allows a small number of bounded reader failures on wider pools, and if the orchestrator asks for another pass without an explicit `--research-readers` override, the follow-up pass automatically narrows to a smaller reader width so targeted verification does not keep paying for another full broad sweep.

Concrete external entities are now handled more conservatively at the product layer. When a current product, ticker, law, policy page, API version, or other external entity materially affects the conclusion, DAN Research is expected to fetch an authoritative page and verify the current identity/status/version/availability or mark that fact explicitly unverified. The structured report artifact still carries explicit adjudication layers: a verification appendix for critical facts (`verified` / `unverified` / `conflicted`), an audit appendix for unresolved logic, freshness, source-authority, methodology, or scope-fit gaps, a claim-level `evidence_integrity` table that canonicalizes source identity, metric identity, unit/scale consistency, time alignment, scope alignment, and same-source consistency, standard `quality_gates`, a `report_readiness` label (`blocked` / `provisional` / `grounded` / `actionable`), and an artifact mode (`final_report`, `provisional_report`, or `blocker_report`). That integrity layer is what keeps “mostly corrected” candidate facts from being treated as clean memo inputs. The default console output and the default `report.md` artifact now render the clean research memo/blocker memo view instead of dumping that internal envelope, while `--json`, session state, and the run/control logs retain the full machine-readable diagnostics. Use `--json` for the full structured envelope or `--output report.md` when you want an explicit Markdown export path.

That same artifact seam now also covers purely orchestrator-authored final syntheses. If the bounded work is already done and you ask for something like “continue to the final report,” DAN Research can answer with `response_kind=report_reply`, save that Markdown under `.dan-research/runs/reply-XX/report.md`, and keep the saved path in session state even though no new bounded `turn-XX` run was launched.

The standard quality gates remain on the structured report for compatibility and operator inspection: `time_anchor`, `scope_boundary`, `source_authority`, `numeric_reconciliation`, `claim_object_fit`, and `final_status`. They are meant to catch cross-domain failures such as mixing historical/current timestamps, making global or absence claims without a searched universe, relying on non-primary sources for existence/status facts, accepting conflicting vendor numbers without reconciliation, or mapping a thesis to the wrong object. The review controller no longer treats those rows as a blanket perfection gate.

One more review nuance now lives at that same product seam: if a continuation objective explicitly says that, after repeated attempts, the report should close provisionally instead of launching another pass, the review/controller pair can now honor that in two narrow cases. The older case is the exact-date / inaccessible-source pattern where the remaining fact is explicitly marked `unverifiable` with downgraded confidence. The newer case is a broader “final/no-more-pass” objective that explicitly asks for a caveated provisional synthesis from the best available grounded fragments. In both cases the product can stop with an honest `provisional_report` instead of looping forever solely because some final caveat remains.

Before broad search starts, DAN Research now also assigns each bounded run one generic temporal frame: `current`, `historical_snapshot`, `trend`, or `timeless`. That frame is inferred from the objective plus the runtime date/timezone, passed through the bounded task and reader briefs, and then persisted on the final report so relative terms like `current`, `latest`, `recent`, or “past 6 months” have one explicit anchor instead of being left implicit.

If review still says another bounded pass is required after the built-in or operator-configured supervision cap is exhausted, DAN Research normally returns the artifact explicitly as `incomplete` / `blocked` and renders it as a blocker report instead of leaving the last report looking finished. The one narrow exception is the explicit final/no-more-pass provisional-close case above: there the cap can end in `provisional_report` rather than `blocker_report` because “close with caveats now” was part of the objective itself. In practice that means the default behavior makes a small number of bounded attempts, while an explicit larger cap or `0`/`unbounded` opt-in lets you spend more budget when a task genuinely warrants it.

The same product seam now also guards the no-tool control stages above the bounded organ. If `decide`, `plan`, or `review` goes quiet for too long, DAN Research can launch one fresh backup durable session for that stage, accept the first acceptable result, and fall back instead of hanging forever when both attempts stall. When a timed-out or cancelled control-stage call leaves its durable session looking busy, the CLI now discards that poisoned session and continues on a fresh one instead of reusing it forever. Quiet-period heartbeats now also switch over to `planning` / `review` during those waits, so stale `completed` lines from the previous bounded pass do not make a wedged continuation planner look like active search work.

The web discovery path is also more source-aware now. `web_search` results are ranked so regulator, exchange, issuer, and official docs/reference pages are preferred over retail quote or summary pages when both are present. The review layer is now intentionally narrower than before: it still blocks clearly untrustworthy outputs such as missing-evidence reports, conflicted facts, blocking audit issues, weakly resolved contradictions, or runs that did not finish cleanly, but it no longer keeps looping just because confidence/readiness/quality-gate metadata is imperfect. Those richer appendices remain in the structured artifact for operator-facing inspection, not as the default final user-facing report.

If a run looks quiet after lines like `ok web_search`, the tool itself may already have returned and the CLI may be waiting on the next model/orchestrator step or a slow grounded verification fetch. DAN Research now emits sparse `research.heartbeat` lines during longer quiet periods so the active worker/phase/query stays visible, and bounded reader cells now time out through the normal packet budget instead of hanging indefinitely. For more visibility, use:

```bash
dan research --workspace . --show-model-trace
tail -f .dan-research/control-plane-events.jsonl
tail -f .dan-research/runs/turn-01/events.jsonl
```

`--show-model-trace` now also prints richer public request/response stats for those control-stage model calls and streams any visible planner/review text deltas inline instead of waiting silently for the final preview. It still does not expose hidden chain-of-thought or private scratchpad text.

The research control plane now also uses the same conservative hedged-controller pattern as DAN Code, but only for the no-tool orchestrator/review decisions above the bounded research organ. The default stays small: up to `2` controller attempts with a `10.0s` stagger. You can override those controls with `DAN_RESEARCH_CONTROL_HEDGE_MAX_ATTEMPTS` and `DAN_RESEARCH_CONTROL_HEDGE_DELAY_SECONDS`.

You can inspect or narrow it explicitly:

```bash
dan organism --list-tools
dan organism --live --model gpt-4.1 --tool file_read --tool file_write --tool shell_command
```

### Run DAN Code

If you want only the coding surface, use the dedicated `dan code` product instead of the full reference organism:

```bash
dan code --workspace .
# or
dan-code --workspace .
# or
dancode --workspace .
```

It uses `.env` / environment settings by default for the provider and model, and keeps product state under `./.dan-code/` inside the workspace:

- `config.json` — workspace-local DAN Code defaults
- `session.json` — resumable session state
- `transcript.jsonl` — compact run history with `event_log_path` / `control_log_path` pointers
- `control-plane-events.jsonl` — workspace-level controller, planner, review, and CLI lifecycle log
- `runs/` — per-turn evidence notes plus `events.jsonl` step logs

`dan code` runs directly on a dedicated coding organism built from the same universal worker membrane. Its runtime shape is explicit:

- one tool-free orchestrator
- a dynamic pool of homogeneous coding workers
- one aggregation cell
- one validator cell

The orchestrator decides worker fan-out per attempt, the workers stay cloneable, the aggregator owns the final bounded candidate, and the validator can reject that candidate and force a repair round. It does not route through the full DAN graph engine unless you choose to build that bridge later.

The stack is intentionally thin and composable: `provider wrapper -> durable orchestrator -> bounded coding organism`. The provider wrapper owns transient API retries, the durable orchestrator owns user-facing decisions, and the bounded coding organism owns implementation/validation work. Those layers talk through small explicit contracts instead of one large shared runtime blob. The durable layer now also carries one shared runtime/session facts packet, so the CLI and orchestrator read workspace root, effective working directory, latest run status, and similar basics from the same source of truth.

In the CLI, that same orchestrator is now the public voice of the run: deterministic lifecycle/status lines stay in `[status]`, while assistant-style narration comes from structured runtime updates (`status.update`) emitted by the orchestrator, workers, aggregator, validator, and final delivery path. The orchestrator plan now also includes a formal `public_response`, so it can first state the interpreted user intent and next action before the implementation plan. Hidden chain-of-thought still stays private.

Role-level tool exposure is also explicit:

- orchestrator: no local tools
- workers: read-oriented local tools only; raw `shell_command` is treated as mutation-capable and is therefore excluded from the read-only basket
- aggregator: full selected tool basket, including writes and raw shell access when enabled
- validator: read-oriented local tools for inspection and focused validation

Each real coding turn now carries standard runtime context automatically, including workspace root, current working directory, current date/time/timezone, active model, enabled tool IDs, and approval mode. That keeps the model from wasting early tool calls on facts the runtime already knows.

Each bounded coding run writes `.dan-code/runs/turn-XX/events.jsonl`, a timestamped JSONL stream of emitted worker/tool/run events. The surrounding control layer also writes `.dan-code/control-plane-events.jsonl`, so controller decisions, project-planner steps, review actions, interactive commands, and run launch/completion events survive outside the bounded run window. The final CLI report and `transcript.jsonl` keep both `event_log_path` and `control_log_path`.

For the chat-like control layer above the coding organism, `dan code` now also uses a bounded hedge on real orchestrator/review LLM decisions: if one no-tool controller call stays slow, a second provider call can launch after a short delay, and the first response that already satisfies the structured decision schema wins. This keeps the hedge above the agents rather than inside the write-heavy coding organism.

The default hedge stays conservative: up to `2` controller attempts with a `2.0s` stagger. You can override those controls with `DAN_CODE_CONTROL_HEDGE_MAX_ATTEMPTS` and `DAN_CODE_CONTROL_HEDGE_DELAY_SECONDS` when you want to test more or less aggressive control-plane speculation.

Provider thinking mode is also configurable per workspace or per run:

- `--thinking-mode auto` keeps the provider default/compatibility behavior
- `--thinking-mode disabled` favors faster visible answer text on providers like Kimi
- `--thinking-mode enabled` keeps explicit reasoning mode on when the provider supports it

`dan code` resolves thinking mode in this order: CLI flag -> `.dan-code/config.json` -> `DAN_CODE_THINKING_MODE` from `.env` / environment -> `auto`.

Bootstrap the workspace-local product config explicitly:

```bash
dan code --workspace . --init
dan code --workspace . --show-config
```

If you omit the task, it starts an interactive coding session and resumes the saved workspace session automatically when present:

```bash
dan code --workspace .
```

That interactive shell now keeps a durable orchestrator alive across turns. Ordinary natural-language messages go to the orchestrator first, and the orchestrator decides whether to answer directly, ask one clarifying question, or launch one bounded coding run. It now reserves bounded coding runs for directly actionable repo/coding tasks instead of defaulting short chat/meta turns into the worker pool, uses recent conversation/report context to resolve referential requests like `the website we talked about`, and clarifies when that reference is still not specific enough. When it does launch coding, the bounded `orchestrator -> worker pool -> aggregator -> validator` organism still does the heavy implementation work, but the resulting report is routed back into the same orchestrator session so it can decide `done`, `continue`, or `clarify`. The shell/orchestrator seam is now explicit as typed facts/context/report packets, so the layers stay diagrammable and replaceable.

Inside the session:
- `/help` shows commands
- `/tools` lists available local tools
- `/status` shows model, tools, and session paths
- `/history` shows recent coding turns
- `/summary` shows the session-level file/test rollup
- `/reset` clears carried-forward session context
- `/clear` is an alias for `/reset`
- `/exit` leaves the session

Interactive sessions now default to `--approval-mode confirm-risky`, which prompts before `file_edit`, `file_write`, `shell_command`, and other mutating tools. One-shot runs default to `--approval-mode auto`. You can override that explicitly:

```bash
dan code --workspace . --approval-mode auto
dan code --workspace . --approval-mode confirm-all
dan code --workspace . --thinking-mode disabled
dan code --workspace . --show-model-trace
```

Tool rounds are unbounded by default now. If you want a hard cap for a particular run, set `--max-tool-rounds N`; leaving it unset, or setting `--max-tool-rounds 0`, keeps the local tool loop unbounded while `max_tool_calls` still acts as the broader safety rail.

Provider completion waits are also bounded by default now. `dan code` resolves the per-completion timeout in this order: `--completion-timeout-seconds` -> `.dan-code/config.json` -> `DAN_CODE_COMPLETION_TIMEOUT_SECONDS` -> default `90s`. Use `--completion-timeout-seconds 0` to disable that bound. During a long quiet provider wait, the CLI now emits sparse heartbeat/status lines and an explicit timeout event instead of sitting silently after the last tool call.

The local tool loop now also injects a shared structured-tool policy. In practice that means `dan code` should prefer `list_directory`, `file_read`, `workspace_check`, `file_edit`, `file_write`, `web_search`, and structured git tools over shell fallbacks, use `workspace_check` for deterministic existence/count/HTML-tag/syntax checks before reaching for shell, use `file_edit` for targeted edits to existing files, batch multiple non-overlapping edits to the same file into one `file_edit(edits=[...])` call when possible, require each batch edit to include `start_line` or a unique `old_string`/`new_string` pair copied from a recent read, use `file_write` for whole-file create/replace/append flows, use `web_search` for live external lookups instead of guessing current facts, avoid shell heredocs when direct file tools are available, and cut down on repeated discovery once it already has the needed fact. The same loop now also validates alternative tool-argument requirement groups (`anyOf` / `oneOf`), so malformed calls like an empty `web_search {}` or an unanchored batch `file_edit` fail through the repair path instead of silently returning an empty success payload. Runtime validation remains strict, while the provider-facing `file_edit` schema is kept compact so strict OpenAI-compatible backends such as Moonshot do not reject the tool list before a worker can start.

The structured-output seam is more tolerant now as well. Fenced ` ```json ... ``` ` replies from the orchestrator or bounded organs are parsed as structured payloads instead of being dumped back to the console as raw text, and local file tools accept `file_path` as a compatibility alias for `path` so minor argument-name mismatches do not waste a tool round.

Greetings, lightweight chat, and natural-language status/workspace/result questions now go through the durable orchestrator instead of a canned local fast-path, so the shell can respond conversationally before deciding whether any coding work is needed. The orchestrator answers those turns from the shared runtime/session facts plus recent report context, while `/clear` still resets the durable conversation state.

By default, `dan code` now prints deterministic lifecycle `[status]` lines plus dynamic assistant updates from both the durable orchestrator and the bounded coding organism, so you can see intake, planning, worker execution, aggregation, validation, retries, and completion without falling back to raw tool spam. Common read-heavy tools such as `list_directory` and `file_read` are also summarized compactly so the useful signal stays visible.

If a bounded coding run fails after producing real files or a partial candidate, `dan code` now preserves and reports that material output instead of collapsing the run to `files: (none)`. Failed runs still stay failed, but the shell will show the concrete files/change summary it actually got far enough to produce.

The same shared facts packet also drives orchestrator-owned status/workspace answers. Questions like current status or current working directory now use the effective DAN Code workspace/tool context instead of falling back to generic assistant copy, regex shortcuts, or the host shell `PWD`.

The control plane now stays on one direct universal-agent path even for explicit coding turns and completed post-run reviews. Those decisions still go through the same durable orchestrator mailbox instead of controller-local fast paths, and `coding_conversation.py` plus `coding_execution.py` now share one `coding_orchestrator` worker definition so the shell stays a thin connector over the same worker substrate it drives.

The bounded coding organism now also preserves the parent handoff membrane when it spawns child coding/aggregation/validation stages. Budget, authority, continuation, and reply-hook state stay on one contract, and a bounded run only ends `completed` when the selected candidate both passes validation and meets the selected `pass_threshold`.

Those orchestrator turns also intentionally avoid reusing the worker-core continuation from the prior coding turn. The durable shell already carries explicit recent conversation plus recent report context, so meta questions like current status or latest results do not need latent acquisition state from the previous coding task.

The orchestrator also normalizes partial replies conservatively now. If the model answers a meta question with plain text or a partial structured payload, `dan code` treats that as a conversational/status response instead of silently launching the bounded coding organism.

Transient provider failures now retry in the shared LLM provider layer before the shell gives up, which is especially important for OpenAI-compatible endpoints such as Kimi. That recovery behavior is shared across agent surfaces rather than being hardcoded into `dan code`.

`--show-model-trace` adds a more detailed public progress trace to the CLI. It prints worker-scoped model request/response previews alongside tool calls, and text-only replies now stream their public deltas live inline (for example `[worker-1][model] stream: ...`) instead of waiting for a final preview line. It still does not expose hidden chain-of-thought or private scratchpad text.

Give it a bounded coding objective directly:

```bash
dan code --workspace . \
  "Inspect the failing worker tests, patch the smallest viable fix, run focused validation, and summarize the result."
```

For SWE-bench-style issue-resolution runs, point `dan code` at a single compatible instance file after you have already checked the repo out at the benchmark base commit:

```bash
dan code \
  --workspace /path/to/checked-out/repo \
  --model kimi-k2.5 \
  --thinking-mode disabled \
  --swebench-instance-file /path/to/instance.json \
  --swebench-predictions-path /path/to/predictions.jsonl \
  --json
```

When `--swebench-instance-file` is present, `dan code` can synthesize the coding objective from the instance if you omit the positional objective. The current workspace is treated as the benchmark checkout, and the run writes benchmark-side artifacts into the same per-turn directory as the normal event log:

- `swebench-instance.json` — the resolved instance record used for the run
- `swebench.patch` — the final workspace `git diff`
- `swebench-prediction.json` — one scorer-compatible prediction object

If you also pass `--swebench-predictions-path`, DAN Code appends the same `{instance_id, model_name_or_path, model_patch}` payload to that JSONL file for the official SWE-bench harness. This path assumes the workspace is already clean and checked out to the correct benchmark base commit; DAN Code does not reset or clone the repo for you.

For the first operator-driven public benchmark adapter, use `tests/eval/swebench_runner.py` to prepare the repo checkout and call the same `dan code` path for one instance:

```bash
PYTHONPATH=src:. python -m tests.eval.swebench_runner \
  --dataset-repo princeton-nlp/SWE-bench_Lite \
  --split dev \
  --instance-id marshmallow-code__marshmallow-1359 \
  --model kimi-k2.5 \
  --thinking-mode disabled \
  --completion-timeout-seconds 90 \
  --max-tool-rounds 10 \
  --json
```

The runner caches benchmark repos under `tests/eval/results/swebench_repo_cache/`, creates one timestamped run directory under `tests/eval/results/`, writes the resolved `instance.json`, prepares a detached `workspace/` checkout at `base_commit`, and tells `dan code` to use a sibling `dan-code/` workdir. When a run exits cleanly, it also records `stdout.log`, `stderr.log`, `report.json`, and `predictions.jsonl` at the top of that run directory. It is intentionally a thin operator tool, not a full scorer or benchmark-set scheduler.

The current smoke-proven path is `princeton-nlp/SWE-bench_Lite` instance `marshmallow-code__marshmallow-1359` with `kimi-k2.5`, which now exits cleanly and emits the expected one-line `swebench.patch` plus top-level `report.json` / `predictions.jsonl`.

Useful options:

```bash
dan code --list-tools
dan code --workspace . --json
dan code --workspace . --tool file_read --tool file_write --tool shell_command
dan code --workspace . --new-session
dan code --workspace . --no-session-persist
dan code --workspace . --quiet-progress
dan code --workspace . --show-model-trace
```

### Measure Raw API Latency

If you want to isolate provider latency from DAN runtime overhead, use the raw parallel chat-completions probe:

```bash
python scripts/measure_raw_llm_latency.py --model gpt-4.1 --count 10 --concurrency 10
```

It hits an OpenAI-compatible `/chat/completions` endpoint directly and reports per-call timings plus aggregate latency stats (`avg`, `median`, `p95`, `min`, `max`, total wall time).

If you want streaming-first responsiveness numbers, use:

```bash
python scripts/measure_raw_llm_latency.py --model gpt-4.1 --count 10 --concurrency 10 --stream
```

That additionally reports:

- `first_chunk_*` latency
- `first_text_*` latency for the first visible streamed text chunk
- `first_answer_text_*` latency for the first actual answer-content chunk

and can print the streamed text live with:

```bash
python scripts/measure_raw_llm_latency.py --model gpt-4.1 --stream --print-stream
```

It reads the same env settings when present:

- `DAN_MODEL` / `DAN_LLM_MODEL`
- `DAN_LLM_API_KEY` / `OPENAI_API_KEY`
- `DAN_LLM_BASE_URL` / `DAN_BASE_URL` / `OPENAI_BASE_URL`

You can also force provider thinking mode when the backend supports it:

```bash
python scripts/measure_raw_llm_latency.py --model kimi-k2.5 --stream --thinking-mode enabled
python scripts/measure_raw_llm_latency.py --model kimi-k2.5 --stream --thinking-mode disabled
```

This matters for Kimi-style responses where streaming may emit `reasoning_content` before normal answer text. The probe treats either path as first visible text, while still measuring first answer-text latency separately.

You can also benchmark an exact raw payload:

```bash
python scripts/measure_raw_llm_latency.py \
  --payload-file .tmp/chat-payload.json \
  --count 10 \
  --concurrency 10 \
  --json
```

### Run a Workflow from Python

```python
from dan.builder import workflow
from dan.engine import Engine, EngineConfig

paper = workflow("paper_writing")
ideas = paper.llm("idea_gen", prompt="Generate research ideas about {topic}")
outline = paper.llm("planner", prompt=f"Create an outline for: {ideas}")
ideas >> outline
graph = paper.build()

config = EngineConfig(
    llm_base_url="https://api.vectorengine.ai/v1",
    llm_api_key="...",
    llm_default_model="claude-sonnet-4-6",
)
engine = Engine(config)
result = await engine.run(graph, inputs={"topic": "supply chain optimization"})
```

See `examples/paper_writing.py` for a full end-to-end workflow with parallel section writing, review-revise loops, and tool calls. See `examples/vibe_research_md/` for a simple factor-research workflow (markdown format, nested composite, mock data).

## Three Authoring Surfaces

All surfaces compile to the same `dan_graph_v1` JSON and coexist:

| Surface | Strength | When to use |
|---------|----------|-------------|
| **Python DSL** (`dan.builder`) | Most programmable — loops, parameterization, testing | Power users, CI, programmatic generation |
| **Visual Editor** | Most interactive — drag-and-drop, live execution, debugging | Exploration, debugging, demos |
| **Markdown agents** (`dan.loader`) | Most accessible — natural language, minimal syntax | Rapid authoring, non-programmers |

## Visual Editor

The editor is a full-featured workflow builder inspired by LangFlow, Flowise, and Coze:

- **Multi-tab workflows** — open multiple workflows as tabs, each with isolated editing and run state; background runs continue on the server and catch up when reactivated
- **Workflow Save As** — promote `_scratch` or any working draft into a durable named workflow ID without losing the current graph state
- **Node palette** — searchable, categorized sidebar with 12 node types plus pre-built templates (ReAct, Plan-Execute, Gate nodes)
- **Full editing** — undo/redo, copy/paste/duplicate, right-click context menus, inline rename, port editor (add/remove/rename ports with schemas)
- **Multi-layer navigation** — double-click composite/loop nodes to drill into sub-graphs; breadcrumb bar for navigation
- **Gate-based control flow** — if/else and while-loop gates with branch-colored handles, visible back-edges, condition badges, and collapsible loop groups
- **Live execution** — pulse/glow animations on active nodes, particle flow on edges, duration badges, streaming LLM output, iteration counters
- **Development timeline** — the Timeline sidebar now has an `Organism Logs` tab that discovers `.dan-code` / `.dan-research` traces, renders lane bars, blocker chains, and critical-path spans from the shared `organism_log_v1` analysis payload, and keeps the older file-history view as a sibling tab
- **Content mode** — desktop-first editorial desk for Hugo/Markdown knowledge bases with pinned-path discovery plus optional `DAN_DEFAULT_CONTENT_ROOTS` / sibling `my-knowledge-base` bootstrap, a nested collapsible folder tree for tiered content repos, a larger preview-first split desk, lighter in-surface preview controls, compact shell chrome, Monaco editing, debounced autosave, fold-down diagnostics, a managed Hugo preview surface tied to the saved page state, and a first desktop-only `Edit on Page` bridge that round-trips supported rendered blocks back to Markdown/frontmatter instead of editing generated HTML
- **Human-in-the-loop** — popup dialog during execution for workflows that require user input
- **Rich logging** — expandable per-node log sections with LLM thinking, tool calls, code output; filtering and click-to-select
- **Desktop messaging controls** — top-shell Settings can connect Telegram or WhatsApp Web as app-global remote-control surfaces, with provider-aware first-run onboarding, Telegram token/helper setup plus bot username hints, in-app WhatsApp QR pairing and reset, dependency guidance, auto-start toggles, and a shell status button visible from every mode
- **Workflow reuse** — wrap saved workflows as reusable composite nodes via palette or context menu
- **Run inputs** — auto-detects `{variable}` placeholders and shows an input dialog before execution
- **Validation** — port-aware connection validation, backend validation API, inline error badges with toast summaries
- **Import/export** — save and load graph JSON; Cmd+K command palette for node search
- **Edge types** — toggle between data/control/context edges; color-coded with labels
- **Auto-layout** — dagre-based layout with one click

## CLI Tools

### `dan-organism-log` — Normalize External Agent Logs

Use this when you already have a JSON or JSONL log from another agent system and want to map it into DAN's shared `organism_log_v1` schema for timing/blocker analysis.

```bash
dan-organism-log summarize external-log.jsonl
dan-organism-log import external-log.jsonl --output normalized.jsonl
dan-organism-log analyze external-log.jsonl --json
dan-organism-log scheduler-replay external-log.jsonl --json
dan-organism-log prompt-pressure .dan-super/runs --json
dan organism-log summarize external-log.jsonl --field timestamp=ts --field event=type --field span_id=call_id
```

The command auto-detects native `organism_log_v1` files and passes them through unchanged. For foreign logs, it uses a generic JSON adapter with optional field overrides so you can point arbitrary source schemas at the canonical fields DAN cares about: timestamp, event, span/call ids, parent ids, trace/session/task ids, tool/model call ids, and wait/blocker metadata. `analyze` emits one visualization-ready payload with:
- timeline lanes and spans for Gantt-style rendering
- dependency edges for blocker and fan-in/fan-out rendering
- direct blocker chains plus critical-path candidates
- per-span inclusive duration, exclusive duration, and inferred wait time where the log contains enough ordering data

The desktop editor now consumes that same payload directly in **Development mode → Timeline → Organism Logs** through `/api/organism-logs` and `/api/organism-logs/analyze`, so DAN-native Code/Research traces can be inspected visually without re-running CLI analysis by hand.

`scheduler-replay` is the first scheduler-facing diagnostic over that same trace substrate. It keeps live behavior unchanged and instead projects:
- observed makespan, exclusive work, and average parallelism
- effective lower bounds (`critical_path` vs `work/capacity`) plus residual slack
- the terminal barrier tail after parallel work has drained
- lane-sequence-only missed-parallelism hints for later queue-policy evaluation

`prompt-pressure` calibrates Super DAN live prompt replay budgets from one trace file or a run directory such as `.dan-super/runs`. It reads `model.requested`, `model.responded`, and `model.context_length_retry` rows, reports target/emergency char pressure, tool-schema overhead, provider prompt-token ratios when usage is available, high-pressure calls, context retries, and a conservative threshold recommendation.

### `dan-run` — Execute a Workflow

```bash
dan-run workflow.json                     # run a JSON graph
dan-run examples/paper_writing.py --server http://127.0.0.1:8000
dan-run "Summarize the latest AI papers"  # natural-language goal → MetaController plans + runs
dan-run workflow.json --interactive        # prompt for HumanNode inputs
```

For benchmark-grade or demo-grade workflow execution, prefer server mode and pass `--server` explicitly. `dan-run` now fails closed when an explicit server target is unreachable, instead of silently falling back to local mode. Use `--local` only for convenience/dev runs, not as the default evidence path for tool-heavy workflows.

### Paper-Writing Smoke Path

The canonical benchmark/demo acceptance path for the paper-writing workflow is the direct Python CLI, not a thin wrapper:

```bash
export DAN_PAPER_WRITING_MODEL_PROFILE=smoke
export DAN_PAPER_WRITING_OUTPUT_DIR=output/paper-writing-smoke

python examples/paper_writing.py \
  "supply chain resilience" \
  --no-human \
  --output-dir "$DAN_PAPER_WRITING_OUTPUT_DIR"
```

Expected result:
- on a fully configured machine, a submission-ready bundle under `output/paper-writing-smoke/`
- on a partially configured machine, a structured degraded artifact bundle that still records dependency and prerequisite failures

`examples/paper_writing.py` now also exports `build()`, so `dan-run examples/paper_writing.py --server ...` is a supported path once the backend is up.

### `dan-chat` — Conversational Workflow Authoring

Build, modify, and run workflows through an interactive REPL. Chat is the unified control plane — the LLM can search workflow history, start/cancel runs, publish/export workflows, and manage the full run lifecycle without leaving the conversation. In the desktop editor, full-screen Chat now supports safe branch-based exploration with a collapsible branch tree plus cross-workflow history discovery for older conversations, and the compact Research/Development sidebars inherit most of the same day-to-day UX: mode pills, slash-command affordances, mentions, smart paste, queued follow-ups, stop generation, richer tool/run rendering, and one-click handoff into full Chat.

```bash
dan-chat                                  # start with scratch workflow
dan-chat --workflow-id my-workflow        # load an existing workflow
dan-chat --local                          # force local mode for in-process chat workflows
dan-chat --confirm                        # require approval before mutations
```

`dan-chat` works without a running server — when the server is unavailable, it automatically falls back to local mode with an in-process ChatManager. Use `--local` to force local mode even if a server is running.

Workflow-aware scheduling is also part of the chat surface now: `/schedule workflow current daily at 8am --timezone Asia/Hong_Kong` binds against the resolved current/linked workflow, and natural follow-ups like `schedule it daily at 8am Hong Kong time` reuse the same scheduler path.

**Chat Commands:**
Inside the REPL, use slash commands to manage your session:
- `/model [name]` — view or change the LLM for this chat
- `/cost` — display cumulative session cost
- `/status` — view active servers, channels, and session info
- `/run` — execute the current workflow
- `/show` — display current graph as ASCII DAG
- `/list`, `/open`, `/save`, `/saveas`, `/new`, `/rename` — manage files
- `/mcp list`, `/mcp tools`, `/mcp install` — manage MCP servers
- `/undo` — revert the last graph mutation
- `/retry` — retry the last prompt
- `/search <query>` — force an explicit grounded web search with fetched excerpts
- `/memory-delete`, `/memory-forget`, `/memory-confirm`, `/memory-reject` — manage learned memory
- `/domains [list|known|add|remove|clear]` — inspect or edit saved common domains
- `/schedule [list|add|remove|pause|resume|workflow]` — manage schedules; `/schedule workflow current ...` targets the resolved current/linked workflow and supports `--timezone`
- `/timezone [show|set|clear]` — inspect or set the default scheduling timezone used when a schedule does not specify one
- `/help` — list all commands

### MCP Server Integration

DAN can also consume external MCP servers as first-class tools in chat and workflow execution.

```bash
# Install the optional MCP client dependency
pip install -e ".[mcp]"

# Start chat or the server
dan-chat
# or
dan-serve

# From chat, install and connect a known MCP server
/mcp install stata

# Inspect configured servers and tools
/mcp list
/mcp tools stata
```

Configured MCP servers are stored in `~/.dan/mcp.json` using the `mcpServers` format shared by tools like Cursor and Claude Desktop. Servers with `autoConnect: true` are connected automatically on startup.

### `dan-up` / `dan-down` — Server Lifecycle

```bash
dan-up                                    # start server (if needed) and drop into chat
dan-up --port 9000                        # use custom port
dan-down                                  # stop background server
```

`dan-up` also reuses an already-healthy DAN server on the requested port even if it was started by DAN Desktop or another launcher. `dan-down` only stops the PID-managed background server started through the manual CLI path.

Inside the REPL:
- **Chat naturally** — describe what you want and the LLM proposes and auto-applies mutations
- **24 capability tools** — experience search, run lifecycle, publish/share/export, graph listing — the LLM picks the right tool based on your intent
- `/undo` — revert the last mutation; `/show` — inspect current graph; `/run` — execute
- `/help` — list all commands

See [docs/cli.md](docs/cli.md) for the full CLI reference with all options, environment variables, and common workflows.

## Builder DSL

```python
from dan.builder import workflow, decompile

wf = workflow("my_workflow")

# Nodes
classifier = wf.llm("classify", model="gpt-4o-mini", prompt="Classify: {input}")
writer = wf.llm("write", model="claude-sonnet-4-6", prompt=f"Write about: {classifier}")
classifier >> writer

# Sub-graphs
with wf.while_loop("refine", condition="score < 0.9", max_iterations=5) as body:
    body.llm("improve", prompt="Improve the draft...")

with wf.for_each("process", items=writer["items"], parallelism=4) as body:
    body.code("transform", code="result = process(item)")

graph = wf.build()          # -> validated Graph (dan_graph_v1 JSON)
code = decompile(graph)      # -> executable Python that reconstructs the graph
```

Four connection mechanisms: f-string magic (`f"Use: {node}"`), `>>` chaining, port subscript (`node["port"]`), explicit `wf.edge()`.

## Project Structure

```
deep-agent-network/
  src/dan/              # Python package
    models/             # Pydantic type system (ports, nodes, edges, graph)
    validation/         # Schema compatibility + graph well-formedness
    engine/             # Async execution engine (scheduler, state, checkpointing)
    executors/          # Built-in executors (LLM, tool, code, control flow)
    builder/            # Fluent DSL (builder, compiler, decompiler)
    migration/          # Graph migration helpers (legacy → gate nodes)
    server/             # FastAPI backend (CRUD, runs, WebSocket events)
  editor/               # React Flow visual editor (TypeScript + Vite)
  examples/             # Runnable workflow scripts
  tests/                # pytest suite (341 tests)
  graphs/               # Saved graph JSON files
  docs/                 # Project tracking and documentation
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=dan

# Run a specific test file
pytest tests/test_engine/test_scheduler.py
```

341 tests covering models, validation, engine, builder, server, migration, and end-to-end workflows.

## API Endpoints

The backend exposes a REST + WebSocket API:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/graphs` | List all graphs |
| POST | `/api/graphs` | Create new graph |
| GET | `/api/graphs/{id}` | Load graph JSON |
| PUT | `/api/graphs/{id}` | Save graph JSON |
| DELETE | `/api/graphs/{id}` | Delete graph |
| POST | `/api/graphs/{id}/apply-mutation` | Apply chat-generated mutation |
| POST | `/api/chat/message` | Send chat message (NL workflow authoring) |
| POST | `/api/v2/chat/message` | Chat/Agent V2 ingress bridge; normalizes the turn, forces the DAN-v2 control plane, and returns the standard chat stream handle |
| POST | `/api/v2/agent-runs` | Foreground-admit a durable V2 Agent task/run through the async board scheduler |
| POST | `/api/v2/agent-runs/admit` | Admit an Agent turn against the task board and optionally start independent work in background immediately |
| GET | `/api/v2/tasks/{task_id}` | Load a V2 task snapshot with queue state and latest progress |
| GET | `/api/v2/threads/{thread_id}/tasks` | List V2 task cards for a chat thread |
| POST | `/api/v2/agent-runs/{run_id}/execute` | Execute a queued V2 Agent run through the internal backend adapter |
| GET/POST | `/api/v2/agent-runs/{run_id}/events` | Replay or append normalized V2 Agent run events |
| WS | `/api/v2/agent-runs/{run_id}/events` | Replay and stream normalized V2 Agent run events |
| POST | `/api/v2/agent-runs/{run_id}/commands` | Append a V2 Agent control command; append/continue commands persist as durable task queue items, status reports run/task/queue state without mutation, branch creates sibling queued Agent runs with lineage, checkpoint append is admitted through backend safe checkpoints, continue-after-current promotes the next queued Agent run after terminal completion, background execution can auto-run promoted continuations, cancel/pause confirm only at backend checkpoints, and resume/retry requeue eligible runs |
| WS | `/api/chat/{channel}/events` | Stream chat/mutation events |
| POST | `/api/gateway/dispatch` | Unified workflow dispatch (JSON, NL text, workflow ID) |
| POST | `/api/runs` | Start execution |
| POST | `/api/runs/{id}/resume` | Resume from checkpoint |
| GET | `/api/runs/{id}` | Run status |
| POST | `/api/runs/{id}/human-input` | Submit human-in-the-loop response |
| POST | `/api/validate` | Validate graph structure |
| WS | `/api/runs/{id}/events` | Live event stream |

Chat/Agent V2 binds every surface session to a workspace. Pass `surface_context.workspace_root`, `surface_context.workspace = "~/project"`, or `surface_context.workspace = { "id": "...", "root": "..." }` to choose one. If omitted, V2 can infer the workspace from explicit path wording in the message, such as `I have this path /repo/app, please fix...`; if no path is found, the server binds the session to `~`. A task created from a path-bearing message keeps that workspace for later same-topic follow-ups.

V2 Agent run events and task snapshots also carry token usage. Each provider model response can emit a `token_usage_recorded` event with per-round prompt/completion/total counts and the running total; completed tasks expose aggregate usage in the run/task records and Telegram final status text.

Active-run steering uses the V2 command queue. `append_followup` commands are stored as checkpoint-append queue items and emit `queue_item_injected` when a backend admits them at a safe checkpoint; queued messages also carry compact operator-context packets for constraints, preferences, target paths, validation requirements, objectives, and attachments. The async admission layer reads the same durable task board before run creation: status turns stay foreground-only, explicit append turns target the active run, disjoint target paths can start in parallel, same-path work becomes `waiting_dependency`, and unclear follow-ups ask one clarification. `status` is read-only and returns a `status_reported` payload with run/task/queue state without changing the run log. `branch_from` creates a sibling queued Agent task/run with explicit source-run/source-task lineage and returns branch snapshots in the command response. `continue_after_current` commands are stored separately and the first queued item is promoted into the next queued Agent run when the current run reaches a terminal state. Background execution auto-runs promoted continuations and ready dependency runs by default, bounded by `max_promoted_continuations`; synchronous execution returns after the current run and leaves promoted work queued. `cancel` and `pause` record requested states immediately, then confirm `stopped` or `paused` only when the backend reaches a checkpoint. Follow-up queueing does not resume a paused run by itself; `resume` clears pause state and requeues the run under a `restart_backend_run_from_paused_boundary` policy. `retry` requeues a terminal run under `restart_backend_run_from_original_request`, preserving compact previous-attempt metadata while clearing stale interruption flags. Direct `dan super-organism` CLI runs do not own this human queue.

Telegram now defaults to the pure V2 path. Ordinary Telegram text stays on V2 Chat, while `/agent <task>` / `/run <task>` / `/build <task>` / `agent: <task>` and obvious workspace/task requests create and execute durable V2 Agent runs. Set `DAN_TELEGRAM_WORKSPACE_ROOT=/path/to/workspace` and optionally `DAN_TELEGRAM_WORKSPACE_ID=name` to bind Telegram sessions globally, or mention a path in the task text to bind only that task; if omitted, V2 uses `~`. Reply text, conversation/lane ids, and bounded recent history are carried as structured V2 context for follow-ups. Telegram V2 Agent progress edits one live status bubble and refreshes it during quiet backend periods every `DAN_TELEGRAM_V2_PROGRESS_INTERVAL` seconds, default `10`, using normalized backend event/tool state plus elapsed time and last-event age. Set `DAN_TELEGRAM_ALLOW_V1=1` only for deliberate legacy debugging.

## Roadmap

| Phase | Status |
|-------|--------|
| 0 — Formal spec (Pydantic types, typed edges, graph contract) | Done |
| 1 — Async execution engine (scheduler, checkpointing, all executors) | Done |
| 1.5 — Builder DSL (fluent API, compiler, decompiler) | Done |
| 2 — Visual editor (FastAPI + React Flow, live streaming) | Done |
| 3 — Paper-writing proof of concept (end-to-end workflow) | Done |
| 3.5 — Frontend design (multi-layer nav, execution viz, logging, palette, polish) | Done |
| 3.75 — Visual editor full editing (tabs, gates, ports, validation, workflow reuse) | Done |
| 4 — Core hardening (retry/fallback, multi-provider, tools, templates) | Done |
| 5 — Markdown agent format (`dan.loader`) | Done |
| 6 — Extended capabilities (RAG, sandbox, validators) | Done |
| 7 — Author & Distribute (CLI, publish API/MCP, messaging, blocks, PyPI) | Done |
| 7.1 — Structure review | Done |
| 7.2 — Cursor-parity chat experience | Done |
| 8 — Observe & recover (persistence, debug workbench) | Done |
| 9 — Deep systems (memory, behavior modifiers, execution primitives, self-evolving) | Done |
| 10 — Token optimization (compression, caching, context management, analytics) | Done |
| 11 — Meta-orchestrator (autonomous planning, repair, self-knowledge) | In progress |
| 12 — Author & Distribute v2 (dan-chat, gateway text dispatch) | Done |
| 13 — Multi-surface gateway | Done |

## License

Private — not yet published.
