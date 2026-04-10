"""Intent Compiler — deterministic compilation of WorkflowIntent to builder DSL code.

The ``IntentCompiler`` takes a validated ``WorkflowIntent`` and produces
executable Python code that uses the ``dan.builder`` DSL to construct a
``Graph``.  Alternatively, ``build_graph()`` constructs a ``Graph`` object
directly in-process without emitting code strings (plan 32-7).

``IntentCompiler`` is the primary deterministic path. ``CoverageChecker`` remains
only as a legacy compatibility shim; callers should attempt deterministic
compilation directly and fall back to LLM codegen only if compilation fails.
"""

from __future__ import annotations

import ast
import logging
import re
import tokenize
from io import StringIO
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field

from dan.models.node_taxonomy import worker_generation_uses_workers
from dan.meta.intent_schema import StageIntent, StageType, WorkflowIntent

if TYPE_CHECKING:
    from dan.builder.refs import NodeRef
    from dan.models.graph import Graph

logger = logging.getLogger(__name__)

_REVIEW_LOOP_STATE_VARS = {"draft", "quality_score", "feedback"}
_CONDITION_KEYWORDS = {
    "and", "or", "not", "true", "false", "True", "False", "is", "in", "None", "none",
}

_TOOL_KEYWORD_MAP: dict[str, str] = {
    "edit file": "file_edit",
    "patch file": "file_edit",
    "read file": "file_read",
    "load file": "file_read",
    "write file": "file_write",
    "save file": "file_write",
    "copy file": "file_copy",
    "move file": "file_move",
    "delete file": "file_delete",
    "list files": "list_directory",
    "csv": "csv_read",
    "spreadsheet": "spreadsheet_read",
    "excel": "spreadsheet_read",
    "pdf": "pdf_read",
    "web search": "web_search",
    "browse": "web_search",
    "search": "web_search",
    "fetch": "web_fetch",
    "http": "http_request",
    "api": "http_request",
    "email": "send_email",
    "mail": "send_email",
    "shell": "shell_command",
    "command": "shell_command",
    "terminal": "shell_command",
    "directory": "list_directory",
    "python": "python_eval",
    "run code": "python_eval",
    "execute code": "python_eval",
    "translate": "text_translate",
    "transcribe": "audio_transcribe",
    "image": "image_describe",
    "screenshot": "browser_screenshot",
    "git": "git_status",
    "notify": "notify",
}


def _infer_tool_id(name: str, description: str) -> str | None:
    """Infer a tool_id from stage name/description using conservative keyword matching."""
    text = re.sub(r"[_\-]+", " ", f"{name} {description}".lower())
    for keyword, tool_id in sorted(
        _TOOL_KEYWORD_MAP.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        pattern = r"\b" + r"\s+".join(re.escape(part) for part in keyword.split()) + r"\b"
        if re.search(pattern, text):
            return tool_id
    logger.warning(
        "No tool keyword match for stage '%s'; falling back to llm stage",
        name or description,
    )
    return "llm_operator"


def _required_tool_args(tool_id: str) -> set[str]:
    """Return required parameter names for a registered tool."""
    try:
        from dan.tools import get_all_tools

        entry = get_all_tools().get(tool_id)
    except Exception:
        return set()
    if not entry:
        return set()
    metadata = entry[1]
    parameters = metadata.get("parameters")
    if not isinstance(parameters, dict):
        return set()
    required = parameters.get("required")
    if not isinstance(required, list):
        return set()
    return {
        str(item).strip()
        for item in required
        if str(item).strip()
    }


def _tool_builder_kwargs(stage: StageIntent, tool_id: str) -> dict[str, Any]:
    """Derive builder kwargs so inferred tool stages are at least minimally grounded."""
    kwargs: dict[str, Any] = {}

    tool_config_raw = stage.config.get("tool_config")
    tool_config = (
        dict(tool_config_raw)
        if isinstance(tool_config_raw, dict)
        else {}
    )
    generalized_args_raw = stage.config.get("generalized_args")
    if not tool_config and isinstance(generalized_args_raw, dict):
        tool_config = dict(generalized_args_raw)

    input_ports_raw = stage.config.get("input_ports")
    if isinstance(input_ports_raw, list):
        kwargs["input_ports"] = input_ports_raw

    output_ports_raw = stage.config.get("output_ports")
    if isinstance(output_ports_raw, list):
        kwargs["output_ports"] = output_ports_raw

    required_args = _required_tool_args(tool_id)
    if required_args:
        if tool_id == "web_search" and "query" in required_args:
            query_text = str(tool_config.get("query") or stage.description or stage.name).strip()
            if query_text:
                tool_config.setdefault("query", query_text)

        if len(required_args) == 1:
            arg_name = next(iter(required_args))
            if arg_name not in tool_config:
                fallback_text = str(
                    stage.config.get(arg_name)
                    or stage.description
                    or stage.name
                ).strip()
                if fallback_text and arg_name in {"query", "path", "url", "command", "text"}:
                    tool_config[arg_name] = fallback_text

        if "input_ports" not in kwargs:
            missing_args = [
                arg_name
                for arg_name in sorted(required_args)
                if tool_config.get(arg_name) in (None, "", [], {})
            ]
            if missing_args:
                kwargs["input_ports"] = [
                    {"name": arg_name, "required": True}
                    for arg_name in missing_args
                ]

    if tool_config:
        kwargs["tool_config"] = tool_config

    return kwargs


def _worker_role_for_stage(stage: StageIntent) -> str:
    return {
        StageType.tool_call: "tool_runner",
        StageType.code_execution: "script",
    }.get(stage.stage_type, "processor")


def _worker_llm_kwargs(stage: StageIntent, *, prompt: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "role": _worker_role_for_stage(stage),
        "description": stage.description or stage.name,
        "llm": {"prompt_template": prompt},
    }
    task_tier = stage.config.get("task_tier") or stage.config.get("model_tier")
    if stage.config.get("model"):
        kwargs["model"] = stage.config["model"]
    if task_tier is not None:
        kwargs["llm"]["task_tier"] = str(task_tier)
    if stage.config.get("system_prompt"):
        kwargs["llm"]["system_prompt"] = str(stage.config["system_prompt"])
    if stage.config.get("temperature") is not None:
        kwargs["llm"]["temperature"] = stage.config["temperature"]
    if stage.config.get("input_ports") is not None:
        kwargs["input_ports"] = stage.config["input_ports"]
    if stage.config.get("output_ports") is not None:
        kwargs["output_ports"] = stage.config["output_ports"]
    return kwargs


def _worker_tool_kwargs(stage: StageIntent, tool_id: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "role": _worker_role_for_stage(stage),
        "description": stage.description or stage.name,
        "tool_ids": [tool_id],
    }
    builder_kwargs = _tool_builder_kwargs(stage, tool_id)
    if builder_kwargs.get("tool_config") is not None:
        kwargs["tool_config"] = builder_kwargs["tool_config"]
    if builder_kwargs.get("input_ports") is not None:
        kwargs["input_ports"] = builder_kwargs["input_ports"]
    if builder_kwargs.get("output_ports") is not None:
        kwargs["output_ports"] = builder_kwargs["output_ports"]
    return kwargs


def _worker_code_kwargs(stage: StageIntent, *, code: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "role": _worker_role_for_stage(stage),
        "description": stage.description or stage.name,
        "code": code,
    }
    if stage.config.get("language") is not None:
        kwargs["language"] = stage.config["language"]
    if stage.config.get("input_ports") is not None:
        kwargs["input_ports"] = stage.config["input_ports"]
    if stage.config.get("output_ports") is not None:
        kwargs["output_ports"] = stage.config["output_ports"]
    return kwargs


def _mask_string_literals(text: str) -> tuple[str, list[str]]:
    """Replace string literals with placeholders so normalization ignores them."""
    placeholders: list[str] = []
    out_parts: list[str] = []
    last_index = 0
    line_offsets = [0]
    for line in text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))
    try:
        for tok in tokenize.generate_tokens(StringIO(text).readline):
            if tok.type != tokenize.STRING:
                continue
            start = line_offsets[tok.start[0] - 1] + tok.start[1]
            end = line_offsets[tok.end[0] - 1] + tok.end[1]
            placeholder = f"__STR{len(placeholders)}__"
            placeholders.append(tok.string)
            out_parts.append(text[last_index:start])
            out_parts.append(placeholder)
            last_index = end
    except tokenize.TokenError:
        return text, []
    out_parts.append(text[last_index:])
    return "".join(out_parts), placeholders


