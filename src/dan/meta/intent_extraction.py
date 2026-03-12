"""LLM intent extraction — system prompt, tool schema, and few-shot examples.

Teaches the LLM to emit a structured ``WorkflowIntent`` via function-calling
rather than free-form builder code.  Used as the first step in the intent
compiler path (24-2): extract → coverage check → compile or fallback.
"""

from __future__ import annotations

import json
import logging
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
- Only use the listed stage types. Do not invent new ones.
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

Explicit phrase → stage_type mapping:
- "review loop", "draft then review", "iterate until quality" → review_loop
- "in parallel", "for each", "process items concurrently" → fan_out
- "search and retrieve", "RAG", "look up then answer", "retrieve context" → rag_retrieval
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
