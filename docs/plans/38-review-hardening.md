# 38: Review Hardening

**Status:** completed *(original review scope)* with follow-up backlog
**Goal:** Address all actionable findings from the 2026-03-17 and 2026-03-19 code reviews, module audits, and deep system reviews, prioritized by risk.

## Motivation

Repository-wide code reviews and deep system reviews across two rounds identified issues spanning security, correctness, cross-platform support, and user experience:

- **2026-03-17 round:** Furnace write safety, lifecycle correctness, startup/config robustness, domain learning fidelity, provider/tool/doc alignment. All addressed in 38-1 through 38-5.
- **2026-03-19 round:** Security gaps (sandbox bypass, unsandboxed exec, XSS), workflow generation bugs, concierge triage correctness, cross-platform Development Mode breakage, UX/onboarding polish, packaged-LSP launching, the progress-ack stream-label follow-up, and the next live-debugging reliability fixes. Tracked in 38-6 through 38-14.

Review documents: `docs/reviews/2026-03-17-*.md` and `docs/reviews/2026-03-19-*.md`.

## Sub-Plans

### Round 1 (2026-03-17) — Completed

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [38-1](38-1-furnace-write-safety.md) | Furnace Write Safety | `source_id` path traversal, artifact containment, source ID collision | P1 | ✅ |
| [38-2](38-2-furnace-lifecycle.md) | Furnace Lifecycle State Machine | cancel semantics, duplicate start/resume, delete/cancel races, multi-subscriber SSE | P2 | ✅ |
| [38-3](38-3-startup-config-hardening.md) | Startup & Config Hardening | lazy `~/.dan` writes, safe-mode startup, dynamic telemetry DB path | P1/P2 | ✅ |
| [38-4](38-4-domain-learning-fidelity.md) | Domain Learning Fidelity | broader keyword seeds, abbreviation aliases, original label preservation | P2 | ✅ |
| [38-5](38-5-provider-tool-doc-alignment.md) | Provider, Tool & Doc Alignment | Google provider test realignment, clipboard error messaging, README safety contract | P2/P3 | ✅ |

### Round 2 (2026-03-19) — Completed

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [38-6](38-6-security-hardening.md) | Security Hardening | workspace sandbox strict mode, exec builtins audit, shell sandbox default, HTML sanitization, SQL parameterization | P1 | ✅ |
| [38-7](38-7-chat-wf-gen-fixes.md) | Chat Dispatch & WF Gen Fixes | validation gate bug, locals() sentinel, CoverageChecker, tool_id fallback, legacy single-node fallback, workflow_query routing, mutation quality gate | P1/P2 | ✅ |
| [38-8](38-8-concierge-triage-correctness.md) | Concierge Triage Correctness | max_tokens=60, child_execution type mismatch, asyncio.run fragility, plan decomposition stub, synthesis quality, state race, route inheritance | P1/P2 | ✅ |
| [38-9](38-9-cross-platform-dev-mode.md) | Cross-Platform Development Mode | terminal /bin/zsh hardcoding, commandExists cross-platform, Electron degradation banner, extension providers, LSP restart | P1/P2 | ✅ |
| [38-10](38-10-ux-onboarding-quick-wins.md) | UX & Onboarding Quick Wins | API key validation, error wrapping, cost visibility, coming-soon modes, reassurance delay, CLI progress, startup summary | P1/P2 | ✅ |
| [38-11](38-11-packaged-lsp-launching.md) | Packaged LSP Launching | bundled Node-based language servers no longer depend on `npx`, cwd, or dev-only packaging | P1 | ✅ |
| [38-12](38-12-chat-progress-ack-preservation.md) | Chat Progress-Ack Preservation | preserve backend `progress_ack` event labels so full-screen chat does not terminate live streams early | P1 | ✅ |
| [38-13](38-13-live-debugging-followups.md) | Live Debugging Follow-Ups | zombie PID launcher hardening and unknown-model chat cost null-handling | P1 | ✅ |
| [38-14](38-14-adapter-snapshot-and-cli-shutdown.md) | Adapter Snapshot and CLI Shutdown Hardening | sync/async adapter snapshot parity plus quiet `dan-chat` Ctrl-C exits | P1 | ✅ |

