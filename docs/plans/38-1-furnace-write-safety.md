# 38-1: Furnace Write Safety

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Eliminate path traversal via `source_id` in Furnace artifact writes and prevent source ID collisions from silently merging unrelated inputs.

## Context

`source_id` values are accepted from callers and used directly in file paths for artifact writes. A malicious or malformed `source_id` like `../../escape` causes writes outside the per-session artifact directory. Additionally, source IDs derived from PDF filenames or URL slugs can collide (two different `paper.pdf` files in different directories map to the same ID).

Relevant code:
- `src/dan/server/routers/furnace.py`: `add_sources()` (L414–418 uses raw `source_ids`), `_execute_furnace_pipeline()` (L839–850 writes artifacts, L888 reads artifacts), `_url_to_source_id()` (L199–209)
- `src/dan/engine/recipe/session_store.py`: `paper_queue: dict[str, PaperStatus]` keyed by `paper_id`

There is currently no sanitization of `source_id` anywhere in the furnace router. The `_slug_fragment()` helper exists but is only used for `corpus_id`/`recipe_id`/`variant_label`, not `source_id`.

## Tasks

- [x] 1. Sanitize `source_id` on ingestion
  - [x] 1-1. Add a `_sanitize_source_id()` helper that strips path separators, `..` components, and non-filename characters
  - [x] 1-2. Apply sanitization in `add_sources()` before storing the ID
  - [x] 1-3. Apply sanitization in `_url_to_source_id()`
- [x] 2. Add artifact path containment check
  - [x] 2-1. Before every artifact write **and read** in `_execute_furnace_pipeline()`, resolve the full path and assert it is inside the session artifact directory (writes at L839–850, read at L888)
  - [x] 2-2. Raise a clear error if the containment check fails
  - [x] 2-3. Check downstream uses in `acquisition.py` where `paper_id` flows into file paths (e.g. `f"{paper_id}.pdf"`, `paper_id + "/index.md"`)
- [x] 3. Prevent source ID collisions
  - [x] 3-1. For PDF sources, incorporate a hash of the full path (not just the stem) into the source ID. Note: `Path(expanded).stem` (L428) can yield `".."` for paths ending with `..`, which is itself a traversal vector
  - [x] 3-2. For URL sources, incorporate enough of the URL (host + path) to avoid slug-only collisions
  - [x] 3-3. Add a collision check at ingestion time — warn or rename if a derived ID already exists in the session
- [x] 4. Add tests
  - [x] 4-1. Test that path traversal source IDs are rejected or sanitized (e.g. `../../etc/passwd`, `../escape`)
  - [x] 4-2. Test that artifact writes always land inside the session directory
  - [x] 4-3. Test that two PDFs with the same filename but different directories get distinct IDs
  - [x] 4-4. Test that two URLs with the same slug but different hosts get distinct IDs

## Decisions

- Canonicalize source IDs at ingestion time and keep the sanitized value as the persisted queue key.
- Use artifact-path containment checks at every dynamic source artifact read/write instead of trusting queue keys.
- For derived IDs, prefer readable slugs plus stable short hashes so collisions are avoided without losing operator-facing context.

## Notes

- `Path("/safe/root") / "../../escape.txt"` resolves to `/private/tmp/escape.txt` on macOS — confirmed in the review.
- The session store uses `paper_id` as key, so collisions silently overwrite queue entries.
- The artifact read path at L888 (`txt_path = artifact_dir / f"{sid}.txt"`) has the same traversal risk as the writes — a malicious `source_id` can cause reads outside the session directory.
- `_url_to_source_id()` only derives IDs from URLs (last path segment stem, truncated to 40 chars) so it doesn't produce `..` or slashes today, but sanitizing its output is defense-in-depth.
- Implemented in `furnace.py`, `acquisition.py`, and `workflows.py`, with focused regressions in `tests/test_furnace_api.py`.
