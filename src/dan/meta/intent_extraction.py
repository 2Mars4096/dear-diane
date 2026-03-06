"""LLM intent extraction — system prompt, tool schema, and few-shot examples.

Teaches the LLM to emit a structured ``WorkflowIntent`` via function-calling
rather than free-form builder code.  Used as the first step in the intent
compiler path (24-2): extract → coverage check → compile or fallback.
"""

from __future__ import annotations

from dan.meta.intent_schema import StageType, WorkflowIntent

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
                        "condition": "quality_score >= 8",
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
]
