# Dear Diane

Dear Diane is a desktop workspace for AI agents, papers, and research notes. Give an agent a project folder, follow its work, and review edits alongside your files. Use native agents such as Codex and Claude Code or models from an API provider.

## Get started

Diane runs a Python backend with a desktop or browser interface.

### Requirements

- Python 3.11 or newer.
- Node.js 22.12 or newer for the desktop or browser interface.
- An OpenRouter API key for the app's initial setup.
- Any native agent CLI you want to use, installed and authenticated separately.

### Install

From the repository root:

```bash
pip install -e ".[dev]"
cd editor
npm install
```

### Configure a model

Create a `.env` file in the repository root. `.env.minimal` provides a short template; `.env.example` describes the optional settings. For example:

```env
OPENAI_API_KEY=...
DIANE_LLM_MODEL=gpt-5.4
```

Replace the example key and choose a model available to your account. These settings configure the built-in agent. Native agents can use their own authenticated accounts. Choose the agent and model in the Lead menu.

To set workspace and data locations:

```env
DIANE_WORKSPACE_ROOT=/absolute/path/to/workspace
DIANE_GRAPHS_DIR=/absolute/path/to/durable-state
DIANE_NOTES_WORKSPACE_ROOT=/absolute/path/to/notes
```

Keep API keys and local configuration out of commits. You can also add API keys through Settings > Providers after opening the app.

### Start the app

From the repository root, start the backend and leave it running:

```bash
diane-up
# or: diane serve --no-reload
```

In a second terminal, start the browser interface from the repository root:

```bash
cd editor
npm run dev
```

Open `http://localhost:5173/#workspace` and complete the initial setup.

To use the Electron desktop interface instead, run this in the second terminal:

```bash
cd editor
npm run electron:dev
```

If the interface cannot connect, check that the backend terminal is still running. Native agent sign-in and API provider keys are separate from the app's connection to its backend.

## Working with agents

### Start a task

1. Create a project from the sidebar and select its working folder. In the desktop app, you can also drop a folder into the workspace.
2. Choose New chat, or click + beside the project's three-dot menu. To add a project directly, choose New project in that menu. Select the Lead agent and its model before sending a request.
3. Describe the task and any constraints. Use Plan mode to inspect and discuss work before allowing changes.
4. Follow the activity indicator while the agent works. Expand Work details to inspect recorded actions.
5. Review the reply and any file changes. Use the Changes side panel to inspect project text edits and send selected differences back as feedback.

Conversations and drafts survive switching between projects. Session titles stay stable after the first request; you can rename, pin, fork, or archive a chat from its menu. Removing a project from Diane leaves the folder and its files in place.

### Plan the work first

Send `/plan` to toggle Plan mode, or `/plan <request>` to start planning a task. Type `/` to browse commands or `$` to select skills. The Plan mode status appears while planning is active. Plans stay in the session conversation.

Diane asks at most three clarification questions, which can appear together. Each question says Choose one or Choose multiple. Recommendations are labelled but never preselected. Use Other for a custom answer; typing a comment selects it automatically. Submit the answers together, or reply in chat.

Diane then presents the outcome, scope, specifications, and next steps. Choose Start this plan once blocking decisions are resolved. Starting a plan preserves the selected skills and execution permissions. Change Read only explicitly before execution.

Native agents can inspect the project while planning. The built-in API planner uses the supplied conversation and context without running tools.

### Follow-ups and side conversations

Use Next to queue a follow-up, or Steer to send a request into a supported active Codex turn. Waiting messages appear in Up next until delivered. Enter steers an active run, Cmd/Ctrl+Enter queues a follow-up, and Shift+Enter inserts a newline.

Native agent runs have no fixed overall time limit in Diane. Stop ends the lead and its managed team.

The Chat side panel is a separate saved conversation with its own Lead, Team, and access settings. Select text in a message or document to stage a reference for your next question. In Reading, the chat already has the active document's identity, so you can ask about it without repeating its filename.

Attachments can be added with the paperclip, drag-and-drop, or clipboard file items. A message supports up to four files, each up to 20 MB. Browser and remote attachments are saved on the machine that runs the task.

### Agents and models

Lead runs the main conversation. Team selects agents it can delegate to. Both use the same model controls.

