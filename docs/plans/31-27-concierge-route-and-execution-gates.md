# 31-27: Concierge Route And Execution Gates

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Make concierge routing less dependent on brittle phrase bags and prevent direct-task execution from claiming completion before required reads/searches/writes actually succeed.

## Tasks
- [x] 1. Tighten the route gate
 - [x] 1-1. Extend classifier LLM output to include `target`, `action_hints`, and a compact deliberation payload.
 - [x] 1-2. Remove broad phrase-bag routing cues that were only acting as weak semantic guesses.
 - [x] 1-3. Keep deterministic fallback conservative and structural when the classifier LLM fails.
- [x] 2. Add bounded deliberation to concierge tool execution
 - [x] 2-1. Store a compact `deliberation` object on `ClassificationResult`.
 - [x] 2-2. Inject an execution-contract block into concierge tool-calling prompts so the model re-checks goal, deliverable, required actions, and completion criteria before acting.
- [x] 3. Tighten the execution gate
 - [x] 3-1. Track required action hints by successful tool completion, not by mere tool invocation.
 - [x] 3-2. Refuse to finalize a required-action direct task when the required capability step is still missing.
- [x] 4. Verify
 - [x] 4-1. Add focused regressions for LLM-supplied route details, execution-contract prompt injection, and failed `file_write` retry behavior.
 - [x] 4-2. Run focused concierge/tool regression suites.

## Decisions
- Broad semantic phrase bags were reduced rather than expanded; the primary classifier should rely on the LLM route payload, not ever-larger regex inventories.
- The execution gate uses successful tool completions as the source of truth for required actions. Failed tool attempts do not satisfy `read_file`, `search_web`, or `write_file`.
- The deliberation payload is intentionally compact and bounded: goal, deliverable, constraints, required actions, completion checks, and next step.

## Notes
- This change keeps the existing `ask` / `agent` / `plan` top-level route surface intact.
- The new deliberation block is prompt-level orchestration guidance, paired with a hard completion gate in `chat_manager.py`.
