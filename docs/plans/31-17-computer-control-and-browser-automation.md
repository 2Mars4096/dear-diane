# 31-17: Computer Control & Browser Automation

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** in-progress
**Goal:** Give DAN a disciplined first computer-use layer: Playwright-first browser automation for web tasks, explicit browser-owned native-dialog handoff, a minimal macOS desktop fallback, and a safety-first allowlist/config model. Treat broad arbitrary desktop automation, external vision export, rich policy editing commands, and deeper lease/audit hardening as follow-on slices after the core path is reliable.

## Problem

DAN can already search the web, fetch URLs, run shell commands, take screenshots, and read/write the clipboard. But it cannot yet **act on the computer** in a structured way:

- **No browser automation.** There is no Playwright/Selenium-style tool for opening pages, clicking buttons, filling forms, downloading files, or handling multi-tab flows.
- **No general desktop control.** DAN cannot move the mouse, type, switch windows, launch apps, or handle native dialogs when a task leaves the browser.
- **No layered fallback.** Real tasks often start in the browser and then spill into native file pickers, spreadsheets, terminals, PDF viewers, or desktop apps. A browser-only tool is insufficient.
- **No safety boundary for computer-use.** General computer control is powerful and risky. DAN needs configurable allowlists by **capability chunk** (observe/browser/input/window/files/system), per-app or per-domain allowlists, approval gates, and audit logs.

The current system is therefore blind in exactly the class of tasks where human users most want help: browser workflows, repetitive desktop operations, file-dialog handling, and mixed browser/native flows.

## Design

Use a **layered control stack**:

1. **Browser layer first** — Playwright-style DOM-native control for websites and web apps
2. **Desktop layer second** — OS-level observation/input/window control when the task leaves the DOM or enters another app
3. **Perception + verification loop** — observe -> act -> verify after every step
4. **Safety-first policy** — chunked allowlists, app/domain scoping, confirmations, and audit trail

This avoids the two bad extremes:

- Browser-only: precise but too narrow
- Blind desktop-only automation: broad but brittle and unsafe

## V1 Boundary

**In scope for v1**

- Playwright-backed browser actions on allowlisted domains
- Browser session persistence per task
- Explicit handoff from browser actions into browser-owned native dialogs
- Minimal macOS desktop primitives: `observe`, `focus`, `click`, `type`, `hotkey`
- Local OCR first, no external vision by default
- Basic safety policy, confirmations, and a simple single-active-session guard

**Follow-on after v1**

- Broad arbitrary cross-app desktop automation
- Rich slash-command policy editing (`/computer allow|deny ...`)
- External vision export with cropping/redaction workflow
- Cross-process or multi-host lease management beyond a simple local guard
- Rich audit/history UI and longer-retention policy analytics

## Tasks

- [x] 1. **Computer control policy and config**
  - [x] 1-1. `ComputerControlConfig` model: `enabled: bool`, `foreground_only: bool = True`, `chunk_policies: ChunkPolicies` where each chunk has its own scoped rules instead of one flat allowlist:
    - `observe`: broad read-only access, optional app allowlist
    - `browser`: `BrowserDomainRule[]` with explicit semantics (`pattern`, `includes_subdomains`, `allow_redirect_targets`, `allow_downloads`)
    - `input`: allowed apps/windows only
    - `window`: allowed apps/windows only
    - `files`: `allowed_download_dirs`, `allowed_upload_roots`, overwrite policy
    - `system`: per-action confirmation by default
    Also define precedence rules: persistent deny > session deny > session grant > persistent allow.
  - [x] 1-2. Config storage: `~/.dan/computer_control.json` with env var overrides. Since policy is now chunk-scoped, use either a JSON env override (`DAN_COMPUTER_POLICY_JSON`) or chunk-specific vars (`DAN_COMPUTER_OBSERVE_APPS`, `DAN_COMPUTER_BROWSER_DOMAINS`, `DAN_COMPUTER_INPUT_APPS`, `DAN_COMPUTER_FILE_DOWNLOAD_DIRS`, etc.) instead of the old flat `ALLOWED_*` model
  - [x] 1-3. Capability-chunk allowlists: users can allow `observe` broadly while restricting `input`/`system` to specific apps or sessions
  - [x] 1-4. Session overrides: temporary approvals for the current task/session without changing the persistent config
  - [x] 1-5. Safety policy: secret-field typing blocked unless explicitly approved; destructive actions (delete, submit, purchase, send, overwrite) require confirmation unless the user has opted into autonomous mode for that chunk