The lead can give a team member a bounded scope and acceptance criteria, inspect its result, and record an evidence-based review. Runtime completion and the lead's acceptance are separate; a follow-up keeps the task criteria and requires a fresh review.

Choose a native account or an API provider for each supported agent. API providers include OpenAI, DeepSeek, Moonshot / Kimi, and OpenRouter. Each provider remembers its model and reasoning choice. Unsupported controls remain disabled.

| Agent | Model sources |
| --- | --- |
| Codex | Native account or any of the four API providers |
| Claude Code | Native account, DeepSeek, Moonshot, or OpenRouter; non-Anthropic OpenRouter models are experimental |
| Default | Built-in agent configuration and supported API models |
| Cursor and Antigravity | Native configuration |

The Default agent limits official OpenAI selections to GPT-4.1 models. Use Codex for GPT-6 tool calling. API models do not support the native fast-mode control.

### Provider keys and skills

In Settings > Providers, choose Connect or Manage to add, replace, or remove a key. Keys stay on the backend host and are not returned to the browser. A saved key takes precedence over an environment key; removing it restores any environment fallback. Saving a key does not require a backend restart.

Settings > Skills controls sharing of installed skills across supported agents. Each CLI's own skill folders stay unchanged.

### Import conversations

Open a project's menu and choose Import native sessions. Select the conversations and import them as forks. Native originals remain intact. Diane's isolated Claude API-provider histories are available as independent history copies. Available import and resume behavior depends on the agent; Antigravity imports are not currently supported.

## Work with files

Drop a file into Diane, use Open file, or select it in the Files panel. PDFs and EPUBs open in the reader. Text and code files have editable tabs, Markdown has a preview, and images, audio, and video have viewers.

For desktop and project files, Save writes to the original. If the file changes on disk while you edit it, reopen it; Download copy lets you keep your draft first. Browser-selected files offer Download edits and leave the original untouched. Unsaved edits survive tab switches and prompt before closing.

Text editing supports UTF-8 files up to 2 MB. Office documents and other unsupported binary formats can be downloaded.

File links in desktop conversations resolve against the project folder. Right-click a local link for actions such as Reveal in Finder, Copy path, and Open With. Use the Files panel for paths on a remote host.

## Read and annotate

Open a PDF or EPUB, then select a passage:

- Ask stages the selection in the Reading chat.
- Comment saves a highlight and an optional note.
- Ref saves a location you can return to. Hover its tag to preview the passage or edit its label.

Reading position, annotations, references, and chat links are remembered. Open reading files are restored after restarting; closing a file removes it from that list. Browser-picked copies stay on that device.

Scanned PDFs use local OCR to make text selectable. Comments and highlights save immediately, including offline. Transcription of selected scan pixels uses OpenRouter; pending quotes retry when connected. Native text PDFs use their existing text directly. Export saves an annotated copy and preserves the original.

### Reader controls

PDFs and EPUBs share searchable Contents, grouped page/sizing controls, and a Reading settings menu. References and Notes stay visible; More contains annotated export and original download. Jump to a page directly or use the slider that appears on hover.

PDF chapters come from embedded bookmarks or labelled headings detected in text or OCR. Scanned pages become eligible as you read them.

EPUBs offer centered Comfortable/Wide reading widths, line spacing, optional link underlines, and Reader/Publisher original styling (fixed-layout books retain publisher styling). Reading fonts include Georgia, Literata, Atkinson Hyperlegible Next, system sans, Typewriter, and Handwritten (Kalam); bundled fonts work offline. PDF shows the same settings layout with unsupported typography controls disabled. Reset reading settings preserves the reading position and annotations. They support continuous vertical scrolling and horizontal single-page or two-page layouts, with keyboard and wheel navigation. Text-size controls adjust the type, and whole-book page numbers recalculate for the window, text size, and layout. You can annotate text, export an annotated EPUB, and use local OCR on illustrations. EPUBs must be unencrypted and no larger than 150 MB compressed or expanded.

### Paper library

Open Papers in the sidebar, or use Cmd/Ctrl+K to search by title, author, citation key, topic, or notes. Search accepts words in any order and small metadata typos. Scroll or use the keyboard to browse every match. An empty search shows pinned and recent papers.

