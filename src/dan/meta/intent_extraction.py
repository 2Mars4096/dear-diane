"""LLM intent extraction — system prompt, tool schema, and few-shot examples.

Teaches the LLM to emit a structured ``WorkflowIntent`` via function-calling
rather than free-form builder code.  Used as the first step in the intent
compiler path (24-2): extract → coverage check → compile or fallback.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Awaitable

from dan.meta.intent_schema import StageType, WorkflowIntent
from dan.meta.tool_catalog import render_tool_id_list

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_STAGE_TYPE_DESCRIPTIONS: dict[str, str] = {
    StageType.transform: "LLM text-to-text processing (summarize, rewrite, extract, classify)",
    StageType.review_loop: "Iterative draft → review → revise cycle with a quality gate",
    StageType.fan_out: "Process a list of items in parallel (for-each / map)",
    StageType.rag_retrieval: "Retrieve context from a knowledge base then answer",
    StageType.tool_call: "Invoke an external tool (web search, file read, API call)",
    StageType.code_execution: "Run a Python code snippet and capture the result",
    StageType.human_approval: "Pause for human review / approval before continuing",
    StageType.conditional: "If-else branching — route to different processing based on a condition",
}

INTENT_EXTRACTION_SYSTEM_PROMPT: str = """\
You are a workflow architect. The user will describe a workflow they want to build. \
Your job is to decompose their goal into a sequence of stages and emit a structured \
workflow intent using the emit_workflow_intent tool.

Supported stage types (use ONLY these):
{stage_types}

Rules:
- Decompose the goal into sequential stages. Each stage needs a name, description, \
and stage_type from the list above.
- CRITICAL: Each distinct step, action, or verb phrase the user mentions MUST become \
its own separate stage. NEVER collapse multiple steps into one stage. If the user says \
"research, analyze, and summarize", that is 3 separate stages, not 1. If the user says \
"search the web, read results, and write a briefing", that is 3 stages.
- A simple count: if the prompt mentions N distinct actions, emit at least N stages.
- Only use the listed stage types. Do not invent new ones.
- For tool-related actions (search, read file, write file, email, fetch URL), use \
stage_type=tool_call with the appropriate tool_id from the available list.
- For code/compute actions (calculate, analyze data, run Python, generate chart), use \
stage_type=code_execution.
- For parallel processing (process each, for each, in parallel), use stage_type=fan_out.
- For review/quality loops (review, iterate, improve until), use stage_type=review_loop \
with reviewer_prompt, condition, and max_iterations.
- For review_loop stages, include a review field with reviewer_prompt, condition, \
and max_iterations.
- Identify global_inputs (what the user must provide) and global_outputs (final \
deliverables).
- If the goal is ambiguous or underspecified, ask a clarification question instead \
of guessing. Never fabricate details the user did not mention.

Common workflow patterns (use these as guidance):
- Linear chain: sequential transform stages
- Review loop: draft → review → revise cycle → use stage_type=review_loop
- Tool chain: sequential tool and LLM calls
- Research + review: research stages followed by quality review
- Comparison: process items in parallel then compare
- Document pipeline: read → process → write
- Code analysis: file operations + code execution + analysis
- Iterative improvement: repeated refinement toward a goal

