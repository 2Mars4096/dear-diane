# Personal workflow contracts — v1

Status: Stage 0 design, 2026-09-29. These are implementation contracts, not shipped endpoints. Parent: [Plan 7](../plans/7-phone-personal-agent.md).

## Pilot scenarios

1. Paste an invitation → inspect anchored extraction → correct missing fields → confirm → download ICS. Stage 3 adds an explicit selected-account calendar proposal and read-back receipt.
2. Import an email deadline → confirm date and timezone → schedule an internal reminder → see it in the inbox after closing/reopening the browser.
3. A selected source changes → compare source revision and user edits → approve an exact update → reconcile provider state and retain a receipt.

## Ownership and storage

- Single authenticated operator per deployment. Resolve `owner_id` on the server; never accept arbitrary owner identity from a request body.
- Personal SQLite sidecar: `DAN_GRAPHS_DIR/personal/state.sqlite3`; private directory 0700, database/attachments 0600. It contains no OAuth secrets. ChatV2Store continues to own execution tasks/runs.
- Tables: schema migrations, captures, attachments, commitments, facts, connections (secret references only), proposals, approvals, receipts, wakeups, notifications, operations, business_events, dispatches.
- Every business row has ID, owner, schema version, revision, created/updated times. Every source/provider link includes connection identity; identical display names do not merge accounts.
- Migrate with a single SQLite transaction, version checks and a verified backup. Reject a newer schema. Use foreign keys and a busy timeout; one persistent host on a local filesystem. No SQLite file on a network share.
- Operation ID uniqueness is `(owner_id, operation_id)` with canonical request hash. Store result and business event in the same transaction. Identical replay returns that result; changed payload under the same ID returns 409. Revision checks and writes share one transaction.
- Captures retain original evidence and SHA-256. Exact duplicate hashes suggest an existing capture; they do not silently merge different obligations. Source anchors use character ranges for text, page+quote for PDFs, and attachment+region for images.
- Facts are explicitly confirmed, editable/deletable records. Deletion removes current retrieval/index data; audit records retain operation IDs, not deleted values. Document retention of backups separately before real-account use.

## Date contract

- Store calendar date, local time, IANA zone and resolved UTC instant separately. No device-zone default, inferred year or numeric-date locale without user confirmation.
- Verify zone conversion by UTC round-trip. A DST gap needs correction; a fold needs an explicit offset/occurrence. Date-only deadlines stay date-only until reminder timing is chosen.
- Preserve user corrections across re-extraction and watched-source changes. Sources propose changes; they do not overwrite confirmed fields.
- ICS export uses stable UID/revision, escaped/folded UTF-8 lines, UTC timed events or DATE all-day events, no attendees or invitation method. Download is not proof of insertion.

## Proposed API examples

```json
POST /api/personal/captures
{"operation_id":"uuid","kind":"paste","text":"Meet Ada on 2026-10-02 at 14:00 Asia/Hong_Kong.","locale":"en-HK"}

201
{"id":"capture_uuid","schema_version":1,"revision":1,"extraction_status":"pending","duplicate_candidates":[]}

PATCH /api/personal/commitments/commitment_uuid
{"operation_id":"uuid","expected_revision":2,"title":"Coffee with Ada","date":"2026-10-02","time":"14:00","timezone":"Asia/Hong_Kong","decision":"confirm"}

409
{"detail":{"code":"revision_conflict","current_revision":3}}
```

- Text limit 64 KiB; file limit 10 MiB, five files per capture, 20 PDF pages. Stream intake with aggregate bounds. MIME signature validation; no arbitrary URL fetching, ZIP extraction, remote image loading from `.eml`, or attachment execution.
- First version is online mutation only. Unsent local drafts have a visible discard control and survive network failures; never queue external writes offline.
- Snapshot returns business-event cursor; events after that cursor replay in sequence. Reconnect replaces state from a fresh snapshot before applying later events. UI badges use business attention, not agent run completion alone.

## Cross-store admission and restart