Use Settings > Library > Open library to add PDF folders or Hugo paper collections. Source files remain in place. You can filter papers, inspect abstracts and notes, copy citations or BibTeX, and add selected references to a chat before sending.

Each library paper has a resumable reading session. Continue reading restores its position, annotations, references, and Reading conversation. A browser connected to the same backend host can resume that saved state; separate hosts have separate libraries. Failed saves retain a local recovery copy with retry and download controls.

Search covers metadata, abstracts, knowledge-base notes, and annotations, without full-PDF or semantic search. The catalogue and literature importer handle PDFs.

### Import literature

Choose Literature from the side-panel tool menu, then open Import. Drop in PDFs, select a configured Hugo collection, and optionally supply BibTeX. Prepare PDFs retrieves citation candidates and asks the selected lead to draft reading notes with source anchors.

Review the matches and notes before choosing Import ready items. The importer saves verified PDF copies and paper pages while preserving originals and existing notes. You can stop preparation or retry individual documents. Imports survive panel closure and reload; interrupted documents offer Retry after a backend restart. Books can receive a labelled overview.

## Notes

Notes supports Markdown and Hugo files, rendered previews, collections, and course-progress information. The side panel keeps its drafts, scroll position, and expanded details when you switch tools or reopen it.

## Browser research

Run `pip install -e '.[browser]'` in the backend environment. Diane can use installed Chrome, or you can install managed Chromium with `python -m playwright install chromium`.

In an Auto-mode project chat, describe the browser task, such as finding a paper's citation and saving `references.bib`. The browser runs on the backend machine in a fresh isolated session and closes when the run finishes. It does not use your existing Chrome profile. Plan mode allows observation and navigation. Login and CAPTCHA challenges may need your help.

On hosts with a display, the browser opens visibly. Set `DIANE_BROWSER_HEADLESS=0` or `1` to override that behavior.

## Remote work

Open Settings > SSH connections > Add SSH connection. Enter a name and hostname, such as `server.example`, `user@host`, or an existing SSH alias. Port and identity file are optional; Diane uses your SSH configuration and agent by default.

Save the connection, then use Check SSH from its menu. For a new host, connect once in your terminal to establish host trust. Password prompts are not supported by this check.

For an existing configured remote workspace, use the folder action to open it. New project can then register a folder on that machine without moving or uploading its files. Remote service deployment, relay setup, and phone access are managed separately from SSH connections.

## Sticky Todo companion

Sticky Todo can send a request to the project selected in your most recently focused Diane window, using its folder and execution settings. Todo shows progress and a brief result. Open in Diane returns to the same session for questions, approvals, and follow-up. Closing Todo leaves the task running in Diane.

The handoff requires a matching Todo companion build. Paired installed-app validation is pending; remote and browser-only handoff are unsupported.

## Settings and navigation

### Appearance and notifications

Appearance settings include eight paired light/dark palettes, a Neon preset, and ten font choices. Set fonts separately for normal text, headings, and saved highlight quotes. Dropdown controls share a live preview, and changes save automatically. Notes has independent appearance controls.

Notifications alert you when work finishes or needs attention. Browser notifications require permission. Switching Settings categories preserves unfinished provider-key entry; keys have explicit save and remove actions.

### Usage

Hover Usage in the sidebar to see each discovered Codex/Claude account and configured API provider. Codex CLI and app profiles have separate quota rows; OpenRouter shows key spending. Unavailable quota is labelled.

Settings > Usage > Open analyzer shows token use across sessions or within one session. Optional model-based classification sends step summaries through OpenRouter, not file contents or command output.

### Keyboard shortcuts

| Action | Shortcut |
| --- | --- |
| Find a paper | Cmd/Ctrl+K |
| Open a file | Cmd/Ctrl+O |
| Save a file | Cmd/Ctrl+S |
| Toggle the sidebar | `⌘/Ctrl + B` |
| Toggle the side panel | `⌘⌥B` / `Ctrl+Alt+B` |
| Switch projects | Cmd/Ctrl+Shift+E |
| Show all projects | Cmd/Ctrl+Shift+0 |
| Return to the previous item | Cmd/Ctrl+1 |
| Open an older recent item | Cmd/Ctrl+2 through Cmd/Ctrl+9 |
| Close a tab | `⌃⌘W` on Mac; `Ctrl+Alt+W` on Windows/Linux |

