# 47: Capability Surface Runtime Alignment

**Status:** completed
**Goal:** Keep capability-tool surfaces aligned with the canonical runtime schema so typed models and cwd-anchored file tools do not fail on stale adapter assumptions or deprecated validator literals.

## Tasks
- [x] 1. Fix typed capability handlers to serialize runtime models correctly
  - [x] 1-1. Stop assuming legacy `.type` / `.config` attributes on runtime node models.
  - [x] 1-2. Return canonical `node_type`, dumped node-specific config, and ports from the typed `Graph` model.
- [x] 1-3. Stop assuming dict-style `.get(...)` access on typed `ActivitySnapshot` payloads.
- [x] 2. Remove stale validator-rule defaults
  - [x] 2-1. Replace generated `format_check` defaults with schema-valid validator rules.
  - [x] 2-2. Normalize legacy `format_check` mutation payloads during chat-side repair.
- [x] 3. Make file-tool workspace resolution independent from raw process cwd
  - [x] 3-1. Route file-tool workspace fallback through `resolve_workspace_root()` instead of `os.getcwd()`.
- [x] 4. Add focused regressions
  - [x] 4-1. Cover `inspect_node` against a typed `CodeOperator`.
  - [x] 4-2. Cover `get_activity` against a typed `ActivitySnapshot`.
  - [x] 4-3. Cover `list_directory(path='.')` when `os.getcwd()` is unavailable.
  - [x] 4-4. Re-run the validator/generation regression slice plus the live `.env` integration subset.

## Decisions
- Capability handlers should treat typed Pydantic payloads as the source of truth and derive stable responses from `model_dump(...)`, rather than reviving older dict/adaptor assumptions.
- Deprecated validator-rule literals should be normalized at generation/mutation boundaries instead of accepted in the canonical schema.
- File-system tools should fall back through `resolve_workspace_root()` rather than raw `os.getcwd()` so capability calls survive missing/deleted cwd states.

## Notes
- The live `.env` rerun exposed a separate remaining bug: auto-inserted validator nodes in `DefaultsEnricher` still do not declare the `data` input port they are rewired to receive.
