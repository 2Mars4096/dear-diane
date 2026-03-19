# Deep Agent Network (DAN) -- User Experience Review

**Date:** 2026-03-19
**Reviewer:** Claude Opus 4.6
**Scope:** Onboarding, error messaging, progressive disclosure, chat UX, configuration, feedback/notification, accessibility and trust

---

## 1. Onboarding / First-Run Experience

### Findings

**P1 -- No API key validation at startup; cryptic failure on first run.**
The `.env.example` ships with `DAN_LLM_API_KEY=your-api-key-here` (line 36). If a user copies `.env.example` to `.env` and runs `dan-chat` or `dan-up` without editing the key, the system will proceed normally through startup, connect to the server, and only fail on the first LLM call with a provider-level HTTP error (likely a 401). There is no startup check that validates the API key is set to something other than the placeholder. The providers directory (`src/dan/providers/`) contains no validation for empty or placeholder API keys.

**Recommendation:** Add a startup guard in `src/dan/cli/__init__.py:resolve_config()` or `src/dan/server/startup.py:_get_engine_config()` that checks for the `your-api-key-here` sentinel and prints an actionable message.

**P2 -- README Quick Start requires two terminals without explaining why.**
The Quick Start section (README.md, lines 53-64) tells users to open two terminals -- one for `dan-serve` and one for `cd editor && npm run dev`. There is no explanation that `dan-up` exists as a simpler one-command alternative, even though `dan-up` is documented later (line 181). A new user following the Quick Start linearly will use the more cumbersome path.

**Recommendation:** Lead with `dan-up` in the Quick Start. The two-terminal approach can be a "development mode" section below.

**P2 -- `dan-up` does not pass through `dan-chat` arguments.**
`src/dan/cli/up.py:drop_into_chat()` (line 126-131) hard-codes the exec into `dan-chat --server {url}` without forwarding any other arguments (like `--workflow-id`, `--mode`, `--model`). A user who runs `dan-up --port 9000 --workflow-id my-flow` will have the port respected but the workflow ID silently dropped.

**P2 -- Local mode fallback is silent about capabilities.**
When `dan-chat` cannot reach the server, it falls back to local mode (line 1535-1537 of `src/dan/cli/chat.py`). The only feedback is "dan-chat -- local mode (server unavailable)". It does not explain what local mode lacks compared to server mode (e.g., no gateway dispatch, no published workflows, no concurrent runs, no adapter integrations).

**P3 -- `git clone` URL is a placeholder.**
README.md line 27: `git clone https://github.com/your-org/deep-agent-network.git` -- the `your-org` placeholder will confuse users who try to follow the Quick Start literally.

**P3 -- No guided first-run experience.**
On first `dan-chat`, the REPL prints a banner with model/tier/learning/MCP status and "Type /help for commands, /exit to quit." There is no welcome message explaining what DAN can do, no example prompt, and no orientation. The banner information (tier policy: off, learning: off) is insider terminology meaningless to a new user.

### Strengths

- Automatic local-mode fallback when the server is unavailable is excellent -- graceful degradation.
- `dan-up` lifecycle management with PID tracking, lock files, and health polling is robust.
- Tab-completion for slash commands is set up automatically.
- Readline history persistence across sessions is a strong usability detail.
- Unknown slash commands produce fuzzy-match suggestions ("Did you mean: ...?").

---

## 2. Error Messaging Quality

### Findings

**P1 -- Server error messages expose raw HTTP status codes and response bodies.**
Throughout `src/dan/cli/chat.py`, error messages bubble up as `RuntimeError(f"Chat API error {resp.status_code}: {resp.text}")` (line 125), etc. A 500 response will dump an internal server traceback. These errors lack actionable guidance.

**Recommendation:** Wrap HTTP errors in a user-friendly layer: for 4xx errors, explain what the user likely did wrong; for 5xx errors, suggest restarting the server or checking logs. Raw details should be verbose/debug-only.

**P1 -- Provider failures during LLM calls produce opaque errors.**
When a provider call fails (wrong API key, rate limit, model not found), the error propagates as a generic `RuntimeError` or `ChatErrorEvent` with the raw upstream error message. There is no mapping from common provider errors to actionable user guidance. A 401 should suggest "Check your DAN_LLM_API_KEY"; a 404 model error should suggest "The model name may be incorrect."

**P2 -- Server startup silently degrades many subsystems.**
In `src/dan/server/startup.py`, at least 15 subsystems can fail during initialization and are silently swallowed with `logger.debug("...skipped", exc_info=True)`. The user sees no indication that a feature they configured is not working.

**Recommendation:** After all startup phases, emit a structured summary listing degraded/unavailable subsystems. Surface this at WARNING level and in the `dan-chat` banner.

**P2 -- "No stream channel returned" is a dead-end error.**
In `src/dan/cli/chat.py` (line 1091-1093), when the server responds without a `stream_channel_id`, the user sees "No stream channel returned" with no context on why or what to do.

### Strengths

