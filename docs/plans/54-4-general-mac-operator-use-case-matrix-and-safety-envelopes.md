# 54-4: General Mac Operator Use-Case Matrix And Safety Envelopes

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** not-started
**Goal:** Freeze the first broad DAN-v2 use-case matrix and the safety envelopes that let one durable chat plane handle local files, shell/git, web, browser, desktop, and messaging work on macOS without collapsing into one unsafe giant tool loop or one brittle fixed-chain library.

## Tasks

- [ ] 1. Freeze the first broad DAN-v2 use-case packs
  - [ ] 1-1. Knowledge and local-context asks: repo/docs/PDF/spreadsheet answers plus current web facts
  - [ ] 1-2. Local operator tasks: create/edit files, run shell/test/build flows, inspect git state, branch, and commit
  - [ ] 1-3. Browser and download tasks: navigate pages, extract text, capture screenshots, submit forms, and download artifacts
  - [ ] 1-4. Desktop and messaging tasks: focus apps, use clipboard/file dialogs, send notifications, and prepare or deliver email / WeChat / Telegram follow-ups
  - [ ] 1-5. Cross-pack chained tasks that may span multiple families without hard-coding one exact route
- [ ] 2. Define the safety and approval envelopes
  - [ ] 2-1. Read-only envelope for local/web asks
  - [ ] 2-2. Local mutation envelope for file/shell/git work
  - [ ] 2-3. External-side-effect envelope for browser input, desktop input, outbound messaging, and account-affecting actions
  - [ ] 2-4. Define when the supervisor may keep looping versus when it must stop, escalate, or ask for approval
- [ ] 3. Decide the organism boundary for broad operator work
  - [ ] 3-1. What the top controller handles inline versus what routes to a bounded operator organism or lane
  - [ ] 3-2. Which capabilities stay deterministic adapters instead of free-form agent behavior
  - [ ] 3-3. Share one recurrent control membrane across operator lanes and specialist organisms rather than inventing a different loop style per family
- [ ] 4. Freeze an acceptance matrix that proves end-to-end generality
  - [ ] 4-1. One frozen benchmark each for ask/research, local mutate, browser/download, and desktop/messaging assistance
  - [ ] 4-2. One cross-surface chained benchmark (for example: find information -> download artifact -> patch local file -> send summary)
  - [ ] 4-3. At least one benchmark per family must require 2+ supervision loops and score whether higher-tier guidance narrows uncertainty, requests the right delta, and avoids busywork
  - [ ] 4-4. Treat benchmark chains as benchmark families or fixtures, not as hard-wired production routes
- [ ] 5. Define explicit non-goals for v1
  - [ ] 5-1. No raw unrestricted AppleScript surface
  - [ ] 5-2. No unsandboxed system-administration autonomy or silent outbound messaging
  - [ ] 5-3. No pseudo-motivational filler as a substitute for concrete direction

## Decisions

- DAN-v2 generality should be expressed as a portfolio of bounded use-case packs, not as “one model can do anything.”
- Browser, desktop, and messaging actions require explicit policy, approval, and audit boundaries.
- Raw AppleScript may exist as a backend implementation detail or tightly scoped adapter, not as a default public agent power.
- The stable architecture element is the supervision loop contract; individual chains are only benchmarks or examples.

## Notes

- This plan makes the rewrite aim at a general Mac/operator plane without losing composability.
- A later follow-up can split communications, scheduler, or program-manager organisms out of the broader operator lane once the use-case matrix is proven.
- "Motivating" high-tier guidance means `why this matters now`, `what exact delta to produce next`, and `when to stop or ask back`, not vague encouragement.
