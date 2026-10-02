# Dear Diane

A workspace for research, code, and ideas. **Diane** is your assistant.

Dear Diane is a focused agent product built as one stack:

1. **Universal Cell** — one bounded worker primitive driven by typed briefs.
2. **Universal Organism** — a dependency-aware plan of Universal Cells.
3. **Diane** — the general-purpose live agent runtime and terminal UI.
4. **Work/Notes** — the desktop and phone workspace for sessions, progress, artifacts, previews, and Markdown notes.

The pre-Universal graph builder, visual graph editor, Code/Research/Content modes, concierge, messaging adapters, publishing, RAG, and product-specific organism families were removed on 2026-08-02. They remain recoverable from Git history.

## Planned phone personal agent

[Plan 7](docs/plans/7-phone-personal-agent.md) stages commitment capture, reminders, connected email/calendar actions, and follow-through on iPhone and Android, browser first. Google is the first provider, followed by broader integrations through shared contracts. This work is planned; these personal-agent features are not yet available.

## Browser research

Ask Dear Diane to use the browser to search for a paper, retrieve its actual BibTeX, and save `references.bib`. Built-in Dear Diane chats and native leads have a lazy browser connection. On desktop hosts it opens visibly; without a display it runs headless. Set `DAN_BROWSER_HEADLESS=0` or `1` to override. The browser runs on the backend machine, uses a fresh isolated session, and closes when the run finishes or stops. It does not attach to your existing Chrome profile.

Install browser support in the backend Python environment with `pip install -e '.[browser]'`; Dear Diane uses installed Chrome when managed Chromium is unavailable, or install it with `python -m playwright install chromium`. Native leads use Dear Diane’s browser bridge, so their shell does not need its own Playwright installation. Use a project chat in Auto mode and describe the browser task normally; no manual browser command is needed. Native agents still require their usual account sign-in. Plan mode permits observation/navigation only. CAPTCHA/login challenges may require human help; no citation is fabricated when access fails.

## Workbench

- **Reading after restart:** open reading files and the selected document are restored, along with existing reading progress and chat links. Browser-picked copies stay on this device. Closing a file removes it from the saved Reading list.
- **PDF selection and Refs:** select a passage and choose **Ref** to keep its highlighted location; hover its tag to preview the passage and edit its label. **Ask** stages selected text or an image crop for chat. Scan columns are detected automatically; **Single Page / Two Pages** changes the reading layout.

- **Sidebar layout:** drag the right edge to resize; double-click to reset. Width and selected project/session are restored after refresh or restart.
- **Workspace list:** sessions, open PDFs, files, Papers, and Settings share one sidebar list. Compact title rows keep project context in the header and hover text; pins stay on the same row. The project ellipsis menu beside the selector opens project editing and native-session import. The magnifier opens search. Cmd/Ctrl+Shift+E opens the shared project picker; Cmd/Ctrl+Shift+0 restores all projects and clears search. File drafts, unread state, and running work remain visible. The old project/recent/pinned lists and top tab row are replaced by this list.
- **Recent-item shortcuts:** hold Cmd (Ctrl on Windows/Linux) to reveal numbers on eligible rows. Cmd/Ctrl+1 returns to the previous item; +2–9 selects older visits, including documents. Existing sessions provide initial destinations before you have visit history. Numbers and row order freeze until the modifier is released. Background updates do not reorder the list; shortcuts also work with the sidebar hidden.
- **Run feedback:** Thinking shows elapsed minutes/seconds and the latest observed activity, with recorded duration retained after completion.
- **Response actions:** Edit request, Regenerate, and Fork are available below the latest completed answer as well as the request.
- **Item actions:** session right-click/ellipsis menus offer Open in workspace, Open in new window, pin, rename, move, unread/read, fork, copy ID/text, and archive. Delete permanently is available directly with a named confirmation and is disabled during running work; archived sessions can also be restored. Files have a close button with unsaved-edit protection. Each window keeps its own selection and documents.
- **Team:** expand a worker for paginated history, supported replies/resume, and Codex questions/one-time approvals. Single-question options answer in one click. After a restart, explicitly resume an interrupted worker. Native subagents observed from a lead retain that lead’s controls.
- **Notifications:** Settings → Notifications has a completion/attention toggle. Alerts are quiet, suppress repeats and watched conversations, and open the relevant session. Browser use requires notification permission; keep the app/browser running.
- **Changes:** choose Changes from the side-panel tool menu. Review project text edits since a prior request or manually marked boundary. The view refreshes automatically; Ask for changes stages selected diff text as feedback and focuses the composer. Git folders only; the latest ten bounded snapshots are kept, with excluded files listed.
- **Parallel edits:** Team delegation uses owned Git worktrees by default, with an explicit shared-workspace option for read-only tasks/non-Git folders. Existing edits are copied into the baseline. Choose Review changes, then Apply changes to project; conflicting source edits stop application. Ignored dependencies/secrets are not copied, and worktrees remain available afterward.