def _restore_string_literals(text: str, placeholders: list[str]) -> str:
    restored = text
    for idx in range(len(placeholders) - 1, -1, -1):
        literal = placeholders[idx]
        restored = restored.replace(f"__STR{idx}__", literal)
    return restored


def _normalize_condition_keywords_for_ast(text: str) -> str:
    return re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", text, flags=re.IGNORECASE), flags=re.IGNORECASE)


def _negate_compare(node: ast.Compare) -> ast.expr:
    if len(node.ops) != 1:
        return ast.UnaryOp(op=ast.Not(), operand=node)
    op = node.ops[0]
    inverted: ast.cmpop
    if isinstance(op, ast.Gt):
        inverted = ast.LtE()
    elif isinstance(op, ast.GtE):
        inverted = ast.Lt()
    elif isinstance(op, ast.Lt):
        inverted = ast.GtE()
    elif isinstance(op, ast.LtE):
        inverted = ast.Gt()
    elif isinstance(op, ast.Eq):
        inverted = ast.NotEq()
    elif isinstance(op, ast.NotEq):
        inverted = ast.Eq()
    elif isinstance(op, ast.Is):
        inverted = ast.IsNot()
    elif isinstance(op, ast.IsNot):
        inverted = ast.Is()
    elif isinstance(op, ast.In):
        inverted = ast.NotIn()
    elif isinstance(op, ast.NotIn):
        inverted = ast.In()
    else:
        return ast.UnaryOp(op=ast.Not(), operand=node)
    return ast.Compare(
        left=node.left,
        ops=[inverted],
        comparators=node.comparators,
    )


def _negate_condition_ast(node: ast.expr) -> ast.expr:
    if isinstance(node, ast.BoolOp):
        flipped_op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return ast.BoolOp(
            op=flipped_op,
            values=[_negate_condition_ast(value) for value in node.values],
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return node.operand
    if isinstance(node, ast.Compare):
        return _negate_compare(node)
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return ast.Constant(value=not node.value)
    if isinstance(node, ast.Name):
        return ast.UnaryOp(op=ast.Not(), operand=node)
    return ast.UnaryOp(op=ast.Not(), operand=node)


def _negate_stop_condition(condition: str) -> str | None:
    try:
        parsed = ast.parse(_normalize_condition_keywords_for_ast(condition), mode="eval")
    except SyntaxError:
        return None
    negated = _negate_condition_ast(parsed.body)
    return ast.unparse(ast.fix_missing_locations(negated))


def _normalize_review_condition(
    condition: str,
    state_vars: set[str] | None = None,
) -> str:
    """Normalize review-loop conditions to builder continue-while semantics."""
    normalized = (condition or "").strip() or "quality_score < 8"
    masked, placeholders = _mask_string_literals(normalized)

    stop_condition = normalized
    stop_like = re.search(
        r"("
        r"\b[A-Za-z_][A-Za-z0-9_]*\b\s*(>=|>)\s*-?\d+(?:\.\d+)?"
        r"|"
        r"-?\d+(?:\.\d+)?\s*(<=|<)\s*\b[A-Za-z_][A-Za-z0-9_]*\b"
        r")",
        masked,
    )
    if stop_like:
        negated = _negate_stop_condition(masked)
        if negated:
            normalized = _restore_string_literals(negated, placeholders)
        else:
            logger.warning(
                "Review-loop condition looked like a stop-condition but could not be parsed; replacing with default: %s",
                stop_condition,
            )
            return "quality_score < 8"
    if normalized != stop_condition:
        logger.warning(
            "Normalizing review-loop condition from stop-condition to continue-while: %s -> %s",
            stop_condition,
            normalized,
        )

    allowed = state_vars or _REVIEW_LOOP_STATE_VARS
    try:
        parsed = ast.parse(normalized, mode="eval")
        identifiers = {
            node.id
            for node in ast.walk(parsed)
            if isinstance(node, ast.Name) and node.id not in _CONDITION_KEYWORDS
        }
    except SyntaxError:
        stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "", normalized)
        identifiers = {
            ident
            for ident in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", stripped)
            if ident not in _CONDITION_KEYWORDS
        }
    unknown = identifiers - allowed
    if unknown:
        logger.warning(
            "Review-loop condition references unknown variables %s (known: %s); replacing with default",
            unknown,
            allowed,
        )
        return "quality_score < 8"
    return normalized


