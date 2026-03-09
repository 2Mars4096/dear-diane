# 28-6: Real-World Test Scenarios

**Parent:** [28-llm-first-chat](28-llm-first-chat.md)
**Status:** completed
**Goal:** Validate the LLM-first chat architecture with three complex, multi-turn, real-world use cases that exercise the full tool chain end-to-end.

## Why

Unit tests prove tools work individually. These scenarios prove the *system* works: the LLM selects the right tools, chains them across multiple turns, handles missing data gracefully, asks the user for help when needed, and produces professional-quality output. If these pass via WhatsApp chat, the architecture is ready for daily use.

## Scenario A: Academic Literature Review

**User prompt (WhatsApp):** "I'm writing a paper on how geopolitical tensions affect international trade flows. Can you do a literature review?"

**Expected multi-turn behavior:**

1. LLM calls `web_search("geopolitics international trade literature review academic papers")` → gets Semantic Scholar / Google Scholar results
2. LLM calls `web_search` again with more specific queries: "export controls firm-level effects", "trade war supply chain disruption", "sanctions trade diversion"
3. For each promising result, LLM calls `web_fetch` to pull abstracts, author info, citation details from Semantic Scholar / NBER / SSRN pages
4. LLM checks if the user has any of these papers locally: `list_directory("~/Dropbox/Projects/my-knowledge-base/static/papers/")` with relevant glob patterns
5. For local PDFs found, LLM calls `pdf_read` to extract content and incorporate into the review
6. LLM produces a structured literature review:
   - Thematic grouping (trade diversion, firm-level costs, supply chain restructuring, etc.)
   - Real citations with author names, journal, year
   - Key findings per paper
   - Research gaps identified
7. LLM identifies papers it couldn't access fully and asks the user:
   > "I found 3 papers that look highly relevant but I could only see the abstract:
   > - Autor et al. (2024) 'The China Shock Revisited' — Journal of Political Economy
   > - Fajgelbaum & Khandelwal (2022) 'The Economic Impacts of the US-China Trade War'
   > - Handley & Limão (2017) 'Policy Uncertainty, Trade, and Welfare'
   >
   > If you have PDFs for any of these, share the path and I'll incorporate them."
8. If user provides a path, LLM calls `pdf_read` and updates the review

**Validation checklist:**
- [x] At least 3 distinct `web_search` queries issued
- [x] `web_fetch` called to pull actual paper metadata
- [x] `list_directory` / `pdf_read` used for local papers
- [x] Output contains real author names, real journals, real years
- [x] No fabricated citations (every citation backed by a tool call)
- [x] Missing-paper follow-up prompt generated
- [ ] User can provide a PDF path and the review gets updated in the conversation

---

## Scenario B: Equity Research Report

**User prompt (WhatsApp):** "Write me a research report on Rocket Lab (RKLB). Include recent news, financials, sentiment, and a view."

**Expected multi-turn behavior:**

1. LLM calls `current_datetime` to anchor "recent" correctly
2. LLM calls `web_search("Rocket Lab RKLB stock price today")` for current price
3. LLM calls `web_search("Rocket Lab RKLB recent news 2026")` for news catalysts
4. LLM calls `web_search("Rocket Lab RKLB earnings revenue 2025 2026")` for financial data
5. LLM calls `web_search("Rocket Lab RKLB analyst sentiment rating")` for sell-side views
6. LLM calls `web_fetch` on 1-2 promising news/report URLs to get full article text
7. LLM produces a structured equity report:
   - **Company overview** — sector, market cap, key business lines
   - **Recent developments** — sourced from actual news articles with dates
   - **Financial snapshot** — revenue, growth, margins (sourced, not fabricated)
   - **Market sentiment** — analyst ratings, recent upgrades/downgrades
   - **Key risks** — competition, customer concentration, launch cadence
   - **View** — LLM's synthesis of bull/bear case
8. Every data point sourced (URL or "per [source], [date]")
9. LLM flags what it couldn't find: "I couldn't access real-time intraday data or institutional ownership. For those, check Bloomberg or your broker."

