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

from dan.meta.goal_contract import render_goal_contract_section
from dan.meta.intent_schema import StageType, WorkflowIntent
from dan.meta.tool_catalog import render_tool_id_list
from dan.workflow_generation_guidance import (
    render_workflow_generation_contract,
    workflow_generation_contract_enabled,
)

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
    StageType.loop: "Stateful loop with a body that repeats until a condition becomes false",
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
- CRITICAL: Each distinct user action must become its own stage. Do not collapse \
multiple verbs into one stage.
- Emit at least as many stages as the prompt clearly implies.
- Only use the listed stage types. Do not invent new ones.
- For tool-related actions (search, read file, write file, email, fetch URL), use \
stage_type=tool_call with the appropriate tool_id from the available list.
- For code/compute actions (calculate, analyze data, run Python, generate chart), use \
stage_type=code_execution.
- Only include config.code for code_execution when you can state concrete runnable \
Python from the user's request. If the code is not concretely derivable, leave \
config.code empty instead of inventing fake logic.
- Never use placeholder payload code or fabricated completion markers such as \
{{"status": "placeholder"}} or {{"statistics": "computed"}} to stand in for real execution.
- For parallel processing (process each, for each, in parallel), use stage_type=fan_out.
- For review/quality loops (review, iterate, improve until), use stage_type=review_loop \
with reviewer_prompt, condition, and max_iterations.
- For generic loops (loop, repeat until, count up/down), use stage_type=loop.
- For review_loop stages, include a review field with reviewer_prompt, condition, \
and max_iterations.
- For conditional stages, include the conditional field.
- For loop stages, include the loop field.
- Identify global_inputs (what the user must provide) and global_outputs (final \
deliverables).
- If the goal is ambiguous or underspecified, ask a clarification question instead \
of guessing. Never fabricate details the user did not mention.

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
- "loop", "repeat until", "count up to", "count down to", "iterate until done" \
→ stage_type=loop
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


def build_intent_extraction_system_prompt() -> str:
    """Return the canonical intent-extraction system prompt."""
    prompt = (
        INTENT_EXTRACTION_SYSTEM_PROMPT
        + "\n\nAvailable tool_ids for tool_call stages (use these exact IDs): "
        + render_tool_id_list()
        + ". "
        + "Do NOT invent tool_ids not in this list. If no tool matches, use "
        + "code_execution with inline Python only when you can provide real runnable "
        + "logic; otherwise leave config.code empty rather than inventing placeholder code."
    )
    if workflow_generation_contract_enabled():
        prompt = (
            f"{prompt}\n\n"
            f"{render_workflow_generation_contract('build', tools_available=False)}"
        )
    return prompt


# ---------------------------------------------------------------------------
# Shared intent extraction helper (plan 32-7, task 3-2)
# ---------------------------------------------------------------------------


