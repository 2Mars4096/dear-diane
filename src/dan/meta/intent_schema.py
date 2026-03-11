"""Structured workflow intent — compact representation for the intent compiler.

The LLM emits a ``WorkflowIntent`` (via function-calling) and a deterministic
compiler maps it to builder DSL code.  For unsupported patterns the compiler
fails fast and hands off to the 24-1 builder codegen path.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StageType(str, Enum):
    """Supported stage archetypes for intent compilation."""

    transform = "transform"
    review_loop = "review_loop"
    fan_out = "fan_out"
    rag_retrieval = "rag_retrieval"
    tool_call = "tool_call"
    code_execution = "code_execution"
    human_approval = "human_approval"
    conditional = "conditional"


class DataSourceType(str, Enum):
    """Where a workflow pulls external data from."""

    file = "file"
    url = "url"
    rag_collection = "rag_collection"
    api = "api"


# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------


class DataSource(BaseModel):
    """External data dependency for a workflow."""

    type: DataSourceType
    config: dict = Field(default_factory=dict)
    description: str = ""


class ReviewRequirement(BaseModel):
    """Specifies how a review-loop stage evaluates quality."""

    reviewer_prompt: str = "Review the output for quality and correctness."
    condition: str = "quality_score >= 8"
    max_iterations: int = Field(default=3, ge=1, le=20)


class ConditionalRequirement(BaseModel):
    """Specifies the branches of a conditional stage."""

    condition: str = "result == true"
    then_description: str = ""
    else_description: str = ""


# ---------------------------------------------------------------------------
# Stage intent
# ---------------------------------------------------------------------------


class StageIntent(BaseModel):
    """A single processing stage inside a workflow intent."""

    name: str
    description: str = ""
    stage_type: StageType = StageType.transform
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    review: ReviewRequirement | None = None
    conditional: ConditionalRequirement | None = None
    parallelism: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _review_loop_requires_review(self) -> StageIntent:
        if self.stage_type == StageType.review_loop and self.review is None:
            raise ValueError(
                f"Stage '{self.name}' has stage_type 'review_loop' "
                "but no 'review' field — ReviewRequirement is required"
            )
        return self

    @model_validator(mode="after")
    def _conditional_requires_conditional(self) -> StageIntent:
        if self.stage_type == StageType.conditional and self.conditional is None:
            raise ValueError(
                f"Stage '{self.name}' has stage_type 'conditional' "
                "but no 'conditional' field — ConditionalRequirement is required"
            )
        return self


# ---------------------------------------------------------------------------
# Top-level workflow intent
# ---------------------------------------------------------------------------


class WorkflowIntent(BaseModel):
    """Compact structured representation of a desired workflow.

    Emitted by the LLM and consumed by the intent compiler.  Validated
    before compilation so the compiler can assume well-formed input.
    """

    goal: str
    stages: list[StageIntent] = Field(min_length=1)
    global_inputs: list[str] = Field(default_factory=list)
    global_outputs: list[str] = Field(default_factory=list)
    data_sources: list[DataSource] = Field(default_factory=list)
    review_requirements: list[ReviewRequirement] = Field(default_factory=list)
    constraints: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_workflow(self) -> WorkflowIntent:
        self._check_duplicate_stage_names()
        self._check_global_input_references()
        self._check_circular_dependencies()
        return self

    # -- helpers (private) ---------------------------------------------------

    def _check_duplicate_stage_names(self) -> None:
        seen: set[str] = set()
        for stage in self.stages:
            if stage.name in seen:
                raise ValueError(f"Duplicate stage name: '{stage.name}'")
            seen.add(stage.name)

    def _check_global_input_references(self) -> None:
        """Verify that stage inputs referencing global inputs actually exist."""
        stage_names = {s.name for s in self.stages}
        stage_outputs: set[str] = set()
        for s in self.stages:
            stage_outputs.update(s.outputs)

        gi = set(self.global_inputs)
        for stage in self.stages:
            for inp in stage.inputs:
                if inp in stage_names or inp in stage_outputs or inp in gi:
                    continue
                raise ValueError(
                    f"Stage '{stage.name}' references input '{inp}' which is "
                    "not a global_input, another stage name, or a stage output"
                )

    def _check_circular_dependencies(self) -> None:
        """Topological-sort check for cycles among stages."""
        stage_names = {s.name for s in self.stages}
        adj: dict[str, list[str]] = {s.name: [] for s in self.stages}
        output_to_stage: dict[str, str] = {}
        for s in self.stages:
            for out in s.outputs:
                output_to_stage[out] = s.name

        for s in self.stages:
            for inp in s.inputs:
                if inp in stage_names:
                    adj[inp].append(s.name)
                elif inp in output_to_stage:
                    adj[output_to_stage[inp]].append(s.name)

        visited: set[str] = set()
        in_stack: set[str] = set()

        def _dfs(node: str) -> None:
            if node in in_stack:
                raise ValueError(f"Circular dependency detected involving stage '{node}'")
            if node in visited:
                return
            in_stack.add(node)
            for nxt in adj.get(node, []):
                _dfs(nxt)
            in_stack.discard(node)
            visited.add(node)

        for name in adj:
            _dfs(name)

    # -- public API ----------------------------------------------------------

    @classmethod
    def to_json_schema(cls) -> dict:
        """Return the JSON Schema for LLM function-calling tool definitions."""
        return cls.model_json_schema()
