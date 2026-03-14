# 1: DAN Custom UI — Master Specification

**Status:** planning
**Goal:** Build a workspace-based desktop app that is as convenient as ChatGPT for simple tasks, as powerful as Cursor/VS Code for coding, and fully leverages DAN's workflow orchestration for complex tail tasks.

## Vision

DAN's engine supports multi-agent workflows, typed graph orchestration, memory, self-evolution, and domain learning. But the current delivery surfaces don't fully expose this power:

- **Messaging (Telegram/WhatsApp):** Good as remote control. Cannot support spatial layouts.
- **CLI (`dan-chat`):** Powerful for power users. No visual layout.
- **Workflow editor:** Specialized for building workflow graphs. Not for doing actual work.

The custom UI fills this gap: a desktop application with **workspace-based layout modes** that arrange DAN's capabilities for specific types of work. The primary goal is to replace Cursor/VS Code as the development environment and ChatGPT/Claude as the chat interface.

## Core Design Principle: Modes = Layout Presets

Modes control **which panels you see and how they're arranged**, not what the AI can do. The underlying capabilities are identical across modes — it's one stove, but Chinese cooking and Western cooking need different counter layouts.

This is a critical distinction from the backend's behavioral modes (`ask` / `conversation` / `agent` / `plan` / `debug`), which control how the AI responds (read-only vs tool-enabled chat vs take action vs plan vs debug). Those behavioral modes are:
- **Auto-detected** from message content (questions → ask, live-data/general tool-enabled chat → conversation, errors → debug, etc.)
- **Overridable** via slash commands for the user-facing modes (`/ask`, `/agent`, `/plan`, `/debug`)
- **Invisible** to the mode bar — they're internal AI behavior, not a user-facing navigation choice

The mode bar shows layout modes only. No confusion between "which panels do I see" and "how smart should the AI be."

## Workspace Model: Two-Layer Navigation

### Layer 1: Workspaces (Outer Tabs)

A workspace is a **logical context**, not a directory. Each workspace carries:
- Multiple pinned filesystem paths (not locked to one root folder)
- Memory and domain knowledge
- Chat thread history
- Active tasks and runs
- Mode-specific panel state

Backed by existing `ProjectStore` projects plus UI-only metadata (pinned paths, tab color/icon, open-thread cache, last active mode). Switching workspace = switching project context.

```
Outer tabs:  [Supply Chain Paper] [DAN Dev] [Kaggle Comp] [+]
```

### Layer 2: Threads (Inner Tabs)

Within each workspace, multiple conversation threads. Each thread is a chat session with its own history.
The lightweight "tab" layer is for currently open or recent threads; the full thread history remains in the sidebar for discovery and archive.

```
Inner tabs:  [Lit review] [Methodology Q&A] [Data cleaning] [+]
```

### File Access: Pinned Roots + Global Search

No single root folder. Each workspace has:
- **Pinned paths**: explicitly added directories (auto-discovered or manual)
- **Recent files**: auto-tracked per workspace (last N files touched)
- **Cmd+P global open**: searches entire filesystem, recent files ranked first
- **Agent-suggested files**: DAN surfaces relevant files based on conversation context

```
┌─ Workspace: DAN Dev ─────────────────┐
│                                       │
│ 📂 Pinned                            │
│   ~/Projects/deep-agent-network/     │
│   ~/Projects/science-cursor/         │
│   [+ Add folder]                     │
│                                       │
│ 🕐 Recent                            │
│   chat_manager.py          2 min ago │
│   prompts.py               1 hr ago  │
│   ChatPanel.tsx            yesterday │
│                                       │
│ 💡 Suggested by DAN                  │
│   ~/Downloads/new-paper.pdf   (new)  │
│                                       │
│ 🔍 Cmd+P to search anywhere         │
└───────────────────────────────────────┘
```

Key differences from VS Code: no single root, agent adds files proactively, Cmd+P searches globally (not just project), recent files are workspace-scoped.

### Filesystem Trust Model

The UI should not fall back to VS Code's single-root restriction, but it still needs an explicit trust model:
- **Read/search anywhere**: Cmd+P, explicit path open, and agent discovery can reference any path on the machine
- **Pinned roots = trusted write zones**: create/rename/delete/write operations are frictionless inside pinned workspace roots
- **Outside pinned roots = explicit confirmation**: the first mutating action outside trusted roots asks for confirmation or offers to pin that location into the workspace
- **System/private paths are not ambient suggestions**: DAN does not proactively surface sensitive/system directories unless the user explicitly navigates there

## The 5 Layout Modes

