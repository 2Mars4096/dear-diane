# 36: Recipe Distillation Spec

**Status:** completed
**Goal:** Define the backend contracts for resumable recipe training, ingredient provenance, paper-corpus memory schema, and the `recipe.md` / `skill.md` artifact pipeline. This is the non-UI counterpart to [1-6-research-workbench-refinement](../UI-plans/1-6-research-workbench-refinement.md).

## Sub-Plans

| # | Sub-Plan | Scope |
|---|----------|-------|
| [36-1](36-1-recipe-training-lifecycle.md) | Resumable recipe training lifecycle | Furnace session state, batch checkpoints, resume-from-checkpoint, benchmark capture |
| [36-2](36-2-ingredient-provenance.md) | Ingredient provenance & market readiness | Ingredient ledger schema, version-to-version diffs, publishable provenance for recipe market |
| [36-3](36-3-paper-corpus-memory.md) | Paper-corpus memory schema | `paper_corpus` metadata layer on MemoryKernel, knowledge_kind taxonomy, retrieval bundles |
| [36-4](36-4-recipe-artifact-contract.md) | `recipe.md` and `skill.md` artifact contract | Frontmatter, required sections, skill projection rules, benchmark hooks |
| [36-5](36-5-paper-acquisition-workflow.md) | Paper acquisition workflow | University proxy download, bibtex-ID naming, KB note creation, summary/provenance handoff |

Detailed subplans exist because Plan `36` is too broad to execute safely as a single unit. Further descendant plans can still be created just-in-time when one of these slices grows large enough.

## Resumable Recipe Training

Recipe training must support stop-and-continue:

1. Train on the first batch (e.g. 10 PDFs). Save a checkpointed recipe version.
2. Test the recipe on a writing task or external evaluation.
3. Add another batch (e.g. 10 more PDFs). Resume from the previous checkpoint.
4. Test again and compare.

Three persistent artifacts:

- **Furnace session state** — what has been ingested, extracted, distilled, skipped, or deferred
- **Recipe version history** — the evolving output artifact and its benchmark results at each checkpoint
- **Ingredient ledger** — the exact papers and sources that fed each version

## Ingredient Provenance

Each recipe version documents what went in:

```yaml
ingredients:
  - paper_id: "acemoglu2012network"
    title: "The Network Origins of Aggregate Fluctuations"
    pdf_path: "~/Dropbox/my-knowledge-base/static/papers/acemoglu2012network.pdf"
    note_path: "~/Dropbox/my-knowledge-base/content/papers/acemoglu2012network/index.md"
    source: "university_proxy"
    included_in_versions: ["0.1.0", "0.2.0"]
    status: "active"
    inclusion_reason: "core network economics paper"
```

Required provenance fields for market readiness:

- exact paper IDs, titles, PDF/note paths, and acquisition source
- inclusion, exclusion, and removal reasons
- which recipe version each ingredient influenced
- version-to-version ingredient diffs

## Corpus Memory Schema

Extend `MemoryItem.metadata` with a `family: "corpus"` layer (source-agnostic — works for papers, blogs, docs, code, conversations, books, videos, notes):

- `corpus_id`, `source_id`, `source_type`, `knowledge_kind`, `generality` (paper / domain / recipe)
- `support_count`, `author_diversity`, `venue_weight`, `confidence`
- `evidence` array linking claims back to source sections/pages

`SourceType` enum: `paper`, `blog`, `documentation`, `code`, `conversation`, `book`, `video`, `note`, `other`

`knowledge_kind` taxonomy:

- `source_metadata`, `source_summary`, `claim`, `method`, `dataset`, `measure`
- `terminology`, `citation_norm`, `rhetorical_move`, `question_pattern`
- `association_edge`, `taste_signal`, `writing_rule`, `anti_pattern`
- `evaluation_case`, `recipe_snapshot`

Mapping to existing `MemoryType`:

- `FACT` → source_metadata, source_summary, claim, method, dataset, measure, association_edge
- `PREFERENCE` → terminology, taste_signal
- `PRINCIPLE` → citation_norm, rhetorical_move, question_pattern, writing_rule, anti_pattern
- `EPISODE` → evaluation_case
- `WORKFLOW_ASSET` → recipe_snapshot

## `recipe.md` Contract

Master artifact. Required sections:

- `## Ingredients` — paper IDs, titles, source roots, acquisition provenance
- `## Training History` — batch-by-batch training log with timestamps and token costs
- `## Checkpoints` — evaluation results after each training increment
- `## Domain Thesis` — what the field values and what counts as contribution
- `## Core Concepts` — stable terms and concept groupings
- `## Association Vectors` — directional concept associations
- `## Methods And Identification` — common empirical/analytical/experimental patterns
- `## Rhetorical Taste` — framing moves, style, and tone
- `## Writing Rules` — machine-usable rules for generation time
- `## Anti-Patterns` — weak moves, reviewer triggers, off-field style
- `## Change Log` — what changed between recipe versions

`skill.md` is a compressed runtime projection: strip evidence appendices, keep highest-value writing rules and taste signals, preserve `recipe_id` provenance.

## Distillation Pipeline

Five-pass furnace loop, resumable between passes:

1. **Normalize** — resolve metadata, canonicalize paper_id, verify paths, record provenance
2. **Extract** — paper-level facts, methods, terminology, rhetorical moves, question patterns
3. **Aggregate** — merge across papers weighted by venue, author diversity, recurrence
4. **Infer taste** — build association graphs, promote robust patterns into taste_signal / question_pattern / anti_pattern
5. **Project + evaluate** — compile recipe.md, derive skill.md, run benchmark comparison

Generality levels:

- `generality = paper` for paper-level observations
- `generality = domain` for repeated cross-paper patterns
- `generality = recipe` for promoted output artifacts

## Configurable Roots

Paper PDF and note paths are user-configurable (not hardcoded):

```yaml
research:
  library:
    pdf_roots:
      - "~/Dropbox/my-knowledge-base/static/papers"
    note_roots:
      - "~/Dropbox/my-knowledge-base/content/papers"
```

Canonical identity: `paper_id = bibtex_id`. Root paths are resolved from settings.

## Engine Reuse

The furnace should reuse the **existing workflow engine**, not introduce a separate runtime.

Use the current builder DSL and node inventory:

- `for_each` for batch-by-batch paper ingestion
- `tool_operator` for `browser_download`, `pdf_read`, `file_write`, `list_directory`, `search_papers`, `citation_verifier`
- `llm_operator` for extraction, aggregation, taste inference, and recipe projection
- `gate` for checkpoint loops, evaluation branches, and retry / continue decisions
- `reduce` for cross-paper aggregation
- `code_operator` only for narrow transforms and scoring helpers

What needs to be added is **domain-specific tooling and persistence**, not a new engine primitive:

- recipe/session persistence
- ingredient ledger persistence
- paper-corpus metadata writes into memory
- browser download as a first-class tool

## Paper Acquisition Workflow

The paper download / rename / summary workflow should also reuse the current engine:

1. `browser_open` + browser navigation tools drive the university proxy / database flow.
2. `browser_download` saves the PDF directly to the configured paper root using the final `bibtex_id.pdf` path.
3. `pdf_read` extracts the paper text.
4. `llm_operator` drafts a summary and extracts metadata/taste signals.
5. `file_write` creates or updates the paper note at `note_root/<bibtex_id>/index.md`.
6. Provenance is passed into the ingredient ledger and future recipe versions.

The only new primitive needed for this workflow is `browser_download`; the rest should compose from what already exists.

## Naming Note

This backend plan does not use the stove metaphor for primary structure, but when the metaphor is referenced in docs or product copy it should use the one-word set:

- `Cooktop`
- `Pantry`
- `Board`
- `Furnace`

## Dependencies

- [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md) — memory kernel substrate
- [31-15-learning-evolution-optimization](31-15-learning-evolution-optimization.md) — learning governance
- [1-6-research-workbench-refinement](../UI-plans/1-6-research-workbench-refinement.md) — UI counterpart

## Likely Files

- `src/dan/engine/memory_kernel.py`
- `src/dan/engine/memory_extractor.py`
- `src/dan/server/concierge/domain_learning.py`
- `src/dan/server/tools/research.py`
- `src/dan/tools/browser_download.py`
- `editor/src/lib/marketplace/recipeModel.ts`

## Decisions

- The furnace extends the existing memory system, not replaces it.
- The furnace and paper-acquisition flow should reuse the existing workflow engine and builder DSL; add narrow tools/stores instead of new runtime components.
- `recipe.md` is the master artifact; `skill.md` is derived.
- Ingredient provenance is first-class for future marketplace trust.
- Recipe training must be resumable and incrementally testable.
