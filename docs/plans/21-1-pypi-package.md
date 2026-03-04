# 21-1: PyPI Package

**Parent:** [21-author-distribute](21-author-distribute.md)
**Status:** completed
**Goal:** Clean up the package structure, define the public API surface, support both monolithic and split installs, set version 0.1.1, and make `pip install dan` work with proper entry points, metadata, and documentation.

## Context

`pyproject.toml` already exists with hatchling build, `dan` package name, optional dependency groups, and a `dan-serve` entry point. This plan formalizes the public API, adds CLI entry points for Phase 12 commands, ensures clean imports, and prepares for PyPI publication.

## Tasks

- [x] 1. **Public API audit and `__init__.py` cleanup**
  - [x] 1-1. Audit `src/dan/__init__.py` — added `HumanNode` export
  - [x] 1-2. Audit `src/dan/engine/__init__.py` — confirmed public surface, added HumanRenderer exports
  - [x] 1-3. Audit `src/dan/builder/__init__.py` — confirmed `workflow`, `decompile`, `NodeRef`, `PortRef` are public
  - [x] 1-4. Audit `src/dan/loader/__init__.py` — confirmed `load`, `load_agents`, `compile_workflow` are public
  - [x] 1-5. Populated `src/dan/meta/__init__.py` with `MetaController`, `MetaControllerConfig`, `WorkflowPlanner`, `DiscoveryService`, `RepairEscalator`, etc.
  - [x] 1-6. Exported `HumanRenderer`, `HumanRenderRequest`, `HumanRenderResponse`, `AutoRenderer`, `ProgrammaticRenderer`, `LegacyCallbackRenderer` from `dan.engine`
  - [x] 1-7. Exported `HumanNode` alongside `HumanInTheLoopNode` from `dan.__init__.py`
  - [x] 1-8. All public `__init__.py` files have `__all__` lists
  - [x] 1-9. Verified no circular imports

- [x] 2. **Package metadata and version**
  - [x] 2-1. Set version to `0.1.1`
  - [x] 2-2. Added `authors`, `license`, `readme`, `keywords`, `classifiers`
  - [x] 2-3. Documented rename procedure in `docs/PACKAGE_SPLIT.md`
  - [x] 2-4. Added `project.urls` (Homepage, Repository, Documentation, Changelog, Issues)
  - [x] 2-5. Confirmed `[tool.hatch.build.targets.wheel]` packages = `["src/dan"]`

- [x] 3. **Entry points for CLI commands**
  - [x] 3-1. `dan-run` → `dan.cli.run:main`
  - [x] 3-2. `dan-status` → `dan.cli.status:main`
  - [x] 3-3. `dan-logs` → `dan.cli.logs:main`
  - [x] 3-4. `dan-publish` → `dan.cli.publish:main`
  - [x] 3-5. `dan-adapter` → `dan.cli.adapter:main`
  - [x] 3-6. `dan-blocks` → `dan.cli.blocks:main`
  - [x] 3-7. Existing `dan-serve` unchanged
  - [x] 3-8. Created `src/dan/cli/` package with placeholder modules (later replaced by 21-2 through 21-5)

- [x] 4. **Optional dependency groups for Phase 12**
  - [x] 4-1. `cli` group: `rich>=13.0`
  - [x] 4-2. `mcp` group: `mcp>=1.0`
  - [x] 4-3. `messaging` group: `python-telegram-bot>=21.0`, `aiosmtplib>=3.0`
  - [x] 4-4. Updated `all` group with new deps
  - [x] 4-5. FastAPI/uvicorn/websockets kept as core deps (decision documented)

- [x] 5. **Split package support (optional install paths)**
  - [x] 5-1. Defined split boundaries in PACKAGE_SPLIT.md
  - [x] 5-2. Single `dan` package with optional groups for now
  - [x] 5-3. Created `docs/PACKAGE_SPLIT.md`

- [x] 6. **Editor static assets**
  - [x] 6-1. Documented in PACKAGE_SPLIT.md (option b: separate npm build step)
  - [x] 6-2. Option (b) chosen for initial release
  - [x] 6-3. Added `editor/dist/` to `.gitignore`

- [ ] 7. **Build and test pipeline** *(deferred — requires clean venv verification)*
  - [ ] 7-1. Verify `python -m build` produces a valid wheel
  - [ ] 7-2. Verify `pip install` in clean venv
  - [ ] 7-3. Verify `import dan; dan.engine.Engine` after install
  - [ ] 7-4. Verify entry points on PATH
  - [ ] 7-5. Run test suite against installed package

- [x] 8. **Documentation for PyPI**
  - [x] 8-1. README.md has install instructions and feature summary
  - [x] 8-2. docs/changelog.md exists and is comprehensive
  - [x] 8-3. Created LICENSE file (MIT)

## Decisions

- Single package for now; split packages are a future optimization. The split boundary is documented but not enforced.
- Version 0.1.1 signals "real but not frozen." Follows semver pre-1.0 conventions. Current `0.2.0` in pyproject.toml was never published to PyPI, so the reset is safe.
- Package name `dan` is used. Renaming requires: pyproject.toml `name` change + `src/dan/` directory rename + import updates. Documented but not automated.
- Editor static assets are optional in the wheel — the server can run without the editor (headless mode).
- `rich` is an optional dependency under `[cli]` group, not a core requirement.
- FastAPI/uvicorn/websockets remain core dependencies unless lazy-import refactoring proves feasible.
- `HumanRenderer`, `HumanRenderRequest`, `HumanRenderResponse` are promoted to public API exports from `dan.engine` — these are the integration points for CLI, messaging, and publish.

## Notes

- This plan is foundational — 21-2 through 21-5 all depend on the package structure and entry points established here.
- The existing `pyproject.toml` is already close to publishable; this is primarily cleanup, formalization, and adding new entry points.
- No code logic changes to engine/builder/loader — purely packaging and API surface work.