async def extract_workflow_intent(
    llm_complete: Callable[..., Awaitable[Any]],
    goal_text: str,
    *,
    model: str | None = None,
    goal_contract: dict[str, Any] | None = None,
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
    system_prompt = build_intent_extraction_system_prompt()
    user_prompt = goal_text
    goal_contract_section = render_goal_contract_section(
        goal_contract,
        preamble=(
            "Use this contract to decide what stages are necessary and what must "
            "be true before the workflow counts as complete."
        ),
    )
    if goal_contract_section:
        user_prompt = f"{goal_text}\n\n{goal_contract_section}"

    try:
        response = await llm_complete(
            system_prompt,
            user_prompt,
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
        repaired = _repair_stage_semantics_from_goal(intent, goal_text)
        repaired = _repair_stage_structure_from_goal(repaired, goal_text)
        return _repair_stage_configs_from_goal(repaired, goal_text)

    if actual == 1 and expected_min >= 2:
        from dan.meta.intent_schema import StageIntent

        parts = _ACTION_SPLIT_RE.split(goal_text)
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
                review = _infer_review_config(part) if stage_type == StageType.review_loop else None
                conditional = _infer_conditional_config(part) if stage_type == StageType.conditional else None
                loop = _infer_loop_config(part) if stage_type == StageType.loop else None
                new_stages.append(StageIntent(
                    name=_slugify_stage(part, i),
                    description=part.strip().capitalize(),
                    stage_type=stage_type,
                    config=config,
                    review=review,
                    conditional=conditional,
                    loop=loop,
                ))
            intent = intent.model_copy(update={"stages": new_stages})

    repaired = _repair_stage_semantics_from_goal(intent, goal_text)
    repaired = _repair_stage_structure_from_goal(repaired, goal_text)
    return _repair_stage_configs_from_goal(repaired, goal_text)


def _infer_stage_type(text: str) -> "StageType":
    """Infer StageType from a phrase."""
    from dan.meta.intent_schema import StageType
    t = text.lower()
    if _infer_deterministic_code_config(text) is not None:
        return StageType.code_execution
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
    if any(kw in t for kw in ("loop", "repeat until", "count up", "counts up", "count down", "counts down", "iterate until done")):
        return StageType.loop
    return StageType.transform


def _infer_stage_config(text: str, stage_type: "StageType") -> dict:
    """Infer config for a stage based on text."""
    from dan.meta.intent_schema import StageType
    if stage_type == StageType.code_execution:
        cfg = _infer_deterministic_code_config(text)
        if cfg is not None:
            return cfg
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


def _infer_conditional_config(text: str) -> "ConditionalRequirement":
    """Provide a default ConditionalRequirement for conditional stages."""
    from dan.meta.intent_schema import ConditionalRequirement

    raw = text.strip()
    lowered = raw.lower()
    if lowered.startswith("if "):
        raw = raw[3:].strip()

    split_match = re.search(r"\b(?:otherwise|else)\b", raw, flags=re.IGNORECASE)
    before_else = raw
    else_text = ""
    if split_match is not None:
        before_else = raw[: split_match.start()].strip(" ,")
        else_text = raw[split_match.end() :].strip(" ,")

    branch_match = re.search(r"\b(?:say|then)\b", before_else, flags=re.IGNORECASE)
    condition_text = before_else
    then_text = ""
    if branch_match is not None:
        condition_text = before_else[: branch_match.start()].strip(" ,")
        then_text = before_else[branch_match.end() :].strip(" ,")

    condition = _normalize_condition_expression(condition_text)
    then_description = then_text or f"Handle true case for {text.strip()[:60]}"
    else_description = else_text or f"Handle false case for {text.strip()[:60]}"
    return ConditionalRequirement(
        condition=condition,
        then_description=then_description,
        else_description=else_description,
    )


def _normalize_condition_expression(text: str) -> str:
    """Convert a small set of natural-language comparisons to Python-ish expressions."""
    condition = text.strip()
    patterns = [
        (r"^(.+?)\s+is\s+bigger\s+than\s+(.+)$", ">"),
        (r"^(.+?)\s+is\s+greater\s+than\s+(.+)$", ">"),
        (r"^(.+?)\s+is\s+less\s+than\s+(.+)$", "<"),
        (r"^(.+?)\s+is\s+smaller\s+than\s+(.+)$", "<"),
        (r"^(.+?)\s+is\s+equal\s+to\s+(.+)$", "=="),
        (r"^(.+?)\s+equals\s+(.+)$", "=="),
    ]
    for pattern, op in patterns:
        match = re.match(pattern, condition, flags=re.IGNORECASE)
        if match is not None:
            lhs = match.group(1).strip()
            rhs = match.group(2).strip()
            return f"{lhs} {op} {rhs}"
    return condition or "result == true"


def _infer_loop_config(text: str) -> "LoopRequirement":
    """Provide a default LoopRequirement for generic loop stages."""
    from dan.meta.intent_schema import LoopRequirement

    target = 3
    target_match = re.search(
        r"\b(?:count(?:s)?\s+(?:up\s+)?(?:till|to)|until)\s+(\d+)\b",
        text,
        flags=re.IGNORECASE,
    )
    if target_match is not None:
        target = max(int(target_match.group(1)), 1)

    return LoopRequirement(
        condition=f"counter < {target}",
        init_code='result = {"counter": 0}',
        body_code=(
            "counter = int(counter) if counter is not None else 0\n"
            'result = {"counter": counter + 1}'
        ),
        result_code=(
            "value = data.get('counter') if isinstance(data, dict) else data\n"
            "result = value"
        ),
        max_iterations=max(target + 2, 5),
        state_schema={"counter": {"type": "integer"}},
        state_defaults={"counter": 0},
    )


def _infer_deterministic_code_config(text: str) -> dict[str, Any] | None:
    """Generate inline code for small deterministic string transforms."""
    lower = text.lower()
    case_kind: str | None = None
    source_text: str | None = None
    target_text: str | None = None
    if any(kw in lower for kw in ("kebab-case", "kebab case", "slugify", "slug")):
        case_kind = "kebab"
    elif any(kw in lower for kw in ("snake_case", "snake case")):
        case_kind = "snake"
    elif any(kw in lower for kw in ("uppercase", "upper-case", "upper case")):
        case_kind = "upper"
    elif any(kw in lower for kw in ("lowercase", "lower-case", "lower case")):
        case_kind = "lower"
    elif any(kw in lower for kw in ("title case", "title-case")):
        case_kind = "title"
    elif any(kw in lower for kw in ("camelcase", "camel case")):
        case_kind = "camel"

    quoted = [
        match.group(1) or match.group(2)
        for match in re.finditer(r"'([^']+)'|\"([^\"]+)\"", text)
        if (match.group(1) or match.group(2))
    ]
    if quoted:
        source_text = quoted[0]
    if len(quoted) >= 2:
        target_text = quoted[1]

    convert_match = re.search(
        r"(?:turn|turns|convert|converts)\s+(.+?)\s+(?:into|to)\s+(.+?)(?:\s+(?:and|then)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if convert_match is not None:
        if source_text is None:
            source_text = convert_match.group(1).strip(" '\"")
        if target_text is None:
            target_text = convert_match.group(2).strip(" '\"")

    if case_kind is None and source_text and target_text:
        case_kind = _infer_case_kind_from_example(source_text, target_text)

    if case_kind is None or not source_text:
        return None

    escaped_source = source_text.replace("\\", "\\\\").replace('"', '\\"')
    code = _render_string_transform_code(case_kind, escaped_source)
    return {"code": code}


def _infer_case_kind_from_example(source_text: str, target_text: str) -> str | None:
    """Infer a supported string transform from a source/target example pair."""
    normalized_target = target_text.strip()
    for case_kind in ("kebab", "snake", "upper", "lower", "title", "camel"):
        if _apply_string_transform(case_kind, source_text) == normalized_target:
            return case_kind
    return None


def _apply_string_transform(case_kind: str, source_text: str) -> str:
    """Apply the same deterministic transform that the generated code will perform."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", source_text.strip()) if w]
    if case_kind == "kebab":
        return "-".join(w.lower() for w in words)
    if case_kind == "snake":
        return "_".join(w.lower() for w in words)
    if case_kind == "upper":
        return source_text.upper()
    if case_kind == "lower":
        return source_text.lower()
    if case_kind == "title":
        return source_text.title()
    if case_kind == "camel":
        if not words:
            return ""
        head = words[0].lower()
        tail = "".join(w[:1].upper() + w[1:].lower() for w in words[1:])
        return head + tail
    raise ValueError(f"Unsupported deterministic code transform: {case_kind}")


def _render_string_transform_code(case_kind: str, source_text: str) -> str:
    """Return a tiny Python snippet for deterministic string transforms."""
    base = [
        "import re",
        f'text = "{source_text}"',
        'words = [w for w in re.split(r"[^A-Za-z0-9]+", text.strip()) if w]',
    ]
    if case_kind == "kebab":
        base.append('result = "-".join(w.lower() for w in words)')
    elif case_kind == "snake":
        base.append('result = "_".join(w.lower() for w in words)')
    elif case_kind == "upper":
        base.append("result = text.upper()")
    elif case_kind == "lower":
        base.append("result = text.lower()")
    elif case_kind == "title":
        base.append("result = text.title()")
    elif case_kind == "camel":
        base.extend([
            "if not words:",
            '    result = ""',
            "else:",
            "    head = words[0].lower()",
            "    tail = ''.join(w[:1].upper() + w[1:].lower() for w in words[1:])",
            "    result = head + tail",
        ])
    else:
        raise ValueError(f"Unsupported deterministic code transform: {case_kind}")
    return "\n".join(base)


def _repair_stage_semantics_from_goal(
    intent: WorkflowIntent,
    goal_text: str,
) -> WorkflowIntent:
    """Retag obvious control-flow stages when extraction kept the right phrase but wrong type."""
    parts = _ACTION_SPLIT_RE.split(goal_text)
    parts = [p.strip() for p in parts if len(p.strip()) > 5]
    stages = list(intent.stages)

    for target_type in (StageType.code_execution, StageType.conditional, StageType.loop):
        if any(s.stage_type == target_type for s in stages):
            continue

        if target_type == StageType.code_execution:
            phrase = next((p for p in parts if _infer_deterministic_code_config(p) is not None), None)
        else:
            phrase = next((p for p in parts if _infer_stage_type(p) == target_type), None)
        if not phrase:
            continue

        stage_index = _find_best_stage_for_phrase(stages, phrase)
        if stage_index is None:
            continue

        stage = stages[stage_index]
        update: dict[str, Any] = {
            "stage_type": target_type,
            "review": None,
            "conditional": None,
            "loop": None,
            "config": dict(stage.config),
        }
        if target_type == StageType.code_execution:
            update["config"] = _infer_deterministic_code_config(phrase) or {}
        elif target_type == StageType.conditional:
            update["conditional"] = _infer_conditional_config(phrase)
        elif target_type == StageType.loop:
            update["loop"] = _infer_loop_config(phrase)
        stages[stage_index] = stage.model_copy(update=update)

    return intent.model_copy(update={"stages": stages})


def _repair_stage_structure_from_goal(
    intent: WorkflowIntent,
    goal_text: str,
) -> WorkflowIntent:
    """Replace a narrow set of brittle patterns with canonical runnable stages."""
    batch_spec = _infer_inline_batch_map_reduce(goal_text)
    if batch_spec is None:
        return intent

    from dan.meta.intent_schema import StageIntent

    items, multiplier = batch_spec
    multiplier_name = {
        2: "double_each",
        3: "triple_each",
        4: "quadruple_each",
    }.get(multiplier, "map_each")
    repaired_stages = [
        StageIntent(
            name="seed_items",
            description="Provide the inline batch items.",
            stage_type=StageType.code_execution,
            config={"code": f"result = {items!r}"},
        ),
        StageIntent(
            name=multiplier_name,
            description=f"Multiply each item by {multiplier}.",
            stage_type=StageType.fan_out,
            config={"body_code": f"result = item * {multiplier}"},
        ),
        StageIntent(
            name="sum_results",
            description="Sum the mapped results.",
            stage_type=StageType.code_execution,
            config={
                "code": (
                    "result = sum(\n"
                    '    entry["result"] if isinstance(entry, dict) and "result" in entry else entry\n'
                    "    for entry in input\n"
                    ")"
                )
            },
        ),
    ]
    return intent.model_copy(update={"stages": repaired_stages})


def _repair_stage_configs_from_goal(
    intent: WorkflowIntent,
    goal_text: str,
) -> WorkflowIntent:
    """Normalize brittle stage configs when the user gave a deterministic example."""
    deterministic_code = _infer_deterministic_code_config(goal_text)
    if deterministic_code is None:
        return intent

    stages = list(intent.stages)
    for idx, stage in enumerate(stages):
        if stage.stage_type != StageType.code_execution:
            continue
        config = dict(stage.config)
        config.update(deterministic_code)
        stages[idx] = stage.model_copy(update={"config": config})
        return intent.model_copy(update={"stages": stages})
    return intent


def _infer_inline_batch_map_reduce(goal_text: str) -> tuple[list[int], int] | None:
    """Recognize tiny inline numeric map-reduce requests like 'over 1 2 3, triple each'."""
    lower = goal_text.lower()
    if not any(sig in lower for sig in ("batch", "for each", "each one", "each item")):
        return None
    if "sum" not in lower:
        return None

    items_match = re.search(
        r"\bover\s+([0-9,\s-]+?)(?:,|\b(?:double|triple|quadruple|multiply|sum)\b|$)",
        goal_text,
        flags=re.IGNORECASE,
    )
    if items_match is None:
        return None

    items = [int(value) for value in re.findall(r"-?\d+", items_match.group(1))]
    if len(items) < 2:
        return None

    multiplier: int | None = None
    if "double" in lower:
        multiplier = 2
    elif "triple" in lower:
        multiplier = 3
    elif "quadruple" in lower:
        multiplier = 4
    else:
        multiply_match = re.search(
            r"(?:multiply|times).+?\bby\s+(-?\d+)\b",
            goal_text,
            flags=re.IGNORECASE,
        )
        if multiply_match is not None:
            multiplier = int(multiply_match.group(1))

    if multiplier is None:
        return None
    return items, multiplier


def _find_best_stage_for_phrase(stages: list[Any], phrase: str) -> int | None:
    """Pick the stage most likely to correspond to the phrase."""
    phrase_words = {w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if len(w) > 2}
    best_index: int | None = None
    best_score = -1
    for idx, stage in enumerate(stages):
        hay = f"{getattr(stage, 'name', '')} {getattr(stage, 'description', '')}".lower()
        words = set(re.findall(r"[a-z0-9]+", hay))
        score = len(words & phrase_words)
        if score > best_score:
            best_score = score
            best_index = idx
    return best_index


def _slugify_stage(text: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())[:30].strip("_")
    return slug or f"stage_{index}"