Dear Diane's icon uses an ivory impressionist network of cooperating agents, shared by new desktop packages and the browser favicon.

Activity shows a short current-action label while working; expand **Work details** for the recorded steps afterward.

Waiting follow-ups appear only in **Up next**, keeping the active reply in view. They enter the conversation when delivered.

Session titles are set from the first request and stay stable across follow-ups. OpenRouter setup offers optional generated titles for future sessions; it is off by default and does not send existing chats for backfill. New installations require an OpenRouter API key, stored on the host.

Click **Session · ID** above the conversation to copy its permanent ID. Paste it into **Search chats or session ID** to find that chat, or give it to a native agent to look up the other session when needed. Agents receive their own Dear Diane session identity and lookup instructions; this does not send messages between sessions.

Click a file link or attachment to open it in the sidecar beside your conversation. PDFs, text/Markdown, images, and media use their previews; unreadable or unsupported files show the error there. Right-click local links or use the file’s **…** menu for native actions, including **Open With…** on macOS. Select text in PDFs or text previews to annotate; **Use in chat** stages the passage and note for your next request. Notes persist across reopening.

Use **Edit** below your latest message, then **Save & resend** to replace its answer while keeping attachments. Finish or stop an active reply before editing; **Cancel** leaves the conversation unchanged.

Choose **Dear Diane settings → Appearance → Color scheme** for eight paired light/dark palettes. **Mode** selects Light, Dark, or System; changes apply immediately and save automatically.

Work opens with a familiar project/chat sidebar: New chat, search, collapsible project folders, and archived chats. Use the + beside Projects to create an empty project, then choose **New chat** when ready; its menu edits the name and folder. In the desktop app, drag a folder into the main workspace: a dashed boundary appears, and dropping creates and opens a project named after the folder. Dropping an already listed folder reopens its project. You can also drop a folder onto the project settings folder field to fill its path. Browsers that hide local paths require pasting the full path. One **side panel** (header toggle) holds Chat, Files, Preview, Activity, and Team as tabs. Chat is a separate saved conversation with the current lead, also opened from selected text. Select message text to stage a source-linked reference above the input. The input grows from one to six lines, then scrolls; expand tool and emitted thinking details when needed. Waiting messages stay out of the transcript and appear in an initially expanded **Up next** queue; a lone active run does not show a queue panel. Stop and steering use the existing Agent V2 controls. On new Codex lead runs, **Steer** sends into the active turn, and **Steer now** sends an existing Up next entry without duplicating it. Other waiting messages retain FIFO order; rejected steering remains queued. Live steering becomes available when the run connection is ready; older CLI runs require a new run after updating.

