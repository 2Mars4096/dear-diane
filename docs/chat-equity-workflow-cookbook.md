# Equity / watchlist workflows — **author in DAN, not in Cursor**

**Policy:** Do **not** paste large `code_operator` blobs or graph JSON from an external assistant. Build the workflow **inside DAN** (editor + **Agent** chat) using **`plan_graph_mutations`** → dry-run → **Apply** → save → **`start_run`**.

**There is no committed equity workflow in this repo.** The old `graphs/daily-equity-research-v3.json` placeholder was removed — you **ask DAN** to create the graph from scratch.

---

## Create the workflow in DAN (human operates UI; DAN proposes mutations)

Do **not** ask an external assistant to hand-author `graphs/*.json` for this. **You** run DAN; the model proposes validated graph ops.

1. **Start DAN** (editor + server).
2. **New workflow** (empty canvas). Name it e.g. **`daily-equity-watchlist`** or **`daily-equity-research-v3`** when you save — your choice.
3. Chat mode **Agent** (so `plan_graph_mutations` / apply is available).
4. Paste the **Master prompt** below. Insist on **dry-run first**; iterate in chat until validation is clean.
5. **Apply** mutations, then **Save** / export to `graphs/` if you want a file on disk.
6. **Run:** editor **Start run** or CLI `dan-run` on the saved path, with `-i watchlist_path=...` (or whatever inputs the graph defines).

Runtime errors → stay in **Agent** chat, paste the traceback, ask for **repair mutations**, Apply again.

---

## How DAN turns text into a graph

1. **Agent chat** with the workflow focused: **`plan_graph_mutations`** proposes ops; the server **dry-runs** against the schema.
2. **NL `dan-run "goal"`** (MetaController) can sometimes plan a run from a string; complex equity flows are usually easier **in the editor** where you see topology.
3. Expect **apply → run → fix → apply** loops; one-shot perfection is not guaranteed.

---

## Master prompt (paste into Agent chat)

Adapt paths and sources (e.g. StockAnalysis, StockTitan, no MarketBeat).

```text
This workflow is EMPTY. Use plan_graph_mutations only (no raw JSON pasted from outside). Build the full graph from scratch.

Set metadata.name to something stable, e.g. daily-equity-watchlist (or daily-equity-research-v3 if I will save under that id).

Goal: daily equity watchlist pipeline
- Input: watchlist_path default ~/Dropbox/Projects/my-knowledge-base/content/blogs/equity-research/watchlist.csv (header: ticker, one symbol per line).
- Read CSV → list of tickers.
- For each ticker (for_each or sequential code_operator): HTTP GET with retries (3x backoff) to:
  - https://stockanalysis.com/stocks/{TICKER}/
  - https://stockanalysis.com/stocks/{TICKER}/forecast/
  - https://www.stocktitan.net/news/{TICKER}/
  Strip HTML to text excerpts; record fetch status per source.
- Read yesterday’s report if present: {kb_root}/{TICKER}/{yesterday}/index.md for day-over-day hints.
- LLM step: produce RKLB-style markdown BODY (sections: Daily Brief table, Key Changes, Fundamental Snapshot, Analyst Consensus, News & Sentiment, Risks/Catalysts, Data Source Status). Use only excerpts; no invented prices.
- Write: {kb_root}/{TICKER}/{YYYY-MM-DD}/index.md with YAML front matter (title, ticker, date ISO Z, author DAN Automated Research, tags, prevReport).
- Aggregate: {kb_root}/{YYYY-MM-DD}/index.md summary table linking each ticker report.
- On failure: partial report + flag failed sources; invalid ticker → log and skip.

Dry-run the plan; if validation errors, self-correct. I will say Apply when clean. Then tell me how to start_run with inputs.
```

---

## Watchlist format (for your KB file)

**Path (your machine):**  
`~/Dropbox/Projects/my-knowledge-base/content/blogs/equity-research/watchlist.csv`

**Format:**

```csv
ticker
RKLB
FRO
ASTS
```

- First non-comment line can be a header: `ticker` (or `symbol`).
- One **uppercase or mixed** symbol per line; blank lines and `#` comments allowed if your DAN graph strips them.

---

## After you save a graph file

Replace the path with wherever DAN wrote the JSON (example):

```bash
dan-run graphs/daily-equity-watchlist.json --local --headless -q -o /tmp/out.json \
  -i watchlist_path="/absolute/path/to/equity-research/watchlist.csv"
```

---

## Tests

- No repo test targets a committed equity graph anymore.
- Add a **run** or **corpus** test only **after** you choose to commit a DAN-generated export you intend to keep.
