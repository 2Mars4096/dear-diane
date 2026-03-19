# 38-9: Cross-Platform Development Mode

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Fix hard failures that prevent Development Mode from working on Windows and Linux, and add honest degradation when running outside Electron.

## Context

- The 2026-03-19 development mode reviews found that terminal creation hardcodes `/bin/zsh` in 3+ renderer-side locations, breaking all Windows and Linux users.
- `commandExists()` uses `which` (macOS/Linux only), breaking LSP detection on Windows.
- CodeMode mounts in browser/Vite builds but most IDE features silently no-op outside Electron.
- Extension language providers fire events but don't route provider calls back, making extension-contributed language features non-functional.

## Tasks

### 1. Platform-aware terminal shell defaults
- [x] 1-1. Replace hardcoded `/bin/zsh` fallback in `TerminalPanel.tsx:147` with platform detection: `powershell.exe` on Windows, `/bin/bash` on Linux, `/bin/zsh` on macOS.
- [x] 1-2. Fix `ChatSidebar.tsx:453` — use the same platform-aware default instead of `/bin/zsh`.
- [x] 1-3. Fix `ModeChatSidebar.tsx:776` — same fix.
- [x] 1-4. Fix `TerminalPanel.tsx:412` shell-command event path — use resolved profile shell instead of hardcoded `/bin/zsh`.

### 2. Add Windows/Linux terminal profiles
- [x] 2-1. In `useSettingsStore.ts:14-18`, add platform-conditional default profiles: `powershell` + `cmd` on Windows, `bash` + `sh` on Linux, keep `zsh` + `bash` + `sh` on macOS.
- [x] 2-2. Set the default profile based on detected platform instead of always `zsh`.

### 3. Cross-platform `commandExists()`
- [x] 3-1. In `lspManager.ts:25-31`, replace `which` with `where` on Windows.
- [x] 3-2. Add a shared utility that detects platform and uses the correct existence check. *(fixed 2026-03-19: `editor/electron/commandExists.ts` now owns the Windows/Unix probe logic and is reused by both `lspManager.ts` and `main.ts`, with focused Vitest coverage for `.exe` fallback behavior.)*

### 4. Non-Electron degradation banner
- [x] 4-1. Add an `isElectron` check at the top of `CodeMode.tsx` that shows a prominent banner when running in browser/Vite: "Development mode requires the desktop app. Some features are unavailable."
- [x] 4-2. Disable or gray out features that silently no-op: file explorer "Open Folder", terminal creation, git operations, LSP.
- [x] 4-3. Keep the mode navigable for demo/preview purposes.

### 5. Make extension language providers functional
- [x] 5-1. In `extensionHostWorker.ts:174-199`, route `registerCompletionItemProvider`, `registerHoverProvider`, `registerDefinitionProvider` calls back to Monaco via IPC instead of just `sendEvent()`. *(2026-03-19: the extension host worker now assigns provider IDs, keeps provider objects alive in the worker, and handles `invokeLanguageProvider` IPC requests; Electron main/preload/bridge and `useMonacoLsp` now register/dispose Monaco providers dynamically from extension-host events.)*
- [x] 5-2. Add a simple round-trip test that an extension-registered completion provider produces completions in the editor. *(2026-03-19: added `editor/src/lib/__tests__/extensionLanguageProviders.test.ts`.)*

### 6. LSP auto-restart on crash
- [x] 6-1. In the LSP manager, detect when a language server process exits unexpectedly and auto-restart it (with exponential backoff, max 3 retries). *(2026-03-19: `LspManager` now stores root URIs, tracks retry budgets, and auto-restarts crashed servers after 1s/2s/4s delays up to 3 times.)*
- [x] 6-2. Surface a notification when a language server crashes and is restarting. *(2026-03-19: `LspManager` now emits `$/dan/serverStatus` lifecycle notifications over the existing `lsp:notification` IPC channel, and `useMonacoLsp` converts restart/exhausted notifications into warning/error toasts.)*

## Primary Files

- `editor/src/components/code/TerminalPanel.tsx`
- `editor/src/components/code/ChatSidebar.tsx`
- `editor/src/components/shared/ModeChatSidebar.tsx`
- `editor/src/store/useSettingsStore.ts`
- `editor/src/components/modes/CodeMode.tsx`
- `editor/src/components/code/FileExplorer.tsx`
- `editor/src/components/code/GitPanel.tsx`
- `editor/electron/main.ts`
- `editor/src/hooks/useMonacoLsp.ts`
- `editor/src/hooks/useLspDocSync.ts`
- `editor/src/lib/extensionHostWorker.ts`

## Decisions

- Platform detection uses `navigator.platform` / `process.platform` depending on context (renderer vs main process).
- Terminal profiles are platform-conditional at initialization, not at every terminal creation.
- Browser/Vite degradation shows a banner but doesn't hide the mode entirely — useful for demos.

## Notes

- Added `editor/src/lib/terminalProfiles.ts` plus `editor/src/__tests__/terminalProfiles.test.ts` so renderer-side terminal defaults are covered independently of Electron runtime wiring.
- Added `editor/electron/commandExists.ts` plus `editor/src/__tests__/commandExists.test.ts` so Electron-side binary detection uses one shared Windows/Unix probe implementation instead of duplicated `which`/`where` helpers.
- `useSettingsStore.ts` now normalizes persisted legacy `/bin/zsh`-first terminal settings onto the current platform defaults, so existing Linux/Windows installs pick up the cross-platform fix without requiring a manual settings reset.
- Browser/Vite Code mode now stays honest instead of silently failing: `CodeMode.tsx` shows a degradation banner, LSP hooks early-return when Electron APIs are unavailable, and `FileExplorer`, `TerminalPanel`, `GitPanel`, `ChatSidebar`, and `ModeChatSidebar` now guard desktop-only folder/terminal/git actions.
- Added `editor/src/lib/extensionLanguageProviders.ts` so extension-host language providers are registered/disposed through one renderer-side bridge instead of bespoke Monaco wiring, with a focused round-trip completion regression under `src/lib/__tests__/extensionLanguageProviders.test.ts`.
- Added `editor/src/__tests__/lspManager.test.ts` so the Electron LSP manager's restart/backoff/exhaustion behavior is covered without spawning real language server binaries.

## Estimate

~2 days
