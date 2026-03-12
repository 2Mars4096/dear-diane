# 1: DAN Custom UI — Master Specification

**Status:** planning
**Goal:** Build a job-based workspace UI that is as convenient as ChatGPT for simple tasks while fully leveraging DAN's workflow orchestration for complex tail tasks.

## Vision

DAN's engine supports multi-agent workflows, typed graph orchestration, memory, self-evolution, and domain learning. But the current delivery surfaces don't fully expose this power:

- **Messaging (Telegram/WhatsApp):** Good as remote control — quick commands, status checks, notifications. Cannot support spatial layouts, side-by-side panels, or rich interactive elements for deep work.
- **CLI (`dan-chat`):** Powerful for power users. No visual layout.
- **Workflow editor:** Specialized for one job (building workflow graphs). Not designed for doing actual work (writing papers, analyzing data, reviewing code).

The custom UI fills this gap: a web application with **job-based workspace modes** that arrange DAN's capabilities for specific types of work.

## Core Design Principle: Jobs, Not Functions

Modes are organized by **what the user is trying to accomplish** (their job), not by tool type (writing, coding, analyzing). A researcher doesn't switch between "compose mode" and "study mode" and "analyze mode" — they're in **Research mode** and need writing, reading, coding, and analysis all available simultaneously, arranged for the research workflow.

Each job cross-cuts multiple functional capabilities:

| Job Mode | What It Bundles |
|---|---|
| **Research** | Read + Write + Code + Analyze + Review |
| **Development** | Code + Read + Write + Debug + Test |
| **Analytics** | Analyze + Code + Write + Visualize |
| **Content** | Write + Design + Analyze + Publish |
| **Operations** | Build + Monitor + Schedule + Debug |
| **Chat** (default) | Conversational interface, quick tasks |

The same capability (e.g., "writing") appears in multiple modes but with different size, position, and connections. Research puts the writing pane front-and-center; Analytics puts it in a secondary report pane.

## The 6 Modes

### Chat (Default)

Full-screen conversational interface. As simple as ChatGPT/Claude for quick tasks. Rich output rendering (tables, charts, code, files). Progressive escalation: when a task becomes complex, the system suggests switching to a specialized mode.

**Who uses it:** Everyone, for quick questions, brief tasks, and casual interaction.

### Research

For: academic papers, literature reviews, systematic investigations, scientific analysis.

**Central artifact:** A document being authored.
**Layout:** Writing pane + PDF reader + reference panel + review panel + pipeline progress + code/data cells.
**Workflow underneath:** search → read → analyze → outline → parallel section writing → review-revise loop → finalize.

### Development

For: building features, debugging, code review, shipping software.

