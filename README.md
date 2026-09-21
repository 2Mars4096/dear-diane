# Deep Agent Network (DAN)

DAN is a focused agent product built as one stack:

1. **Universal Cell** — one bounded worker primitive driven by typed briefs.
2. **Universal Organism** — a dependency-aware plan of Universal Cells.
3. **Super DAN** — the general-purpose live agent runtime and terminal UI.
4. **Work/Notes** — the desktop and phone workspace for sessions, progress, artifacts, previews, and Markdown notes.

The pre-Universal graph builder, visual graph editor, Code/Research/Content modes, concierge, messaging adapters, publishing, RAG, and product-specific organism families were removed on 2026-08-02. They remain recoverable from Git history.

## Workbench

Activity shows a short current-action label while working; expand **Work details** for the recorded steps afterward.

Waiting follow-ups appear only in **Up next**, keeping the active reply in view. They enter the conversation when delivered.

Session titles are set from the first request and stay stable across follow-ups.

Click **Session · ID** above the conversation to copy its permanent ID. Paste it into **Search chats or session ID** to find that chat, or give it to a native agent to look up the other session when needed. Agents receive their own DAN session identity and lookup instructions; this does not send messages between sessions.

Click a filename in a past message to open its saved attachment in the default desktop app (images, videos, PDFs, Word documents, text, and other supported files). Browser users get a preview or download.

Use **Edit** below your latest message, then **Save & resend** to replace its answer while keeping attachments. Finish or stop an active reply before editing; **Cancel** leaves the conversation unchanged.

Choose **DAN settings → Appearance → Color scheme** for eight paired light/dark palettes. **Mode** selects Light, Dark, or System; changes apply immediately and save automatically.

Work opens with a familiar project/chat sidebar: New chat, search, collapsible project folders, and archived chats. Use the + beside Projects to create an empty project, then choose **New chat** when ready; its menu edits the name and folder. In the desktop app, drop a folder onto the folder field to fill its path. Browsers that hide local paths require pasting the full path. One **side panel** (header toggle) holds Chat, Files, Preview, Activity, and Team as tabs. Chat is a separate saved conversation with the current lead, also opened from selected text. Select message text to stage a source-linked reference above the input. The input grows from one to six lines, then scrolls; expand tool and emitted thinking details when needed. Waiting messages stay out of the transcript and appear in an initially expanded **Up next** queue; a lone active run does not show a queue panel. Stop and steering use the existing Agent V2 controls. On new Codex lead runs, **Steer** sends into the active turn, and **Steer now** sends an existing Up next entry without duplicating it. Other waiting messages retain FIFO order; rejected steering remains queued. Live steering becomes available when the run connection is ready; older CLI runs require a new run after updating.

- **Sidebar:** click the toggle left of the project name, or press `⌘/Ctrl + B`. The right side panel toggles with `⌘⌥B` / `Ctrl+Alt+B`.
- **Projects:** `⌘/Ctrl + Shift + P` opens the carousel. Scroll or use left/right arrows, then Enter. Escape cancels.
- **Sessions:** `⌘/Ctrl + Shift + S` opens the current project's wheel. Press `1–8` to open; arrows or `WASD` aim (hold two directions for diagonals); `Q/E/Z/C` also select diagonals directly. Press Enter to open. The newest eight sessions keep stable slots based on creation, never last edit.
- **All sessions:** use the project sidebar or the wheel's “All sessions” button. Drafts survive switching.
- **Processes:** the side panel's Processes tab runs commands that must stay up (dev servers, watchers). They keep running after a chat or agent run ends; agents start them through DAN for the same reason.
- **Skills:** DAN settings → Skills lets Claude Code use skills installed for Codex or Cursor, without changing any CLI's own folders.
- **Usage:** hover the sidebar's Usage button for remaining quota (5h/week/model windows) on the accounts you used most recently.
- **Reader:** click a PDF in Files (or **Read** in Preview); it opens as a tab beside the conversation. The side panel's **Reading** and **Notes** tabs follow the active PDF. Select text → **Ask** stages it in the side chat with the page's text; **Comment** saves a highlight with an optional note. Scanned pages are OCR'd in-app so they become selectable; highlights are text-anchored so they survive PDF changes; **Export** saves a copy with comments as PDF highlights. Zoom, page references (**Refs**), comments, and your position are remembered per PDF.

Codex events use the shared activity display. The [native worker design](docs/UI-plans/2-native-agent-workers.md) describes persistent Codex/Claude children; a Claude worker adapter is still pending.

## Requirements

- Python 3.11+
- Node.js 20+ for the desktop/web GUI
- Flutter 3.41+ only when building the optional phone app

## Install

```bash
pip install -e ".[dev]"
cd editor
npm install
```

## Configure a model

Set the provider key and model you intend to use:

```env
OPENAI_API_KEY=...
DAN_LLM_MODEL=gpt-5.4
```

Use `.env.minimal` as the shortest copyable template; `.env.example` documents the optional retained settings.

Anthropic and Google models can use `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY`. Model names route by prefix (`gpt-*`, `claude-*`, `gemini-*`); exact overrides can be supplied as JSON in `DAN_MODEL_PROVIDER_MAP`.

Useful paths:

```env
DAN_WORKSPACE_ROOT=/absolute/path/to/workspace
DAN_GRAPHS_DIR=/absolute/path/to/durable-state
DAN_NOTES_WORKSPACE_ROOT=/absolute/path/to/notes
```

## Run

Start the server:

```bash
dan-up
# or: dan serve --no-reload
```

Start the browser GUI:

```bash
cd editor
npm run dev
```

