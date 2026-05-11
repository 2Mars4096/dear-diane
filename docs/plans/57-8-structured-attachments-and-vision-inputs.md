# 57-8: Structured Attachments and Vision Inputs

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Make files, screenshots, images, and figures first-class inputs to V2 triage and Agent runs so Telegram photo/document turns and frontend uploads are handled reliably.

## Tasks
- [x] 1. Define `AttachmentRef`
  - [x] 1-1. Fields: id, source surface, native file/message ids, kind, mime type, local path, display name, caption, size, checksum, created timestamp, and retention policy
  - [x] 1-2. Classify kind as `image`, `figure`, `pdf`, `document`, `audio`, `video`, `data`, or `unknown`
  - [x] 1-3. Include optional extracted text or preview metadata without making extraction mandatory at ingress
  - [x] 1-4. Preserve a compatibility parser for legacy `[Attachment: path]` text markers
- [ ] 2. Add a safe attachment ingestion contract
  - [ ] 2-1. Store downloaded Telegram/WhatsApp/frontend files under a contained media root
  - [ ] 2-2. Deduplicate by native file id/checksum where possible
  - [ ] 2-3. Enforce size, extension, and path-containment guards before any worker sees the path
  - [ ] 2-4. Record cleanup/retention behavior for private vs shared surfaces
- [ ] 3. Thread attachments through triage and Agent runs
  - [x] 3-1. Include attachment refs in `SurfaceTurn`
  - [x] 3-2. Let triage use attachment presence/caption/reply context for task binding and Chat vs Agent routing
  - [ ] 3-3. Pass selected attachment refs into `AgentRunRequest` and `PolicyEnvelope`
  - [ ] 3-4. Preserve attachment refs in chat thread history and task snapshots
- [ ] 4. Handle figure and image understanding
  - [ ] 4-1. Route image/figure attachments to `image_describe` or a vision-capable worker when the user asks about the visual content
  - [ ] 4-2. For multi-image turns, preserve ordering and captions
  - [ ] 4-3. If the user sends a figure without a task, ask a concise clarification or default to description/extraction depending on mode
  - [ ] 4-4. Make generated/changed figure artifacts appear in Agent result artifacts and final summaries
- [ ] 5. Validate with focused attachment tests
  - [x] 5-1. Telegram photo plus caption creates a structured image attachment
  - [ ] 5-2. Telegram document/PDF gets suggested `pdf_read` or `file_read` routing
  - [x] 5-3. Legacy text attachment marker still maps into an attachment ref
  - [ ] 5-4. Path traversal or missing files fail before Agent execution
  - [ ] 5-5. Figure attachments are visible in task snapshots and final result links

## Decisions
- V2 attachment handling is structured. Text markers are compatibility, not the source of truth.
- Attachment ingestion is surface-agnostic; Telegram, WhatsApp, frontend, and CLI upload paths should converge on `AttachmentRef`.
- Vision work belongs in the Agent/worker layer when it is part of a task, but triage may use lightweight metadata and captions for routing.

## Notes
- 2026-04-29: First slice landed structured attachment refs from primary `attachment_path`, `surface_context.appended_attachments`, and legacy text markers, with filename-based kind inference and focused tests.
- Existing chat attachment prompt context can be reused as a fallback while V2 moves to structured refs.
- This plan is the explicit home for "figure input" in the Chat/Agent V2 control plane.
