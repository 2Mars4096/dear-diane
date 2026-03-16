"""Reusable workflow templates for the recipe distillation system (Plan 36-5).

These workflows use the existing DAN builder DSL and node types.
No new engine runtime is needed — only domain-specific composition.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _chunk_by_sections(text: str, max_tokens: int = 30000) -> list[str]:
    """Split text by section headers; sub-split at paragraphs if chunk exceeds max_tokens."""
    approx_chars_per_token = 4
    max_chars = max_tokens * approx_chars_per_token
    section_pattern = re.compile(
        r"^(#{1,6}\s+.+|\d+(?:\.\d+)*\s+.+)$",
        re.MULTILINE,
    )
    parts: list[str] = []
    last_end = 0
    for m in section_pattern.finditer(text):
        if m.start() > last_end:
            parts.append(text[last_end : m.start()].strip())
        last_end = m.start()
    if last_end < len(text):
        parts.append(text[last_end:].strip())
    if not parts:
        parts = [text.strip()] if text.strip() else []
    chunks: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            if p:
                chunks.append(p)
        else:
            paras = re.split(r"\n\s*\n", p)
            buf = ""
            for para in paras:
                if buf and len(buf) + len(para) + 2 > max_chars:
                    chunks.append(buf.strip())
                    buf = ""
                buf = f"{buf}\n\n{para}" if buf else para
            if buf.strip():
                chunks.append(buf.strip())
    return chunks


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


def build_source_reader_workflow(
    session_id: str,
    source_ids: list[str],
    pdf_paths: dict[str, str] | None = None,
    urls: dict[str, str] | None = None,
) -> Any:
    """Build a workflow that reads all sources before the furnace starts.

    For each source: pdf_read if pdf_path available, else web_fetch if url available.
    Collects all outputs into a merge node.
    """
    try:
        from dan.builder import workflow
    except ImportError:
        logger.warning("dan.builder not available; cannot build source reader workflow")
        return None

    pdf_paths = pdf_paths or {}
    urls = urls or {}
    read_nodes: list[tuple[str, Any]] = []
    wf = workflow(
        f"source_reader_{session_id}",
        description=f"Read all sources for session {session_id}",
        tags=["recipe", "source_reader"],
    )
    for sid in source_ids:
        path = pdf_paths.get(sid)
        url = urls.get(sid)
        if path:
            node = wf.tool(
                f"read_pdf_{sid}",
                tool_id="pdf_read",
                tool_config={"path": path},
                output_ports=[{"name": "result"}],
            )
            read_nodes.append((sid, node))
        elif url:
            node = wf.tool(
                f"read_web_{sid}",
                tool_id="web_fetch",
                tool_config={"url": url},
                output_ports=[{"name": "result"}],
            )
            read_nodes.append((sid, node))
    if not read_nodes:
        return None

    merge_input_ports = [{"name": f"input_{i}"} for i in range(len(read_nodes))]
    merge = wf.reduce(
        "merge_sources",
        reducer="'\\n\\n---\\n\\n'.join(str(x.get('text') or x.get('content') or x) for v in inputs.values() for x in (v if isinstance(v, list) else [v]) if x)",
        input_ports=merge_input_ports,
    )
    for i, (_sid, node) in enumerate(read_nodes):
        wf.edge(node["result"], merge[f"input_{i}"])
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
7. Extract document structure: [{heading, level, one_sentence_summary}] — per-section summaries
8. Note citation norms (how prior work is referenced)

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
6. Detect dominant section patterns across sources in the field

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


def build_chunked_extraction_workflow(
    corpus_id: str,
    recipe_id: str,
    chunk_count: int = 1,
) -> Any:
    """Build a distillation workflow that chunks long papers before extraction.

    Same five passes as batch distillation, but the extract phase runs per chunk
    and merges results before aggregate.
    """
    try:
        from dan.builder import workflow
        from dan.builder.refs import NodeRef
        from dan.models.context import MergeStrategy
    except ImportError:
        logger.warning("dan.builder not available; cannot build chunked extraction workflow")
        return None

    max_tokens = max(1000, 30000 // max(1, chunk_count))
    chunk_code = f"""\
from dan.engine.recipe.workflows import _chunk_by_sections
chunks = _chunk_by_sections(input, max_tokens={max_tokens})
result = chunks if chunks else [input]
"""

    wf = workflow(
        f"distill_chunked_{corpus_id}",
        description=f"Distill recipe {recipe_id} from corpus {corpus_id} (chunked)",
        tags=["recipe", "distillation", "furnace", "chunked"],
    )

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

    chunker = wf.code(
        "chunk_text",
        code=chunk_code,
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "result"}],
    )
    wf.edge(normalize["text"], chunker["input"])

    extract_system = """You are a domain knowledge extractor. For the given paper text:
1. Extract claims (key findings and propositions)
2. Extract methods (empirical, analytical, or experimental approaches)
3. Extract terminology (domain-specific terms and definitions)
4. Extract measures (variables, metrics, outcomes)
5. Identify rhetorical moves (how arguments are structured)
6. Identify question patterns (what questions the field asks)
7. Extract document structure: [{heading, level, one_sentence_summary}] — per-section summaries
8. Note citation norms (how prior work is referenced)

Tag each extraction with confidence (0-1) and page/section evidence.
Return as structured JSON."""

    with wf.for_each(
        "section_pipeline",
        items=chunker["result"],
        merge_strategy=MergeStrategy.APPEND,
    ) as body:
        extract = body.llm(
            "extract",
            system_prompt=extract_system,
            prompt="Extract knowledge from paper chunk:\n{input}",
            input_ports=[{"name": "input"}],
            output_ports=[{"name": "text"}],
        )
        body.edge(body.item, extract["input"])

    pipeline_ref = NodeRef("section_pipeline", "for_each", wf)
    merge_extractions = wf.reduce(
        "merge_extractions",
        reducer="'\\n\\n---\\n\\n'.join(str(x) for v in inputs.values() for x in (v if isinstance(v, list) else [v]) if x)",
    )
    wf.edge(pipeline_ref["results"], merge_extractions["input"])

    aggregate = wf.llm(
        "aggregate",
        system_prompt="""You are a domain knowledge aggregator. Given extractions from multiple papers:
1. Identify recurring patterns (terminology, methods, claims that appear in 3+ papers)
2. Weight by venue quality and author diversity
3. Merge duplicate concepts and reconcile conflicting claims
4. Promote high-recurrence items from paper-level to domain-level
5. Flag low-confidence or contradictory findings
6. Detect dominant section patterns across sources in the field

Return domain-level patterns as structured JSON with support counts.""",
        prompt="Aggregate extractions from papers:\n{input}",
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "text"}],
    )
    wf.edge(merge_extractions["result"], aggregate["input"])

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
    wf.edge(aggregate["text"], infer["input"])

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
    wf.edge(infer["text"], project["input"])

    return wf.build()
