# 38-9: Cross-Platform Development Mode

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** not-started
**Goal:** Fix hard failures that prevent Development Mode from working on Windows and Linux, and add honest degradation when running outside Electron.

## Context

- The 2026-03-19 development mode reviews found that terminal creation hardcodes `/bin/zsh` in 3+ renderer-side locations, breaking all Windows and Linux users.
- `commandExists()` uses `which` (macOS/Linux only), breaking LSP detection on Windows.
- CodeMode mounts in browser/Vite builds but most IDE features silently no-op outside Electron.
- Extension language providers fire events but don't route provider calls back, making extension-contributed language features non-functional.

## Tasks

### 1. Platform-aware terminal shell defaults
- [ ] 1-1. Replace hardcoded `/bin/zsh` fallback in `TerminalPanel.tsx:147` with platform detection: `powershell.exe` on Windows, `/bin/bash` on Linux, `/bin/zsh` on macOS.
- [ ] 1-2. Fix `ChatSidebar.tsx:453` — use the same platform-aware default instead of `/bin/zsh`.
- [ ] 1-3. Fix `ModeChatSidebar.tsx:776` — same fix.
- [ ] 1-4. Fix `TerminalPanel.tsx:412` shell-command event path — use resolved profile shell instead of hardcoded `/bin/zsh`.

### 2. Add Windows/Linux terminal profiles
- [ ] 2-1. In `useSettingsStore.ts:14-18`, add platform-conditional default profiles: `powershell` + `cmd` on Windows, `bash` + `sh` on Linux, keep `zsh` + `bash` + `sh` on macOS.
- [ ] 2-2. Set the default profile based on detected platform instead of always `zsh`.

### 3. Cross-platform `commandExists()`
- [ ] 3-1. In `lspManager.ts:25-31`, replace `which` with `where` on Windows.
- [ ] 3-2. Add a shared utility that detects platform and uses the correct existence check.

### 4. Non-Electron degradation banner
- [ ] 4-1. Add an `isElectron` check at the top of `CodeMode.tsx` that shows a prominent banner when running in browser/Vite: "Development mode requires the desktop app. Some features are unavailable."
- [ ] 4-2. Disable or gray out features that silently no-op: file explorer "Open Folder", terminal creation, git operations, LSP.
- [ ] 4-3. Keep the mode navigable for demo/preview purposes.

### 5. Make extension language providers functional
- [ ] 5-1. In `extensionHostWorker.ts:174-199`, route `registerCompletionItemProvider`, `registerHoverProvider`, `registerDefinitionProvider` calls back to Monaco via IPC instead of just `sendEvent()`.
- [ ] 5-2. Add a simple round-trip test that an extension-registered completion provider produces completions in the editor.

### 6. LSP auto-restart on crash
- [ ] 6-1. In the LSP manager, detect when a language server process exits unexpectedly and auto-restart it (with exponential backoff, max 3 retries).
- [ ] 6-2. Surface a notification when a language server crashes and is restarting.

## Primary Files

- `editor/src/components/code/TerminalPanel.tsx`
- `editor/src/components/code/ChatSidebar.tsx`
- `editor/src/components/shared/ModeChatSidebar.tsx`
- `editor/src/store/useSettingsStore.ts`
- `editor/src/components/modes/CodeMode.tsx`
- `editor/electron/main.ts`
- `editor/src/lib/lspManager.ts`
- `editor/src/lib/extensionHostWorker.ts`

## Decisions

- Platform detection uses `navigator.platform` / `process.platform` depending on context (renderer vs main process).
- Terminal profiles are platform-conditional at initialization, not at every terminal creation.
- Browser/Vite degradation shows a banner but doesn't hide the mode entirely — useful for demos.

## Estimate

~2 days