- [x] 2. **Browser automation layer**
  - [x] 2-1. `BrowserController` protocol: `open(url)`, `click(selector)`, `type(selector, text)`, `fill(selector, text)`, `select(selector, value)`, `wait_for(selector|network|idle)`, `extract_text(selector|page)`, `screenshot()`, `download()`, `upload()`, `list_tabs()`, `switch_tab()`
  - [x] 2-2. Playwright-first backend (`PlaywrightBrowserController`) — do **not** use Selenium as the primary path; Playwright is more reliable for modern web apps, waits, downloads, and multi-tab handling
  - [x] 2-3. Browser session model: persistent context per task (cookies, tabs, downloads, current URL) with explicit cleanup on task end. Add `BrowserSessionContext` / handoff token so browser-owned native dialogs can be tied to an approved domain/session.
  - [ ] 2-4. Native dialog boundary: if a browser action triggers a native file picker or save dialog, hand off to the desktop layer instead of failing. Desktop handoff is allowed **only** for explicitly modeled browser-owned native surfaces (file pickers, save dialogs, permission sheets, browser chrome), not arbitrary content inside the browser window.
  - [x] 2-5. Domain allowlist enforcement: browser automation only acts on domains allowed by `ComputerControlConfig`

- [x] 3. **Desktop automation layer**
  - [x] 3-1. `DesktopController` protocol: `screenshot(region=None)`, `ocr(region=None)`, `list_windows()`, `focus_window(title|app)`, `launch_app(app_name)`, `move_mouse(x, y)`, `click(button="left")`, `double_click()`, `right_click()`, `drag(start, end)`, `type_text(text)`, `hotkey(keys)`, `scroll(dx, dy)`, `clipboard_read()`, `clipboard_write(text)`
  - [x] 3-2. macOS backend first: Accessibility API + Quartz events + AppleScript where useful. Reuse/upgrade existing `screenshot` and `clipboard` tools rather than duplicating them.
  - [ ] 3-3. Cross-platform interface now, partial backend later: define protocol + capability contracts for Windows/Linux even if v1 only fully implements macOS. Non-macOS backends are follow-on work, not part of the first implementation.
  - [ ] 3-4. Window/app allowlist enforcement: `input`, `window`, and `system` chunks only work inside approved apps/windows. **Critical rule:** desktop control inside browser-owned content areas is denied by default; only browser-triggered native dialog handoffs with a valid `BrowserSessionContext` token are allowed.
  - [ ] 3-5. File-dialog support: save/open/upload flows via desktop backend so browser workflows can cross the native boundary
  - [x] 3-6. macOS permission doctor: detect and explain missing Screen Recording, Accessibility, and Apple Events permissions; provide startup capability probe and degraded-mode reporting
  - [ ] 3-7. Stable app identity: match apps/windows by bundle ID where possible, not just display name/title, to avoid spoofing and fragile app matching
  - [ ] 3-8. **V1 scope guard:** keep the first implementation narrow: browser automation + explicit native-dialog handoff + minimal desktop primitives (`observe`, `focus`, `click`, `type`, `hotkey`) on macOS. Broader arbitrary cross-app workflows are a follow-on slice after the safety/lease model proves reliable

- [x] 4. **Perception and verification loop**
  - [x] 4-1. `UIObservation` model: `surface_type: Literal["browser", "desktop"]`, `screenshot_path: str | None`, `ocr_text: str | None`, `window_title: str | None`, `page_url: str | None`, `elements: list[ObservedElement] | None`
  - [x] 4-2. `ObservedElement` model: `label`, `role`, `bounds`, `confidence`, `selector: str | None` so browser/native targets can be handled uniformly
  - [ ] 4-3. Execution loop: **observe -> act -> verify**. After every click/type/hotkey, re-observe and confirm the expected state changed; otherwise retry or escalate
  - [ ] 4-4. Use deterministic methods first: DOM selectors first, then local OS/UI tree access (Accessibility / AX on macOS), then local OCR, then vision-model interpretation as fallback-only for ambiguous UIs.
  - [ ] 4-5. Privacy boundary for external models:
    - OCR text sent to an external LLM goes through 31-10 provider-boundary tokenization
    - Raw screenshots / image crops do **not** become safe just because 31-10 exists; image export requires a separate high-risk `vision_export` approval path
    - v1 default: local OCR only; external vision fallback is disabled unless the user explicitly enables it **and** `DAN_PII_PROTECTION=1`
  - [ ] 4-6. Follow-on slice: add local pre-send redaction/cropping for screenshots (region selection, blur/redaction overlays). v1 sidesteps most of this by keeping OCR local and keeping external vision export disabled by default.

