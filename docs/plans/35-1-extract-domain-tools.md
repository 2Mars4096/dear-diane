# 35-1: Extract Domain Tools from app.py

**Parent:** [35-server-module-decomposition](35-server-module-decomposition.md)
**Status:** completed
**Goal:** Move the domain-specific tool implementations out of `app.py` into a `server/tools/` package, while keeping tool IDs, registry behavior, and startup wiring stable.

## Current State

Lines 234–1220 of `app.py` contain tool implementations that have no business in an HTTP server module:

- **LaTeX/paper tools** (~390 lines): `_slugify`, `_normalize_latex_content`, `_ensure_informs3_cls`, `_compile_latex`, `_save_paper`, `_package_submission`, `_extract_citation_keys`, `_extract_bib_keys`, `_build_placeholder_bib_entries`, `_run_command`, `_http_get_json`
- **Research tools** (~90 lines): `_search_papers`, `_citation_verifier`, `_check_latex_deps`
- **Quant/backtest tools** (~400 lines): `_run_strategy_script`, `_run_backtest`, `_save_grid_csv`, `_plot_backtest` (two are already marked DEPRECATED)
- **General tools** (~120 lines): `_run_python`, `_rag_index_documents`, `_get_department_state`, `_update_department_state`

## Safety Rules

- Keep tool IDs exactly unchanged.
- Keep `_build_tool_registry()` behavior unchanged from the caller's perspective.
- Do not rename callables during extraction unless the old name remains as an alias in the new module.
- Do not make `server/tools/*` import `app.py`; inject dependencies instead.

## Tasks

- [x] 1. Create `src/dan/server/tools/__init__.py`
  - [x] 1-1. Re-export the extracted tool functions
  - [x] 1-2. Expose a single `register_server_tools(...)` helper
- [x] 2. Create `src/dan/server/tools/_shared.py`
  - [x] 2-1. Move `_http_get_json`, `_run_command`
  - [x] 2-2. Add only low-level helpers shared across modules
- [x] 3. Create `src/dan/server/tools/latex.py`
  - [x] 3-1. Move `_slugify`, `INFORMS3_CLS_URLS`, `_CITE_PATTERN`, `_BIB_ENTRY_PATTERN`
  - [x] 3-2. Move `_extract_citation_keys`, `_extract_bib_keys`, `_build_placeholder_bib_entries`
  - [x] 3-3. Move `_normalize_latex_content`, `_ensure_informs3_cls`
  - [x] 3-4. Move `_compile_latex`, `_save_paper`, `_package_submission`, `_check_latex_deps`
- [x] 4. Create `src/dan/server/tools/research.py`
  - [x] 4-1. Move `_search_papers`, `_citation_verifier`
- [x] 5. Create `src/dan/server/tools/quant.py`
  - [x] 5-1. Move `_run_strategy_script` and its inner helper functions
  - [x] 5-2. Move `_run_backtest`
  - [x] 5-3. Move `_save_grid_csv`, `_plot_backtest` with deprecation comments preserved
- [x] 6. Create `src/dan/server/tools/general.py`
  - [x] 6-1. Move `_run_python`
  - [x] 6-2. Move `_get_department_state`, `_update_department_state`
  - [x] 6-3. Implement `rag_index_documents` as an injected factory/closure, not via direct `app.py` import
- [x] 7. Add `register_server_tools(registry, *, get_indexer=None)` to centralize tool registration
- [x] 8. Update `_build_tool_registry()` in `app.py` to delegate to `register_server_tools(...)`
- [x] 9. Extraction-focused verification
  - [x] 9-1. Import smoke test — all exports from `dan.server.tools` resolve
  - [ ] 9-2. Run targeted tests for moved tool families (deferred — no existing tool-level tests)
  - [ ] 9-3. Server startup smoke test (deferred — requires live server)
- [x] 10. Defer cosmetic cleanup
  - [x] 10-1. Keep old underscore names or aliases during extraction
  - [x] 10-2. Only remove compatibility aliases in a later cleanup PR

## Decisions

- `_rag_index_documents` takes `get_indexer` as its first parameter; `register_server_tools` wraps it in a closure with the standard signature so the registry sees a normal `(pdf_dir, collection, glob_pattern, **kwargs)` callable.
- `Path(__file__).resolve().parents[3]` updated to `parents[4]` in quant.py and general.py since the file moved one level deeper.
- `urllib.parse` import kept in `app.py` per instruction (even though the only direct usage was in the extracted `_search_papers`). `zipfile` and `urllib.request` removed. `execute_python` import removed from `app.py`.

## Notes

- `_slugify` stays with the latex tool family.
- `rag_index_documents` is the one tool here that currently depends on app-level RAG state. Treat it as a dependency-injected tool, not as a reason for `server/tools/*` to know about `app.py`.
- This sub-plan is safe to run in parallel with Plan 34 as long as `_build_tool_registry()` remains a stable boundary and the diff in `app.py` stays minimal.