- The `dan-up` timeout message is excellent: "Server failed to start within timeout. Check logs: ~/.dan/logs/server.log" -- tells the user exactly where to look.
- Mutation dry-run failures are surfaced clearly with actionable guidance.
- The graceful shutdown handler in `dan-run` is user-friendly.

---

## 3. Progressive Disclosure

### Findings

**P1 -- Two "coming soon" placeholder modes visible in the ModeBar.**
In `editor/src/store/useAppStore.ts` (lines 14-20), `MODE_CONFIGS` defines 6 modes. Two (`analytics` and `content`) are marked `enabled: false` and render as grayed-out tabs with "(coming soon)" tooltip. They occupy space in the mode bar and signal incomplete software.

**Recommendation:** Hide disabled modes entirely. Use a roadmap section in settings rather than grayed-out tabs in primary navigation.

**P2 -- `.env.example` is 246 lines with 80+ variables.**
Well-organized with clear sections, but the sheer volume is intimidating. The "Quick Profiles" section helps, but scrolling through learning tiers, self-adaptive behavior, model tiering, concierge behavior, embeddings, PII protection, and telemetry is overwhelming for first-time setup.

**Recommendation:** Create a minimal `.env.minimal` with only the 3 required variables. Keep the full `.env.example` as an advanced reference.

**P3 -- `/help` output is likely overwhelming for new users.**
The command registry supports commands across multiple groups and surfaces. No tiered help system (e.g., `/help` for essentials, `/help all` for everything).

**P3 -- `dan furnace` CLI subcommand exposed without context.**
Listed alongside user-facing commands without explaining what Furnace is.

### Strengths

- Mode bar uses clear icons and short labels with keyboard shortcuts in tooltips.
- Lazy loading with Suspense and skeleton fallbacks keeps initial load fast.
- The `ErrorBoundary` provides clean recovery UI with "Try again" button.
- Deferred-mount strategy per workspace ensures modes only render when first activated.

---

## 4. Chat UX

### Findings

**P2 -- Dual identity: "DAN the workflow builder" vs "DAN the personal assistant".**
The system has two system prompts presenting different identities:
- `SYSTEM_PROMPT_TEMPLATE`: "You are a graph-aware assistant for DAN, an agentic workflow builder."
- `UNIFIED_SYSTEM_PROMPT`: "You are DAN, a personal AI assistant with full tool access."

The mode is auto-detected (`build` for scratch, `mutate` for existing workflows) with no explanation of what it means to the user.

**Recommendation:** When mode determines behavior, surface it clearly: "Chat mode: workflow building. Type /mode ask for general questions."

**P2 -- Reassurance messages have a 10-second initial delay.**
The default `DAN_CONCIERGE_REASSURANCE_DELAY` is 10 seconds in code (`runtime.py` line 139). This means 10 seconds of silence before any feedback. Then every 20 seconds, another update.

**Recommendation:** Reduce initial delay to 2-3 seconds, or emit a lightweight acknowledgment immediately.

**P2 -- Message queue behavior is not clearly communicated.**
The REPL says messages will be queued, but the distinction between slash commands being processed inline vs regular messages sent as separate requests is not explained.

### Strengths

- The `UNIFIED_SYSTEM_PROMPT` contains 21 well-crafted non-negotiable rules preventing common LLM failure modes.
- The `_maybe_prompt_resume_workflow` function offers quick resume of recent workflows with relative timestamps -- excellent returning-user experience.
- Preference extraction system learns user preferences from conversation history with explicit confirmation before persisting.

---

## 5. Configuration Complexity

### Findings

**P2 -- Inconsistency between .env.example defaults and code defaults.**
- `.env.example` line 123: `DAN_CONCIERGE_REASSURANCE_DELAY=2.0` (commented out). Code default: `10` seconds.
- `.env.example` ships with `DAN_FULL_TOOLS=1` active, but comment says "defaults to 0."

**Recommendation:** Reconcile code defaults with `.env.example` values.

**P3 -- Undocumented env vars.**
`DAN_USE_CODEGEN_BUILD`, `DAN_MUTATION_AUTO_RETRY`, `DAN_MUTATION_AUTO_RETRY_MAX`, `DAN_LLM_CALL_TIMEOUT`, `DAN_CHAT_MAX_CONTEXT_RATIO` are not in `.env.example`. The `LLM_API_KEY` legacy name is checked as fallback but undocumented.

**P3 -- "Tier" terminology overloaded.**
Learning system uses "tiers" (0, 1, 2). Model system also uses "tiers" (micro, routine, reasoning, critical). Different systems sharing the same terminology without disambiguation.

### Strengths

- `.env.example` is well-structured with clear section headers, comments, and progressive disclosure.
- "Quick Profiles" with one-line toggles is a good pattern for reducing cognitive load.
- Cascading provider configuration is well-documented.
- "You only need ONE baseline provider path" sets the right expectation.

---

## 6. Notification / Feedback UX

### Findings

