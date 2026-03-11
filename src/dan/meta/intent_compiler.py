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

DOMAIN_PATTERN_PREFERENCES: dict[str, list[str]] = {
    "paper_rendering": ["research_review", "fan_out_fan_in", "rag_qa", "linear_chain"],
    "literature_review": ["research_review", "fan_out_fan_in", "rag_qa", "linear_chain"],
    "equity_research": ["tool_augmented", "data_pipeline", "research_review"],
    "data_analysis": ["data_pipeline", "tool_augmented", "linear_chain"],
    "code_generation": ["tool_augmented", "research_review", "linear_chain"],
}


# ---------------------------------------------------------------------------
# Coverage checker
# ---------------------------------------------------------------------------


class CoverageResult(BaseModel):
    """Result of checking whether a WorkflowIntent can be fully compiled."""

    fully_covered: bool
    supported_stages: list[str] = Field(default_factory=list)
    unsupported_stages: list[str] = Field(default_factory=list)
    recommendation: Literal["compile", "fallback", "partial", "compose"]
    constituent_patterns: list[str] | None = None


class CoverageChecker:
    """Pre-validates whether all stages in a WorkflowIntent are compilable.

    Phase 14 rule: if ANY stage is unsupported the entire intent falls back
    to builder codegen (24-1).  No partial compilation.
    """

    SUPPORTED_TYPES: set[StageType] = set(StageType)

    def check(self, intent: WorkflowIntent, *, try_compose: bool = True) -> CoverageResult:
        """Validate that every stage type in *intent* is in ``SUPPORTED_TYPES``.

        When ``try_compose`` is True and single-pattern match fails, attempts
        decomposition into constituent catalog patterns (max 3).
        """
        supported: list[str] = []
        unsupported: list[str] = []

        for stage in intent.stages:
            if stage.stage_type in self.SUPPORTED_TYPES:
                supported.append(stage.name)
            else:
                unsupported.append(stage.name)

        fully_covered = len(unsupported) == 0

        if fully_covered:
            recommendation = "compile"
        elif try_compose:
            decomposition = self._try_decompose(intent)
            if decomposition is not None and len(decomposition) <= 3:
                return CoverageResult(
                    fully_covered=True,
                    supported_stages=supported + unsupported,
                    unsupported_stages=[],
                    recommendation="compose",
                    constituent_patterns=decomposition,
                )
            recommendation = "fallback"
        else:
            recommendation = "fallback"

        return CoverageResult(
            fully_covered=fully_covered,
            supported_stages=supported,
            unsupported_stages=unsupported,
            recommendation=recommendation,
        )

    def _try_decompose(self, intent: WorkflowIntent) -> list[str] | None:
        """Try to decompose an intent into constituent catalog patterns (max 3)."""
        stage_types = [s.stage_type for s in intent.stages]
        if not stage_types:
            return None

        patterns: list[str] = []
        i = 0
        while i < len(stage_types):
            matched = False
            for length in range(min(len(stage_types) - i, 4), 0, -1):
                segment = stage_types[i : i + length]
                pattern = self._match_segment(segment)
                if pattern:
                    patterns.append(pattern)
                    i += length
                    matched = True
                    break
            if not matched:
                return None
            if len(patterns) > 3:
                return None
        return patterns if patterns else None

    def _match_segment(self, segment: list[StageType]) -> str | None:
        """Match a sequence of stage types to a catalog pattern."""
        for name, entry in COVERAGE_CATALOG.items():
            cat_types = entry["stage_types"]
            if len(cat_types) == len(segment) and all(
                ct == st for ct, st in zip(cat_types, segment)
            ):
                return name
            if len(cat_types) == 1 and all(st == cat_types[0] for st in segment):
                return name
        return None

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
            condition = s.review.condition if s.review else "quality_score >= 8"
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
                rc = rs.review.condition if rs.review else "quality_score >= 8"
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
            for s in stages:
                entry, exit_, stage_lines = self._compile_stage(
                    s, WorkflowIntent(goal="", stages=stages)
                )
                lines.extend(stage_lines)
                ref_var = exit_

        if prev_ref_var and lines:
            lines.insert(0, f"{prev_ref_var} >> {ref_var}")

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
