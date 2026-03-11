# 31-14: Progressive Response UX

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Replace the "silence → big dump" response pattern with phase-chunked progressive disclosure. Each logical phase of work gets its own chat block that evolves in-place with sub-step progress. The user sees continuous, compact status and can steer at checkpoints — no dead air.

## Problem

Today DAN's response pattern is: the user sends a message, waits 10-60 seconds (or minutes for complex tasks), then gets everything at once. Every second of silence erodes confidence exponentially — "is it working? is it stuck? is it doing something I don't want?"

What users need is: **acknowledge → plan → progress → checkpoint → result**. A continuous stream of useful information, with opportunities to steer.

DAN already has pieces: reassurance messages after 5s (29-2 §7), `RunProgressTracker` (26-5), streaming edits on Telegram (30-3), `ProgressReporter` (25-7-4). But they're generic ("Working on your request...") and disconnected. The upgrade is making progress **task-specific** and **phase-chunked**.

## Design Pattern

```
[Block 1 — Planning]     one block, edited in-place with sub-steps
[Block 2 — Execution]    new block per phase, edited in-place
[Block 3 — Result]       final block with output + interactive checkpoint
```

- **New block** = new phase (planning → execution → result). Signals "we've moved to the next stage."
- **Edit within block** = sub-step progress (silent, no new notification)
- **Final edit of each block** = completed summary that stays as permanent record
- **Interactive checkpoint** = only at block boundaries (review plan? how much detail in results?)

### Concrete Example (Telegram)

**Block 1 (planning phase)** — one message, edited in-place:
```
Edit 1: "🔍 Thinking..."
Edit 2: "🔍 Exploring files... (found 3 relevant)"
Edit 3: "📋 Planning — 4 steps identified"
Edit 4: "✅ Plan ready:
  1. Generate do-file  2. Run regression
  3. Extract coefficients  4. Format table
Review before I start?"
```

User replies: "go ahead"

**Block 2 (execution phase)** — new message, edited in-place:
```
Edit 1: "▶ Step 1/4: Generating do-file..."
Edit 2: "▶ Step 2/4: Running regression..."
Edit 3: "▶ Step 3/4: Extracting coefficients..."
Edit 4: "✅ All steps complete (38s)"
```

**Block 3 (result)** — new message, final:
```
log(asset) = 0.034 (t=3.21, p<0.01) ✓ significant
Full table has 12 variables.
[Show full] [Just significant]   ← inline keyboard
```

Three messages total. Three notifications. Within each message, edits are silent.

## Deterministic vs LLM Split

| Deterministic (zero-latency, always) | LLM (when content decisions needed) |
|---------------------------------------|--------------------------------------|
| When to create a new block (phase transition) | Plan narration (adapt to user expertise) |
| Sub-step labels ("running script...") | Result summarization (what matters from 47 rows) |
| Timing / elapsed counters | Relevance filtering (user asked about log(asset) → highlight it) |
| Size hints ("47 rows, 12 columns") | Checkpoint question framing ("want full table or key vars?") |
| Error notices | Pre-flight clarifying questions |

**Rule: progress messages never wait for an LLM call** — that would defeat the purpose.

## Tasks

- [x] 1. **ProgressRenderer protocol**
  - [x] 1-1. `ProgressRenderer` protocol: `announce_plan(steps, estimated_time)`, `phase_update(label)`, `phase_complete(summary)`, `checkpoint(summary, options) -> str`, `deliver_result(content)`, `heartbeat(elapsed, message)`
  - [x] 1-2. `ProgressPhase` model: `id: str`, `name: str`, `status: Literal["pending", "active", "completed", "failed"]`, `started_at: datetime | None`, `elapsed_seconds: float`, `summary: str | None`
  - [x] 1-3. `ProgressSession` — manages the block lifecycle: creates new blocks on phase transition, routes sub-step updates to the current block, enforces throttling (max 1 edit per 0.5s)

- [x] 2. **Surface-specific renderers**
  - [x] 2-1. `EditorProgressRenderer` — streaming sections in ChatPanel. Each block is a collapsible section with live timer, progress bar, and expandable sub-steps. Checkpoints render as inline buttons.
  - [x] 2-2. `CLIProgressRenderer` — Rich `Live` panel per phase. Spinner + status line + elapsed time within each block. Checkpoints as text prompts.
  - [x] 2-3. `TelegramProgressRenderer` — new message per phase, `edit_message_text` for sub-step updates (reuse existing `_stream_with_edits` throttle at 0.5s). Checkpoints as inline keyboard buttons. Respects 4096-char Telegram limit (finalize and start new message if exceeded).
  - [x] 2-4. `WhatsAppProgressRenderer` — bookend pattern: one message at start (plan summary + ETA), silence during execution, one message with result. Checkpoints as text questions ("reply 'full' or ask about specific variables"). Max one mid-point heartbeat for tasks >5 minutes.