Hold Cmd or Ctrl to reveal recent-item shortcut numbers in the sidebar. Back and Forward revisit chats and files. Drag the sidebar edge or side-panel divider to resize it.

### Closing Diane

On macOS, closing the window leaves work running. Quit Dear Diane stops the app and the local backend and agents it owns. Separately started backends and remote services have their own lifecycles. Saved conversations remain available after quitting.

## Desktop updates

Use Settings > About & updates > Check for updates. Checks use the public GitHub release feed without requiring a GitHub account or GitHub CLI. Diane reports when no release is available.

Diane checks at startup and every 15 minutes, catching up when the window regains focus. A dot marks an available or downloaded update. Choose Download and install to download the update and restart. The progress bar shows percentage and transferred/total MB.

Finish active work and queued messages first. If work starts during the download, Diane keeps the prepared update until you finish.

Under Update options, use Install local update for a detected prepared build or choose a build manually; `DIANE_LOCAL_UPDATE_PATH` can specify its location. The installer validates the bundle, keeps a rollback copy, and preserves chats and settings.

Desktop updates replace the app shell. Update Python and its dependencies separately.

## Development

From the repository root:

```bash
python -m compileall -q src/diane

cd editor
npm test
npm run electron:compile
npm run build:verify
```

Live-provider and real-browser evaluations are opt-in. Flutter 3.41 or newer is needed only to build the optional phone client. Planned phone personal-agent features are not yet available.

The Python package is `dear-diane`, with imports under `diane`. Use `dear-diane --help` for commands; use `DIANE_*` configuration names. Existing `dan` aliases and legacy `DAN_*` variables remain supported; `DIANE_*` takes precedence when both are set.

Team settings let you include worker providers and set **Max parallel workers** (1–4, default 2). This shared limit excludes the lead; the lead can use fewer workers and can assign multiple workers to one provider. Settings apply to the next run.

Diane requests the selected native Codex model’s catalog context maximum and full 1M context for supported Claude models. Automatic compaction stays enabled; actual usable limits depend on the model and account.

The Team panel groups worker sessions beneath their lead run. Select a lead to return to its conversation, or expand a worker to inspect its assignment, result, lead review and recorded integration. Completion alone does not imply acceptance.

Use **Team → + Add worker** to configure each worker independently, including multiple workers using the same CLI with different models. Expand a worker to change its name, account, model or reasoning. The lead selects a configured worker, and each worker takes one task at a time within the Team-wide parallel limit.

Workers can optionally create child sessions using their own agent, account, model and reasoning settings. Enable **Allow child sessions** for a Diane, Codex or Claude Code worker. Children share the team’s parallel limit, appear beneath their parent, and stop when that parent finishes.


Team changes apply with your next message: steering updates the active lead’s available workers and capacity; queued messages use their saved Team settings when they start. Existing worker sessions keep their configuration.


Editing workers in Git projects use separate worktrees. The lead inspects their changes and records a review before merging them back; conflicting project changes are preserved. Shared-workspace workers are for read-only inspection.


Codex and Claude Code leads support live steering from the composer. Claude steering interrupts the current generation and delivers the correction in the same native session; queued messages remain separate.


The Team panel shows every configured worker, including idle slots. Workers get default names such as Worker 1 and Worker 2; rename them in Team settings. Assigned tasks and child sessions appear beneath their worker, with previous assignments collapsed.



Claude Code offers Read only, Ask before actions, Accept edits, native Auto, and Full access. Configure **Command sandbox** separately in the agent settings. Permission requests appear inline with **Allow once** and **Deny**; native deny rules and managed policies still apply. Mode default enables the sandbox except in Full access. Changes apply to the next run.

Local development desktop builds prefer the project’s `.venv` Python. Startup checks that the selected environment can import the backend; `DIANE_PYTHON` explicitly selects another interpreter.

In chat, expand the **Lead** heading to see worker task summaries and results. Select a worker to open its conversation in the Team sidebar.

Long conversations show recent messages first. Use **Load earlier messages** above the transcript to reveal older history.

Leads can inspect earlier worker sessions in the same conversation and resume eligible stopped work with its existing native context and working copy. Explicit slash commands control runs; ordinary words such as “resume” and “stop using…” remain conversational.