### Post-Round Follow-Up

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [38-15](38-15-lexical-triage-scenario-catalog.md) | Lexical Triage Scenario Catalog | replace ad-hoc regex/keyword routing with named lexical scenarios plus mandatory LLM fallback on ambiguity | P1 | planned |
| [38-16](38-16-workflow-build-contract-and-repair.md) | Workflow Build Contract & Repair Hardening | unify build-boundary validation, run-readiness checks, and bounded mechanical repair before save/apply/run | P1 | planned |
| [38-17](38-17-trace-to-workflow-distillation.md) | Trace-to-Workflow Distillation | let DAN execute a task first, then generalize the audited action trace into a reusable workflow draft that is compiled and contract-validated | P2 | in_progress |

## Dependencies / Sequencing

Round 2 recommended execution order:
```
38-7 (Chat & WF Gen Fixes)       ← confirmed regressions, fix first
38-6 (Security Hardening)         ← security before new features
38-8 (Concierge Triage)           ← max_tokens=60 is highest-leverage single fix
38-10 (UX & Onboarding)           ← high ROI, mostly small changes
38-9 (Cross-Platform Dev Mode)    ← important but lower urgency if all users are on macOS
```

All Round 2 sub-plans are independent and can run in any order.

Follow-up recommended execution order:
```
38-15 (workflow lexical follow-up routing)
38-8 follow-up (tiered concierge scope and stage overlays)
38-16 (workflow build contract, repair, and run-readiness)
38-17 (trace-to-workflow distillation from audited successful runs)
```

## Success Criteria

### Round 1 (completed)
- [x] No Furnace artifact writes outside the session directory regardless of `source_id` content
- [x] Server starts cleanly when `~/.dan` is not writable (features degrade, server doesn't crash)
- [x] Furnace cancel actually stops the running worker; duplicate starts are prevented
- [x] Domain learning captures common real-world domains and abbreviations
- [x] Google provider regression tests pass against current API surface
- [x] README safety claims match actual file-tool behavior

### Round 2 (completed)
- [x] All `exec()` calls use restricted builtins; workspace sandbox strict mode available
- [x] Triage LLM produces parseable JSON instead of falling back to heuristics most of the time
- [x] Terminal creation works on Windows and Linux; Development Mode shows honest degradation in browser
- [x] Users see actionable errors, cost visibility, and API key guidance on first run
- [x] Chat dispatch correctly separates workflow queries from workflow builds
- [x] All existing tests continue to pass

## Decisions

- Execute the five Round 2 sub-plans in parallel tracks.
- Round 2 was triggered by a second wave of reviews on 2026-03-19 covering general project health, concierge triage, chat dispatch, workflow generation, development mode, and user experience.
- `38-15` is a follow-up extracted from the 38-8 triage hardening discussion after the original review wave was closed: the current lexical routing layer still needs a smaller, more explicit contract.
- `38-16` is a follow-up extracted from the workflow-generation trust gap after 38-7/24/33: generation paths exist, but the build artifact still lacks one explicit contract-and-repair boundary.
- `38-17` extends the same reliability track for harder tasks: when one-shot build is not the best seed, distill a successful audited execution trace into a reusable workflow draft and validate it against the same build contract.
- Together, `38-15`, the 38-8 follow-up scope, `38-16`, and `38-17` form one workflow-reliability track: route the turn correctly, keep the concierge role narrow, enforce the artifact contract before save/apply/run, and learn reusable workflows from successful task traces.

## Notes

- Review documents live in `docs/reviews/`.
- Round 1 was implemented on 2026-03-17.
- One finding from the 2026-03-19 chat-dispatch review (P1: `prompts.py` import-order regression) was already fixed — variables are now in correct definition order. Not tracked as a Round 2 task.
