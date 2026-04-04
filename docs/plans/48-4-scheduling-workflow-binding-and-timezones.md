# 48-4: Scheduling Workflow Binding and Timezones

**Parent:** [48-workflow-continuity-and-control-plane-hardening](48-workflow-continuity-and-control-plane-hardening.md)
**Status:** completed
**Goal:** Make scheduling behave like a first-class workflow continuation surface, with reliable workflow binding and explicit timezone semantics.

## Dependencies
- **48-1** must land first: the resolver and revision model are consumed here. Split with 48-1 task 3-1: that task wires the shared resolver into the scheduling path at a minimal level (identity only); this plan (48-4) adds revision-in-schedule-metadata, `current`/`this` reliability in workflow-aware turns, timezone semantics, and feedback UX on top.
- **48-2** should land or be coordinated: task 1-2 (`current`/`this` workflow references) depends on how 48-2 defines "current workflow" in the continuation path.

## Tasks
- [x] 1. Bind schedules to resolved workflow identity
  - [x] 1-1. Reuse the 48-1 workflow resolver in scheduling commands.
  - [x] 1-2. Support `current` / `this` workflow references reliably in workflow-aware chat turns.
  - [x] 1-3. Carry workflow revision/freshness metadata into scheduling so "schedule this workflow" can confirm what exact saved revision is being scheduled.
- [x] 2. Add timezone-aware scheduling semantics
  - [x] 2-1. Support explicit timezone input such as `Asia/Hong_Kong`.
  - [x] 2-2. Parse user-facing phrases like "8pm Hong Kong time" into stored schedule metadata plus UTC execution times. Prefer deterministic parsing (library + locale rules) for common patterns and explicit IANA identifiers; fall back to LLM structured extraction only for genuinely ambiguous or novel phrases. On parse failure, surface a clarifying question rather than defaulting silently.
  - [x] 2-3. Define recurrence semantics in the stored IANA timezone so DST-aware zones continue firing at the intended local wall-clock time.
  - [x] 2-4. Define timezone precedence: explicit timezone in the turn, then stored user preference, then workspace/server default, then UTC.
  - [x] 2-5. Add a task to persist and surface the user timezone preference (per-user setting; storage TBD -- config store or user profile record). Wire the read path into the precedence chain.
- [x] 3. Improve schedule feedback
  - [x] 3-1. Show resolved workflow target explicitly.
  - [x] 3-2. Show next run in both local timezone and UTC where helpful.
- [x] 4. Preserve backward compatibility
  - [x] 4-1. Keep current UTC-based commands working.
  - [x] 4-2. Avoid breaking existing persisted `ScheduleEntry` records. Strategy: new timezone/revision fields are optional; legacy rows default to UTC at read time; no destructive migration required.
- [x] 5. Add scheduler regressions
  - [x] 5-1. Test exact workflow ID, `current`, and display-name-driven resolution where permitted (follows 48-1 precedence/ambiguity rules; exact/normalized name or must-ask).
  - [x] 5-2. Test timezone parsing, persistence, and next-run computation/display.

## Decisions
- Scheduling should target a resolved exact workflow, not a guessed display string.
- Timezone must be first-class user-facing state, not just an internal UTC assumption.
- Recurrence should be interpreted in the stored schedule timezone using IANA timezone semantics; relative phrases like "tonight" should use the resolved user/explicit timezone, not server local time.
- User preference persistence shipped in `UserProfile.preferred_timezone` with `/timezone` and `/tz` command support wired through concierge runtime.

## Notes
- This sub-plan addresses a directly user-visible gap: "8pm Hong Kong time" should not require manual UTC conversion.
- Edge cases to document during implementation: user changes timezone preference after schedules exist (existing schedules keep their stored IANA zone; only new schedules pick up the updated preference); recurring schedules crossing a DST boundary (fire at intended wall-clock time per stored IANA zone); cancelling/editing schedules that already carry the new identity/revision fields.
- Initial scope covers all chat surfaces that can trigger scheduling (editor, Telegram, CLI); adapter-specific quirks feed the same resolver and scheduling contract.
- Scheduler compatibility was also hardened for workflow-aware fast-command paths: if `current` resolves to the sole linked workflow ID but metadata is absent on that path, scheduling still binds to the linked workflow instead of failing closed on a stale catalog message.