Open `http://localhost:5173/#workspace`.

Start the Electron desktop app in development:

```bash
cd editor
npm run electron:dev
```

Run Super DAN directly:

```bash
dan super-organism --model "$DAN_LLM_MODEL" "Inspect this workspace and implement the requested change"
dan super-tui
```

Other retained commands are `dan serve`, `dan up`, `dan down`, and `dan editor`.

## Product surface

Work provides:

- workspace and session management;
- protected task blueprints and execution attempts;
- durable Agent V2 task/run/event state;
- live progress, steering, validation, repair, evidence, and artifacts;
- file browsing and preview;
- a PDF reader with ask-in-sidecar, highlights, and page references;
- structured image attachments.

Notes provides:

- Markdown/Hugo file navigation and editing;
- recent and collection views;
- rendered preview and knowledge navigation;
- learning-course/progress metadata;
- the same Super DAN composer and durable session model as Work.

The mobile app consumes the same loopback/WireGuard-safe HTTP API. `/api/workspace-wireguard` is read-only and never starts or reconfigures a host VPN service.

## Core Python API

```python
from dan import build_cell

cell = build_cell(model="gpt-5.4", role_label="implementer")
assert cell.metadata["universal_cell"] is True
```

For typed briefs and organism plans, see [docs/llm-api-guide.md](docs/llm-api-guide.md).

## Validation

```bash
python -m compileall -q src/dan

cd editor
npm test
npm run electron:compile
npm run build:verify
```

Focused Python tests live under `tests/test_worker`, `tests/test_cli`, `tests/test_server`, and `tests/eval`. Live-provider and real-browser evals remain opt-in.

## Repository map

```text
src/dan/worker/          Universal Cell, briefs, contracts, scheduler, organism
src/dan/worker/organisms Universal Organism, Super DAN, local tool runtime
src/dan/cli/             Retained server, Super DAN, and TUI commands
src/dan/server/          Work/Notes, sessions, and Agent V2 control plane
src/dan/providers/       OpenAI, Anthropic, and Google provider adapters
src/dan/tools/           Selectively loaded Super DAN capabilities
src/dan/skills/          Skill discovery and loading
editor/                  Work/Notes React + Electron app
mobile/                  Optional Flutter phone app
docs/plans/              Active numbered roadmap (1–5)
```

## Archive policy

Git is the archive. Do not add an in-tree legacy archive or reintroduce old product modes for compatibility. If historical code is needed, inspect `0d630dca`, the complete pushed pre-cutover recovery point (including legacy tests and their assets).

### Manager models and native subagents

The composer model menu includes OpenRouter DeepSeek V4.1 Flash and Kimi K2.6. Set `OPENROUTER_API_KEY` on the DAN server, or reuse `DAN_LLM_API_KEY` with `DAN_LLM_BASE_URL=https://openrouter.ai/api/v1`.

Open **Subagents** beside the model picker to enable Codex, Claude Code, or Antigravity workers and choose their account, model, reasoning, and supported fast mode. Codex accounts come from local codexx configuration (`DAN_CODEXX_CONFIG` can override its path). Claude uses its current configuration or profiles under `DAN_CLAUDE_ACCOUNTS_DIR` (default `~/.claude-accounts`). Install and authenticate each CLI separately; Antigravity uses `agy`. Unsupported controls stay disabled.

**Import native session** in the project sidebar finds conversations matching the project's folder. Select one and choose **Import as fork**, or Cancel. JSONL transcript imports support up to 256 MB. Imports preserve original history; Codex/Claude native continuation forks before use. Antigravity import is currently unavailable because its headless fork path is unverified. Workers run concurrently under a DAN manager; remaining workers stop when the manager finishes. Authenticated live acceptance and restart-resume/approval UI remain pending.

Project **⋯** menus offer New chat, Edit project, and **Remove from DAN**. Removal requires confirmation and hides the project in the current DAN profile; folders, files, native sessions, and saved DAN chat history remain intact.

Project menus (•••) include **Import native sessions**, with multi-select and **Select all**. Imports create forks and preserve native originals. The sidebar footer opens **DAN settings** for profile-wide preferences.

Choose **Lead** (DAN, Codex, Claude Code, or Antigravity), configure account/model/reasoning/fast inside the same dropdown, and choose delegates in **Team**. Native leads retain their sessions and can delegate through a run-scoped bridge. Native follow-ups queue after the current turn; saved follow-ups resume automatically after a backend restart using their recorded account and settings. Stop closes the lead and its team. Installed native CLIs require their own login.

In **Archived chats**, **Delete all archived chats** removes archived DAN copies after confirmation. Active chats, native originals, and project files are preserved.

Codex’s built-in subagents also appear automatically in **Team**, with recorded command activity and final replies. This does not require enabling a DAN delegate. These children are controlled by their Codex lead, so their cards do not offer individual Stop buttons.

### Desktop updates

Open **DAN settings → Updates** to choose a prepared local `DAN.app`, then **Install and restart**. Finish active runs and queued messages first. DAN stages and verifies the new build, closes its owned backend, replaces the app, and reopens it; chats/settings stay in Application Support. A previous app copy is retained for rollback. Local Mac installs preserve your icon.

Published releases use `electron-updater`; **Check for updates** and **Download** become available when the packaged release channel is configured. The channel points to private GitHub Releases for this repository. Use **Sign in to GitHub** in Updates to authorize in your browser; the personal build uses the installed GitHub CLI behind the scenes. A production signing identity and published releases are still needed. See [release setup](docs/desktop-updates.md). The Python backend is currently separately installed; a desktop update does not upgrade that environment.
