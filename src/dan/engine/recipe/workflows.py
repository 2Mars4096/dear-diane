"""Reusable workflow templates for the recipe distillation system (Plan 36-5).

These workflows use the existing DAN builder DSL and node types.
No new engine runtime is needed — only domain-specific composition.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_paper_acquisition_workflow(
    paper_id: str,
    title: str = "",
    pdf_url: str = "",
    pdf_root: str = "",
    note_root: str = "",
) -> Any:
    """Build a paper acquisition workflow using the DAN builder DSL.

    Workflow shape:
    1. browser_download → save PDF to pdf_root/<paper_id>.pdf (when pdf_url provided)
    2. pdf_read → extract paper text
    3. llm_operator → summarize, extract metadata and taste signals
    4. file_write → create note at note_root/<paper_id>/index.md

    Returns a compiled Graph, or None if builder is unavailable.
    """
    try:
        from dan.builder import workflow
    except ImportError:
        logger.warning("dan.builder not available; cannot build acquisition workflow")
        return None

    wf = workflow(
        f"acquire_{paper_id}",
        description=f"Acquire, read, summarize, and note paper: {paper_id}",
        tags=["recipe", "acquisition", "paper"],
    )

    dest_pdf = f"{pdf_root}/{paper_id}.pdf" if pdf_root else f"~/.dan/papers/{paper_id}.pdf"
    dest_note = f"{note_root}/{paper_id}/index.md" if note_root else f"~/.dan/notes/{paper_id}/index.md"

    # Step 1: Download PDF (if URL provided) — uses selector on current page
    download = None
    if pdf_url:
        download = wf.tool(
            "download_pdf",
            tool_id="browser_download",
            tool_config={
                "selector": "a[href$='.pdf']",
                "destination_path": dest_pdf,
            },
            output_ports=[{"name": "result"}],
        )

    # Step 2: Read the PDF (path from tool_config)
    read_pdf = wf.tool(
        "read_pdf",
        tool_id="pdf_read",
        tool_config={"path": dest_pdf},
        output_ports=[{"name": "result"}],
    )

    # Step 3: LLM extraction — summarize and extract metadata/taste signals
    extract = wf.llm(
        "extract_knowledge",
        system_prompt="""You are a research paper analyst. Given the full text of a paper:

1. Write a concise summary (3-5 sentences).
2. Extract structured metadata:
   - title, authors, year, venue, doi
   - key claims (list)
   - methods used (list)
   - datasets mentioned (list)
   - key terminology and definitions (list)
3. Extract taste signals:
   - rhetorical moves (how arguments are structured)
   - citation norms (how prior work is referenced)
   - writing rules (patterns worth emulating)
   - anti-patterns (weak moves to avoid)

Return as JSON with keys: summary, metadata, claims, methods, datasets,
terminology, rhetorical_moves, citation_norms, writing_rules, anti_patterns.""",
        prompt=f"Extract knowledge from this paper:\n\nTitle: {title or paper_id}\n\nFull text:\n{{input}}",
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "text"}],
    )

    # Step 4: Write the note file (path/mode from tool_config, content from extract)
    write_note = wf.tool(
        "write_note",
        tool_id="file_write",
        tool_config={"path": dest_note, "mode": "overwrite"},
        input_ports=[{"name": "content"}],
        output_ports=[{"name": "result"}],
    )

    # Wire the pipeline
    if download is not None:
        download >> read_pdf
    read_pdf >> extract
    wf.edge(extract["text"], write_note["content"])

    return wf.build()


def build_batch_distillation_workflow(
    corpus_id: str,
    recipe_id: str,
) -> Any:
    """Build a batch recipe distillation workflow using the DAN builder DSL.

    Five-pass furnace loop:
    1. Normalize — resolve metadata, canonicalize paper_id
    2. Extract — paper-level facts, methods, terminology, rhetorical moves
    3. Aggregate — merge across papers weighted by venue, recurrence
    4. Infer taste — build association graphs, promote patterns
    5. Project + evaluate — compile recipe.md, derive skill.md

    Returns a compiled Graph, or None if builder is unavailable.
    """
    try:
        from dan.builder import workflow
    except ImportError:
        logger.warning("dan.builder not available; cannot build distillation workflow")
        return None

    wf = workflow(
        f"distill_{corpus_id}",
        description=f"Distill recipe {recipe_id} from corpus {corpus_id}",
        tags=["recipe", "distillation", "furnace"],
    )

    # Pass 1: Normalize — resolve metadata for each paper
    normalize = wf.llm(
        "normalize",
        system_prompt="""You are a bibliographic normalizer. For each paper:
- Verify or generate the canonical bibtex_id (authorYEARkeyword format)
- Normalize author names
- Verify title, year, venue
- Flag any issues (missing metadata, duplicate papers)

Return JSON: {papers: [{paper_id, title, authors, year, venue, status, issues}]}""",
        prompt=f"Normalize metadata for papers in corpus {corpus_id}:\n{{input}}",
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "text"}],
    )

    # Pass 2: Extract — per-paper knowledge extraction
    extract = wf.llm(
        "extract",
        system_prompt="""You are a domain knowledge extractor. For the given paper text:
1. Extract claims (key findings and propositions)
2. Extract methods (empirical, analytical, or experimental approaches)
3. Extract terminology (domain-specific terms and definitions)
4. Extract measures (variables, metrics, outcomes)
5. Identify rhetorical moves (how arguments are structured)
6. Identify question patterns (what questions the field asks)
7. Note citation norms (how prior work is referenced)

Tag each extraction with confidence (0-1) and page/section evidence.
Return as structured JSON.""",
        prompt="Extract knowledge from paper:\n{input}",
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "text"}],
    )

    # Pass 3: Aggregate — cross-paper merging
    aggregate = wf.llm(
        "aggregate",
        system_prompt="""You are a domain knowledge aggregator. Given extractions from multiple papers:
1. Identify recurring patterns (terminology, methods, claims that appear in 3+ papers)
2. Weight by venue quality and author diversity
3. Merge duplicate concepts and reconcile conflicting claims
4. Promote high-recurrence items from paper-level to domain-level
5. Flag low-confidence or contradictory findings

Return domain-level patterns as structured JSON with support counts.""",
        prompt="Aggregate extractions from papers:\n{input}",
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "text"}],
    )

    # Pass 4: Infer taste — build association graphs, detect writing style
    infer = wf.llm(
        "infer_taste",
        system_prompt="""You are a domain taste analyst. Given aggregated patterns:
1. Build directional association vectors between concepts
2. Identify high-salience-but-unexplored question zones
3. Promote strong patterns into taste signals and writing rules
4. Identify anti-patterns (weak moves, reviewer triggers, off-field style)
5. Characterize the domain's rhetorical taste (formality, evidence standards, framing)

Return taste_signals, writing_rules, anti_patterns, association_vectors as JSON.""",
        prompt="Infer domain taste from aggregated patterns:\n{input}",
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "text"}],
    )

    # Pass 5: Project — compile recipe.md
    project = wf.llm(
        "project_recipe",
        system_prompt="""You are a recipe compiler. Given:
- Ingredient list (papers processed)
- Domain-level patterns
- Taste signals and writing rules
- Anti-patterns

Compile a recipe.md document with these sections:
## Domain Thesis, ## Core Concepts, ## Association Vectors,
## Methods And Identification, ## Rhetorical Taste,
## Writing Rules, ## Anti-Patterns

Each section should be concise, actionable, and evidence-linked.""",
        prompt=f"Compile recipe for {recipe_id}:\n{{input}}",
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "text"}],
    )

    # Wire the five-pass pipeline
    normalize >> extract >> aggregate >> infer >> project

    return wf.build()