- [ ] 5. **Tool surface and concierge integration**
  - [ ] 5-1. New capability tools:
    - `browser_open`, `browser_click`, `browser_type`, `browser_fill`, `browser_select`, `browser_wait`, `browser_extract`, `browser_screenshot`, `browser_tabs`, `browser_download`, `browser_upload`
    - `desktop_observe`, `desktop_windows`, `desktop_focus`, `desktop_launch`, `desktop_click`, `desktop_drag`, `desktop_type`, `desktop_hotkey`, `desktop_scroll`
  - [x] 5-2. `ComputerUseController`: high-level runtime helper that chooses browser actions when the target is in the DOM, then falls back to desktop control when the task crosses into native UI
  - [ ] 5-3. **V1 command surface (minimal):**
    - `/computer status`
    - `/computer doctor`
    - `/computer approve <request-id>` (text fallback only for a pending approval request)
    Config/policy edits stay in `~/.dan/computer_control.json` and env vars for v1. Follow-on slice: `/computer allow|deny ...` once 31-16 command infrastructure is in place and the core runtime is proven.
  - [ ] 5-4. Registry note (31-16): register `/computer` via `CommandDescriptor` if 31-16 has landed. Otherwise, wire a temporary fast-command shim with a TODO for registry migration. Treat `/computer` as a **policy/diagnostics** command family, not the imperative execution layer itself.
  - [ ] 5-5. Progress integration (31-14): computer-use tasks emit phase updates like "opening browser", "filling form", "waiting for download", "switching to file dialog", "verification failed; retrying"
  - [ ] 5-6. Approval integration: reuse 31-14's `required_clarification` / checkpoint model for sensitive/destructive/system actions. Surface-native buttons should be preferred; `/computer approve <request-id>` is only the text fallback. Pending approvals must survive pause/resume (31-11) and cross-surface transfer (31-13).
  - [x] 5-7. `ComputerUseLeaseManager`: **v1 simple guard** — one active input-control session per host/process by default. Read-only observation may run concurrently, but mouse/keyboard actions from scheduled/background tasks are denied unless the host is idle and explicitly granted. Follow-on slice: cross-process / richer lease coordination if concurrent local runtimes become common.

- [x] 6. **Safety, approvals, and audit trail**
  - [x] 6-1. Action classification: `read_only`, `benign_input`, `sensitive_input`, `destructive`, `system_level`
  - [x] 6-2. Approval gates:
    - `read_only`: no confirmation by default
    - `benign_input`: allowed within allowlisted apps
    - `sensitive_input`: confirmation unless session override exists
    - `destructive` / `system_level`: always confirm unless user explicitly opted in
  - [x] 6-3. Audit log: persist timestamped action records with app/window/domain, action type, target class, and verification result. **V1:** minimal redacted local log only. Never persist typed secret values, raw OCR text, or user-visible screenshot paths for sensitive actions; store only redacted metadata (length/hash/classification). Richer audit/history UX is follow-on.
  - [ ] 6-4. Foreground-only rule by default: no background clicking/typing into hidden windows unless explicitly enabled
  - [ ] 6-5. Abort/fail-safe: global stop path if repeated verification failures occur or the active window no longer matches the expected target
  - [ ] 6-6. File safety: define `allowed_download_dirs`, `allowed_upload_roots`, overwrite confirmation rules, no-auto-open defaults, and TTL cleanup for screenshots, temp crops, and downloads

- [x] 7. **Tests and docs**
  - [x] 7-1. Unit tests: config matching, chunk/app/domain allowlist checks, action classification, approval policy, `ComputerUseController` browser→desktop fallback logic
  - [x] 7-2. Browser integration tests: mock Playwright backend for navigation, click, fill, download, multi-tab, and native-dialog handoff. Add explicit tests for "deny desktop click in browser content area on non-allowlisted domain" and "allow native dialog handoff tied to approved browser session."
  - [x] 7-3. Desktop integration tests: mocked desktop backend for observe/click/type/window focus; macOS manual smoke checklist for real backend
  - [x] 7-4. Safety tests: secret-field block, destructive action confirmation, wrong-window abort, domain denylist rejection, screenshot export blocked when `DAN_PII_PROTECTION=0`
  - [ ] 7-5. Update architecture, `docs/commands.md`, `docs/llm-api-guide.md`, changelog. Touch `docs/cli.md` only if a new binary/flag is added.

## Decisions

