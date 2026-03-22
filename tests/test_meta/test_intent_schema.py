"""Tests for dan.meta.intent_schema — WorkflowIntent Pydantic models."""

import pytest
from pydantic import ValidationError

from dan.meta.intent_schema import (
    DataSource,
    DataSourceType,
    LoopRequirement,
    ReviewRequirement,
    StageIntent,
    StageType,
    WorkflowIntent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stage(name: str = "s1", **kw) -> dict:
    return {"name": name, **kw}


def _make_intent(stages: list[dict] | None = None, **kw) -> dict:
    base: dict = {
        "goal": "test workflow",
        "stages": stages or [_make_stage()],
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# StageType enum
# ---------------------------------------------------------------------------


class TestStageType:
    def test_has_all_nine_values(self):
        expected = {
            "transform",
            "review_loop",
            "fan_out",
            "rag_retrieval",
            "tool_call",
            "code_execution",
            "human_approval",
            "conditional",
            "loop",
        }
        assert {e.value for e in StageType} == expected

    def test_string_access(self):
        assert StageType("transform") is StageType.transform


# ---------------------------------------------------------------------------
# DataSource
# ---------------------------------------------------------------------------


class TestDataSource:
    @pytest.mark.parametrize("dtype", list(DataSourceType))
    def test_each_type_validates(self, dtype: DataSourceType):
        ds = DataSource(type=dtype)
        assert ds.type is dtype
        assert ds.config == {}
        assert ds.description == ""

    def test_with_config_and_description(self):
        ds = DataSource(
            type=DataSourceType.api,
            config={"endpoint": "https://example.com"},
            description="Main API",
        )
        assert ds.config["endpoint"] == "https://example.com"
        assert ds.description == "Main API"


# ---------------------------------------------------------------------------
# ReviewRequirement
# ---------------------------------------------------------------------------


class TestReviewRequirement:
    def test_defaults(self):
        r = ReviewRequirement()
        assert r.max_iterations == 3
        assert "quality" in r.reviewer_prompt.lower()

    def test_max_iterations_bounds(self):
        ReviewRequirement(max_iterations=1)
        ReviewRequirement(max_iterations=20)
        with pytest.raises(ValidationError):
            ReviewRequirement(max_iterations=0)
        with pytest.raises(ValidationError):
            ReviewRequirement(max_iterations=21)


class TestLoopRequirement:
    def test_defaults(self):
        loop = LoopRequirement()
        assert loop.condition == "counter < 3"
        assert loop.max_iterations == 10
        assert loop.state_defaults == {"counter": 0}


# ---------------------------------------------------------------------------
# StageIntent
# ---------------------------------------------------------------------------


class TestStageIntent:
    def test_minimal(self):
        s = StageIntent(name="step")
        assert s.stage_type is StageType.transform
        assert s.inputs == []
        assert s.outputs == []
        assert s.review is None
        assert s.loop is None
        assert s.parallelism == 1

    def test_review_loop_requires_review(self):
        with pytest.raises(ValidationError, match="review_loop.*ReviewRequirement"):
            StageIntent(name="bad", stage_type=StageType.review_loop)

    def test_review_loop_with_review_passes(self):
        s = StageIntent(
            name="rev",
            stage_type=StageType.review_loop,
            review=ReviewRequirement(),
        )
        assert s.review is not None

    def test_non_review_loop_ignores_review(self):
        s = StageIntent(
            name="t",
            stage_type=StageType.transform,
            review=ReviewRequirement(),
        )
        assert s.review is not None

    def test_loop_requires_loop_config(self):
        with pytest.raises(ValidationError, match="loop.*LoopRequirement"):
            StageIntent(name="counter", stage_type=StageType.loop)

    def test_loop_with_loop_config_passes(self):
        s = StageIntent(
            name="counter",
            stage_type=StageType.loop,
            loop=LoopRequirement(),
        )
        assert s.loop is not None


# ---------------------------------------------------------------------------
# WorkflowIntent — valid cases
# ---------------------------------------------------------------------------


class TestWorkflowIntentValid:
    def test_minimal(self):
        wi = WorkflowIntent(**_make_intent())
        assert wi.goal == "test workflow"
        assert len(wi.stages) == 1

    def test_multi_stage_pipeline(self):
        stages = [
            _make_stage("research", outputs=["findings"]),
            _make_stage("draft", inputs=["findings"], outputs=["article"]),
            _make_stage(
                "review",
                inputs=["article"],
                stage_type="review_loop",
                review={"reviewer_prompt": "Check facts", "condition": "score >= 7"},
            ),
        ]
        wi = WorkflowIntent(**_make_intent(stages=stages))
        assert len(wi.stages) == 3
        assert wi.stages[2].review is not None

    def test_global_inputs_referenced_by_stages(self):
        stages = [_make_stage("s1", inputs=["user_query"])]
        wi = WorkflowIntent(**_make_intent(stages=stages, global_inputs=["user_query"]))
        assert wi.global_inputs == ["user_query"]

    def test_stage_referencing_another_stage_name(self):
        stages = [
            _make_stage("producer", outputs=["data"]),
            _make_stage("consumer", inputs=["producer"]),
        ]
        WorkflowIntent(**_make_intent(stages=stages))

    def test_constraints_and_data_sources(self):
        wi = WorkflowIntent(
            **_make_intent(
                data_sources=[{"type": "file", "config": {"path": "data.csv"}}],
                constraints={"max_cost": 5.0},
            )
        )
        assert len(wi.data_sources) == 1
        assert wi.constraints["max_cost"] == 5.0


# ---------------------------------------------------------------------------
# WorkflowIntent — invalid cases
# ---------------------------------------------------------------------------


class TestWorkflowIntentInvalid:
    def test_empty_stages_raises(self):
        with pytest.raises(ValidationError):
            WorkflowIntent(goal="empty", stages=[])

    def test_duplicate_stage_names_raises(self):
        stages = [_make_stage("dup"), _make_stage("dup")]
        with pytest.raises(ValidationError, match="Duplicate stage name.*dup"):
            WorkflowIntent(**_make_intent(stages=stages))

    def test_circular_dependency_raises(self):
        stages = [
            _make_stage("a", inputs=["b"], outputs=["ao"]),
            _make_stage("b", inputs=["a"], outputs=["bo"]),
        ]
        with pytest.raises(ValidationError, match="[Cc]ircular"):
            WorkflowIntent(**_make_intent(stages=stages))

    def test_unknown_input_reference_raises(self):
        stages = [_make_stage("s1", inputs=["nonexistent"])]
        with pytest.raises(ValidationError, match="not a global_input"):
            WorkflowIntent(**_make_intent(stages=stages))

    def test_missing_goal_raises(self):
        with pytest.raises(ValidationError):
            WorkflowIntent(stages=[StageIntent(name="s")])  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# JSON Schema export
# ---------------------------------------------------------------------------


class TestJsonSchema:
    def test_produces_valid_schema(self):
        schema = WorkflowIntent.to_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "goal" in schema["properties"]
        assert "stages" in schema["properties"]

    def test_required_fields_present(self):
        schema = WorkflowIntent.to_json_schema()
        required = schema.get("required", [])
        assert "goal" in required
        assert "stages" in required

    def test_stage_type_enum_in_schema(self):
        schema = WorkflowIntent.to_json_schema()
        defs = schema.get("$defs", schema.get("definitions", {}))
        assert "StageType" in defs
        stage_type_def = defs["StageType"]
        enum_vals = stage_type_def.get("enum", [])
        assert len(enum_vals) == 9
        assert "transform" in enum_vals

    def test_round_trip_via_schema(self):
        """Validate that a dict conforming to the schema deserialises cleanly."""
        intent_data = _make_intent(
            stages=[_make_stage("a", outputs=["x"]), _make_stage("b", inputs=["x"])],
            global_inputs=["seed"],
        )
        wi = WorkflowIntent.model_validate(intent_data)
        dumped = wi.model_dump()
        restored = WorkflowIntent.model_validate(dumped)
        assert restored == wi