# ---------------------------------------------------------------------------
# Coverage catalog
# ---------------------------------------------------------------------------

COVERAGE_CATALOG: dict[str, dict] = {
    "linear_chain": {
        "description": "N sequential transform stages",
        "stage_types": [StageType.transform],
        "composable": True,
    },
    "review_loop": {
        "description": "Draft → review → gate loop",
        "stage_types": [StageType.review_loop],
        "composable": True,
    },
    "fan_out_fan_in": {
        "description": "Parallel processing with for_each",
        "stage_types": [StageType.fan_out],
        "composable": True,
    },
    "rag_qa": {
        "description": "RAG retrieval → LLM answer",
        "stage_types": [StageType.rag_retrieval],
        "composable": True,
    },
    "data_pipeline": {
        "description": "Ingest / code execution / analysis (also: code_analysis)",
        "stage_types": [StageType.tool_call, StageType.code_execution, StageType.transform],
        "composable": True,
    },
    "tool_augmented": {
        "description": "LLM + tool calls (also: tool_chain, web_briefing)",
        "stage_types": [StageType.tool_call, StageType.transform],
        "composable": True,
    },
    "human_gate": {
        "description": "Human-in-the-loop approval",
        "stage_types": [StageType.human_approval],
        "composable": True,
    },
    "comparison": {
        "description": "Fan-out items then compare/rank",
        "stage_types": [StageType.fan_out, StageType.transform],
        "composable": True,
    },
    "research_review": {
        "description": "Research chain followed by review/improvement loop",
        "stage_types": [StageType.transform, StageType.review_loop],
        "composable": True,
    },
    "document_pipeline": {
        "description": "Read → process → write document",
        "stage_types": [StageType.tool_call, StageType.transform, StageType.tool_call],
        "composable": True,
    },
    "multi_source_merge": {
        "description": "Multiple source tools then fan-out + synthesis",
        "stage_types": [StageType.tool_call, StageType.fan_out, StageType.transform],
        "composable": True,
    },
    "conditional_branch": {
        "description": "If-else branching with then/else LLM branches",
        "stage_types": [StageType.conditional],
        "composable": True,
    },
}


class CoverageResult(BaseModel):
    """Legacy API for tests and quality suite; compile path is ``IntentCompiler``."""

    fully_covered: bool = True
    recommendation: Literal["compile", "compose", "fallback", "partial"] = "compile"
    supported_stages: list[str] = Field(default_factory=list)
    unsupported_stages: list[str] = Field(default_factory=list)
    constituent_patterns: list[str] | None = None


class CoverageChecker:
    """Pass-through shim: ``IntentCompiler.compile()`` is the real coverage gate."""

    SUPPORTED_TYPES: ClassVar[set[StageType]] = set(StageType)

    def check(
        self,
        intent: WorkflowIntent,
        *,
        try_compose: bool = True,
    ) -> CoverageResult:
        _ = try_compose
        return CoverageResult(
            fully_covered=True,
            recommendation="compile",
            supported_stages=[s.name for s in intent.stages],
            unsupported_stages=[],
        )

    def describe_coverage(self) -> str:
        lines: list[str] = ["Intent pattern catalog (deterministic compiler targets):"]
        for name in sorted(COVERAGE_CATALOG):
            entry = COVERAGE_CATALOG[name]
            desc = str(entry.get("description", ""))
            st_vals = [st.value for st in entry.get("stage_types", [])]
            lines.append(f"  {name}: {desc}  [{', '.join(st_vals)}]")
        lines.append("Stage types:")
        for st in StageType:
            lines.append(f"  {st.value}")
        return "\n".join(lines)


DOMAIN_PATTERN_PREFERENCES: dict[str, list[str]] = {
    "paper_rendering": ["research_review", "fan_out_fan_in", "rag_qa", "linear_chain"],
    "literature_review": ["research_review", "fan_out_fan_in", "rag_qa", "linear_chain"],
    "equity_research": ["tool_augmented", "data_pipeline", "research_review"],
    "data_analysis": ["data_pipeline", "tool_augmented", "linear_chain"],
    "code_generation": ["tool_augmented", "research_review", "linear_chain"],
}


# ---------------------------------------------------------------------------
# Direct-build error (plan 32-7)
# ---------------------------------------------------------------------------


