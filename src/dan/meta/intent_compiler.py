"""Intent Compiler — deterministic compilation of WorkflowIntent to builder DSL code.

The ``IntentCompiler`` takes a validated ``WorkflowIntent`` and produces
executable Python code that uses the ``dan.builder`` DSL to construct a
``Graph``.  A ``CoverageChecker`` pre-validates whether all stages can be
compiled deterministically; when coverage is partial the caller can fall
back to the LLM-based codegen path (plan 24-1).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from dan.meta.intent_schema import StageIntent, StageType, WorkflowIntent


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
        "description": "Ingest → process → analyze",
        "stage_types": [StageType.tool_call, StageType.code_execution, StageType.transform],
        "composable": True,
    },
    "tool_augmented": {
        "description": "LLM + tool calls",
        "stage_types": [StageType.tool_call, StageType.transform],
        "composable": True,
    },
    "human_gate": {
        "description": "Human-in-the-loop approval",
        "stage_types": [StageType.human_approval],
        "composable": True,
    },
}


# ---------------------------------------------------------------------------
# Coverage checker
# ---------------------------------------------------------------------------


class CoverageResult(BaseModel):
    """Result of checking whether a WorkflowIntent can be fully compiled."""

    fully_covered: bool
    supported_stages: list[str] = Field(default_factory=list)
    unsupported_stages: list[str] = Field(default_factory=list)
    recommendation: Literal["compile", "fallback", "partial"]


class CoverageChecker:
    """Pre-validates whether all stages in a WorkflowIntent are compilable.

    Phase 14 rule: if ANY stage is unsupported the entire intent falls back
    to builder codegen (24-1).  No partial compilation.
    """

    SUPPORTED_TYPES: set[StageType] = set(StageType)

    def check(self, intent: WorkflowIntent) -> CoverageResult:
        """Validate that every stage type in *intent* is in ``SUPPORTED_TYPES``.

        If any stage is unsupported, ``recommendation`` is always ``"fallback"``
        (Phase 14 — no partial compilation).
        """
        supported: list[str] = []
        unsupported: list[str] = []

        for stage in intent.stages:
            if stage.stage_type in self.SUPPORTED_TYPES:
                supported.append(stage.name)
            else:
                unsupported.append(stage.name)

        fully_covered = len(unsupported) == 0
        recommendation: Literal["compile", "fallback", "partial"] = (
            "compile" if fully_covered else "fallback"
        )

        return CoverageResult(
            fully_covered=fully_covered,
            supported_stages=supported,
            unsupported_stages=unsupported,
            recommendation=recommendation,
        )

    def describe_coverage(self) -> str:
        """Return a human-readable summary of supported patterns."""
        lines = ["Supported workflow patterns:"]
        for name, entry in COVERAGE_CATALOG.items():
            types = ", ".join(st.value for st in entry["stage_types"])
            lines.append(f"  - {name}: {entry['description']} (types: {types})")
        lines.append("")
        lines.append(
            f"Supported stage types: {', '.join(st.value for st in sorted(self.SUPPORTED_TYPES, key=lambda s: s.value))}"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Intent compiler
# ---------------------------------------------------------------------------


class IntentCompiler:
    """Deterministically compiles WorkflowIntent into dan.builder Python code."""

    def compile(self, intent: WorkflowIntent) -> str:
        """Return executable Python code that builds the workflow."""
        lines: list[str] = []

        needs_noderef = any(
            s.stage_type in (StageType.review_loop, StageType.fan_out)
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
        }
        return dispatch[stage.stage_type](stage, intent)

    # -- stage-type compilers ------------------------------------------------

    def _compile_transform(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        prompt = _escape(stage.description or f"Process: {stage.name}")
        lines = [f'{var} = wf.llm("{stage.name}", prompt="{prompt}")']
        return var, var, lines

    def _compile_review_loop(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        assert stage.review is not None  # guaranteed by model validator

        condition = _escape(stage.review.condition)
        max_iter = stage.review.max_iterations
        draft_id = f"{stage.name}_draft"
        reviewer_id = f"{stage.name}_reviewer"
        draft_var = _var_name(draft_id)
        reviewer_var = _var_name(reviewer_id)
        reviewer_prompt = _escape(stage.review.reviewer_prompt)
        draft_prompt = _escape(stage.description or f"Generate draft for: {stage.name}")

        lines = [
            (
                f'with wf.while_loop("{stage.name}", '
                f'condition="{condition}", '
                f"max_iterations={max_iter}) as body:"
            ),
            f'    {draft_var} = body.llm("{draft_id}", prompt="{draft_prompt}")',
            f'    {reviewer_var} = body.llm("{reviewer_id}", prompt="{reviewer_prompt}")',
            f"    {draft_var} >> {reviewer_var}",
            f'{var} = NodeRef("{stage.name}", "while_loop", wf)',
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

        lines = [
            f'with wf.for_each("{stage.name}", parallelism={parallelism}) as body:',
            f'    {proc_var} = body.llm("{proc_id}", prompt="{proc_prompt}")',
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
            f'{answer_var} = wf.llm("{answer_id}", prompt="{answer_prompt}")',
            f"{retrieve_var} >> {answer_var}",
        ]
        return retrieve_var, answer_var, lines

    def _compile_tool_call(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        tool_id = stage.config.get("tool_id", "web_search")
        lines = [f'{var} = wf.tool("{stage.name}", tool_id="{_escape(tool_id)}")']
        return var, var, lines

    def _compile_code_execution(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        code = stage.config.get("code", "result = 'done'")
        escaped_code = _escape(code)
        lines = [f'{var} = wf.code("{stage.name}", code="{escaped_code}")']
        return var, var, lines

    def _compile_human_approval(
        self, stage: StageIntent, intent: WorkflowIntent
    ) -> tuple[str, str, list[str]]:
        var = _var_name(stage.name)
        prompt = _escape(stage.description or f"Please review and approve: {stage.name}")
        lines = [f'{var} = wf.human_in_the_loop("{stage.name}", prompt="{prompt}")']
        return var, var, lines


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
