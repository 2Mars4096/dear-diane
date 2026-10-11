# Dear Diane

Dear Diane is a desktop workspace for working with AI agents, reading papers, and keeping research notes. You can give an agent a project folder, follow its progress, review its edits, and ask follow-up questions alongside the files you are working on.

The app brings conversations, documents, and notes into one workspace. It supports native coding agents such as Codex and Claude Code, as well as models accessed through an API.

## Get started

The current setup requires a Python backend and the desktop or browser interface. Desktop updates replace the app; they do not install or upgrade Python and its dependencies.

You need:

- Python 3.11 or newer.
- Node.js 22.12 or newer for the desktop or browser interface.
- An OpenRouter API key for the app's initial setup.
- Any native agent CLI you want to use, installed and authenticated separately.

Run the installation commands from the repository root:

```bash
pip install -e ".[dev]"
cd editor
npm install
```

### Configure a model

Create a `.env` file in the repository root. `.env.minimal` provides a short template; `.env.example` describes the optional settings. For example:

```env
OPENAI_API_KEY=...
DAN_LLM_MODEL=gpt-5.4
```

Replace the example key and choose a model available to your account. These settings configure the built-in agent. Native agents can use their own authenticated accounts, and the app's Lead menu lets you choose the agent and model for a conversation.

You can set workspace and data locations explicitly:

```env
DAN_WORKSPACE_ROOT=/absolute/path/to/workspace
DAN_GRAPHS_DIR=/absolute/path/to/durable-state
DAN_NOTES_WORKSPACE_ROOT=/absolute/path/to/notes
```

Keep API keys and local configuration out of commits. You can also add API keys through Settings > Providers after opening the app.

### Start the app

From the repository root, start the backend and leave it running:

```bash
diane-up
# or: diane serve --no-reload
```

In a second terminal, also starting from the repository root, launch the browser interface:

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

## Your first task

1. Create a project from the sidebar and select its working folder. In the desktop app, you can also drop a folder into the workspace.
2. Choose New chat. Select the Lead agent and its model before sending a request.
3. Describe the task and any constraints. Use Plan mode to inspect and discuss work before allowing changes.
4. Follow the activity indicator while the agent works. Expand Work details to inspect recorded actions.
5. Review the reply and any file changes. Use the Changes side panel to inspect project text edits and send selected differences back as feedback.

Conversations and drafts survive switching between projects. Session titles stay stable after the first request; you can rename, pin, fork, or archive a chat from its menu. Removing a project from Diane leaves the folder and its files in place.

### Follow-ups and side conversations

Use Next to queue a separate follow-up. Waiting messages appear in Up next and enter the conversation when delivered. Supported Codex runs also offer Steer for sending a request into the active turn. Stop ends the lead and its managed team.

The Chat side panel is a separate saved conversation with its own Lead, Team, and access settings. Select text in a message or document to stage a reference for your next question. In Reading, the chat already has the active document's identity, so you can ask about it without repeating its filename.

Attachments can be added with the paperclip, drag-and-drop, or clipboard file items. A message supports up to four files, each up to 20 MB. Browser and remote attachments are saved on the machine that runs the task.

## Choose agents and models

Lead runs the main conversation. Team configures agents that the lead can delegate to. Both use the same model controls.

Choose a native account or an API provider for each supported agent. API providers include OpenAI, DeepSeek, Moonshot / Kimi, and OpenRouter. Each provider remembers its model and reasoning choice. Unsupported controls remain disabled.

| Agent | Model sources |
| --- | --- |
| Codex | Native account or any of the four API providers |
| Claude Code | Native account, DeepSeek, Moonshot, or OpenRouter; non-Anthropic OpenRouter models are experimental |
| Default | Built-in agent configuration and supported API models |
| Cursor and Antigravity | Native configuration |

The Default agent currently limits official OpenAI selections to GPT-4.1 models. Use Codex for GPT-6 tool calling. API models do not support the native fast-mode control.

In Settings > Providers, choose Connect or Manage to add, replace, or remove a key. Keys stay on the backend host and are not returned to the browser. A saved key takes precedence over an environment key; removing it restores any environment fallback. Saving a key does not require a backend restart.

Settings > Skills controls sharing of installed skills across supported agents. Each CLI's own skill folders stay unchanged.

To bring an existing conversation into a project, open its menu and choose Import native sessions. Select the conversations and import them as forks. Native originals remain intact. Available import and resume behavior depends on the agent; Antigravity imports are not currently supported.

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

EPUBs support chapter navigation, text-size controls, and single-page or two-page layouts. You can select text, annotate, and export an annotated EPUB. Illustrations support local OCR. EPUBs must be unencrypted and no larger than 150 MB compressed or expanded. The paper catalogue and literature importer currently handle PDFs.

### Paper library