- **Playwright first, not Selenium.** Selenium is a fallback option only if Playwright becomes impossible in some environment. The plan assumes Playwright as the browser-control substrate.
- **Layered, not monolithic.** Browser automation is primary; desktop automation is fallback and broader coverage. They share policy and observation models but are separate backends.
- **Desktop fallback is constrained, not universal.** Desktop control may not bypass browser-domain policy by acting inside arbitrary browser content. In browser windows, desktop automation is limited to explicit native handoff surfaces unless separately approved.
- **Capability chunks are the main safety unit.** Users should be able to say "allow observation and browser actions, but block system-level actions" without hand-editing dozens of flags.
- **macOS backend first, protocol cross-platform from day one.** DAN already has macOS-specific `screenshot` and `clipboard` capabilities. Build on that reality, but design the interface so Windows/Linux backends can plug in later.
- **No blind autonomous clicking.** All computer-use actions must follow observe -> act -> verify. Failure to verify is a first-class runtime outcome, not a silent assumption.
- **Approval UX is shared, not bespoke.** Computer-use approvals reuse the `required_clarification` / checkpoint flow from 31-14 so behavior stays coherent across CLI, editor, Telegram, and WhatsApp.
- **V1 is intentionally narrow.** The first implementation should optimize for reliable browser automation plus browser-owned native dialogs and a few allowlisted local apps, not full arbitrary remote-desktop replacement on day one.
- **Runtime first, product surface second.** Get the browser/native handoff and safety model working before growing the `/computer` command family or rich policy-edit UX.

## Dependencies

- Existing `screenshot` and `clipboard` capabilities (25-13) — reuse for desktop observation and clipboard I/O
- `ChatCapabilityRegistry` (25-1) and 31-3 capability exposure — tool registration path
- `ProgressReporter` / 31-14 Progressive Response UX — long computer-use tasks need visible progress
- 31-10 PII Tokenization — OCR text sent to an external LLM should be tokenized at the provider boundary; raw screenshots require separate `vision_export` approval and local redaction/cropping
- 31-16 Command Surface Unification — optional `/computer` command family should register via the command registry
- Messaging adapters / CLI / editor surfaces — delivery and approval UX

## Primary Files

- `pyproject.toml` — add `playwright` dependency and optional extra group (modify)
- `src/dan/server/capability_registry.py` — capability context / tool registration seam (modify)
- `src/dan/server/app.py` — startup wiring, backend availability checks, permission doctor exposure (modify)
- `src/dan/server/chat_factory.py` — local/server chat runtime wiring for computer-use capability availability (modify)
- `src/dan/tools/browser_control.py` — `BrowserController`, Playwright backend, session model (new)
- `src/dan/tools/desktop_control.py` — `DesktopController`, macOS backend, observation/input/window primitives (new)
- `src/dan/server/concierge/computer_policy.py` — allowlist config, approval policy, action classification (new)
- `src/dan/server/concierge/computer_use.py` — `ComputerUseController`, observe/act/verify loop, browser→desktop fallback (new)
- `src/dan/server/capability_handlers.py` — register computer-use capability tools and minimal `/computer` command wiring (modify)
- `src/dan/server/concierge/runtime.py` — transient approval plumbing / optional command shim if 31-16 not present (modify as needed)
- `src/dan/providers/` — optional provider wrapper integration when screenshot/OCR text is sent to external models (modify as needed)
- `tests/test_tools/test_browser_control.py` — browser tool tests (new)
- `tests/test_tools/test_desktop_control.py` — desktop tool tests (new)
- `tests/test_concierge/test_computer_policy.py` — allowlist/approval policy tests (new)

## Estimate

5-7 days for the narrowed v1 core. Follow-on hardening/integration slices would add another 4-6 days if/when the broader desktop/vision/policy surface is pursued.

## Notes

- DAN already has the first two seeds of desktop control: `screenshot` and `clipboard`. This plan extends those into a coherent computer-use stack.
- The browser layer is useful on its own, but the real value comes from the browser→desktop handoff for native dialogs and non-web apps.
- To keep implementation tractable, v1 should center on browser automation plus explicit native-dialog handoff and a minimal macOS desktop backend. Broader general desktop automation is a follow-on expansion, not a day-one requirement.
- OCR should prefer a local/deterministic path first. Vision LLMs are powerful but expensive, slower, and risk leaking screen contents. OCR text can use 31-10 tokenization; raw images need separate approval plus local cropping/redaction.
- The initial backend should target **personal-use local control**, not remote fleet-style desktop automation.
- Computer use is a **local GUI-host feature**. Remote/chat surfaces may request it, but execution only works on a live host with GUI access and required OS permissions; otherwise DAN should return a deterministic unavailable/permission-needed result.
- A good implementation bar for v1 is: browser automation feels reliable on allowlisted domains, native upload/download dialogs work, and the system fails safely when permissions, domains, or host availability do not match policy.