- [ ] 3. **Concierge/executor wiring**
  - [x] 3-1. Create `ProgressSession` when concierge starts processing a non-trivial message (skip for fast commands, simple greetings)
  - [x] 3-2. **Delayed acknowledgment** (adapter-owned for messaging, concierge for CLI/editor): messaging surfaces (telegram/whatsapp/email) bypass the concierge reassurance timer entirely — the Telegram fleet owns a drain-queue + timer in `_stream_with_edits` (10s initial delay, 20s repeat, configurable via env vars). Fast replies never produce a progress bubble. Queue hints include elapsed time + queue position. CLI/editor still uses the concierge timer with phase-aware text.
  - [x] 3-3. **Plan disclosure** (<2s, LLM): after solver/planner runs, emit plan block with step list and ETA. Offer review checkpoint for complex plans (>3 steps).
  - [x] 3-4. **Phase transitions** (deterministic): `_make_phase_event` yields `progress_ack` events at phase boundaries (context, execution) for concierge-owned surfaces, throttled per `ProgressSession` (not globally) so one conversation/surface does not suppress another. Messaging surfaces filter concierge phase events and rely on adapter-owned status updates instead.
  - [x] 3-5. **Result checkpoint** (LLM): on large outputs (>1000 tokens), generate a summary + options instead of dumping raw output. Respect user's stated focus from the original question.
  - [x] 3-6. **Heartbeat** (deterministic): for operations >5s, emit heartbeat with elapsed time. Messaging surfaces use adapter-owned timers (Telegram currently 10s initial, 20s repeat) and attachment-/poll-only replies replace any visible progress bubble with a terminal delivery outcome (success or explicit delivery failure). CLI/editor keep concierge-side heartbeat behavior.

- [x] 4. **Pre-flight clarification** *(core module; LLM question generation deferred to wiring)*
  - [x] 4-1. `InteractionRequest` kinds: `required_clarification` (blocks execution) vs `advisory_checkpoint` (optional steering)
  - [x] 4-2. Cost/time threshold: if the planned task will take >10s or >$0.10 (estimated), emit 1-2 targeted clarifying questions before committing
  - [x] 4-3. Question generation: LLM examines the user message + available context (dataset columns, file contents) and generates specific questions (not generic "what do you want?")
  - [x] 4-4. Quick-confirm mode: if the questions have obvious defaults, present them as "I'll use X and Y — ok?" instead of blocking
  - [x] 4-5. Configurable: `DAN_PREFLIGHT_CLARIFY=1` (default on), `DAN_PREFLIGHT_THRESHOLD_SECONDS=10`

- [x] 5. **Interactive checkpoints** *(core models and rendering; runtime timeout/disconnect deferred to wiring)*
  - [x] 5-1. `CheckpointOptions` model: `summary: str`, `options: list[CheckpointOption]` where each option has `label: str`, `value: str`, `is_default: bool`, `is_safe_default: bool = False` (at most one option; used as fallback on clarification timeout)
  - [x] 5-2. Surface-specific rendering: inline keyboard (Telegram), expandable sections (editor), text prompt (CLI/WhatsApp)
  - [x] 5-3. Auto-proceed applies **only** to `advisory_checkpoint`. `required_clarification` blocks until the user responds or an explicitly stated safe default is confirmed. **Edge cases:** (a) Timeout: `required_clarification` times out after `DAN_CLARIFICATION_TIMEOUT_SECONDS` (default 300s on messaging surfaces, infinite on CLI/editor). On timeout, use `CheckpointOption.is_safe_default` if one exists; otherwise pause the task and notify the user. (b) Surface disconnect: if the user's surface drops, the clarification transfers to their next active surface (31-13) or pauses the task. (c) App close: paused tasks with pending clarifications are resumable via `/resume` (31-11).
  - [x] 5-4. Result filtering: when user selects a detail level, LLM formats the output accordingly ("show me only log(asset)" → extract and present that coefficient with context)