1. Commit dispatch row and unique key before admission.
2. Add an Agent V2 admission lookup keyed by that same key; reserve deterministic task/run IDs durably before starting execution. Admission must return the existing identity on replay, including concurrent requests.
3. Store returned task/run association in personal SQLite. If the process dies between 2 and 3, lookup by dispatch key repairs the association; never admit a new random run.
4. Before implementing automatic replay, test crash windows before reservation, after reservation, after run creation, and after receipt persistence. Existing follow-up recovery is insufficient and must not be reused as an exactly-once guarantee.
5. Internal reminders are SQLite outbox events, requiring no LLM or Agent V2 admission. Unique `(wakeup_id, occurrence)` prevents duplicate visible reminders. Claims use expiring leases; pause/cancel is checked within the committing transaction.

## Connector broker boundary

- Real-account work requires a separate OS identity/container for the broker, separate from agent shell/network processes. File mode 0600 under the same agent UID is insufficient.
- Browser approvals authenticate directly to the broker/control plane. Agent tools receive run-scoped capabilities bound to owner, connection, operation and expiry; they cannot mint approval decisions.
- Broker owns OAuth callbacks/state, encrypted tokens, refresh/revocation and keys outside record backups. It validates schema, resource ownership, current revision, allowed operation and single-use approval hash before every write.
- Record intent before sending; deterministic provider IDs where supported. Timeout becomes unknown outcome. Query the provider before retry; unresolved outcomes require review.
- Disable real adapters until a negative test demonstrates that agent shell/file/network cannot read tokens, forge approval, invoke arbitrary broker methods or cross account boundaries.
- Public OAuth rollout, email send, guests, deletion, purchases and personal messaging access remain outside the first connector release.

## Budgets and measurement

- Initial safety limits proposed for local fixture runs: 1 extraction + at most 1 schema-repair call, 60 seconds, 4,096 output tokens, zero tools or descendants; 30 fixture requests per evaluation.
- These are provisional bounds, not measured cost estimates. Record model, input/output tokens, latency, repair count and field accuracy in an isolated evaluation report before selecting pilot per-task/daily monetary caps.
- Refuse another model dispatch when either configured cap is exhausted. Deterministic reminders continue. Never estimate monetary spend as zero when pricing is missing.

## Deployment and acceptance

- Local development uses an isolated localhost server. Proposed pilot reuses Plan 6's always-on host behind a trusted HTTPS endpoint over the private network. The user selected Mac hosting for now, and Mac app delivery is a priority alongside phone. Trusted HTTPS/domain choice remains open; no public exposure is provisioned.
- OAuth requires registered exact redirect URI and dedicated test account; configure consent/scopes before enabling connections.
- iPhone and Android physical devices must test installation, keyboard, upload, reconnect and session expiry. Stage 4 additionally requires push opt-in/denial and a seven-day pilot with browser closure and Mac sleep/restart catch-up for this local pilot.
- A successful desktop browser test cannot close these physical-device gates.

## Exit evidence and remaining gates

- [x] Three scenarios and reviewable record/API/recovery/broker contracts.
- [x] Thirty synthetic bilingual fixture cases in `tests/fixtures/personal/capture-v1.json`.
- [x] SQLite schema-v1 initialization, concurrent operation replay, rollback and restart tests for manual records.
- [x] Personal dispatch uses `reserve_dispatch` with a durable unique key, deterministic IDs and cross-store crash tests. General Agent V2 starts still use their existing path.
- [x] [Live extraction evidence](extraction-evaluation-2026-09-29.json): 30/30 after canonical UTC-alias normalization; all ambiguity fixtures request clarification and no expected-null critical field was invented. Median 1.85s, max 6.85s, 164,616 input and 21,281 output tokens across the run.
- [x] Conservative model-usage reservations default to $0.50 per extraction and $2.00 per UTC day, with OpenRouter per-request input/output price ceilings. Live synthetic verification passed; actual invoice accounting and account fees remain outside these reservations. See `setup.md` and `budget-evaluation-2026-09-29.json`.
- [x] Mac execution host selected.
- [ ] Trusted HTTPS and physical-device setup selected.
- [x] Initial [provider matrix](providers.md) researched; unverified rows explicitly deferred. No live provider conformance claimed.