Open Papers in the sidebar, or use Cmd/Ctrl+K to search by title, author, citation key, topic, or notes. Search accepts words in any order and small metadata typos. An empty search shows pinned and recent papers.

In Settings > Library > Open library, open the library settings to add PDF folders or Hugo paper collections. Source files remain in place. You can filter papers, inspect abstracts and notes, copy citations or BibTeX, and add selected references to a chat before sending.

Each library paper has a resumable reading session. Continue reading restores its position, annotations, references, and Reading conversation. A browser connected to the same backend host can resume that saved state; separate hosts have separate libraries. Failed saves retain a local recovery copy with retry and download controls.

Library search covers metadata, abstracts, knowledge-base notes, and annotations. It does not search the full PDF text or provide semantic search.

### Import literature

Choose Literature from the side-panel tool menu, then open Import. Drop in PDFs, select a configured Hugo collection, and optionally supply BibTeX. Prepare PDFs retrieves citation candidates and asks the selected lead to draft reading notes with source anchors.

Review the matches and notes before choosing Import ready items. The importer saves verified PDF copies and paper pages while preserving originals and existing notes. You can stop preparation or retry individual documents. Imports survive panel closure and reload; interrupted documents offer Retry after a backend restart. Books can receive a labelled overview.

## Notes and browser research

Notes supports Markdown and Hugo files, rendered previews, collections, and course-progress information. The side panel keeps its drafts, scroll position, and expanded details when you switch tools or reopen it.

To enable browser research, install `pip install -e '.[browser]'` in the backend environment. Diane can use installed Chrome, or you can install managed Chromium with `python -m playwright install chromium`.

In an Auto-mode project chat, describe the browser task, such as finding a paper's citation and saving `references.bib`. The browser runs on the backend machine in a fresh isolated session and closes when the run finishes. It does not use your existing Chrome profile. Plan mode allows observation and navigation. Login and CAPTCHA challenges may need your help.

On hosts with a display, the browser opens visibly. Set `DAN_BROWSER_HEADLESS=0` or `1` to override that behavior.

## Remote work

Open Settings > SSH connections > Add SSH connection. Enter a name and hostname, such as `server.example`, `user@host`, or an existing SSH alias. Port and identity file are optional; Diane uses your SSH configuration and agent by default.

Save the connection, then use Check SSH from its menu. For a new host, connect once in your terminal to establish host trust. Password prompts are not supported by this check.

For an existing configured remote workspace, use the folder action to open it. New project can then register a folder on that machine without moving or uploading its files. Remote service deployment, relay setup, and phone access are managed separately from SSH connections.

## Sticky Todo companion

Sticky Todo can send a request to the project selected in your most recently focused Diane window, using its folder and execution settings. Todo shows progress and a brief result. Open in Diane returns to the same session for questions, approvals, and follow-up. Closing Todo leaves the task running in Diane.

This requires a matching Todo companion build. Paired installed-app validation is still pending; remote and browser-only handoff are not supported.

## Settings and navigation

Settings has separate pages for appearance, notifications, providers, agents, skills, SSH connections, the library, usage, and updates. Appearance changes save automatically. Provider keys have explicit save and remove actions. Switching categories preserves unfinished key entry.

Appearance includes eight paired light/dark palettes and independent Notes appearance controls. Notifications can alert you when work finishes or needs attention; browser notifications require permission.

Hover Usage in the sidebar to see remaining account quota. Settings > Usage > Open analyzer shows token use across sessions or within one session. Optional model-based classification sends step summaries through OpenRouter, not file contents or command output.

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

On macOS, closing the window leaves work running. Quit Dear Diane stops the app and the local backend and agents it owns. Separately started backends and remote services have their own lifecycles. Saved conversations remain available after quitting.

## Desktop updates

Use Settings > About & updates > Check for updates. Checks use the public GitHub release feed and do not require a GitHub account or GitHub CLI. If no release has been published, Diane reports that instead of asking you to sign in.

The app also checks at startup and every 15 minutes, with a catch-up check on focus. A dot marks an available or downloaded update. When an update is available, the button changes to Download and install. One click downloads it, installs it, and restarts Diane. Finish active work and queued messages first. If work starts during the download, Diane keeps the prepared update and asks you to finish before restarting.

Under Update options, use Install local update for a detected prepared build or choose a build manually; `DAN_LOCAL_UPDATE_PATH` can specify its location. The installer validates the bundle, keeps a rollback copy, and preserves chats and settings.

Desktop updates currently cover the app shell for an existing backend installation. They do not update Python or its dependencies.

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

The Python package is `dear-diane`, with imports under `diane`. Use `dear-diane --help` for commands; existing `dan` aliases and `DAN_*` configuration names remain supported. Existing installations keep their saved data and settings when updating.
