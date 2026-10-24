# 7-2: Ask about a report from the phone

**Parent:** [Phone handoff](7-1-phone-handoff.md)
**Status:** completed (source implementation and validation)
**Goal:** Open Diane chat from a report preview with a visible, durable reference to the report being read.

## Tasks

- [x] Review the current Android companion and identify the file/output preview as the report surface.
- [x] Add Ask to file/output previews; open chat and focus the question without sending automatically.
- [x] Preserve existing question text and stage a removable, inspectable report reference.
- [x] Save report drafts per computer and conversation, including unsent workspaces.
- [x] Include report title, source, capture time and a bounded text excerpt in Send, Steer and Queue.
- [x] Retain citation metadata in canonical and offline-cached history.
- [x] Reject stale file, output and prompt-log responses after source/scope changes.
- [x] Resolve relative report paths against their original source workspace and isolate delayed new-chat receipts.
- [x] Complete regression, static analysis and actual-widget visual checks.
- [x] Compile the Android ARM64 debug package.
- [ ] Complete paired physical-phone acceptance with the intended backend and installed app.

## Decisions

- Continue `fix/phone-handoff-ux`, the existing unmerged phone PR, rather than the older `codex/phone-personal-agent` experiment. Main has no dedicated Report screen or phone Ask action; Library outputs/files are the current report surface.
- Ask stages one report for the next question. Asking about another replaces that staged reference while retaining the typed question. Sending is explicit.
- Keep the backend contract unchanged. A generic file attachment carries structured citation metadata for readable history, while the outgoing message text includes the source reference and quoted excerpt so all execution paths receive context.
- Snapshots contain at most 2,000 UTF-16 code units, without splitting surrogate pairs. The source path or URL remains available for reading the full report. Server truncation and unavailable content stay explicit; loading/error placeholders never become evidence.
- Report snapshots and draft keys remain scoped to their original computer/conversation. Cached sent citations reopen their recorded excerpt without a network read.
- Existing desktop execution profiles and permissions remain authoritative. This change does not add a new cloud backend or install a phone build.

## Phone review — October 24

| Priority | Finding | Disposition |
| --- | --- | --- |
| High | A late preview response could replace the currently selected report, creating a citation with the wrong content. | Fixed with request generations, source-scope checks and preview resets. |
| High | A delayed first-message receipt could select an old workspace's new chat and move the current workspace's draft there. | Fixed by matching the original workflow/root before selecting or migrating the draft. |
| High | Note autosave reads the current note path and shared editor when its timer fires. Switching notes during a pending save can discard edits or target the wrong note. | Open; static review of `_handleNoteEditChanged`, `_saveActiveNote` and `_selectNote` in `mobile/lib/main.dart`. Needs note-scoped drafts/saves and dedicated regression coverage. |
| Medium | Existing screenshot drafts are stored only with the current selection and are cleared when changing conversations. | Open for screenshots; report references now have independent per-conversation persistence. |
| Medium | Attach a screenshot accepts a clipboard image data URL, not the ordinary Android gallery/clipboard image workflow. | Open; native picker/share support is separate work. |
| Medium | Output previews render raw text, reject binary report bodies, and previously ignored truncation. | Citation handling now labels partial/unavailable content; richer Markdown/PDF reading remains separate work. |

## Validation

- Flutter 3.47.8 / Dart 3.13.7: **all 47 Flutter tests pass**, including 22 added report/state/layout checks; static analysis reports no issues.
- The actual `report-chat` widget rendering was inspected with real fonts and fictional report data. Small-screen keyboard tests pass both online and offline.
- Android ARM64 debug APK compilation succeeds with the existing native project configuration and JDK 17. The temporary JDK initially could not validate Gradle's download certificate; using the system Java CA store fixed validation without disabling TLS checks. Flutter-generated dependency/native-config migrations were excluded from this feature commit.
- Tests use fictional report content and fake computer responses. They do not contact a model provider or a personal backend.
- Coverage includes explicit sending, draft preservation/removal, all three delivery actions, per-chat and restart recovery, stale previews, relative source paths, delayed first-message receipts, storage failure and concurrent draft edits.
- Responsive checks cover 360×640, a 270px keyboard, 2× text, long titles and offline mode. The first report-chip layout overflowed in this combination; compact the header while typing and keep detailed notices in scrollable history.
- Live provider use, physical-phone networking, production signing and installation remain unverified by these source checks.
