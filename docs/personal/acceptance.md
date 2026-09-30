# Plan 7 acceptance ledger

## Personal research integration (2026-09-30)

Workspace search and bounded public page/PDF reading are connected to chat and voice, with cited sources, context, cancellation and shared model reservation enforcement. Automated transport, dispatch, follow-up and UI checks cover the new path. Live dinner trials exposed variable recommendation quality and missing official hours/menu data; these are retained in the research evaluation report. Public research is implemented; arbitrary Workspace shell/files, interactive browser actions and connected-account execution remain separate unfinished capabilities.

Updated 2026-09-30. Mac hosts execution; Mac Electron and phone browsers share the Personal UI. This ledger distinguishes completed local behavior, remaining implementation and external acceptance. Plan 7 as a whole is **not complete**. The user has deferred physical-phone testing; the current wrap-up is the local Mac/browser milestone. Account setup and phone release are not prerequisites for using that milestone.

| Stage | Implemented and checked | Remaining implementation | External acceptance |
|---|---|---|---|
| 0: contracts | Three pilot scenarios, ownership, SQLite migrations, durable admission journal, 30 bilingual/adversarial fixtures; provider feasibility matrix | Broker isolation implementation; account-wide billing limits remain provider-managed | Register test accounts; select and configure private HTTPS/device setup |
| 1: capture/review | Paste; bounded text PDF/EML/TXT and PNG/JPEG; local bilingual OCR; source anchors; ambiguity/DST review; duplicates; original preview/download/deletion; unsent draft; confirmation; ICS download. 30/30 saved live extraction outputs pass revalidation | Scanned PDF OCR is outside the first-release text-PDF scope; full fact/history erasure belongs to privacy rollout | Device-specific upload/keyboard checks belong to Stage 4 |
| 2: follow-through | Durable extraction with saved limits/pause, per-call authorization, USD reservations and OpenRouter price ceilings; deduplicated Agent V2 admission; stop/recovery; atomic reminders; overdue catch-up; pause/resume/wait; user-reported completion; Home groups/history/event reconnect | Broader operational acceptance; actual-cost accounting and additional provider price controls | Multi-day behavior and interventions measured in pilot |
| 3: Google | Contracts/provider research; mock-only timed/all-day exact approvals, identity-checked read-back receipts and reconciliation tests | Isolated authenticated broker; OAuth UI; real adapters and live conformance; user-facing proposals/approvals/receipts; selected-thread discovery; runtime policy enforcement | Dedicated Google OAuth app/account, broker isolation negative checks and live event read-back |
| 4: phone release | Manifest, install guidance, private-data-free service worker, offline draft, responsive layout; isolated desktop/390px and native Electron checks | Session revocation, push subscriptions/outbox/quiet hours/preferences, authenticated notification links, opt-in source watches and update proposals | Trusted HTTPS; physical iPhone/Android installation and push; seven elapsed days and ten test commitments |
| 5: provider independence | Microsoft and protocol-provider feasibility research | Microsoft adapter, common contract tests, explicit multi-account destination UI, protocol spikes/troubleshooting | Dedicated Microsoft and protocol test accounts; identical live workflows and cross-account checks |
| 6: expanded jobs | Capability/feasibility matrix only | Claim/return packets, approved correspondence, selected file/task destinations, comparison/goal templates, selected service/native routes | Five representative pilot cases per selected job, provider receipts and intervention/cost evidence |

## Current operational limits

- Mac must be awake with the backend running. Closing the browser is supported; turning off the host stops execution until catch-up.
- Extraction is separately enabled with `DAN_PERSONAL_MODEL`; limits are two model calls, 4,096 output tokens per call, 60 seconds and 30 attempts per operator per UTC day. USD reservations now default to $0.50 per extraction and $2.00 per UTC day with OpenRouter price ceilings. Reservations are conservative model-usage allocations, not billed spend; account fees and other app usage require provider-account controls.
- Reminders are durable in-app inbox records. Phone push and verified external calendar insertion are not implemented.
- Source deletion removes uploaded original bytes from current state. Captured text and older backups remain, explicitly disclosed before deletion. There is no full personal-history erasure control yet.
- A completed commitment records the user's report. It does not establish attendance, refund receipt or provider-side completion.
- Real-account tokens must remain unavailable to ordinary agent processes. A same-user file/process is not the planned broker boundary.
- `PYTHONPATH=src python -m dan.personal` runs this checkout on loopback with separate persistent `.personal-local/` records; see `setup.md`.
- The local Mac bundle is a build artifact. The installed app and separately installed Python backend have not been upgraded by this worktree.

## Evidence

- Continuous voice pilot: four live-tested synthesis IDs, successful real recognition/chat/synthesis path, two automatic Chromium turns with interruption, and native synthetic microphone cleanup. 101 personal/connector backend and 388 frontend checks pass. Human microphone/voice-quality acceptance and latency tuning remain open; see [voice evidence and limits](voice.md).

- Conversational entry: durable turns, replay/stop, local action receipts, Activity disclosure and shared AI reservations. Real-model create → conversational time correction passes; initial failure retained in `conversation-evaluation-2026-09-30.json`. Browser confirms draft reload, Activity navigation and no overflow at 390px; native Electron navigation/titlebar passes.

- Convenience pass: compact confirmed summaries, inline editing/cancel, direct reminder changes, grouped actions and reduced copy. Browser checks cover capture/confirm, save/cancel edits, schedule/change, pause/resume, completion and ICS download; desktop/390px layouts checked.

- Saved spending controls: browser save/reload retains the daily limit; production build/bundle budgets and refreshed Mac package/signature pass.
- Latest combined checks: 98 backend and 382 frontend tests pass. See [connector contracts](connector-contracts.md) for the mock-only boundary and remaining production requirements.

- Budget/connector follow-up: nine budget-contract tests and 17 mock-action tests pass, plus live synthetic extraction with price ceilings. Desktop browser verifies exhausted budget feedback while manual capture, confirmation and reminders remain usable.

- Local wrap-up: 375 frontend and 50 backend checks pass. New launcher serves the built UI and retains exactly one synthetic capture across process restart, with mode-0600 SQLite state. Physical-phone testing is explicitly deferred.

- `mock-calendar-evaluation-2026-09-29.json`: complete synthetic capture, approval, create, update and lost-response reconciliation; two mutations produce one event.
- `extraction-evaluation-2026-09-29.json`: synthetic live extraction outputs, latency/tokens and normalization notes.
- Backend regressions: personal store/extraction/intake/lifecycle/reminders, durable dispatch and remote access.
- Frontend regressions: 382 checks pass, including capture replay/drafts, extraction stop, reminder refresh, cancellation intent, lifecycle revisions/history, inline source-deletion confirmation and reconnect failure. Production build/budgets and changed-file lint pass.
- UI consistency: system typography, shared palette, compact controls, flat sections, completed-record summaries and phone focus/back navigation. Chromium passes at 360/390/430px in light/dark mode, including edited reminder preservation during remote changes. Isolated native Electron navigation/titlebar passes.
- Live synthetic bilingual PNG: English date/time plus Chinese meeting text recognized locally; retained original visible alongside text.
- Physical-device, real-account and elapsed pilot checks remain open. No mock/browser-emulation result substitutes for these gates.
