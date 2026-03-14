# 1-3: Research Mode Workspace

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** not-started
**Goal:** Build the flagship specialized workspace: a research environment for academic papers, literature reviews, and systematic investigations. Proves the job-based mode architecture works and delivers DAN's strongest differentiator — no competing tool combines agent orchestration with a purpose-built research workspace.

## Context

Research is DAN's motivating example (development-plan.md Section 1, Section 6.2). The engine already supports the full pipeline: PDF ingestion, RAG retrieval, parallel section writing, review-revise loops, LaTeX generation. What's missing is a workspace that makes this pipeline visible and interactive.

Research as a job bundles: reading (PDFs, papers), writing (sections, drafts), coding (data analysis, figures), analysis (statistics, comparisons), and review (structured feedback loops). The workspace arranges all of these for the research workflow — the user never leaves "Research mode" to access any of them.

Research mode lives inside the same workspace shell as Chat and Code. That means workspace tabs, shared memory, the same bottom chat composer, and a left sidebar that is research-specific artifact navigation rather than the old generic project sidebar.

## Layout

```
┌─ Workspace Tabs ───────────────────────────────────────────────┐
│ [Supply Chain Paper] [DAN Dev] [Kaggle Comp] [+]               │
├──────────┬──────────────────────────┬──────────────────┤
│ Research │      Primary Panel       │   Context Panel   │
│ Nav      │  (Writing | Reading)     │                   │
│          │                          │  Tab: References  │
│ ◇ Papers │  [Tab: Editor]           │  Tab: Reviews     │
│ ◇ Data   │  [Tab: PDF Reader]       │  Tab: Outline     │
│ ◇ Figs   │                          │  Tab: Notes       │
│ ◇ Learn  │                          │  Tab: Distillation│
│          │                          │                  │
│──────────│──────────────────────────│──────────────────│
│ Pipeline │      Secondary Panel     │                  │
│ Progress │  (Code | Data | Figures) │                  │
│          │                          │                  │
│ ✓ Search │  [Tab: Code Cells]       │                  │
│ ● Write  │  [Tab: Figures]          │                  │
│ ○ Review │  [Tab: Data]             │                  │
│──────────┴──────────────────────────┴──────────────────│
│ Chat Bar: > "Add a methods section comparing..."       │
└────────────────────────────────────────────────────────┘
```

## Tasks

### 0. Workspace Shell Integration
- [ ] 0-1. Research mode runs inside the shared workspace shell: workspace tabs, mode bar, persistent bottom chat composer
- [ ] 0-2. Left sidebar becomes research artifact navigation (papers, datasets, figures, notes, recent files) instead of the old project sidebar
- [ ] 0-3. Workspace context carries from Chat/Code: same workspace memory, same thread history, same pinned filesystem roots
- [ ] 0-4. Research artifact shelves can surface files from outside pinned roots, but first-touch writes/ingest outside trusted workspace roots require explicit confirmation or pinning

### 1. Writing Pane (Primary Panel — Editor Tab)
- [ ] 1-1. Markdown editor with live preview (toggle or split view)
- [ ] 1-2. LaTeX math support: inline `$...$` and block `$$...$$` rendering via KaTeX
- [ ] 1-3. Section navigation: outline-driven jump-to-section (synced with Outline tab in context panel)
- [ ] 1-4. Auto-save to project artifacts (debounced, via existing artifact store API)
- [ ] 1-5. Inline AI actions: select text → floating toolbar with "Expand" / "Rewrite formally" / "Add citation" / "Simplify" / "Translate"
- [ ] 1-6. Citation insertion: type `[@` → autocomplete from reference panel, inserts `[@author2024]` with metadata
- [ ] 1-7. Track changes: agent-proposed edits shown as inline diffs (green/red), per-change accept/reject buttons
- [ ] 1-8. Section status indicators: which sections are complete, which are being generated, which need review
- [ ] 1-9. Export: one-click Markdown, LaTeX, or PDF (via existing LaTeX compile tool or pandoc)

### 2. PDF Reader (Primary Panel — Reader Tab)
- [ ] 2-1. PDF rendering via pdf.js or react-pdf
- [ ] 2-2. Text selection actions: "Summarize selection" / "Add to notes" / "Cite this" / "Ask about this" / "Translate"
- [ ] 2-3. Highlight and annotate: persistent per-project annotations stored as metadata
- [ ] 2-4. Side-by-side reading: split reader to compare two papers
- [ ] 2-5. Drag-and-drop PDF anywhere in workspace → auto-ingest: extract metadata (title, authors, year, abstract), add to references, index for RAG
- [ ] 2-6. Citation-linked navigation: click a citation in the writing pane → opens referenced paper in reader
- [ ] 2-7. Page-level AI summary: sidebar showing per-page key claims and methodology notes
- [ ] 2-8. Search within PDF: full-text search with highlight