### Chat (Default)

Full-screen conversational interface. As simple as ChatGPT/Claude. Rich output rendering. Progressive escalation when tasks become complex.

**Who uses it:** Everyone, for quick questions, brief tasks, casual interaction.
**Layout:** Open-thread tabs + thread history (left) + conversation (center) + optional context panel (right).

### Code

Full IDE for building features, debugging, code review, shipping software. The target is to replace Cursor/VS Code for daily development work.

**Who uses it:** Developers who want to code inside DAN.
**Layout:** File explorer (left) + Monaco editor tabs (center) + terminal (bottom) + chat sidebar (right, toggleable).
**Differentiator:** The chat IS the AI layer. Cursor's value is "AI in the editor." DAN's value is "editor in the AI." The chat brain is more powerful; the code panels are its hands.

### Research

For: academic papers, literature reviews, systematic investigations.

**Central artifact:** A document being authored, plus a long-running domain knowledge distillation track when needed.
**Layout:** Writing pane + PDF reader + reference panel + review panel + recipe/distillation tab + pipeline progress.

### Analytics

For: equity research, financial modeling, Kaggle, data science.

**Central artifact:** Data-derived insights.
**Layout:** Data table + code cells + chart panel + experiment tracker + report pane.

### Operations

For: pipeline management, workflow editing, monitoring, scheduled tasks.

**Central artifact:** Running systems/processes.
**Layout:** Workflow graph (current editor) + run dashboard + log stream + schedule manager.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     DAN Desktop App                         │
│ ┌─ Workspace Tabs ────────────────────────────────────────┐ │
│ │ [Supply Chain] [DAN Dev] [Kaggle] [+]                   │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─ Open Threads ──────────────────────────────────────────┐ │
│ │ [Debug auth] [Prompt refactor] [Scratch] [+]            │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─ Mode Bar ──────────────────────────────────────────────┐ │
│ │ Chat │ Code │ Research │ Analytics │ Operations          │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─ Sidebar ──────┬─ Workspace ────────────────────────────┐ │
│ │ Mode Sidebar   │                                        │ │
│ │ (files /       │  [Mode-specific panels]                │ │
│ │ history / nav) │                                        │ │
│ │                │                                        │ │
│ └────────────────┴────────────────────────────────────────┘ │
│ ┌─ Chat Bar (always available) ───────────────────────────┐ │
│ │ > direct the agent from any mode...                     │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Backend Mode vs Layout Mode

| Concept | What It Controls | User Sees | How Selected |
|---|---|---|---|
| **Layout mode** (Chat/Code/Research/...) | Panel arrangement, visible surfaces | Mode bar at top | Click or Cmd+1..5 |
| **Behavioral mode** (`ask` / `conversation` / `agent` / `plan` / `debug`) | Tool availability, prompt tuning | Subtle badge (optional) | Auto-detected from message; override via `/ask`, `/agent`, etc. |

The backend modes are wired to:
- `ChatCapabilityRegistry.get_tools(mode)` — gates which tools the LLM receives
- System prompt hints — mode-specific guidance
- `detect_chat_mode()` in `helpers.py` — auto-detection from message keywords
- `ChatStore` mode persistence — per-thread behavioral mode stored in metadata

Layout modes NEVER restrict backend capabilities. Code mode still has full agent power. Research mode still has full agent power. The layout just changes which panels render the AI's output.

### Shared Infrastructure

- **App shell:** Workspace tabs (top), mode bar, sidebar, notification area, settings
- **Chat bar:** Always-visible input at the bottom of any mode. In Chat mode, this is the primary composer integrated into the conversation view. In other modes, this is the single source of truth for composing messages; side chat panels show transcript/status, not a second independent input box.
- **Panel framework:** Resizable, rearrangeable panels per mode layout. Generic `Panel` component.
- **Event router:** Maps engine events to the right panel in the active mode.
- **Workspace context:** Persistent across mode switches. Same workspace, same memory, same threads.

### Runtime: Electron

Electron desktop application. Reasons: native file access, full keyboard shortcuts, dedicated window, system tray, file watching, drag-and-drop with full paths, Chrome DevTools, auto-updater. Trade-off accepted: ~150MB binary.

The React code works identically in Electron or as a web page served by `dan-serve`.

### Relationship to Existing Code

- `editor/` React app is the foundation (React + Tailwind + Zustand + Vite + WebSocket + chat panel)
- `electron/` folder alongside `editor/` for Electron main process
- React Flow workflow canvas → Operations mode
- Existing ChatPanel → Chat mode (full-screen) + chat bar (bottom bar in other modes)
- Monaco editor, xterm.js, file explorer → Code mode (new)
- FastAPI backend (`dan-serve`) + WebSocket → unchanged
- File ops: Electron Node.js APIs for UI-level interactions; backend tools for agent-driven file ops

