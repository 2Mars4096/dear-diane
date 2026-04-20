# 3-2: Markdown Editor and Frontmatter

**Parent:** [3-content-mode-and-live-preview](3-content-mode-and-live-preview.md)
**Status:** not-started
**Goal:** Build a page-level authoring surface for existing Markdown content pages with structured frontmatter editing, a real markdown editor, and Hugo-aware authoring helpers.

## Context

- The target content workflow is Markdown-first: frontmatter plus body text remain the canonical file format.
- The initial Hugo knowledge-base target uses YAML frontmatter, leaf-bundle pages, citations, shortcodes, math, and inline HTML, so a trivial WYSIWYG editor would immediately lose fidelity.
- DAN already ships Monaco-based editing surfaces in Development mode; Phase 1 should reuse that editor foundation where practical instead of introducing a second large editor stack.
- The primary risk is data loss or unwanted reformatting: the editor model must preserve content accurately even when frontmatter is invalid or partially edited.

## No-Change Zones

- Keep raw Markdown editable at all times; no rich-text-only surface.
- Keep frontmatter/body separation explicit rather than hiding metadata behind a CMS abstraction.
- Keep Phase 1 focused on existing pages; do not couple this plan to new-page generation or media management.

## Tasks

- [ ] 1. Define the page file model
  - [ ] 1-1. Split a page into raw frontmatter plus markdown body while preserving exact disk content for round-tripping
  - [ ] 1-2. Parse YAML frontmatter into a structured model with precise diagnostics when parsing fails
  - [ ] 1-3. Infer the page type/section from path and frontmatter without hardcoding only one repo shape
- [ ] 2. Build the editing surfaces
  - [ ] 2-1. Add a Markdown editor surface for the page body
  - [ ] 2-2. Add a structured frontmatter form for the common fields users should not have to type manually every time
  - [ ] 2-3. Keep a raw frontmatter escape hatch for uncommon keys and exact manual edits
  - [ ] 2-4. Add an outline/heading navigator for longer pages
- [ ] 3. Add Hugo-aware authoring helpers
  - [ ] 3-1. Snippet insertion for common shortcodes and page patterns
  - [ ] 3-2. Citation/page reference helpers that accelerate `@pageID`-style linking without changing the underlying text format
  - [ ] 3-3. Content-type-aware field hints for notes, blogs, papers, logs, and similar page families
- [ ] 4. Handle draft state and external edits safely
  - [ ] 4-1. Keep an in-memory dirty draft separate from the last disk version
  - [ ] 4-2. Detect external file changes and offer reload/merge choices instead of silent overwrite
  - [ ] 4-3. Preserve editing state through mode switches and workspace restores
- [ ] 5. Verify editor correctness
  - [ ] 5-1. Add parsing/serialization regressions for valid and invalid frontmatter cases
  - [ ] 5-2. Add UI coverage for structured frontmatter editing plus raw frontmatter fallback

## User-Facing Acceptance

- A user can edit both frontmatter and body without losing Markdown fidelity.
- Common metadata fields are faster to edit than in a raw text editor, but unusual fields are still accessible.
- Hugo-specific writing patterns like shortcodes and citations remain plain-text authoring workflows, just with better assistance.

## Decisions

- Phase 1 stays Markdown-first; no block editor or rich-text storage layer.
- Reuse existing editor infrastructure where possible instead of introducing a second heavyweight editor stack just for Content mode.
- Structured frontmatter editing is additive; raw frontmatter remains available as the source-of-truth escape hatch.

## Notes

- The first success bar is not "beautiful prose tooling"; it is faithful editing for real pages that already exist.
- If content-type-specific forms become too broad, bias toward a compact common field set plus raw YAML fallback.