### 3. Reference Panel (Context Panel — Tab)
- [ ] 3-1. Reference list: all project references with metadata (author, year, title, journal, DOI)
- [ ] 3-2. Search/filter within references (title, author, keyword, year range)
- [ ] 3-3. Add reference: PDF upload, DOI lookup, manual entry, or auto-discovered by DAN during literature search
- [ ] 3-4. Citation format selection: APA, Chicago, Harvard, Vancouver, custom CSL
- [ ] 3-5. BibTeX/RIS export (full bibliography or selected entries)
- [ ] 3-6. Interaction: click reference → open in reader; drag reference onto editor → insert citation
- [ ] 3-7. Reference status: read/unread, relevance score, notes attached
- [ ] 3-8. "Find similar papers" action per reference (triggers literature search workflow)

### 4. Review Panel (Context Panel — Tab)
- [ ] 4-1. Display structured reviewer feedback from review-loop workflow nodes
- [ ] 4-2. Each comment: severity badge (major/minor/editorial), section reference, suggestion text
- [ ] 4-3. Click comment → jumps to relevant section in writing pane and highlights the passage
- [ ] 4-4. Per-comment actions: accept (apply suggestion) / dismiss / reply (ask reviewer to elaborate)
- [ ] 4-5. Iteration tracking: "Round 1: 8 major, 4 minor → Round 2: 2 major, 1 minor → Round 3: accepted"
- [ ] 4-6. Manual review input: user can add their own comments for the agent to address
- [ ] 4-7. Reviewer configuration: select review criteria (methodology rigor, writing clarity, novelty, completeness)
- [ ] 4-8. "Request another review round" button → triggers review-loop iteration

### 5. Outline Panel (Context Panel — Tab)
- [ ] 5-1. Document outline as a tree: sections, subsections, headings
- [ ] 5-2. Synced with writing pane: click heading → jumps to section; editing updates outline live
- [ ] 5-3. Drag-and-drop reordering: rearrange sections by dragging in the outline
- [ ] 5-4. Section metadata: word count, status (draft/review/final), assigned agent (if parallel writing)
- [ ] 5-5. "Add section" action: type section title → AI generates content positioned correctly
- [ ] 5-6. "Generate outline" quick action: from topic/abstract → auto-create section structure