- [x] 6. **Surface-adaptive verbosity**
  - [x] 6-1. Verbosity levels: `full` (editor/CLI — stream everything), `compact` (Telegram — edit-in-place), `minimal` (WhatsApp — bookend only)
  - [x] 6-2. Auto-detect from surface type: pass `surface_type: str` (e.g., "editor", "cli", "telegram", "whatsapp") into the `ProgressSession` constructor. The concierge resolves this from the incoming `SurfaceMessage.surface` field — not from `CapabilityContext` (which lacks a surface-type enum).
  - [x] 6-3. User override: `DAN_PROGRESS_VERBOSITY` env var or `/progress full|compact|minimal` command
  - [x] 6-4. Anti-noise rules: max 1 heartbeat per 30s on messaging surfaces; no progress messages for tasks completing in <3s (delay first progress block by ~500ms; if task completes before then, skip progress entirely). Note: 31-12's quiet hours suppress DAN-*initiated* contact, not progress on user-*requested* tasks — don't apply quiet hours to in-flight progress updates.

- [x] 7. **Tests and docs**
  - [x] 7-1. Unit tests: ProgressSession lifecycle, block creation/finalization, throttling, heartbeat timing, verbosity level selection
  - [x] 7-2. Renderer tests: verify each surface renderer produces expected output format (Telegram edits, WhatsApp bookends, CLI Rich panels)
  - [x] 7-3. Integration test: mock concierge task with 3 phases → verify block sequence and checkpoint interaction
  - [x] 7-4. Update architecture, changelog

## Surface Capabilities Reference

| Capability | Editor | CLI | Telegram | WhatsApp |
|-----------|--------|-----|----------|----------|
| In-place update | Yes (streaming) | Yes (`\r` / Rich Live) | Yes (`editMessageText`) | **No** |
| Collapsible sections | Yes | Partial (Rich) | No | No |
| Buttons/choices | Yes (UI) | No (text prompt) | Yes (inline keyboard) | Limited (text) |
| Notification cost | Zero | Zero | Low (edits silent) | **High** (phone buzzes) |
| **Verbosity level** | `full` | `full` | `compact` | `minimal` |

## Dependencies

- Telegram `edit_message_text` + `_stream_with_edits` (30-3) — already implemented
- `RunProgressTracker` (26-5) — existing CLI progress infrastructure
- `ProgressReporter` (25-7-4) — push/pull progress reporting
- Concierge reassurance (29-2 §7) — existing early-response pattern (to be superseded)
- `ClarificationRequest` / `ClarificationResponse` (25-7-2) — existing clarification protocol
- `ChatManager` streaming support — existing token-by-token streaming
- Inline keyboard support on Telegram (30-3)

## Primary Files

- `src/dan/server/concierge/progress.py` — `ProgressRenderer` protocol, `ProgressSession`, `ProgressPhase`, `CheckpointOptions`
- `src/dan/adapters/telegram_fleet.py` — `TelegramProgressRenderer` (extends existing `_stream_with_edits` in fleet, not adapter)
- `src/dan/adapters/whatsapp_adapter.py` — `WhatsAppProgressRenderer` (bookend pattern)
- `src/dan/cli/chat.py` — `CLIProgressRenderer` (Rich Live panels)
- `src/dan/server/concierge/runtime.py` — wiring into concierge processing pipeline

## Estimate

2-3 days

## Notes

- This supersedes the existing generic reassurance messages (29-2 §7). The 5s-delay "Working on your request..." becomes the instant acknowledgment in Block 1, with task-specific content.
- The edit-in-place pattern on Telegram is already proven — it's how streaming responses work today. This extends it from token-level streaming to phase-level progress.
- Blocks are permanent records. After completion, each block's final state remains visible in the conversation as a log of what happened. This doubles as an audit trail.
- The "verbosity inversely proportional to notification cost" principle ensures WhatsApp users aren't bombarded while editor users get full visibility.
- Pre-flight clarification (task 4) reuses the existing `ClarificationRequest` model from 25-7-2. The new piece is making it proactive (DAN asks before starting expensive work) rather than reactive (DAN asks when stuck).
- Required clarification and advisory checkpoints are separate interaction types. Only advisory checkpoints can auto-proceed on silence; required clarifications must block or use an explicitly confirmed safe default.
- Post-review cleanup scoped `/progress` verbosity overrides to the requesting `external_id` instead of a single process-global variable, so one active conversation can no longer silently change progress verbosity for every other surface/session.
- `Status: completed` refers to the approved messaging-progress slice that shipped in this iteration. Remaining unchecked items below are deferred follow-ons, not regressions in the shipped adapter-owned progress path.