**Validation checklist:**
- [x] At least 4 distinct `web_search` queries (price, news, financials, sentiment)
- [x] `current_datetime` called to anchor dates
- [x] `web_fetch` used on at least 1 URL for full article
- [x] All financial figures sourced (no fabricated numbers)
- [x] Report includes real recent news events with dates
- [x] Disclaimer for data the LLM couldn't verify
- [x] Output is structured and readable on WhatsApp (short paragraphs, bold headers)

---

## Scenario C: Deep Research Report

**User prompt (WhatsApp):** "Do a deep research report on the current state of nuclear fusion energy. Cover latest breakthroughs, key companies, timeline to commercialization, and remaining challenges."

**Expected multi-turn behavior:**

1. LLM calls `current_datetime` to know the current date
2. LLM plans a research structure (internal reasoning, no tool call needed)
3. LLM calls `web_search` across multiple angles:
   - "nuclear fusion breakthrough 2025 2026"
   - "nuclear fusion companies funding investment"
   - "ITER tokamak progress timeline"
   - "inertial confinement fusion NIF results"
   - "nuclear fusion commercialization timeline"
   - "nuclear fusion remaining engineering challenges"
4. For high-value results, LLM calls `web_fetch` to read full articles
5. LLM produces a comprehensive report:
   - **Executive summary** — 3-4 sentence overview
   - **Recent breakthroughs** — NIF ignition, tokamak milestones, private-sector achievements (with dates and sources)
   - **Key players** — public programs (ITER, NIF) + private companies (Commonwealth Fusion, TAE, Helion, etc.) with funding amounts
   - **Timeline** — realistic assessment of when grid-scale fusion power is expected
   - **Remaining challenges** — plasma containment, materials science, tritium supply, economics
   - **Investment landscape** — total funding, key investors, recent rounds
   - **Sources** — list of all URLs/articles consulted
6. LLM may say: "For deeper analysis, I'd recommend these reports that I found referenced but couldn't access in full: [list]. Let me know if you can share any."

**Validation checklist:**
- [x] At least 5 distinct `web_search` queries across different angles
- [x] `web_fetch` used to read at least 2 full articles
- [x] All factual claims sourced with dates
- [x] Report covers all requested sections
- [x] No fabricated company names, funding amounts, or dates
- [x] Sources section at the end
- [x] Readable on WhatsApp (structured with bold headers, not a wall of text)

---

## Scenario D: Computer Task (Run Regression)

**User prompt (WhatsApp):** "I have a dataset at ~/Dropbox/Projects/data/trade_flows.csv. Run a gravity model regression — log trade on log GDP, log distance, and contiguity. Give me the results."

**Expected multi-turn behavior:**

1. LLM calls `file_read("~/Dropbox/Projects/data/trade_flows.csv")` to inspect the data (first 50 lines or so)
2. LLM examines column names, data types, identifies the right variable names
3. LLM calls `shell_command` to run a Python script:
   ```
   python3 -c "
   import pandas as pd
   import statsmodels.api as sm
   df = pd.read_csv('~/Dropbox/Projects/data/trade_flows.csv')
   ... regression code ...
   print(results.summary())
   "
   ```
4. LLM reads the regression output from shell_command result
5. LLM produces a structured summary:
   - **Model specification** — dependent variable, independent variables, N observations
   - **Key coefficients** — GDP elasticity, distance elasticity, contiguity dummy (with standard errors, p-values)
   - **Model fit** — R², F-statistic
   - **Interpretation** — 1-2 sentences on what the results mean economically
6. If the script fails (missing package, wrong column name), LLM reads the error, adjusts, and retries

**Validation checklist:**
- [x] `file_read` called first to inspect data before writing code
- [x] `shell_command` used to execute regression (not fabricated results)
- [x] Regression output contains real numbers from the actual data
- [x] LLM interprets results correctly (sign, magnitude, significance)
- [x] Error recovery: if first attempt fails, LLM adjusts and retries
- [x] Output is concise for WhatsApp (summary, not full statsmodels output)

---

## Scenario E: Casual & Utility Tasks (Mixed)

**A sequence of quick tasks in one WhatsApp conversation to test that simple tasks stay fast and correct:**

