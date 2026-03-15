# 36-5: Paper Acquisition Workflow

**Parent:** [36-recipe-distillation-spec](36-recipe-distillation-spec.md)
**Status:** completed
**Goal:** Reuse the existing workflow engine to automate paper download, canonical naming, summary, and note creation for the user's external paper library and knowledge base.

## Tasks

- [x] 1. Add the missing browser download primitive
  - [x] 1-1. Expose `browser_download` as a built-in tool
  - [x] 1-2. Allow direct destination paths so downloads can land in `~/Dropbox/...`
  - [x] 1-3. Register `browser_download` in computer-control capabilities
- [x] 2. Make external paper-library paths usable by the workflow
  - [x] 2-1. Allow explicit absolute / `~/` paths in built-in file tools
  - [x] 2-2. Make `list_directory` return usable paths outside the workspace root
- [x] 3. Build the end-to-end workflow graph
  - [x] 3-1. University proxy / database navigation
  - [x] 3-2. BibTeX lookup and `paper_id = bibtex_id`
  - [x] 3-3. Save directly to `pdf_root/<bibtex_id>.pdf`
  - [x] 3-4. Summarize and extract metadata
  - [x] 3-5. Write or update `note_root/<bibtex_id>/index.md`
- [x] 4. Link acquisition to the recipe system
  - [x] 4-1. Capture acquisition source in ingredient provenance
  - [x] 4-2. Feed summary + metadata into paper-corpus memory

## Workflow Shape

Use existing nodes only:

- `tool_operator` for browser navigation, `browser_download`, `pdf_read`, `file_write`
- `llm_operator` for summary, metadata extraction, and BibTeX normalization
- `gate` for retry / missing-download / verification branches
- `for_each` for batch import

No new engine runtime is needed.

## Decisions

- Save directly to the final destination path instead of downloading into the workspace and then renaming.
- Use `bibtex_id` as canonical paper identity.
- External paper-library paths are first-class and should not require copying files into the project workspace.

## Notes

- The workflow should assume a persistent browser profile so university logins can survive across sessions.
- The summary/note-writing steps must align with the user's knowledge-base `index.md` conventions.