- **Mac window:** drag a blank part of the top bar to move Dear Diane; double-click it to expand, then double-click again to restore the previous size. Project/chat labels and toolbar buttons keep their own actions.
- **Sidebar:** click the toggle left of the project name, or press `⌘/Ctrl + B`. The right side panel toggles with `⌘⌥B` / `Ctrl+Alt+B`.
- **Projects:** `⌘/Ctrl + Shift + E` opens the carousel using a left-hand shortcut. Scroll or use left/right arrows, then Enter. Escape cancels.
- **Sessions:** `⌘/Ctrl + Shift + S` opens the current project's wheel. Press `1–8` to open; arrows or `WASD` aim (hold two directions for diagonals); `Q/E/Z/C` also select diagonals directly. Press Enter to open. The newest eight sessions keep stable slots based on creation, never last edit.
- **All sessions:** use the project sidebar or the wheel's “All sessions” button. Drafts survive switching.
- **Side tabs:** switching Reading, Notes, Files, Processes, and other right-side tools preserves drafts, scroll, and expanded logs, including after closing/reopening the panel.
- **Close tab:** `⌃⌘W` (Control–Command–W) on Mac; `Ctrl+Alt+W` on Windows/Linux. Unsaved edits prompt before closing. Hover a tab’s × to see the shortcut; macOS window shortcuts stay unchanged.
- **Processes:** the side panel's Processes tab runs commands that must stay up (dev servers, watchers). They keep running after a chat or agent run ends; agents start them through Dear Diane for the same reason.
- **Skills:** Dear Diane settings → Skills lets Claude Code use skills installed for Codex or Cursor, without changing any CLI's own folders.
- **Usage:** hover the sidebar's Usage button for remaining quota (5h/week/model windows) on the accounts you used most recently.
- **Token usage:** Settings → Usage → **Open analyzer**. *All sessions* combines your recent Claude Code and Codex sessions (tokens per day, by project, agent, model, and activity, plus the changes that would save the most). *By session* shows one session's chat rounds, most expensive steps, and avoidable patterns. It reads the transcripts already on your computer. **Label stages with model** optionally refines the activity labels with Jev (`jev-latest`) through your OpenRouter key; it sends step summaries, never file contents or command output.
- **Literature:** open the right side panel, click its current tool name (such as **Files ▾**), and choose **Literature**. Browse your library or drop a batch of PDFs into **Import**. Choose a configured Hugo KB, optionally supply BibTeX, then **Prepare PDFs** using the selected lead. Dear Diane retrieves Crossref citation candidates, checks each match, and drafts notes with source anchors. Review uncertain matches, retry individual documents, or stop preparation. **Import ready items** saves verified PDF copies and `index.md` pages; originals and existing notes are preserved. Imports survive panel closure and reload; queued work resumes after a backend restart, while interrupted documents offer Retry. Books support an explicitly labelled overview. No Zotero installation is needed.

- **Reader:** click a PDF in Files (or **Read** in Preview); it opens as a tab beside the conversation. The side panel's **Reading** and **Notes** tabs follow the active PDF. Select text → **Ask** stages it in the side chat with the page's text; **Comment** saves a highlight with an optional note. Scanned pages are OCR'd in-app so they become selectable; highlights are text-anchored so they survive PDF changes; **Export** saves a copy with comments as PDF highlights. Zoom, page references (**Refs**), comments, and your position are remembered per PDF.

Codex events use the shared activity display. The [native worker design](docs/UI-plans/2-native-agent-workers.md) records adapter support and remaining live acceptance checks.

PDFs automatically use a local compatibility font renderer to avoid browser font conversion issues. No repair button or per-file setup is needed; the original PDF is unchanged.

## Papers and reading sessions

Use **⌘K / Ctrl+K** to find a paper by title, author, citation key, topic, or reading notes. Search accepts words in any order and small metadata typos. Arrow keys select, Enter opens, and Escape returns to your work. An empty search shows pinned and recent papers.

**Papers** in the sidebar opens the full library: filter by tags/year/journal, switch list/table views, inspect abstracts and knowledge-base notes, copy citations/BibTeX, or select papers and **Add to chat**. References are staged for your next message; nothing sends automatically. Library filters and scroll survive switching tabs.

Each library paper has one resumable reading session. **Continue reading** in the sidebar restores its PDF position, annotations, saved page references, and Reading conversation, independently of the current project. Closing the tab keeps the session. Reading state is saved on the Dear Diane host, so another browser connected to that same host can resume it. Failed saves retain a local recovery copy with retry/download controls.

The library discovers your existing `my-knowledge-base` through the Notes folder conventions. **Paper library settings** lets you add Hugo project roots or PDF folders and hide the sidebar entry. You can reopen it through **Dear Diane settings → Open paper library**. Sources remain in place; the library does not edit knowledge-base Markdown or PDFs. Missing PDFs stay visible in search with unavailable-file feedback. Sources refresh when Dear Diane regains focus, every 30 seconds while visible, or through **Refresh**.

Search currently covers metadata, abstracts, knowledge-base notes, and saved annotation text. PDF full-text and semantic search are not included. Separate hosts have separate libraries; browser-only dropped PDFs continue using the existing local-copy workflow.

## Open files in Dear Diane