### 6. Notes Panel (Context Panel — Tab)
- [ ] 6-1. Free-form notes area for the research project (Markdown)
- [ ] 6-2. Quick-capture from PDF reader (selected text + page reference auto-appended)
- [ ] 6-3. Quick-capture from chat (agent response excerpt → note)
- [ ] 6-4. Taggable notes for organization (#methodology, #data, #findings)
- [ ] 6-5. Notes searchable and retrievable by DAN's memory system

### 7. Distillation Tab (Context Panel — Tab)
- [ ] 7-1. **"Learn 100 Papers" tab**: a first-class tab inside Research mode for long-running, self-running domain learning sessions
- [ ] 7-2. **Corpus setup**: define topic/domain, target paper count, inclusion criteria, source folders/URLs/search queries, and whether the run is exploratory vs benchmarked
- [ ] 7-3. **Long-running run control**: start, pause, resume, cancel, and background the learning session so it can keep running for hours/days
- [ ] 7-4. **Progress surface**: show discovered / ingested / read / extracted / distilled counts (for example `37 / 100 complete`), retries/failures, and estimated time remaining
- [ ] 7-5. **Distillation outputs**: extracted terminology, methodological patterns, citation norms, rhetorical style, recurring datasets, canonical questions, and open tensions in the field
- [ ] 7-6. **Latent field map**: show directional association clusters / concept vectors and candidate "high-salience but under-explored" question areas distilled from the corpus
- [ ] 7-7. **Human steering**: pin especially important papers, exclude noisy papers, add notes, and adjust extraction focus without restarting the whole long-running session
- [ ] 7-8. **Benchmark hooks**: store before/after writing-quality checkpoints and links to any external paper-quality evaluation workflow used to measure the experiment
- [ ] 7-9. **Recipe snapshot/export**: package the distilled domain state into a reusable recipe/domain artifact for later retrieval, sharing, or marketplace publication

### 8. Pipeline Progress (Sidebar Section)
- [ ] 8-1. Visual pipeline stages: vertical list with status icons (✓ completed, ● active, ○ queued, ✗ failed)
- [ ] 8-2. Default stages for research: Search → Read → Analyze → Outline → Write → Review → Finalize
- [ ] 8-3. Long-running distillation pipeline preset: Discover → Ingest → Extract → Distill → Evaluate
- [ ] 8-4. Per-stage detail on click: which nodes ran, duration, outputs, token cost
- [ ] 8-5. Estimated time remaining (from existing duration estimator)
- [ ] 8-6. Controls: pause pipeline, resume, cancel, skip stage, redo stage
- [ ] 8-7. "Add stage" / "Remove stage" for custom pipelines
- [ ] 8-8. Stage-to-panel linking: clicking "Write" stage highlights the writing pane; clicking "Review" highlights review panel; clicking "Distill" highlights the Distillation tab

### 9. Code & Data Panel (Secondary Panel)
- [ ] 9-1. Code cells: Jupyter-like executable cells for data analysis within the research project
- [ ] 9-2. Cell output rendering: tables, charts (Chart.js/Plotly), statistical summaries, images
- [ ] 9-3. Figure gallery tab: all generated figures with captions, click to enlarge
- [ ] 9-4. "Insert into paper" action: select a figure or table → adds reference in writing pane with caption
- [ ] 9-5. Data browser tab: uploaded datasets, intermediate results, with preview (first 50 rows)
- [ ] 9-6. Tool integration: cells can call DAN's built-in tools (web_search, pdf_read, python_eval, etc.)

### 10. Engine Event Routing
- [ ] 10-1. `artifact_created` with `type=document_section` → writing pane (append/update section content)
- [ ] 10-2. `artifact_created` with `type=figure` → figure gallery
- [ ] 10-3. `artifact_created` with `type=reference` → reference panel (add discovered paper)
- [ ] 10-4. Review-loop node output events → review panel (structured comments)
- [ ] 10-5. Long-running distillation events / artifacts → Distillation tab (progress updates, extracted patterns, domain recipe snapshots)
- [ ] 10-6. `node_started` / `node_completed` → pipeline progress (update stage status)
- [ ] 10-7. Streaming `llm_chunk` events from writing nodes → writing pane (live draft generation, cursor follows)
- [ ] 10-8. Tool output events (search results) → reference panel or notes (auto-capture discoveries)
- [ ] 10-9. Error events → pipeline progress (mark stage failed) + notification

### 11. Research Quick-Starts
- [ ] 11-1. "New Literature Review": provide topic → auto-creates search → read → synthesize → write workflow
- [ ] 11-2. "New Paper": provide topic/abstract → auto-creates outline → parallel write → review workflow
- [ ] 11-3. "Review This Paper": upload PDF → structured review (methodology, contribution, weakness, questions)
- [ ] 11-4. "Compare Papers": upload 2+ PDFs → structured comparison matrix (methodology, findings, limitations)
- [ ] 11-5. "Summarize Paper": upload PDF → one-page structured summary
- [ ] 11-6. "Learn 100 Papers": provide domain/topic → auto-creates discover → ingest → extract → distill → evaluate workflow and opens the Distillation tab
- [ ] 11-7. Quick-start selector shown on empty research workspace or via the command palette (Cmd+Shift+P)

### 12. Domain Profiles
- [ ] 12-1. Academic profile: LaTeX export, BibTeX citations, INFORMS/AER style, formal academic tone
- [ ] 12-2. Market research profile: survey templates, interview protocols, business tone, executive summary focus
- [ ] 12-3. Policy analysis profile: regulatory sources, impact assessment framework, government style
- [ ] 12-4. Profile selection: on project creation, via settings, or auto-detected from content
- [ ] 12-5. Profile affects: default templates, citation style, writing tone, available quick-starts, reviewer criteria, and distillation templates

## Decisions
- (filled in during execution)

## Notes
- This is the flagship mode. It should feel like a purpose-built research IDE, not a chatbot with panels bolted on.
- The writing pane is the central artifact. Everything else (reader, references, reviews, code) supports it.
- Pipeline progress maps directly to DAN's existing run event stream — no new backend endpoints needed.
- PDF handling uses existing `pdf_read` tool + RAG infrastructure (`dan.executors.rag`).
- Review panel maps to DAN's existing review-loop workflow pattern (GateNode with review/revise body).
- The long-running 100-paper learning area starts as a tab inside Research mode, not a separate top-level mode. If it eventually grows too large or too different from the writing workflow, it can later split into its own dedicated surface.
- Consider existing OSS components: react-pdf for PDF rendering, CodeMirror/Monaco for editor, KaTeX for math.
- The research mode is the proof-of-concept for the entire job-based mode architecture. If this works well, the other modes follow the same pattern.
