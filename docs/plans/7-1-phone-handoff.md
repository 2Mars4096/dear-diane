# 7-1: Reliable phone handoff and conversation UX

**Parent:** [Phone personal-agent roadmap](../development-plan.md)
**Status:** done (source implementation and validation)
**Goal:** Make the Android phone a reliable, comfortable place to talk to Diane, review remote work, and answer decisions without losing desktop context.

## Tasks

- [x] Review current phone and backend paths; reproduce destructive transcript saves and command replay duplication.
- [x] Confirm the six fixes and phone/backend/execution-host responsibilities with the user.
- [x] Choose Diane / Work / Library navigation and an attention-first work view.
- [x] Preserve canonical transcript metadata and concurrent messages with additive updates.
- [x] Persist request receipts and reject conflicting request-ID reuse across the full command lifecycle.
- [x] Keep a durable phone outbox with truthful delivery, retry, and restart restoration.
- [x] Recover after network/app lifecycle changes and isolate responses by server/session/run.
- [x] Inherit the desktop execution profile and permission policy for phone continuations.
- [x] Restore the selected conversation and per-conversation drafts.
- [x] Surface pending native decisions, steering, and acknowledged stop controls.
- [x] Implement accessible conversation, work, library, and connection layouts.
- [x] Run backend and Flutter regressions and inspect actual Flutter renderings.
- [x] Record the contract, limitations, and installation steps in tracked documentation.

## Decisions

- The phone owns interaction, cached views, drafts, and its outbox. The backend owns durable acceptance, deduplication, state, and routing. The execution computer owns agent processes, tools, and artifacts.
- Backend and executor may share the existing remote computer. This slice does not require a new cloud service or runtime.
- Reuse existing sessions, tasks, runs, and native approval contracts. The Work view groups real recorded work; a richer durable issue/commitment layer remains follow-up scope.
- Preserve remote execution profiles and permissions. A phone presentation mode must not widen execution permissions or silently select a different agent/account.
- No phone progress projection is written back as a replacement transcript.
- Use a quiet paper/ink interface with restrained teal, persistent primary navigation, progressive technical detail, and usable narrow/large-text layouts.

## Notes

- Baseline: `2581669e8c8e07670db8fede7eb770a245fc6eef`; branch: `fix/phone-handoff-ux`.
- Official Flutter 3.41.9 / Dart 3.11.5 matches the current package SDK constraint.
- Physical-device VPN, cellular handoff, live provider approval, and installed desktop/phone acceptance require the paired environment and remain separate from deterministic source validation.

## Validation

- Backend: 59 targeted checks pass, including 14 new phone regressions. The selections cover phone/session state, transcript merge/revision, restart/queue receipts, shared storage, product/session-title compatibility and existing native command/settings behavior. Use a fresh pytest `--basetemp` outside the repository.
- Flutter: the complete `flutter test --no-pub` suite passes 25 tests. `flutter analyze --no-pub` reports no issues.
- UI: five actual widget renderings inspected; focused layout checks cover 360×640, a 270px keyboard inset, 2× text, dark mode and offline recovery, including their combined state.
- Recovery checks cover lost acknowledgements/restart, unreadable or delayed local storage, stale responses, worker approval identity collisions, uncertain receipt reads, and polling beyond the former six-minute window.
- Packaging: a clean debug Android ARM64 APK build succeeds with Flutter 3.41.9 / Dart 3.11.5 and a full JDK 17. The app has not been installed on a physical phone or activated on the remote computer in this session.