In desktop conversations, click file/folder links or inline code paths from Codex or Claude to open the actual local target. Relative links use the project folder. Right-click for Open, Reveal in Finder, Open in Cursor (when installed), Copy path, and file-only Save as / Copy file contents. Remote-host paths remain accessible through the project Files panel.

Drop files into Dear Diane or use **Open file** (⌘/Ctrl+O). PDFs open in the reader; text/code files open in editable tabs, Markdown supports Preview, and images/audio/video open in viewers. Project Files also open in tabs. Use **Save** or ⌘/Ctrl+S for original desktop/project files; changed files on disk require reopening, with **Download copy** available to preserve your draft. Unsaved edits survive tab/Notes switches and prompt before closing.

Browser-selected files stay in your browser and offer **Download edits**, leaving the original unchanged. Text editing supports UTF-8 files up to 2 MB. Office documents and other binary formats offer download rather than in-app editing.

## Remote machines and phone access

In **Settings → Connections → Add SSH connection**, enter a display name and hostname (`server.example`, `user@host`, or an existing SSH alias). SSH port and identity file are optional; the default uses your SSH config/agent. Save, then use **⋯ → Check SSH**. The folder icon opens an existing configured remote workspace. This section manages SSH only; relay, phone-access, and deployment controls are not included. Existing remote services and their saved configuration are preserved.

Settings has section headings and jump buttons for Appearance, Connections, Agents & skills, Library, Usage, and App updates.

To work on an existing remote project, open its configured workspace, then choose **New project** and browse folders on that machine. This registers the folder without moving or uploading files. Chats and agents use that remote working folder.

Existing remote services keep their configuration. Relay and phone-access setup are managed separately from this SSH section.

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