IMPORTANT stage_type selection rules (do NOT default everything to transform):
- "search", "web search", "fetch URL", "read file", "write file", "email", "send" \
→ stage_type=tool_call (with appropriate tool_id)
- "run code", "Python", "compute", "calculate", "statistics", "chart", "analyze data" \
→ stage_type=code_execution
- "in parallel", "for each", "process N items", "concurrently", "fan out" \
→ stage_type=fan_out
- "review loop", "draft then review", "iterate until quality", "revision cycles" \
→ stage_type=review_loop
- "RAG", "retrieve context", "look up then answer", "knowledge base" \
→ stage_type=rag_retrieval
- "approve", "human review", "sign off" → stage_type=human_approval
- "if/else", "check whether", "branch based on" → stage_type=conditional
- Only use transform for pure LLM text processing with no tools, code, or control flow.
""".format(
    stage_types="\n".join(
        f"  - {st.value}: {desc}" for st, desc in _STAGE_TYPE_DESCRIPTIONS.items()
    )
)

# ---------------------------------------------------------------------------
# Function-calling tool schema
# ---------------------------------------------------------------------------


def build_intent_tool_schema() -> dict:
    """Return the function-calling tool definition for ``emit_workflow_intent``."""
    return {
        "type": "function",
        "function": {
            "name": "emit_workflow_intent",
            "description": "Emit a structured workflow intent based on the user's goal.",
            "parameters": WorkflowIntent.model_json_schema(),
        },
    }


# ---------------------------------------------------------------------------
# Few-shot examples
# ---------------------------------------------------------------------------

INTENT_FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "user": "Write a research paper with literature review and methodology sections",
        "intent": {
            "goal": "Write a research paper with literature review and methodology sections",
            "stages": [
                {
                    "name": "literature_search",
                    "stage_type": "rag_retrieval",
                    "description": "Search for relevant papers",
                },
                {
                    "name": "outline",
                    "stage_type": "transform",
                    "description": "Create paper outline from literature",
                },
                {
                    "name": "draft_sections",
                    "stage_type": "fan_out",
                    "description": "Draft each section in parallel",
                },
                {
                    "name": "review_revise",
                    "stage_type": "review_loop",
                    "description": "Review and revise draft",
                    "review": {
                        "reviewer_prompt": "Check for clarity, citations, and logical flow",
                        "condition": "quality_score < 8",
                        "max_iterations": 3,
                    },
                },
            ],
            "global_inputs": ["topic", "target_journal"],
            "global_outputs": ["final_paper"],
        },
    },
    {
        "user": "Analyze customer feedback data and generate a report",
        "intent": {
            "goal": "Analyze customer feedback data and generate a report",
            "stages": [
                {
                    "name": "ingest_data",
                    "stage_type": "tool_call",
                    "description": "Load feedback data from file",
                    "config": {"tool_id": "file_read"},
                },
                {
                    "name": "categorize",
                    "stage_type": "fan_out",
                    "description": "Categorize each review by topic",
                },
                {
                    "name": "analyze",
                    "stage_type": "transform",
                    "description": "Analyze sentiment and extract themes",
                },
                {
                    "name": "report",
                    "stage_type": "transform",
                    "description": "Generate summary report",
                },
            ],
            "global_inputs": ["data_path"],
            "global_outputs": ["report"],
        },
    },
    {
        "user": "Research the top 5 AI companies and write a comparison report with review",
        "intent": {
            "goal": "Research AI companies and write comparison report",
            "stages": [
                {
                    "name": "search",
                    "stage_type": "tool_call",
                    "description": "Search for top AI companies",
                    "config": {"tool_id": "web_search"},
                },
                {
                    "name": "analyze_each",
                    "stage_type": "fan_out",
                    "description": "Analyze each company",
                    "parallelism": 5,
                },
                {
                    "name": "compare",
                    "stage_type": "transform",
                    "description": "Compare and rank companies",
                },
                {
                    "name": "review_report",
                    "stage_type": "review_loop",
                    "description": "Review comparison report",
                    "review": {
                        "reviewer_prompt": "Check for factual accuracy and completeness",
                        "condition": "quality_score < 8",
                        "max_iterations": 2,
                    },
                },
            ],
            "global_inputs": ["topic"],
            "global_outputs": ["comparison_report"],
        },
    },
    {
        "user": "Build a pipeline that reads a CSV file, runs Python analysis, generates a chart, and emails the report",
        "intent": {
            "goal": "Data analysis pipeline with CSV input and email output",
            "stages": [
                {
                    "name": "read_data",
                    "stage_type": "tool_call",
                    "description": "Read CSV data file",
                    "config": {"tool_id": "csv_read"},
                },
                {
                    "name": "analyze",
                    "stage_type": "code_execution",
                    "description": "Run statistical analysis and generate chart",
                    "config": {"code": "import json; result = {'statistics': 'computed', 'chart_path': 'chart.png'}"},
                },
                {
                    "name": "write_report",
                    "stage_type": "transform",
                    "description": "Write analysis report from statistics",
                },
                {
                    "name": "send_report",
                    "stage_type": "tool_call",
                    "description": "Email the final report",
                    "config": {"tool_id": "send_email"},
                },
            ],
            "global_inputs": ["data_path", "recipient_email"],
            "global_outputs": ["report"],
        },
    },
]


# ---------------------------------------------------------------------------
# Shared intent extraction helper (plan 32-7, task 3-2)
# ---------------------------------------------------------------------------


async def extract_workflow_intent(
    llm_complete: Callable[..., Awaitable[Any]],
    goal_text: str,
    *,
    model: str | None = None,
) -> WorkflowIntent | None:
    """Extract a WorkflowIntent from goal text using LLM function calling.

    Parameters
    ----------
    llm_complete : callable
        ``async (system_prompt, user_prompt, model, temperature, tools) -> response``.
        Should return the raw LLM response that includes ``tool_calls``.
    goal_text : str
        The user's goal description.
    model : str, optional
        Model to use for extraction.

    Returns ``None`` if extraction fails or intent is invalid.
    """
    tool_schema = build_intent_tool_schema()
    system_prompt = (
        INTENT_EXTRACTION_SYSTEM_PROMPT
        + "\n\nAvailable tool_ids for tool_call stages (use these exact IDs): "
        + render_tool_id_list()
        + ". "
        + "Do NOT invent tool_ids not in this list. If no tool matches, use "
        + "code_execution with inline Python instead."
    )

    try:
        response = await llm_complete(
            system_prompt,
            goal_text,
            model,
            0.3,
            [tool_schema],
        )

        if hasattr(response, "tool_calls") and response.tool_calls:
            for tc in response.tool_calls:
                if tc.function.name == "emit_workflow_intent":
                    args = json.loads(tc.function.arguments)
                    return WorkflowIntent.model_validate(args)

        if isinstance(response, str):
            try:
                data = json.loads(response)
                return WorkflowIntent.model_validate(data)
            except (json.JSONDecodeError, Exception):
                pass

    except Exception:
        logger.debug("Intent extraction failed", exc_info=True)
        return None

    return None


_ACTION_SPLIT_RE = re.compile(
    r",\s*(?:and\s+|then\s+)?|\.\s+|;\s+|\bthen\b|\bafter\s+that\b"
)


def _estimate_min_stages(goal_text: str) -> int:
    """Heuristic: count distinct action phrases to estimate expected stage count."""
    parts = _ACTION_SPLIT_RE.split(goal_text.lower())
    action_phrases = [s.strip() for s in parts if len(s.strip()) > 5]
    return max(1, len(action_phrases))


def validate_and_expand_intent(
    intent: WorkflowIntent,
    goal_text: str,
) -> WorkflowIntent:
    """Post-extraction check: if prompt implies more stages than extracted, expand.

    Catches the common LLM failure mode of collapsing "research, analyze, summarize"
    into a single stage.
    """
    expected_min = _estimate_min_stages(goal_text)
    actual = len(intent.stages)

    if actual >= expected_min or actual >= 3:
        return intent

    if actual == 1 and expected_min >= 2:
        from dan.meta.intent_schema import StageIntent

        parts = _ACTION_SPLIT_RE.split(goal_text.lower())
        parts = [p.strip() for p in parts if len(p.strip()) > 5]

        if len(parts) >= 2:
            logger.info(
                "Expanding single-stage intent to %d stages from prompt phrases",
                len(parts),
            )
            new_stages = []
            for i, part in enumerate(parts):
                stage_type = _infer_stage_type(part)
                config = _infer_stage_config(part, stage_type)
                review = _infer_review_config(part) if stage_type.value == "review_loop" else None
                new_stages.append(StageIntent(
                    name=_slugify_stage(part, i),
                    description=part.strip().capitalize(),
                    stage_type=stage_type,
                    config=config,
                    review=review,
                ))
            intent = intent.model_copy(update={"stages": new_stages})

    return intent


def _infer_stage_type(text: str) -> "StageType":
    """Infer StageType from a phrase."""
    from dan.meta.intent_schema import StageType
    t = text.lower()
    if any(kw in t for kw in ("review loop", "review and revise", "revise", "iterate until", "revision cycle")):
        return StageType.review_loop
    if any(kw in t for kw in ("in parallel", "for each", "each item", "concurrently", "fan out")):
        return StageType.fan_out
    if any(kw in t for kw in (
        "search", "web", "fetch", "read file", "load file", "email", "send",
        "read a csv", "read csv", "read pdf", "ingest", "download", "scrape",
        "pull data",
    )):
        return StageType.tool_call
    if any(kw in t for kw in (
        "run code", "python", "compute", "calculate", "chart", "statistic",
        "analyze data", "run analysis", "generate chart", "financial ratio",
    )):
        return StageType.code_execution
    if any(kw in t for kw in ("rag", "retrieve context", "knowledge base", "document collection")):
        return StageType.rag_retrieval
    if any(kw in t for kw in ("human approval", "human review", "sign off")):
        return StageType.human_approval
    if any(kw in t for kw in ("if ", "check whether", "branch based on")):
        return StageType.conditional
    return StageType.transform


def _infer_stage_config(text: str, stage_type: "StageType") -> dict:
    """Infer config for a stage based on text."""
    from dan.meta.intent_schema import StageType
    if stage_type == StageType.tool_call:
        from dan.meta.intent_compiler import _infer_tool_id
        return {"tool_id": _infer_tool_id(text, text)}
    return {}


def _infer_review_config(text: str) -> "ReviewRequirement":
    """Provide a default ReviewRequirement for review_loop stages."""
    from dan.meta.intent_schema import ReviewRequirement
    return ReviewRequirement(
        reviewer_prompt=f"Review the output for quality: {text.strip()[:80]}",
        condition="quality_score < 8",
        max_iterations=3,
    )


def _slugify_stage(text: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())[:30].strip("_")
    return slug or f"stage_{index}"