**Central artifact:** A codebase being modified.
**Layout:** File tree + code editor + terminal + diff viewer + test panel + debug inspector.
**Workflow underneath:** understand → plan → implement → test → review → deploy (DAN's debug/agent graph templates).

### Analytics

For: equity research, financial modeling, Kaggle, data science, business intelligence.

**Central artifact:** Data-derived insights.
**Layout:** Data table + code cells + chart panel + experiment tracker + report pane.
**Workflow underneath:** ingest → EDA → feature engineering → model → evaluate → report.

### Content

For: marketing campaigns, blog posts, social media, newsletters, presentations.

**Central artifact:** Audience-facing content.
**Layout:** Content editor + multi-channel preview + asset library + engagement dashboard + publishing panel.
**Workflow underneath:** ideate → create → review → publish → measure → optimize.

### Operations

For: pipeline management, automation, monitoring, scheduled tasks.

**Central artifact:** Running systems/processes.
**Layout:** Workflow graph (current editor) + run dashboard + log stream + schedule manager + cost monitor.
**Workflow underneath:** design → deploy → monitor → troubleshoot → optimize.

## Architecture

```
┌─────────────────────────────────────────────┐
│                 DAN Web App                 │
│ ┌─ Mode Bar ──────────────────────────────┐ │
│ │ Chat │ Research │ Dev │ Analytics │ ...  │ │
│ └─────────────────────────────────────────┘ │
│ ┌─ Sidebar ──┬─ Workspace ───────────────┐ │
│ │ Projects   │                           │ │
│ │ Tasks      │  [Primary]    [Context]   │ │
│ │ Memory     │                           │ │
│ │ Settings   │  [Secondary]  [Aux]       │ │
│ │            │                           │ │
│ └────────────┴───────────────────────────┘ │
│ ┌─ Chat Bar (always available) ──────────┐ │
│ │ > direct the agent from any mode...    │ │
│ └────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

### Shared Infrastructure

- **App shell:** Mode bar (top), project sidebar (left), notification area (top-right), settings
- **Chat bar:** Always-visible input at the bottom of any mode. In Chat mode, this is the full interface. In other modes, it's a command line for directing the agent.
- **Panel framework:** Resizable, rearrangeable panels within each mode's workspace. Generic `Panel` component with header, content, resize handles, collapse/expand.
- **Event router:** Maps engine events (`node_completed`, `artifact_created`, `review_comment`, etc.) to the right panel in the active mode.
- **Project context:** Persistent across mode switches. Same project, same memory, same conversation history.

### Runtime: Electron

The app ships as an **Electron** desktop application, not a browser tab. Two reasons drove this choice:

**1. Every mode needs local file access.** PDFs, data files, code, assets, project trees. The browser sandbox makes this clunky (upload dialogs instead of native file pickers, no save-to-path, no file watching, no full keyboard control).

**2. Cross-platform rendering consistency.** DAN's UI is complex: React Flow (SVG graph rendering), PDF.js, Monaco/CodeMirror code editors, Chart.js, WebSocket streaming, multi-panel resizing. Electron ships its own Chromium — what you see in development is exactly what every user sees on every platform. The alternative (Tauri) uses the OS's built-in webview (WebKit on macOS, WebView2 on Windows, WebKitGTK on Linux), which means three different rendering engines with different CSS/JS support levels, different SVG behavior, and different edge cases. For a complex multi-panel workspace, this is an unacceptable testing burden.

What Electron gives us that a browser can't:
- **Cross-platform consistency** — same Chromium engine on macOS, Windows, and Linux
- **Native file dialogs** — OS-native open/save that browse real directories
- **Full keyboard shortcuts** — Cmd+W, Cmd+T, Cmd+N belong to DAN, not the browser
- **Dedicated window** — Alt+Tab shows "DAN", not "Chrome"
- **System tray** — always accessible, pairs with `dan-serve` daemon
- **File watching** — native fs events for live reload of local files
- **Drag-and-drop with full paths** — drop from Finder, get the real path (not a blob)
- **Node.js on native side** — same language as frontend, no Rust toolchain needed
- **Chrome DevTools** — built-in, best-in-class debugging
- **Auto-updater** — electron-updater, battle-tested

**Trade-off accepted:** Electron ships Chromium (~150MB binary, ~200MB+ memory). This is the cost of guaranteed rendering consistency. Every major productivity desktop app (VS Code, Cursor, Slack, Discord, Figma, Notion, Linear) made the same trade-off.

The React code is identical whether running in Electron or served as a web page. The web app can also be served by `dan-serve` as a fallback for remote/browser access.

### Relationship to Existing Code

- The current `editor/` React app is the **foundation**. React + Tailwind + Zustand + WebSocket + chat panel.
- An `electron/` folder is added alongside `editor/` for the Electron main process.
- React Flow workflow canvas → **primary panel in Operations mode**.
- Existing chat panel → **Chat mode** (full-screen) + **chat bar** (bottom bar in other modes).
- New panels (PDF viewer, writing editor, data table, etc.) → added as React components.
- FastAPI backend (`dan-serve`) + WebSocket event stream → **unchanged**. The UI consumes existing endpoints.
- File operations use Electron's Node.js APIs for UI-level interactions (open/save dialogs, file watching) and the backend's existing tools for agent-driven file ops.

### Domain Profiles Within Modes

Each mode supports domain specializations via DAN's existing domain profile system (`src/dan/data/generation_profiles/`):

| Mode | Example Profiles |
|---|---|
| Research | Academic (LaTeX, citations, INFORMS) / Market research / Policy analysis |
| Development | Web app / Data engineering / ML infra / DevOps |
| Analytics | Financial (factors, market data) / Data science (Kaggle, experiments) / BI |
| Content | Blog/SEO / Social media / Newsletter / Documentation |
| Operations | ETL pipelines / Model serving / Report scheduling |

Profiles customize tool palette, templates, workflow patterns, and vocabulary — not the workspace layout.

## Progressive Escalation

The UI mirrors the engine's solver: simple tasks stay in chat, complex tasks grow the workspace.

1. User types in chat bar → direct LLM response (stays in current mode)
2. Task needs tools → tool outputs render inline with rich formatting
3. Task needs multiple steps → plan sidebar appears showing progress
4. Task needs specialized workspace → system suggests mode switch ("This looks like a research task. Switch to Research mode?")
5. User can always manually switch modes via mode bar or Cmd+1..6

## Development Phases

### Phase 1: Shell + Chat + Research ← current plan
Prove the job-based mode architecture works. Build shared infrastructure, default chat mode, and the flagship research workspace.
- [1-1-app-shell-framework](1-1-app-shell-framework.md) — panel system, mode switching, sidebar, chat bar
- [1-2-chat-mode](1-2-chat-mode.md) — full-screen chat with rich output, progressive escalation
- [1-3-research-mode](1-3-research-mode.md) — research workspace (PDF, writing, references, review, pipeline)

### Phase 2: Analytics + Management → (not yet planned)
Second job mode + admin dashboard. Data tables, code cells, charts, experiment tracking. Plus: memory browser, cost analytics, schedule manager.

### Phase 3: Development Mode → (not yet planned)
File tree, code editor (Monaco), terminal, diff viewer, test panel. Deep integration with DAN's debug workflow templates.

### Phase 4: Content + Operations → (not yet planned)
Content mode (editor, multi-channel preview, publishing). Operations mode (evolve current workflow editor into operations workspace).

### Phase 5: Polish + Custom Modes → (not yet planned)
User-configurable workspace layouts. Mode auto-detection from project context. Domain learning integration (workspace adapts based on usage patterns).

## Design Principles

1. **Zero-config start** — Open the app, start typing. No mode selection required. Chat mode is the default.
2. **Progressive disclosure** — Don't overwhelm. Simple tasks look simple. Complexity appears only when the task demands it.
3. **Keyboard-first** — Cmd+K for commands, Cmd+1..6 for modes, `/` for slash commands, `@` for mentions. Power users never touch the mouse.
4. **Memory-aware** — Remembers preferences, past work, style. No re-configuring every session.
5. **Context carries** — Switching modes keeps project, memory, and conversation. No data loss on mode switch.
6. **The workflow is accessible, not mandatory** — A small "Show pipeline" toggle reveals the underlying workflow graph. Most users never need it.

## Constraints

- **The engine and API don't change.** The UI consumes existing REST + WebSocket endpoints.
- **The existing editor keeps working.** This is additive. Operations mode wraps the current editor, not replaces it.
- **Electron (Chromium).** Not browser-only (sandboxed file access, stolen keyboard shortcuts, tab competition). Not Tauri (three different rendering engines across platforms — unacceptable for a complex multi-panel UI). Electron guarantees cross-platform consistency at the cost of ~150MB binary.
- **Performance budget:** Mode switch < 100ms. Panel resize at 60fps. Initial load < 3s.
- **Can develop in parallel with core engine fixes** (Phase 23, 28-5, etc.) since UI and engine touch different codebases (`editor/` vs `src/dan/`).