### Marketplace & Extension Architecture

The app supports multiple pluggable marketplaces for extending capabilities:

| Marketplace | What It Provides | Priority |
|---|---|---|
| **VS Code Extensions** (Open VSX + compatible VSIX sources) | Language support, themes, snippets, debuggers, formatters | Phase 2 (critical for Code mode) |
| **DAN Skills Hub** | Authored procedural skills (like Cursor rules) | Phase 2 |
| **DAN Recipe Store** | Distilled domain knowledge from token-burning sessions | Phase 3+ |
| **MCP Server Registry** | External tool servers (Stata, R, databases) | Phase 2 |

Architecture: generic `MarketplaceRegistry` interface with per-marketplace adapters. Each adapter handles search, install, uninstall, update, settings. Registry sources must stay swappable: Open VSX can be the default, but the architecture must also support compatible VSIX imports and future registry adapters without rewriting Code mode.

See [1-5-marketplace-extensions](1-5-marketplace-extensions.md) for detailed plan.

## Progressive Escalation

1. User types in chat bar → direct LLM response (stays in current mode)
2. Task needs tools → tool outputs render inline with rich formatting
3. Task needs multiple steps → plan sidebar appears showing progress
4. Task needs specialized workspace → system suggests mode switch ("This looks like a coding task. Switch to Code mode?")
5. User can always manually switch modes via mode bar or Cmd+1..5

## Development Phases

### Phase 1: Shell + Chat + Code Core ← current priority
Prove the architecture. Build the two things needed for daily use: chat and coding.
- [1-1-app-shell-framework](1-1-app-shell-framework.md) — panel system, mode switching, workspace tabs, sidebar
- [1-2-chat-mode](1-2-chat-mode.md) — full-screen chat with rich output, progressive escalation, workspace navigation
- [1-4-code-mode](1-4-code-mode.md) — Phase 1: Monaco editor, file explorer, terminal, diff, search, git, chat integration

### Phase 2: Code Mode Advanced + Marketplace
Full VS Code parity. Extensions ecosystem. Marketplace framework.
- [1-4-code-mode](1-4-code-mode.md) — Phase 2: LSP, debugger, advanced editor, AI code features
- [1-5-marketplace-extensions](1-5-marketplace-extensions.md) — VS Code extensions, DAN skills, MCP servers, generic marketplace framework

### Phase 3: Research Mode
Academic papers, literature reviews, systematic investigations.
- [1-3-research-mode](1-3-research-mode.md) — writing pane, PDF reader, references, reviews, recipe/distillation tab for long-running 100-paper learning, pipeline progress

### Phase 4: Analytics + Operations
Data science workspace + workflow management.
- Analytics mode (data tables, code cells, charts, experiment tracker)
- Operations mode (evolve current workflow editor into operations workspace)

### Phase 5: Polish + Custom Modes
User-configurable layouts. Mode auto-detection. Domain learning integration. Recipe marketplace. If the long-running recipe/distillation tab outgrows Research mode, it can later become its own dedicated surface.

## Design Principles

1. **Zero-config start** — Open the app, start typing. Chat mode is the default.
2. **Progressive disclosure** — Simple tasks look simple. Complexity appears only when the task demands it.
3. **Keyboard-first** — Cmd+Shift+P for command palette, Cmd+P for quick open, Cmd+1..5 for modes, `/` for slash, `@` for mentions. Reserve Cmd+K for context-sensitive inline AI actions inside editor-like surfaces.
4. **Memory-aware** — Remembers preferences, past work, style. No re-configuring every session.
5. **Context carries** — Switching modes keeps workspace, memory, and threads. No data loss.
6. **The workflow is accessible, not mandatory** — "Show pipeline" toggle reveals the underlying graph.
7. **Workspaces are logical, not physical** — A workspace references multiple directories, not locked to one folder.
8. **Extensible** — Marketplaces add capabilities without core changes.

## Constraints

- **The engine and API don't change.** The UI consumes existing REST + WebSocket endpoints.
- **The existing editor keeps working.** Operations mode wraps the current editor.
- **Electron (Chromium).** Cross-platform consistency at the cost of ~150MB binary.
- **Performance budget:** Mode switch < 100ms. Panel resize at 60fps. Initial load < 3s.
- **Can develop in parallel with core engine fixes** since UI and engine touch different codebases.
