# 38-11: Packaged LSP Launching

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Make the editor's bundled Node-based language servers launch reliably in packaged or cwd-shifted desktop runs instead of depending on `npx` lookup from the `editor/` folder.

## Tasks
- [x] 1. Replace cwd-sensitive bundled LSP launches.
 - [x] 1-1. Resolve bundled package bin paths from the app's own `node_modules` instead of ambient PATH lookup.
 - [x] 1-2. Launch bundled Node CLIs via `process.execPath` plus `ELECTRON_RUN_AS_NODE=1`.
 - [x] 1-3. Pass the workspace root as child `cwd` so project resolution does not depend on the app launch directory.
- [x] 2. Ship runtime-needed LSP packages in desktop builds.
 - [x] 2-1. Move `typescript`, `typescript-language-server`, `pyright`, and `vscode-langservers-extracted` into runtime dependencies.
 - [x] 2-2. Sync the editor lockfile after the dependency move.
- [x] 3. Add focused regression coverage.
 - [x] 3-1. Assert bundled language servers launch via `process.execPath` instead of `npx`.

## Decisions
- Bundled Node-based language servers should not depend on `npx` or the app's current working directory; only true system-binary servers (`gopls`, `rust-analyzer`, `clangd`) should launch directly by command name.
- Use `process.execPath` with `ELECTRON_RUN_AS_NODE=1` so packaged Electron builds can run bundled JS CLIs without a separate Node installation.

## Notes
- Root cause reproduced from the repo root: `npx --no-install vscode-json-language-server --stdio` exits with npm 404 because the bin name is not a package name once `editor/node_modules/.bin` is out of scope.
- Validation: `cd editor && npm run test -- --run src/__tests__/lspManager.test.ts` and `cd editor && npm run electron:compile`.
- `npm install` hit an existing local `node-pty` rebuild/toolchain issue (`<functional> file not found`), so the dependency metadata was synced with `npm install --package-lock-only --ignore-scripts`.