1. **"What day is it today?"** → `current_datetime` → "Saturday, March 8, 2026"
2. **"Write me a birthday greeting for my colleague Sarah who loves hiking"** → pure text generation, no tools needed → warm, personal greeting
3. **"Find me some funny cat videos"** → `web_search("funny cat videos 2026")` → list of YouTube/TikTok links
4. **"What's 15% compound growth on $10,000 over 7 years?"** → `shell_command("python3 -c \"print(10000 * 1.15**7)\"")` → "$26,600.20"
5. **"Copy that number to my clipboard"** → `clipboard(action="write", content="26600.20")` → "Copied"
6. **"Translate 'the meeting is postponed to next week' to Chinese"** → pure text → "会议推迟到下周"
7. **"Email sarah@example.com a reminder about the team lunch tomorrow"** → `send_email(to="sarah@example.com", subject="Team lunch reminder", body="...")` → "Sent"
8. **"Take a screenshot"** → `screenshot()` → "Screenshot saved to ~/.dan/screenshots/..." + file delivery

**Validation checklist:**
- [x] Each task completes in ≤1 tool call (no unnecessary multi-turn)
- [x] `current_datetime` used for date (not guessed)
- [x] Calculation done via `shell_command` (not mental math)
- [x] `clipboard`, `send_email`, `screenshot` all exercise correctly
- [x] Text-only tasks (greeting, translation) produce no tool calls
- [ ] Total response time < 10s per simple task (no routing overhead)
- [x] Conversation flows naturally across all 8 tasks in one thread

---

## Implementation

### Test fixtures
- [x] 1. Create `tests/scenarios/` directory for real-world scenario tests
- [x] 2. Each scenario as a test class with:
  - Scripted user messages (initial + follow-ups)
  - Mock or live tool responses (both variants)
  - Validation functions checking tool call sequences, output structure, citation accuracy
- [x] 3. Smoke-test mode: run against live LLM + live Tavily for real end-to-end validation (CI-excluded, manual trigger)

### Evaluation criteria (per scenario)
- [x] 4. Define scoring rubric:
  - **Tool selection accuracy** — did the LLM call the right tools? (0-10)
  - **Multi-turn coherence** — did tool results feed correctly into subsequent calls? (0-10)
  - **Output quality** — is the final report structured, sourced, readable? (0-10)
  - **Anti-fabrication** — are all factual claims backed by tool calls? (0-10)
  - **Follow-up handling** — does the LLM correctly identify gaps and ask for help? (0-10)

### WhatsApp formatting
- [x] 5. Ensure reports are readable on WhatsApp:
  - Bold headers with `*Header*` syntax
  - Short paragraphs (3-4 lines max)
  - Bullet points with `•` or `-`
  - URLs on their own line (WhatsApp auto-linkifies)
  - Total message under 4096 chars (WhatsApp limit) — split into multiple messages if needed

## Files

| File | Action |
|---|---|
| `tests/scenarios/test_literature_review.py` | Create — Scenario A |
| `tests/scenarios/test_equity_report.py` | Create — Scenario B |
| `tests/scenarios/test_deep_research.py` | Create — Scenario C |
| `tests/scenarios/test_computer_task.py` | Create — Scenario D |
| `tests/scenarios/test_casual_utility.py` | Create — Scenario E |
| `tests/scenarios/conftest.py` | Create — shared fixtures, mock tool infrastructure |
| `tests/scenarios/README.md` | Create — how to run scenarios (mock vs live) |

## Dependencies

- **28-2 (Unified Tool Dispatch)** — multi-turn tool loop is required for all three scenarios
- **28-4 (System Prompt Design)** — the unified prompt must guide multi-step research behavior
- **25-13 (Conversation File Tools)** — web_search + pdf_read + list_directory must be wired

## Notes

- These scenarios are deliberately hard. They require 5-10 tool calls across 3-5 turns. If the LLM-first architecture can handle them from a WhatsApp chat, it can handle anything.
- The literature review scenario specifically tests the "I need your help" flow — the LLM should know when it can't access something and ask the user instead of fabricating.
- The equity report scenario tests sourced-data discipline — every number must come from a tool call.
- The deep research scenario tests breadth — the LLM must search multiple angles and synthesize.