**P2 -- Progress system has no CLI integration beyond reassurance messages.**
The `CLIProgressRenderer` (lines 253-291 of `progress_ux.py`) appends to an internal `self.output` list without actually printing to the terminal. The output list is never consumed by the CLI REPL. CLI users get only basic reassurance messages but never see richer phase-by-phase progress.

**Recommendation:** Wire the `CLIProgressRenderer` output to the actual terminal in the REPL event loop.

**P2 -- ConnectionBanner shows "Backend unavailable -- reconnecting..." but no cause or action.**
Does not distinguish between "server crashed" vs "server not started" vs "network issue". Does not suggest running `dan-up` or checking server logs.

### Strengths

- Surface-specific rendering strategy is well-designed. WhatsApp uses "bookend" pattern respecting rate limits. Telegram edits messages in-place.
- Verbosity level system (`full`/`compact`/`minimal`) with per-surface defaults and user override via `/progress` command.
- Pre-flight clarification system prevents wasted work on expensive operations by asking targeted questions first.

---

## 7. Accessibility and Trust

### Findings

**P1 -- No explicit cost visibility by default.**
`DAN_SHOW_COST=1` is commented out. The `/cost` command exists but is opt-in. Users can unknowingly accumulate significant API costs, especially with tier policy routing to expensive models or background learning LLM calls. `DAN_COST_CONFIRM_THRESHOLD` defaults to $0.50 but is also commented out.

**Recommendation:** Enable cost display by default. Show cumulative session cost in the REPL prompt or after each response.

**P2 -- Mutations are auto-applied by default.**
When `confirm_mode` is False (the default), mutations are applied automatically without user approval. The LLM can modify the user's workflow graph without explicit consent. While `/undo` is available, the default behavior is "apply first, ask questions later."

**Recommendation:** For new users, default to confirmation mode, or at least display the mutation diff clearly before auto-applying.

**P2 -- Silent subsystem failures during startup could mask safety features.**
PII protection, cost confirmation thresholds, and mutation confirmation are safety features. If their initialization fails silently, the user may believe they are protected when they are not.

**P3 -- Tool sandbox description has an important caveat buried inline.**
README line 14: "relative paths stay sandboxed to the workspace root, while explicit absolute paths are trusted and allowed" -- security-relevant information in a parenthetical clause.

### Strengths

- System prompt rules prevent fabrication and require transparency about failures.
- Mutation dry-run validates changes before applying, and failed dry-runs block auto-apply.
- `/undo` command with 10-deep stack provides a safety net.
- Preference confirmation flow respects user agency by asking before persisting.

---

## Summary Table

| # | Category | Severity | Finding |
|---|----------|----------|---------|
| 1 | Onboarding | P1 | No API key validation; placeholder key silently accepted |
| 2 | Error | P1 | Raw HTTP status codes/bodies shown as user-facing errors |
| 3 | Error | P1 | Provider failures produce opaque messages |
| 4 | Trust | P1 | No cost visibility by default |
| 5 | Disclosure | P1 | Two "coming soon" tabs visible in primary navigation |
| 6 | Onboarding | P2 | README Quick Start uses two-terminal approach; `dan-up` buried |
| 7 | Onboarding | P2 | `dan-up` does not pass through `dan-chat` arguments |
| 8 | Onboarding | P2 | Local-mode fallback doesn't explain reduced capabilities |
| 9 | Chat | P2 | Dual identity not surfaced to user |
| 10 | Chat | P2 | 10-second silence before any reassurance feedback |
| 11 | Feedback | P2 | CLI progress renderer output is never printed to terminal |
| 12 | Feedback | P2 | ConnectionBanner shows no cause or action |
| 13 | Error | P2 | 15+ subsystems silently degrade during startup |
| 14 | Error | P2 | "No stream channel returned" is a dead-end error |
| 15 | Config | P2 | .env.example defaults differ from code defaults |
| 16 | Trust | P2 | Mutations auto-applied without confirmation by default |
| 17 | Trust | P2 | Silent startup failures could mask safety features |
| 18 | Config | P2 | .env.example is 246 lines with 80+ variables |
| 19 | Onboarding | P3 | Clone URL uses `your-org` placeholder |
| 20 | Onboarding | P3 | No guided first-run experience or example prompt |
| 21 | Disclosure | P3 | `/help` output likely overwhelming for new users |
| 22 | Disclosure | P3 | `dan furnace` exposed without context |
| 23 | Config | P3 | Undocumented env vars |
| 24 | Config | P3 | "Tier" terminology overloaded |
| 25 | Trust | P3 | Sandbox caveat buried in README parenthetical |

**Top-priority items for best ROI:**
1. Add API key placeholder detection at startup (P1, quick win)
2. Wrap provider/HTTP errors in user-friendly messages with actionable guidance (P1)
3. Enable cost display by default or show session cost summary (P1)
4. Hide disabled "coming soon" modes from the ModeBar (P1, quick win)
5. Reduce reassurance delay to 2-3 seconds and wire CLI progress renderer to output (P2)
