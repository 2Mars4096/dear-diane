# Development Mode Review

Date: 2026-03-19

Scope reviewed:
- `editor/src/components/modes/CodeMode.tsx`
- `editor/src/components/code/*` development surfaces
- `editor/src/lib/electronBridge.ts`
- `editor/electron/main.ts`
- relevant backend capability/dispatch tests for development-mode chat and tool use

## Overall verdict

Mostly yes on the packaged Electron path: the editor build passes, Electron compiles, the mode-chat tests pass, and the backend capability/dispatch suites I ran were green. On macOS desktop, the product is close to a real daily-driver development surface.

It is not yet at a clean "works like Codex / Claude Code / Cursor" bar across all advertised environments, though. The original review's cross-platform terminal defaults and missing truthfulness/gating outside Electron have now been addressed; the main still-open issue from this review is the presence of a few still-placeholder development panels.

## Resolution updates (2026-03-19)

- **Addressed:** cross-platform terminal defaults, browser/Vite degradation truthfulness, extension provider round-tripping, and LSP restart/self-healing.
- **Still open:** placeholder Workflow/Furnace/secondary development panels that still look more complete than they are.

## Findings

### P1: Windows/Linux terminal flows are broken by hard-coded `/bin/zsh` defaults *(Addressed 2026-03-19)*

- `editor/package.json:21-22` advertises `dist:win` and `dist:linux`.
- `editor/src/store/useSettingsStore.ts:14-18` seeds terminal profiles with only Unix shells and sets `zsh` as the default at `editor/src/store/useSettingsStore.ts:94-95`.
- `editor/src/components/code/TerminalPanel.tsx:147` falls back to `/bin/zsh`, and the shell-command event path at `editor/src/components/code/TerminalPanel.tsx:412` explicitly creates terminals with `shell: "/bin/zsh"`.
- `editor/src/components/code/ChatSidebar.tsx:453` also hard-codes `shell: "/bin/zsh"` for "Run in Terminal".
- This bypasses the platform-aware fallback already present in `editor/electron/main.ts:507`, which would otherwise choose `powershell.exe` on Windows.
- User impact:
  - fresh Windows builds will fail to open the default terminal out of the box.
  - Linux machines without `zsh` installed will hit the same issue.
  - higher-level development flows built on terminal execution, including `Apply & Test` in `editor/src/components/modes/CodeMode.tsx:777-803`, inherit the same breakage.

**Resolution:** Renderer-side terminal creation now uses platform-aware shell/profile defaults across `TerminalPanel.tsx`, `ChatSidebar.tsx`, `ModeChatSidebar.tsx`, and `useSettingsStore.ts`, with shared test coverage for terminal profile selection.

### P2: Development mode is mounted in browser/Vite builds even though most IDE capabilities silently degrade outside Electron *(Addressed 2026-03-19)*

- `editor/package.json:10` defines `npm run dev` as plain `vite`, while `editor/package.json:16` uses a separate `electron:dev` path.
- `editor/src/components/shell/AppShell.tsx:232-240` always mounts `CodeMode`; there is no top-level Electron gate or degraded-mode banner in `editor/src/components/modes/CodeMode.tsx:467-620`.
- Core native bridges no-op or fail outside Electron:
  - `editor/src/lib/electronBridge.ts:254-258` makes `openDirectory()` return `null`.
  - `editor/src/lib/electronBridge.ts:269-347` makes fs reads/writes/dir operations return `null` or `false`.
  - `editor/src/lib/electronBridge.ts:362-507` makes git operations return `stderr: "Not in Electron"`.
  - `editor/src/lib/electronBridge.ts:509-537` makes terminal creation return `null`.
  - `editor/src/lib/electronBridge.ts:554-634` makes LSP requests return `null`.
- The user-facing development shell still presents those features as available:
  - `editor/src/components/code/FileExplorer.tsx:1017-1019` uses `openDirectory()` for the empty-state "Open Folder" action, so in browser mode the button is effectively a no-op.
  - the rest of Code mode still renders the normal file/git/terminal/LSP/chat surfaces.
- User impact:
  - a user running the browser/Vite client can enter Development mode and see a full IDE shell that cannot actually open a workspace folder or perform the production-grade local actions the UI implies.
  - that mismatch is especially costly because `npm run dev` is the most obvious local entrypoint.

**Resolution:** `CodeMode.tsx` now shows an explicit non-Electron degradation banner, and the desktop-only folder/terminal/git/LSP actions are guarded so the browser path is honest instead of silently no-oping.

### P3: Development mode still exposes unfinished panels as if they are first-class tools

- `editor/src/components/modes/CodeMode.tsx:85-121` defines `WorkflowSidebarPanel()` and `FurnaceSidebarPanel()` as static copy plus one generic button each.
- `editor/src/components/modes/CodeMode.tsx:900-912` exposes both panels directly in the activity bar.
- `editor/src/components/code/ProblemsPanel.tsx:155` defines internal `Problems` / `Output` / `Debug Console` tabs, but `editor/src/components/code/ProblemsPanel.tsx:466-469` still renders "coming soon" for the non-Problems tabs.
- User impact:
  - the core coding path is stronger than these panels suggest, but the shell still advertises partially implemented tooling in places where users expect production-ready behavior.
  - that hurts trust for a "replace Cursor" workflow even when the underlying editor/chat/terminal path is otherwise healthy.

## Validation notes

Passing:
- `cd editor && npm run build`
  - build completed successfully; Vite warned that local Node `20.17.0` is below its recommended `20.19+`, but the build still passed.
- `cd editor && npm run electron:compile`
- `cd editor && npx vitest run src/components/shared/__tests__/ModeChatSidebar.test.ts src/lib/__tests__/editorChat.test.ts`
- `pytest -q tests/test_server/test_shell_command_capability.py tests/test_server/test_app_terminal_output.py tests/test_server/test_capability_registry.py tests/test_concierge/test_fast_commands.py tests/test_concierge/test_tiered_dispatch.py`
- `pytest -q tests/test_server/test_capability_exposure.py tests/test_server/test_capability_run_lifecycle.py`

No new code was edited during the original review pass; see the resolution updates above for the fixes that landed afterward.