class DirectBuildError(Exception):
    """Raised when in-process Graph construction fails.

    Carries enough context for the caller to log, diagnose, and fall back
    to the codegen path.
    """

    def __init__(
        self,
        message: str,
        *,
        stage_name: str | None = None,
        stage_type: str | None = None,
        underlying: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.stage_name = stage_name
        self.stage_type = stage_type
        self.underlying = underlying


class MissingCodeStageError(DirectBuildError):
    """Raised when a code-execution stage lacks runnable code."""

    def __init__(self, stage_name: str, description: str = "") -> None:
        detail = (
            f"Code stage '{stage_name}' has no runnable code and remains unresolved. "
            "Deterministic intent compilation must fall back to real workflow code generation."
        )
        if description:
            detail = f"{detail} Stage description: {description[:200]}"
        super().__init__(
            detail,
            stage_name=stage_name,
            stage_type=StageType.code_execution.value,
        )


# ---------------------------------------------------------------------------
# Intent compiler
# ---------------------------------------------------------------------------


class IntentCompiler:
    """Deterministically compiles WorkflowIntent into dan.builder Python code."""

    def compile(self, intent: WorkflowIntent, *, domain: str | None = None) -> str:
        """Return executable Python code that builds the workflow."""
        lines: list[str] = []

        needs_noderef = any(
            s.stage_type in (StageType.fan_out,)
            for s in intent.stages
        )

        lines.append("from dan.builder import workflow")
        if needs_noderef:
            lines.append("from dan.builder.refs import NodeRef")
        lines.append("")
        lines.append(f'wf = workflow("{_slugify(intent.goal[:50])}")')
        lines.append("")

        prev_exit: str | None = None
        for stage in intent.stages:
            entry_var, exit_var, code_lines = self._compile_stage(stage, intent)
            lines.extend(code_lines)
            if prev_exit:
                if prev_exit.startswith("__both__:"):
                    _, branch_a, branch_b = prev_exit.split(":")
                    lines.append(f"{branch_a} >> {entry_var}")
                    lines.append(f"{branch_b} >> {entry_var}")
                else:
                    lines.append(f"{prev_exit} >> {entry_var}")
            prev_exit = exit_var
            lines.append("")

        lines.append("graph = wf.build()")
        return "\n".join(lines)

    def _compile_stage(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        """Dispatch to stage-type-specific compiler.

        Returns ``(entry_var, exit_var, code_lines)``.
        """
        dispatch = {
            StageType.transform: self._compile_transform,
            StageType.review_loop: self._compile_review_loop,
            StageType.fan_out: self._compile_fan_out,
            StageType.rag_retrieval: self._compile_rag_retrieval,
            StageType.tool_call: self._compile_tool_call,
            StageType.code_execution: self._compile_code_execution,
            StageType.human_approval: self._compile_human_approval,
            StageType.conditional: self._compile_conditional,
            StageType.loop: self._compile_loop,
        }
        return dispatch[stage.stage_type](stage, intent)

    # -- stage-type compilers ------------------------------------------------

    def _compile_transform(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        prompt = _escape(stage.description or f"Process: {stage.name}")
        if worker_generation_uses_workers():
            lines = [f'{var} = wf.worker("{stage.name}", {", ".join(f"{k}={repr(v)}" for k, v in _worker_llm_kwargs(stage, prompt=prompt).items())})']
        else:
            lines = [f'{var} = wf.llm("{stage.name}", prompt="{prompt}")']
        return var, var, lines

    def _compile_review_loop(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        assert stage.review is not None  # guaranteed by model validator

        condition = _escape(_normalize_review_condition(stage.review.condition))
        max_iter = stage.review.max_iterations
        reviewer_prompt = _escape(stage.review.reviewer_prompt)
        draft_prompt = _escape(stage.description or f"Generate draft for: {stage.name}")

        lines = [
            f"{var} = wf.review_loop(",
            f'    writer_prompt="{draft_prompt}",',
            f'    reviewer_prompt="{reviewer_prompt}",',
            f'    name="{stage.name}",',
            f"    max_rounds={max_iter},",
            f'    condition="{condition}",',
            f")",
        ]
        return var, var, lines

    def _compile_fan_out(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        parallelism = max(stage.parallelism, 1)
        proc_id = f"{stage.name}_proc"
        proc_var = _var_name(proc_id)
        proc_prompt = _escape(stage.description or f"Process item for: {stage.name}")
        body_code = str(stage.config.get("body_code") or "").strip()
        body_input_ports = stage.config.get("body_input_ports")
        body_output_ports = stage.config.get("body_output_ports")

        if body_code:
            if worker_generation_uses_workers():
                if body_input_ports is None:
                    body_input_ports = [{"name": "item"}, {"name": "index"}]
                worker_kwargs = _worker_code_kwargs(
                    StageIntent(
                        name=proc_id,
                        stage_type=StageType.code_execution,
                        description=stage.description or stage.name,
                        config={
                            "code": body_code,
                            "input_ports": body_input_ports,
                            "output_ports": body_output_ports,
                        },
                    ),
                    code=body_code,
                )
                body_line = f'    {proc_var} = body.worker("{proc_id}", {", ".join(f"{k}={repr(v)}" for k, v in worker_kwargs.items())})'
            else:
                body_args = [f'code="{_escape(body_code)}"']
                if body_input_ports is None:
                    body_input_ports = [{"name": "item"}, {"name": "index"}]
                body_args.append(f"input_ports={repr(body_input_ports)}")
                if body_output_ports is not None:
                    body_args.append(f"output_ports={repr(body_output_ports)}")
                body_line = f'    {proc_var} = body.code("{proc_id}", {", ".join(body_args)})'
        else:
            if worker_generation_uses_workers():
                body_line = f'    {proc_var} = body.worker("{proc_id}", {", ".join(f"{k}={repr(v)}" for k, v in _worker_llm_kwargs(stage, prompt=proc_prompt).items())})'
            else:
                body_line = f'    {proc_var} = body.llm("{proc_id}", prompt="{proc_prompt}")'

        lines = [
            f'with wf.for_each("{stage.name}", parallelism={parallelism}) as body:',
            body_line,
            f'{var} = NodeRef("{stage.name}", "for_each", wf)',
        ]
        return var, var, lines

    def _compile_rag_retrieval(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        collection = stage.config.get("collection", "default")
        retrieve_id = f"{stage.name}_retrieve"
        answer_id = f"{stage.name}_answer"
        retrieve_var = _var_name(retrieve_id)
        answer_var = _var_name(answer_id)
        answer_prompt = _escape(
            stage.description or "Answer the question using the retrieved context."
        )

        lines = [
            f'{retrieve_var} = wf.rag("{retrieve_id}", collection="{_escape(collection)}")',
            (
                f'{answer_var} = wf.worker("{answer_id}", {", ".join(f"{k}={repr(v)}" for k, v in _worker_llm_kwargs(stage, prompt=answer_prompt).items())})'
                if worker_generation_uses_workers()
                else f'{answer_var} = wf.llm("{answer_id}", prompt="{answer_prompt}")'
            ),
            f"{retrieve_var} >> {answer_var}",
        ]
        return retrieve_var, answer_var, lines

    def _compile_tool_call(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        tool_id = stage.config.get("tool_id") or _infer_tool_id(
            stage.name,
            stage.description or "",
        )
        if not tool_id or tool_id == "llm_operator":
            prompt = _escape(stage.description or f"Process: {stage.name}")
            if worker_generation_uses_workers():
                lines = [f'{var} = wf.worker("{stage.name}", {", ".join(f"{k}={repr(v)}" for k, v in _worker_llm_kwargs(stage, prompt=prompt).items())})']
            else:
                lines = [f'{var} = wf.llm("{stage.name}", prompt="{prompt}")']
            return var, var, lines
        if worker_generation_uses_workers():
            worker_kwargs = _worker_tool_kwargs(stage, tool_id)
            lines = [f'{var} = wf.worker("{stage.name}", {", ".join(f"{k}={repr(v)}" for k, v in worker_kwargs.items())})']
        else:
            args = [f'tool_id="{_escape(tool_id)}"']
            for key, value in _tool_builder_kwargs(stage, tool_id).items():
                args.append(f"{key}={repr(value)}")
            lines = [f'{var} = wf.tool("{stage.name}", {", ".join(args)})']
        return var, var, lines

    def _compile_code_execution(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        code = str(stage.config.get("code") or "").strip()
        if not code:
            raise MissingCodeStageError(stage.name, stage.description or stage.name)
        if worker_generation_uses_workers():
            worker_kwargs = _worker_code_kwargs(stage, code=code)
            lines = [f'{var} = wf.worker("{stage.name}", {", ".join(f"{k}={repr(v)}" for k, v in worker_kwargs.items())})']
        else:
            escaped_code = _escape(code)
            args = [f'code="{escaped_code}"']
            if stage.config.get("input_ports") is not None:
                args.append(f"input_ports={repr(stage.config['input_ports'])}")
            if stage.config.get("output_ports") is not None:
                args.append(f"output_ports={repr(stage.config['output_ports'])}")
            lines = [f'{var} = wf.code("{stage.name}", {", ".join(args)})']
        return var, var, lines

    def _compile_human_approval(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        prompt = _escape(stage.description or f"Please review and approve: {stage.name}")
        lines = [f'{var} = wf.human_in_the_loop("{stage.name}", prompt="{prompt}")']
        return var, var, lines

    def _compile_conditional(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        assert stage.conditional is not None
        gate_var = _var_name(f"{stage.name}_gate")
        then_var = _var_name(f"{stage.name}_then")
        else_var = _var_name(f"{stage.name}_else")
        condition = _escape(stage.conditional.condition)
        then_desc = _escape(stage.conditional.then_description or f"Handle true case for {stage.name}")
        else_desc = _escape(stage.conditional.else_description or f"Handle false case for {stage.name}")
        lines = [
            f'{gate_var}, {then_var}, {else_var} = wf.branch(',
            f'    condition="{condition}",',
            f'    then_prompt="{then_desc}",',
            f'    else_prompt="{else_desc}",',
            f'    name="{stage.name}",',
            f")",
        ]
        # exit_var is a sentinel — the compiler's chaining logic uses it
        # to wire ``exit_var >> next_entry_var``.  We return a special
        # marker so compile() can wire both branches to the next stage.
        return gate_var, f"__both__:{then_var}:{else_var}", lines

    def _compile_loop(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        assert stage.loop is not None
        init_var = _var_name(f"{stage.name}_init")
        body_var = _var_name(f"{stage.name}_body")
        gate_var = _var_name(f"{stage.name}_gate")
        result_var = _var_name(f"{stage.name}_result")
        condition = _escape(stage.loop.condition)
        init_code = _escape(stage.loop.init_code)
        body_code = _escape(stage.loop.body_code)
        result_code = _escape(stage.loop.result_code)
        state_schema = repr(stage.loop.state_schema)
        state_defaults = repr(stage.loop.state_defaults)
        lines = [
            f'{init_var} = wf.code("{stage.name}_init", code="{init_code}", output_ports=[{{"name": "counter"}}])',
            f'{body_var} = wf.code("{stage.name}_body", code="{body_code}", input_ports=[{{"name": "counter", "required": False}}], output_ports=[{{"name": "counter"}}])',
            (
                f'{gate_var} = wf.gate("{stage.name}_gate", condition="{condition}", '
                f'gate_mode="while", max_iterations={stage.loop.max_iterations}, '
                f'state_schema={state_schema}, state_defaults={state_defaults}, '
                'input_ports=[{"name": "counter"}], '
                'output_ports=[{"name": "continue"}, {"name": "done"}])'
            ),
            f'{result_var} = wf.code("{stage.name}_result", code="{result_code}", input_ports=[{{"name": "data", "required": False}}], output_ports=[{{"name": "result"}}])',
            f'wf.edge({init_var}["counter"], {body_var}["counter"])',
            f'wf.edge({body_var}["counter"], {gate_var}["counter"])',
            f'wf.edge({gate_var}["continue"], {body_var}["counter"])',
            f'wf.edge({gate_var}["done"], {result_var}["data"])',
        ]
        return init_var, result_var, lines

    # -- direct in-process graph construction (plan 32-7) --------------------

    def build_graph(
        self, intent: WorkflowIntent, *, domain: str | None = None,
    ) -> Graph:
        """Construct a ``Graph`` directly by calling the builder API in-process.

        Same dispatch logic as ``compile()`` but produces a ``Graph`` object
        without emitting or executing code strings.  Raises
        ``DirectBuildError`` on failure so the caller can fall back to codegen.
        """
        from dan.builder import workflow as wf_factory
        from dan.builder.refs import NodeRef

        wf = wf_factory(_slugify(intent.goal[:50]))

        prev_ref: NodeRef | None = None
        prev_is_branch = False
        branch_refs: tuple[NodeRef, NodeRef] | None = None

        for stage in intent.stages:
            try:
                entry_ref, exit_ref = self._build_stage(stage, wf)
            except DirectBuildError:
                raise
            except Exception as exc:
                raise DirectBuildError(
                    f"Failed to build stage '{stage.name}' ({stage.stage_type.value}): {exc}",
                    stage_name=stage.name,
                    stage_type=stage.stage_type.value,
                    underlying=exc,
                ) from exc

            if prev_ref is not None:
                if prev_is_branch and branch_refs is not None:
                    branch_refs[0] >> entry_ref
                    branch_refs[1] >> entry_ref
                else:
                    prev_ref >> entry_ref

            if isinstance(exit_ref, tuple):
                prev_is_branch = True
                branch_refs = exit_ref
                prev_ref = exit_ref[0]
            else:
                prev_is_branch = False
                branch_refs = None
                prev_ref = exit_ref

        try:
            return wf.build()
        except Exception as exc:
            raise DirectBuildError(
                f"Graph compilation failed: {exc}", underlying=exc,
            ) from exc

    def _build_stage(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef | tuple[NodeRef, NodeRef]]:
        """Dispatch to stage-type-specific builder.

        Returns ``(entry_ref, exit_ref)`` where ``exit_ref`` is either a
        single ``NodeRef`` or a ``(then_ref, else_ref)`` tuple for
        conditional stages.
        """
        dispatch = {
            StageType.transform: self._build_transform,
            StageType.review_loop: self._build_review_loop,
            StageType.fan_out: self._build_fan_out,
            StageType.rag_retrieval: self._build_rag_retrieval,
            StageType.tool_call: self._build_tool_call,
            StageType.code_execution: self._build_code_execution,
            StageType.human_approval: self._build_human_approval,
            StageType.conditional: self._build_conditional,
            StageType.loop: self._build_loop,
        }
        handler = dispatch.get(stage.stage_type)
        if handler is None:
            raise DirectBuildError(
                f"Unsupported stage type: {stage.stage_type.value}",
                stage_name=stage.name,
                stage_type=stage.stage_type.value,
            )
        return handler(stage, wf)

    def _build_transform(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef]:
        prompt = stage.description or f"Process: {stage.name}"
        if worker_generation_uses_workers():
            ref = wf.worker(stage.name, **_worker_llm_kwargs(stage, prompt=prompt))
        else:
            ref = wf.llm(stage.name, prompt=prompt)
        return ref, ref

    def _build_review_loop(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef]:
        assert stage.review is not None
        draft_prompt = stage.description or f"Generate draft for: {stage.name}"
        ref = wf.review_loop(
            writer_prompt=draft_prompt,
            reviewer_prompt=stage.review.reviewer_prompt,
            name=stage.name,
            max_rounds=stage.review.max_iterations,
            condition=_normalize_review_condition(stage.review.condition),
        )
        return ref, ref

    def _build_fan_out(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef]:
        from dan.builder.refs import NodeRef as NR

        parallelism = max(stage.parallelism, 1)
        proc_prompt = stage.description or f"Process item for: {stage.name}"
        body_code = str(stage.config.get("body_code") or "").strip()
        body_input_ports = stage.config.get("body_input_ports")
        body_output_ports = stage.config.get("body_output_ports")
        with wf.for_each(stage.name, parallelism=parallelism) as body:
            if body_code:
                if body_input_ports is None:
                    body_input_ports = [{"name": "item"}, {"name": "index"}]
                if worker_generation_uses_workers():
                    body_kwargs = _worker_code_kwargs(
                        StageIntent(
                            name=f"{stage.name}_proc",
                            stage_type=StageType.code_execution,
                            description=stage.description or stage.name,
                            config={
                                "code": body_code,
                                "input_ports": body_input_ports,
                                "output_ports": body_output_ports,
                            },
                        ),
                        code=body_code,
                    )
                    body.worker(f"{stage.name}_proc", **body_kwargs)
                else:
                    body_kwargs = {
                        "code": body_code,
                        "input_ports": body_input_ports,
                    }
                    if body_output_ports is not None:
                        body_kwargs["output_ports"] = body_output_ports
                    body.code(f"{stage.name}_proc", **body_kwargs)
            else:
                if worker_generation_uses_workers():
                    body.worker(f"{stage.name}_proc", **_worker_llm_kwargs(stage, prompt=proc_prompt))
                else:
                    body.llm(f"{stage.name}_proc", prompt=proc_prompt)
        ref = NR(stage.name, "for_each", wf)
        return ref, ref

    def _build_rag_retrieval(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef]:
        collection = stage.config.get("collection", "default")
        retrieve_id = f"{stage.name}_retrieve"
        answer_id = f"{stage.name}_answer"
        answer_prompt = (
            stage.description
            or "Answer the question using the retrieved context."
        )
        retrieve_ref = wf.rag(retrieve_id, collection=collection)
        if worker_generation_uses_workers():
            answer_ref = wf.worker(answer_id, **_worker_llm_kwargs(stage, prompt=answer_prompt))
        else:
            answer_ref = wf.llm(answer_id, prompt=answer_prompt)
        retrieve_ref >> answer_ref
        return retrieve_ref, answer_ref

    def _build_tool_call(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef]:
        tool_id = stage.config.get("tool_id") or _infer_tool_id(
            stage.name,
            stage.description or "",
        )
        if not tool_id or tool_id == "llm_operator":
            prompt = stage.description or f"Process: {stage.name}"
            if worker_generation_uses_workers():
                ref = wf.worker(stage.name, **_worker_llm_kwargs(stage, prompt=prompt))
            else:
                ref = wf.llm(stage.name, prompt=prompt)
            return ref, ref
        if worker_generation_uses_workers():
            ref = wf.worker(stage.name, **_worker_tool_kwargs(stage, tool_id))
        else:
            ref = wf.tool(stage.name, tool_id=tool_id, **_tool_builder_kwargs(stage, tool_id))
        return ref, ref

    def _build_code_execution(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef]:
        code = str(stage.config.get("code") or "").strip()
        if not code:
            raise MissingCodeStageError(stage.name, stage.description or stage.name)
        if worker_generation_uses_workers():
            ref = wf.worker(stage.name, **_worker_code_kwargs(stage, code=code))
        else:
            kwargs: dict[str, Any] = {"code": code}
            if stage.config.get("input_ports") is not None:
                kwargs["input_ports"] = stage.config["input_ports"]
            if stage.config.get("output_ports") is not None:
                kwargs["output_ports"] = stage.config["output_ports"]
            ref = wf.code(stage.name, **kwargs)
        return ref, ref

    def _build_human_approval(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef]:
        prompt = stage.description or f"Please review and approve: {stage.name}"
        ref = wf.human_in_the_loop(stage.name, prompt=prompt)
        return ref, ref

    def _build_conditional(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, tuple[NodeRef, NodeRef]]:
        assert stage.conditional is not None
        condition = stage.conditional.condition
        then_desc = (
            stage.conditional.then_description
            or f"Handle true case for {stage.name}"
        )
        else_desc = (
            stage.conditional.else_description
            or f"Handle false case for {stage.name}"
        )
        gate_ref, then_ref, else_ref = wf.branch(
            condition=condition,
            then_prompt=then_desc,
            else_prompt=else_desc,
            name=stage.name,
        )
        return gate_ref, (then_ref, else_ref)

    def _build_loop(
        self, stage: StageIntent, wf: Any,
    ) -> tuple[NodeRef, NodeRef]:
        assert stage.loop is not None
        init_ref = wf.code(
            f"{stage.name}_init",
            code=stage.loop.init_code,
            output_ports=[{"name": "counter"}],
        )
        body_ref = wf.code(
            f"{stage.name}_body",
            code=stage.loop.body_code,
            input_ports=[{"name": "counter", "required": False}],
            output_ports=[{"name": "counter"}],
        )
        gate_ref = wf.gate(
            f"{stage.name}_gate",
            condition=stage.loop.condition,
            gate_mode="while",
            max_iterations=stage.loop.max_iterations,
            state_schema=stage.loop.state_schema,
            state_defaults=stage.loop.state_defaults,
            input_ports=[{"name": "counter"}],
            output_ports=[{"name": "continue"}, {"name": "done"}],
        )
        result_ref = wf.code(
            f"{stage.name}_result",
            code=stage.loop.result_code,
            input_ports=[{"name": "data", "required": False}],
            output_ports=[{"name": "result"}],
        )
        wf.edge(init_ref["counter"], body_ref["counter"])
        wf.edge(body_ref["counter"], gate_ref["counter"])
        wf.edge(gate_ref["continue"], body_ref["counter"])
        wf.edge(gate_ref["done"], result_ref["data"])
        return init_ref, result_ref

    # -- composed direct build (plan 32-7) ------------------------------------

    def build_graph_composed(
        self,
        intent: WorkflowIntent,
        constituent_patterns: list[str],
        *,
        domain: str | None = None,
    ) -> Graph:
        """Build a multi-pattern intent by calling convenience methods directly.

        Mirrors ``compile_composed()`` but produces a ``Graph`` in-process.
        """
        from dan.builder import workflow as wf_factory
        from dan.builder.refs import NodeRef as NR

        wf = wf_factory(_slugify(intent.goal[:50]))
        segments = self._partition_stages(intent.stages, constituent_patterns)
        prev_ref: NodeRef | tuple[NodeRef, NodeRef] | None = None

        for seg_idx, (pattern, stages) in enumerate(segments):
            try:
                ref = self._build_segment(pattern, stages, seg_idx, wf, prev_ref)
            except DirectBuildError:
                raise
            except Exception as exc:
                raise DirectBuildError(
                    f"Failed to build segment {seg_idx} (pattern={pattern}): {exc}",
                    stage_name=stages[0].name if stages else None,
                    stage_type=pattern,
                    underlying=exc,
                ) from exc
            prev_ref = ref

        try:
            return wf.build()
        except Exception as exc:
            raise DirectBuildError(
                f"Graph compilation failed: {exc}", underlying=exc,
            ) from exc

    def _build_segment(
        self,
        pattern: str,
        stages: list[StageIntent],
        seg_idx: int,
        wf: Any,
        prev_ref: NodeRef | tuple[NodeRef, NodeRef] | None,
    ) -> NodeRef | tuple[NodeRef, NodeRef]:
        """Build a single segment using convenience methods where possible."""
        from dan.builder.refs import NodeRef as NR

        ref: NodeRef | tuple[NodeRef, NodeRef] | None = None
        first_entry_ref: NodeRef | None = None

        if pattern == "linear_chain" and len(stages) >= 2:
            steps = tuple(
                (s.name, s.description or s.name) for s in stages
            )
            ref = wf.chain(*steps)
        elif pattern == "review_loop" and stages:
            s = stages[0]
            reviewer_prompt = (
                s.review.reviewer_prompt if s.review else "Review for quality"
            )
            max_iter = s.review.max_iterations if s.review else 3
            draft_prompt = s.description or s.name
            condition = _normalize_review_condition(
                s.review.condition if s.review else "quality_score < 8"
            )
            ref = wf.review_loop(
                writer_prompt=draft_prompt,
                reviewer_prompt=reviewer_prompt,
                name=s.name,
                max_rounds=max_iter,
                condition=condition,
            )
        elif pattern == "research_review":
            transform_stages = [
                s for s in stages if s.stage_type == StageType.transform
            ]
            review_stages = [
                s for s in stages if s.stage_type == StageType.review_loop
            ]
            chain_ref = None
            if transform_stages:
                steps = tuple(
                    (s.name, s.description or s.name) for s in transform_stages
                )
                chain_ref = wf.chain(*steps)
            if review_stages:
                rs = review_stages[0]
                rp = (
                    rs.review.reviewer_prompt
                    if rs.review
                    else "Review for quality"
                )
                mi = rs.review.max_iterations if rs.review else 3
                dp = rs.description or rs.name
                rc = _normalize_review_condition(
                    rs.review.condition if rs.review else "quality_score < 8"
                )
                ref = wf.review_loop(
                    writer_prompt=dp,
                    reviewer_prompt=rp,
                    name=rs.name,
                    max_rounds=mi,
                    condition=rc,
                )
                if chain_ref is not None:
                    chain_ref >> ref
            else:
                ref = chain_ref
        elif pattern == "fan_out_fan_in" and stages:
            s = stages[0]
            proc_prompt = s.description or s.name
            with wf.for_each(
                s.name, parallelism=max(s.parallelism, 1)
            ) as body:
                body.llm(f"{s.name}_proc", prompt=proc_prompt)
            ref = NR(s.name, "for_each", wf)
        else:
            first_entry_ref = None
            for s in stages:
                entry, exit_ref = self._build_stage(
                    s, wf,
                )[:2]
                if first_entry_ref is None:
                    first_entry_ref = entry
                ref = exit_ref

        if ref is None:
            raise DirectBuildError(
                f"Segment {seg_idx} produced no nodes",
                stage_type=pattern,
            )

        if prev_ref is not None:
            entry_ref = first_entry_ref if first_entry_ref is not None else ref
            if isinstance(prev_ref, tuple):
                prev_ref[0] >> entry_ref
                prev_ref[1] >> entry_ref
            else:
                prev_ref >> entry_ref

        return ref

    # -- composed compilation ------------------------------------------------

    def compile_composed(
        self,
        intent: WorkflowIntent,
        constituent_patterns: list[str],
        *,
        domain: str | None = None,
    ) -> str:
        """Compile a multi-pattern intent by chaining convenience method calls."""
        lines: list[str] = [
            "from dan.builder import workflow",
            "from dan.builder.refs import NodeRef",
            "",
            f'wf = workflow("{_slugify(intent.goal[:50])}")',
            "",
        ]

        segments = self._partition_stages(intent.stages, constituent_patterns)
        prev_ref_var: str | None = None

        for seg_idx, (pattern, stages) in enumerate(segments):
            ref_var, code = self._compile_segment(
                pattern, stages, seg_idx, prev_ref_var
            )
            lines.extend(code)
            lines.append("")
            prev_ref_var = ref_var

        lines.append("graph = wf.build()")
        return "\n".join(lines)

    def _partition_stages(
        self,
        stages: list[StageIntent],
        patterns: list[str],
    ) -> list[tuple[str, list[StageIntent]]]:
        """Partition stages into segments matching the pattern sequence."""
        result: list[tuple[str, list[StageIntent]]] = []
        idx = 0
        for pattern in patterns:
            cat_types = COVERAGE_CATALOG[pattern]["stage_types"]
            n = max(len(cat_types), 1)
            if len(cat_types) == 1:
                count = 0
                while (
                    idx + count < len(stages)
                    and stages[idx + count].stage_type == cat_types[0]
                ):
                    count += 1
                n = max(count, 1)
            seg = stages[idx : idx + n]
            result.append((pattern, seg))
            idx += n
        if idx < len(stages) and result:
            last_pattern, last_stages = result[-1]
            result[-1] = (last_pattern, last_stages + stages[idx:])
        return result

    def _compile_segment(
        self,
        pattern: str,
        stages: list[StageIntent],
        seg_idx: int,
        prev_ref_var: str | None,
    ) -> tuple[str, list[str]]:
        """Compile a single segment using convenience methods where possible."""
        lines: list[str] = []
        ref_var = f"seg_{seg_idx}"
        first_entry_var: str | None = None

        if pattern == "linear_chain" and len(stages) >= 2:
            steps = []
            for s in stages:
                desc = _escape(s.description or s.name)
                steps.append(f'    ("{s.name}", "{desc}")')
            steps_str = ",\n".join(steps)
            lines.append(f"{ref_var} = wf.chain(\n{steps_str},\n)")
        elif pattern == "review_loop" and stages:
            s = stages[0]
            reviewer_prompt = _escape(
                s.review.reviewer_prompt if s.review else "Review for quality"
            )
            max_iter = s.review.max_iterations if s.review else 3
            draft_prompt = _escape(s.description or s.name)
            condition = _normalize_review_condition(
                s.review.condition if s.review else "quality_score < 8"
            )
            lines.append(
                f"{ref_var} = wf.review_loop(\n"
                f'    writer_prompt="{draft_prompt}",\n'
                f'    reviewer_prompt="{reviewer_prompt}",\n'
                f'    name="{s.name}",\n'
                f"    max_rounds={max_iter},\n"
                f'    condition="{_escape(condition)}",\n'
                f")"
            )
        elif pattern == "research_review":
            transform_stages = [
                s for s in stages if s.stage_type == StageType.transform
            ]
            review_stages = [
                s for s in stages if s.stage_type == StageType.review_loop
            ]

            if transform_stages:
                chain_steps = [
                    f'    ("{s.name}", "{_escape(s.description or s.name)}")'
                    for s in transform_stages
                ]
                lines.append(
                    "chain_ref = wf.chain(\n" + ",\n".join(chain_steps) + ",\n)"
                )

            if review_stages:
                rs = review_stages[0]
                rp = _escape(
                    rs.review.reviewer_prompt
                    if rs.review
                    else "Review for quality"
                )
                mi = rs.review.max_iterations if rs.review else 3
                dp = _escape(rs.description or rs.name)
                rc = _normalize_review_condition(
                    rs.review.condition if rs.review else "quality_score < 8"
                )
                lines.append(
                    f"{ref_var} = wf.review_loop(\n"
                    f'    writer_prompt="{dp}",\n'
                    f'    reviewer_prompt="{rp}",\n'
                    f'    name="{rs.name}",\n'
                    f"    max_rounds={mi},\n"
                    f'    condition="{_escape(rc)}",\n'
                    f")"
                )
            else:
                ref_var = "chain_ref"
        elif pattern == "fan_out_fan_in" and stages:
            s = stages[0]
            desc = _escape(s.description or s.name)
            lines.append(
                f'with wf.for_each("{s.name}", parallelism={max(s.parallelism, 1)}) as body:\n'
                f'    body.llm("{s.name}_proc", prompt="{desc}")\n'
                f'{ref_var} = NodeRef("{s.name}", "for_each", wf)'
            )
        else:
            first_entry_var = None
            for s in stages:
                entry, exit_, stage_lines = self._compile_stage(
                    s, WorkflowIntent(goal="", stages=stages)
                )
                if first_entry_var is None:
                    first_entry_var = entry
                lines.extend(stage_lines)
                ref_var = exit_

        if prev_ref_var and lines:
            entry_var = first_entry_var if first_entry_var is not None else ref_var
            if prev_ref_var.startswith("__both__:"):
                _, branch_a, branch_b = prev_ref_var.split(":")
                lines.insert(0, f"{branch_b} >> {entry_var}")
                lines.insert(0, f"{branch_a} >> {entry_var}")
            else:
                lines.insert(0, f"{prev_ref_var} >> {entry_var}")

        return ref_var, lines


# ---------------------------------------------------------------------------
# Helpers (module-level for reuse / testing)
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert text to a kebab-friendly workflow name."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:40] or "workflow"


def _var_name(stage_name: str) -> str:
    """Convert a stage name to a valid Python variable name."""
    var = re.sub(r"[^a-zA-Z0-9_]", "_", stage_name)
    if var and var[0].isdigit():
        var = f"s_{var}"
    return var or "node"


def _escape(text: str) -> str:
    """Escape a string for embedding inside double-quoted Python strings."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
