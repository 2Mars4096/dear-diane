# 48-2: Workflow Follow-up Lane and Context Pack

**Parent:** [48-workflow-continuity-and-control-plane-hardening](48-workflow-continuity-and-control-plane-hardening.md)
**Status:** not-started
**Goal:** Route obvious workflow continuation turns through a narrower, faster action lane with an explicit workflow context pack.

## Dependencies
- **48-1** must land first: the lane consumes the shared workflow resolver and revision metadata.
- Coordinate with **48-6**: lane instrumentation and scenario IDs should align with the acceptance harness so work is not duplicated; avoid one-off metrics that 48-6 will replace with proper baselines.
- **48-3** (mutations) and **48-4** (scheduling) will extend the lane's exit points and may add fields to the context pack; the pack schema and lane exit contracts defined here should be treated as stable interfaces that downstream sub-plans extend, not duplicate.

## Tasks
- [ ] 1. Define the dedicated workflow follow-up lane
  - [ ] 1-1. Enumerate the workflow continuation intents: build, edit, run, rerun, schedule, delete, inspect. Note: `inspect` is read-only and may share the pack but not the same action shortcuts as mutating intents.
  - [ ] 1-2. Define the lexical/history/context signals that should short-circuit generic concierge ambiguity.
  - [ ] 1-3. Define negative signals and opt-out conditions so the fast lane stays conservative when a turn only mentions a workflow in passing.
  - [ ] 1-4. Define a mid-turn fallback path back to generic concierge routing when the workflow lane no longer has enough evidence to continue safely. Scope: this applies to the single user-message turn boundary (resolver confidence too low, resolver error, or empty/invalid pack); internal planner steps and streaming partial intent are not in scope for mid-turn fallback.
- [ ] 2. Build a compact workflow context pack
  - [ ] 2-1. Include resolved workflow ID, display name, revision, required inputs, last mutation preview, and most recent run/schedule state.
  - [ ] 2-2. Keep the pack intentionally small: target under roughly 600 tokens serialized (measured via tiktoken `cl100k_base` as the reference tokenizer; enforcement is best-effort truncation, not hard rejection) and keep it to one compact system/context block. Define a field priority order for truncation when the pack exceeds budget: identity fields first (always kept), then run/schedule state, then last mutation preview, then required inputs. Canonical serialization is compact JSON.
  - [ ] 2-3. Refresh or invalidate the pack when the resolver reports a stale revision instead of blindly reusing outdated workflow state.
- [ ] 3. Reduce non-LLM overhead on workflow follow-ups
  - [ ] 3-0. Inventory current concierge stages on the workflow continuation path (routing, triage, context assembly, prep) and identify which are skippable vs required.
  - [ ] 3-1. Skip unnecessary concierge stages for turns already known to be workflow continuations.
  - [ ] 3-2. Parallelize safe lookup/context assembly steps where it reduces wall-clock time.
- [ ] 4. Wire the lane into runtime routing
  - [ ] 4-1. Use the workflow identity resolver from 48-1.
  - [ ] 4-2. Preserve current behavior for truly ambiguous turns that still need generic routing.
  - [ ] 4-3. Prefer a false negative over a false positive: if the workflow lane is not confident, fall back to the generic path instead of forcing a workflow action.
- [ ] 5. Test continuity behavior
  - [ ] 5-1. Add multi-turn regressions for build -> edit, build -> run, build -> schedule, and save-as -> continue.
  - [ ] 5-2. Add timing assertions or instrumentation for the DAN-owned portion of workflow follow-up latency.
  - [ ] 5-3. Add regressions for false-positive avoidance and fast-lane fallback when a turn mentions a workflow but is actually asking for something else.

## Decisions
- Workflow continuation should be a first-class lane, not just a side effect of generic concierge heuristics.
- Smaller, explicit workflow state is better than repeatedly reconstructing context from long thread history.
- False positives are more harmful than false negatives here; the workflow fast lane should be intentionally conservative and degrade back to generic routing when uncertain.

## Notes
- This is the sub-plan most likely to improve the "same model feels smarter in Cursor" complaint because it removes avoidable orchestration noise.
- When the lane falls back to generic routing (4-3), fallback is silent from the user's perspective; telemetry records the fallback reason for debugging but no UX copy is surfaced.