Run Dear Diane directly:

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
- the same Dear Diane composer and durable session model as Work.

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
src/dan/worker/organisms Universal Organism, Dear Diane, local tool runtime
src/dan/cli/             Retained server, Dear Diane, and TUI commands
src/dan/server/          Work/Notes, sessions, and Agent V2 control plane
src/dan/providers/       OpenAI, Anthropic, and Google provider adapters
src/dan/tools/           Selectively loaded Dear Diane capabilities
src/dan/skills/          Skill discovery and loading
editor/                  Work/Notes React + Electron app
mobile/                  Optional Flutter phone app
docs/plans/              Active numbered roadmap (1–7)
```

## Archive policy

Git is the archive. Do not add an in-tree legacy archive or reintroduce old product modes for compatibility. If historical code is needed, inspect `0d630dca`, the complete pushed pre-cutover recovery point (including legacy tests and their assets).

### Agents and models

**Lead** and **Team** share the same model controls. Choose **Default**, **Codex**, **Claude Code**, **Cursor**, or **Antigravity**, then choose **Native** (Default configuration for the built-in agent) or **API**. API providers are OpenAI, DeepSeek, Moonshot / Kimi, and OpenRouter. Each provider remembers its model and reasoning choice; native choices retain supported fast mode.

Add or remove keys in **Settings → Agents & skills → API providers** on the local app. Keys are stored in a host-local file with owner-only permissions, never in browser profiles or model catalogs. Saved keys take precedence over server environment keys; removing a saved key restores any environment fallback. No backend restart is needed after saving a key.

Codex supports all four API providers. Claude Code supports DeepSeek, Moonshot, and OpenRouter; non-Anthropic OpenRouter models remain experimental. Default uses Chat Completions and currently limits official OpenAI selections to GPT-4.1 models; choose Codex for GPT-6 tool calling. Cursor and Antigravity use native sources. API fast mode is unavailable; reasoning choices follow the selected model's capabilities.

Server keys can also use `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY` (or `KIMI_API_KEY`), and `OPENROUTER_API_KEY`, with `DAN_` overrides. Provider selection does not rewrite CLI configuration or log out native accounts.

Open **Team** to enable delegates and configure each harness/model combination. Codex accounts come from local codexx configuration (`DAN_CODEXX_CONFIG` can override its path). Native Claude uses its current configuration or profiles under `DAN_CLAUDE_ACCOUNTS_DIR` (default `~/.claude-accounts`). Install each CLI separately and authenticate it when using native models; Antigravity uses `agy`. Unsupported controls stay disabled.

**Import native session** in the project sidebar finds conversations matching the project's folder. Select one and choose **Import as fork**, or Cancel. JSONL transcript imports support up to 256 MB. Imports preserve original history; Codex/Claude native continuation forks before use. Antigravity import is currently unavailable because its headless fork path is unverified. Workers run concurrently under a Dear Diane manager; remaining workers stop when the manager finishes. Authenticated live acceptance and restart-resume/approval UI remain pending.

Project **⋯** menus offer New chat, Edit project, and **Remove from Dear Diane**. Removal requires confirmation and hides the project in the current Dear Diane profile; folders, files, native sessions, and saved Dear Diane chat history remain intact.

Project menus (•••) include **Import native sessions**, with multi-select and **Select all**. Imports create forks and preserve native originals. The sidebar footer opens **Dear Diane settings** for profile-wide preferences.

Native leads retain their sessions and can delegate through a run-scoped bridge. Switching model sources keeps Dear Diane history while using separate native continuations for each source. Native follow-ups queue after the current turn; saved follow-ups resume automatically after a backend restart using their recorded account and settings. Stop closes the lead and its team.

In **Archived chats**, **Delete all archived chats** removes archived Dear Diane copies after confirmation. Active chats, native originals, and project files are preserved.

Codex’s built-in subagents also appear automatically in **Team**, with recorded command activity and final replies. This does not require enabling a Dear Diane delegate. These children are controlled by their Codex lead, so their cards do not offer individual Stop buttons.

### Desktop updates

Open **Dear Diane settings → Updates → Install local update** to install an automatically detected prepared build without selecting a file. Finish active runs and queued messages first. Dear Diane verifies the build, closes its owned backend, replaces the app, and reopens it; chats/settings stay in Application Support. A rollback copy and your icon are retained. **Check for updates** tries a prepared local build before GitHub. Manual selection is under **Other update options**; `DAN_LOCAL_UPDATE_PATH` can specify another `.app`.

Published releases use `electron-updater`; **Check for updates** and **Download** become available when the packaged release channel is configured. The channel points to private GitHub Releases for this repository. Use **Sign in to GitHub** in Updates to authorize in your browser; the personal build uses the installed GitHub CLI behind the scenes. A production signing identity and published releases are still needed. See [release setup](docs/desktop-updates.md). The Python backend is currently separately installed; a desktop update does not upgrade that environment.

## Naming and compatibility

The product is **Dear Diane** and the assistant is **Diane**. After installing the Python package, use `dear-diane --help`; existing `dan` commands still work. New desktop builds are named `Dear Diane.app`.

Existing Python imports/package name (`dan`), `DAN_*` environment variables, backend IDs, browser storage keys, bundle ID (`com.dan.desktop`), remote services, and the desktop `Application Support/dan` profile remain stable so existing installations retain their chats, accounts, settings, and update identity. The full internal rename is tracked in [the migration plan](docs/diane-name-migration.md).


### Background work and quitting

On macOS, closing the window keeps Dear Diane and its current work running in the background. Use **Quit Dear Diane** to stop the app and its owned local backend/agent process tree. Shutdown waits briefly for cleanup, then terminates captured survivors. Separately started backends and remote services have their own lifecycles. Saved sessions remain available after quitting.

Steer messages join the current run; accepted requests appear before that run's answer. Next messages start a separate follow-up after the current run ends. Messages awaiting delivery remain in **Up next**.

Chat supports file attachments through the paperclip, drag-and-drop into the composer, or clipboard file items (up to four files, 20 MB each). Browser and remote attachments are saved on the execution host. After setup succeeds, refreshing retains the workspace while connection checks run in the background.

Diane uses separate Codex session storage while reusing your selected account. Existing Diane chats continue from their saved history in a private native session; importing a Codex chat copies its conversation history. Old entries already visible in Codex remain there.

Files open in main-pane tabs alongside chats. Use Back/Forward (including mapped mouse buttons) to revisit chats and files, and drag the divider to resize an open side pane.

The sidebar’s Reading section lists up to five recent open PDFs/files, with Show all for the rest, separately from project chats. Desktop Back/Forward is also available from the Go menu.

Scanned PDF comments save immediately, including offline. Selected pixels and the OCR hint are transcribed through OpenRouter using Qwen3-VL 32B (GLM-4.6V fallback); native text PDFs use their text directly. Pending quotes retry when connected, while your comment and highlight remain saved. **Single Page / Two Pages** changes the page arrangement; scan-column detection is automatic.
