"""Tests for Plan 24-4: Error extraction, classification, artifact mapping,
and correction strategies."""

from __future__ import annotations

import pytest

from dan.meta.diagnosis import (
    ArtifactMapper,
    ArtifactType,
    AutoFixApplier,
    CorrectionStrategy,
    CorrectionStrategySelector,
    DiagnosisLoop,
    ErrorArtifact,
    ErrorClassifier,
    GenerationError,
    GenerationErrorType,
    GenerationStage,
    RePromptComposer,
)
from dan.sandbox import SandboxResult


@pytest.fixture
def classifier() -> ErrorClassifier:
    return ErrorClassifier()


# ---------------------------------------------------------------------------
# Sandbox — syntax error
# ---------------------------------------------------------------------------


class TestSandboxSyntaxError:
    def test_syntax_error_extracted(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(
            exit_code=1,
            stderr=(
                'Traceback (most recent call last):\n'
                '  File "gen.py", line 12, in <module>\n'
                '    x = (1 +\n'
                '         ^\n'
                'SyntaxError: unexpected EOF while parsing'
            ),
        )
        errors = classifier.classify_sandbox_result(result)
        assert len(errors) == 1
        err = errors[0]
        assert err.stage == GenerationStage.sandbox
        assert err.error_type == GenerationErrorType.syntax_error
        assert err.source_line == 12
        assert err.recoverable is True

    def test_indentation_error_classified_as_syntax(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(
            exit_code=1,
            stderr=(
                '  File "gen.py", line 5\n'
                '    return x\n'
                '    ^\n'
                'IndentationError: unexpected indent'
            ),
        )
        errors = classifier.classify_sandbox_result(result)
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.syntax_error
        assert errors[0].source_line == 5
        assert errors[0].recoverable is True


# ---------------------------------------------------------------------------
# Sandbox — import error
# ---------------------------------------------------------------------------


class TestSandboxImportError:
    def test_module_not_found(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(
            exit_code=1,
            stderr=(
                'Traceback (most recent call last):\n'
                '  File "gen.py", line 1, in <module>\n'
                '    import nonexistent_pkg\n'
                "ModuleNotFoundError: No module named 'nonexistent_pkg'"
            ),
        )
        errors = classifier.classify_sandbox_result(result)
        assert len(errors) == 1
        err = errors[0]
        assert err.error_type == GenerationErrorType.import_error
        assert err.source_line == 1
        assert err.recoverable is True

    def test_import_error_generic(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(
            exit_code=1,
            stderr=(
                'Traceback (most recent call last):\n'
                '  File "gen.py", line 3, in <module>\n'
                '    from foo import bar\n'
                "ImportError: cannot import name 'bar' from 'foo'"
            ),
        )
        errors = classifier.classify_sandbox_result(result)
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.import_error


# ---------------------------------------------------------------------------
# Sandbox — name error
# ---------------------------------------------------------------------------


class TestSandboxNameError:
    def test_name_error(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(
            exit_code=1,
            stderr=(
                'Traceback (most recent call last):\n'
                '  File "gen.py", line 8, in <module>\n'
                '    print(undefined_var)\n'
                "NameError: name 'undefined_var' is not defined"
            ),
        )
        errors = classifier.classify_sandbox_result(result)
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.name_error
        assert errors[0].source_line == 8
        assert errors[0].recoverable is True


# ---------------------------------------------------------------------------
# Sandbox — runtime error
# ---------------------------------------------------------------------------


class TestSandboxRuntimeError:
    def test_type_error_is_runtime(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(
            exit_code=1,
            stderr=(
                'Traceback (most recent call last):\n'
                '  File "gen.py", line 10, in <module>\n'
                "    1 + 'a'\n"
                "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
            ),
        )
        errors = classifier.classify_sandbox_result(result)
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.runtime_error
        assert errors[0].recoverable is False

    def test_zero_division(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(
            exit_code=1,
            stderr=(
                'Traceback (most recent call last):\n'
                '  File "gen.py", line 4, in <module>\n'
                '    x = 1 / 0\n'
                'ZeroDivisionError: division by zero'
            ),
        )
        errors = classifier.classify_sandbox_result(result)
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.runtime_error


# ---------------------------------------------------------------------------
# Sandbox — success
# ---------------------------------------------------------------------------


class TestSandboxSuccess:
    def test_exit_code_zero_returns_empty(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(exit_code=0, stderr="", stdout="ok")
        errors = classifier.classify_sandbox_result(result)
        assert errors == []

    def test_exit_code_nonzero_empty_stderr(self, classifier: ErrorClassifier) -> None:
        result = SandboxResult(exit_code=137, stderr="")
        errors = classifier.classify_sandbox_result(result)
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.runtime_error
        assert "137" in errors[0].message


# ---------------------------------------------------------------------------
# Build errors
# ---------------------------------------------------------------------------


class TestBuildErrors:
    def test_duplicate_node(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_build_errors(["Duplicate node 'summarize' already exists"])
        assert len(errors) == 1
        err = errors[0]
        assert err.stage == GenerationStage.build
        assert err.error_type == GenerationErrorType.duplicate_node
        assert err.artifact_id == "summarize"
        assert err.recoverable is True

    def test_port_conflict(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_build_errors(["Port 'output' on node 'a' conflicts with existing port"])
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.port_conflict

    def test_schema_mismatch(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_build_errors(["Schema mismatch on edge 'e1': expected string, got int"])
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.schema_mismatch

    def test_unknown_node_type(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_build_errors(["Unknown node_type: 'fancy_node'"])
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.build_error
        assert errors[0].artifact_id == "fancy_node"

    def test_edge_target(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_build_errors(["Edge target 'missing_node' does not exist"])
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.missing_edge_target

    def test_multiple_build_errors(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_build_errors([
            "Duplicate node 'x'",
            "Port 'foo' conflict on node 'y'",
        ])
        assert len(errors) == 2
        assert errors[0].error_type == GenerationErrorType.duplicate_node
        assert errors[1].error_type == GenerationErrorType.port_conflict


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_reachability(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_validation_errors(
            ["Node 'orphan_node' is unreachable from any entry point"]
        )
        assert len(errors) == 1
        err = errors[0]
        assert err.stage == GenerationStage.validation
        assert err.error_type == GenerationErrorType.reachability
        assert err.artifact_id == "orphan_node"
        assert err.recoverable is True

    def test_cycle_not_recoverable(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_validation_errors(
            ["Data-edge cycle detected through non-loop node 'bad_node'"]
        )
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.cycle
        assert errors[0].recoverable is False

    def test_missing_port(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_validation_errors(
            ["Node 'llm1' has required input port 'query' that is not connected"]
        )
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.missing_port

    def test_edge_endpoint_source_not_found(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_validation_errors(
            ["Edge 'e1': source node 'ghost' not found"]
        )
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.edge_endpoint

    def test_edge_endpoint_no_port(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_validation_errors(
            ["Edge 'e2': source node 'A' has no output port 'missing' (available: ['text'])"]
        )
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.edge_endpoint

    def test_hyperedge(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_validation_errors(
            ["Warning: hyperedge 'he1' attach_to references non-existent node 'x'"]
        )
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.hyperedge

    def test_schema_warning(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify_validation_errors(
            ["Edge 'e1': schema incompatibility — type mismatch string vs int"]
        )
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.schema_mismatch


# ---------------------------------------------------------------------------
# classify() unified dispatch
# ---------------------------------------------------------------------------


class TestClassifyDispatch:
    def test_dispatch_sandbox_result(self, classifier: ErrorClassifier) -> None:
        sr = SandboxResult(
            exit_code=1,
            stderr=(
                '  File "gen.py", line 7\n'
                'SyntaxError: invalid syntax'
            ),
        )
        errors = classifier.classify(sr)
        assert len(errors) == 1
        assert errors[0].stage == GenerationStage.sandbox

    def test_dispatch_string_list_build(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify(["Unknown edge_type: 'fancy'"])
        assert len(errors) == 1
        assert errors[0].stage == GenerationStage.build

    def test_dispatch_string_list_validation(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify(["Node 'x' is unreachable from any entry point"])
        assert len(errors) == 1
        assert errors[0].stage == GenerationStage.validation

    def test_dispatch_plain_string(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify("Some random error")
        assert len(errors) == 1

    def test_dispatch_mixed_list(self, classifier: ErrorClassifier) -> None:
        errors = classifier.classify([
            "Duplicate node 'a'",
            "Node 'b' is unreachable from any entry point",
        ])
        assert len(errors) == 2
        stages = {e.stage for e in errors}
        assert GenerationStage.build in stages
        assert GenerationStage.validation in stages


# ---------------------------------------------------------------------------
# Model serialization
# ---------------------------------------------------------------------------


class TestModelSerialization:
    def test_generation_error_roundtrip(self) -> None:
        err = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.syntax_error,
            message="SyntaxError: invalid syntax",
            source_line=12,
            artifact_id="gen.py",
            recoverable=True,
        )
        data = err.model_dump()
        restored = GenerationError.model_validate(data)
        assert restored == err
        assert data["stage"] == "sandbox"
        assert data["error_type"] == "syntax_error"


# ===========================================================================
# Plan 24-4 Tasks 2-3: Artifact mapping & correction strategies
# ===========================================================================


# ---------------------------------------------------------------------------
# ArtifactMapper
# ---------------------------------------------------------------------------


class TestArtifactMapperSandbox:
    @pytest.fixture
    def mapper(self) -> ArtifactMapper:
        return ArtifactMapper()

    def test_syntax_error_maps_to_code_line(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.syntax_error,
            message="SyntaxError: unexpected EOF while parsing",
            source_line=5,
            recoverable=True,
        )
        code = "\n".join(f"line{i}" for i in range(1, 10))
        artifact = mapper.map_to_artifact(error, code=code)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.code_line
        assert artifact.artifact_id == "5"
        assert "line5" in artifact.context

    def test_import_error_maps_to_code_line(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.import_error,
            message="ModuleNotFoundError: No module named 'foo'",
            source_line=1,
        )
        code = "import foo\nprint('hi')"
        artifact = mapper.map_to_artifact(error, code=code)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.code_line
        assert artifact.artifact_id == "1"

    def test_no_source_line_returns_none(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.runtime_error,
            message="Process exited with code 1",
            source_line=None,
            recoverable=False,
        )
        assert mapper.map_to_artifact(error, code="x = 1") is None

    def test_surrounding_lines_context(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.name_error,
            message="NameError: name 'x' is not defined",
            source_line=4,
        )
        code = "\n".join(f"L{i}" for i in range(1, 9))
        artifact = mapper.map_to_artifact(error, code=code)
        assert artifact is not None
        assert "L1" in artifact.context
        assert "L4" in artifact.context
        assert "L7" in artifact.context


class TestArtifactMapperBuild:
    @pytest.fixture
    def mapper(self) -> ArtifactMapper:
        return ArtifactMapper()

    def test_port_conflict_maps_to_port(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.build,
            error_type=GenerationErrorType.port_conflict,
            message="Port 'output' on node 'summarize' conflicts with existing port",
            artifact_id="output",
        )
        artifact = mapper.map_to_artifact(error)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.port
        assert artifact.artifact_id == "summarize:output"

    def test_duplicate_node_maps_to_node(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.build,
            error_type=GenerationErrorType.duplicate_node,
            message="Duplicate node 'summarize' already exists",
            artifact_id="summarize",
        )
        artifact = mapper.map_to_artifact(error)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.node
        assert artifact.artifact_id == "summarize"

    def test_missing_edge_target_maps_to_edge(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.build,
            error_type=GenerationErrorType.missing_edge_target,
            message="Edge target 'ghost_node' does not exist",
            artifact_id="ghost_node",
        )
        artifact = mapper.map_to_artifact(error)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.edge
        assert artifact.artifact_id == "ghost_node"

    def test_fallback_to_node_with_artifact_id(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.build,
            error_type=GenerationErrorType.build_error,
            message="Unknown node_type: 'fancy_node'",
            artifact_id="fancy_node",
        )
        artifact = mapper.map_to_artifact(error)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.node


class TestArtifactMapperValidation:
    @pytest.fixture
    def mapper(self) -> ArtifactMapper:
        return ArtifactMapper()

    def test_reachability_maps_to_node(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.reachability,
            message="Node 'orphan_node' is unreachable from any entry point",
            artifact_id="orphan_node",
        )
        artifact = mapper.map_to_artifact(error)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.node
        assert artifact.artifact_id == "orphan_node"

    def test_cycle_maps_to_node_with_ids(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.cycle,
            message="Data-edge cycle detected: 'a' -> 'b' -> 'a'",
            artifact_id="a",
            recoverable=False,
        )
        artifact = mapper.map_to_artifact(error)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.node
        assert "a" in artifact.artifact_id
        assert "b" in artifact.artifact_id

    def test_missing_port_maps_to_port(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.missing_port,
            message="Node 'llm1' has required input port 'query' that is not connected",
            artifact_id="llm1",
        )
        artifact = mapper.map_to_artifact(error)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.port
        assert artifact.artifact_id == "llm1:query"

    def test_edge_endpoint_maps_to_edge(self, mapper: ArtifactMapper) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.edge_endpoint,
            message="Edge 'e1': source node 'ghost' not found",
            artifact_id="e1",
        )
        artifact = mapper.map_to_artifact(error)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.edge
        assert artifact.artifact_id == "e1"


# ---------------------------------------------------------------------------
# CorrectionStrategySelector
# ---------------------------------------------------------------------------


class TestCorrectionStrategySelector:
    @pytest.fixture
    def selector(self) -> CorrectionStrategySelector:
        return CorrectionStrategySelector()

    def test_import_error_dan_module_auto_fix(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.import_error,
            message="ModuleNotFoundError: No module named 'dan.builder'",
        )
        assert selector.select(error, None) == CorrectionStrategy.auto_fix

    def test_import_error_unknown_module_re_prompt(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.import_error,
            message="ModuleNotFoundError: No module named 'nonexistent_pkg'",
        )
        assert selector.select(error, None) == CorrectionStrategy.re_prompt

    def test_syntax_error_re_prompt(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.syntax_error,
            message="SyntaxError: invalid syntax",
        )
        assert selector.select(error, None) == CorrectionStrategy.re_prompt

    def test_cycle_suggest_to_user(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.cycle,
            message="Data-edge cycle detected",
        )
        assert selector.select(error, None) == CorrectionStrategy.suggest_to_user

    def test_port_conflict_auto_fix(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.build,
            error_type=GenerationErrorType.port_conflict,
            message="Port 'output' on node 'a' conflicts",
        )
        assert selector.select(error, None) == CorrectionStrategy.auto_fix

    def test_missing_port_not_connected_re_prompt(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.missing_port,
            message="Node 'llm1' has required input port 'query' not connected",
        )
        assert selector.select(error, None) == CorrectionStrategy.re_prompt

    def test_missing_port_with_available_ports_auto_fix(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.missing_port,
            message=(
                "Edge 'e2': source node 'A' has no output port 'outpt' "
                "(available: ['output', 'result'])"
            ),
        )
        assert selector.select(error, None) == CorrectionStrategy.auto_fix

    def test_edge_endpoint_with_available_ports_auto_fix(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.edge_endpoint,
            message=(
                "Edge 'e2': source node 'A' has no output port 'outpt' "
                "(available: ['output', 'result'])"
            ),
        )
        assert selector.select(error, None) == CorrectionStrategy.auto_fix

    def test_classifier_edge_endpoint_routes_to_auto_fix(
        self,
        classifier: ErrorClassifier,
        selector: CorrectionStrategySelector,
    ) -> None:
        errors = classifier.classify_validation_errors([
            (
                "Edge 'e2': source node 'A' has no output port 'outpt' "
                "(available: ['output', 'result'])"
            )
        ])
        assert len(errors) == 1
        assert errors[0].error_type == GenerationErrorType.edge_endpoint
        assert selector.select(errors[0], None) == CorrectionStrategy.auto_fix

    def test_reachability_re_prompt(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.reachability,
            message="Node 'x' is unreachable",
        )
        assert selector.select(error, None) == CorrectionStrategy.re_prompt

    def test_unknown_type_defaults_to_re_prompt(
        self, selector: CorrectionStrategySelector
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.build,
            error_type=GenerationErrorType.unknown,
            message="Something unexpected",
        )
        assert selector.select(error, None) == CorrectionStrategy.re_prompt


# ---------------------------------------------------------------------------
# AutoFixApplier
# ---------------------------------------------------------------------------


class TestAutoFixApplier:
    @pytest.fixture
    def applier(self) -> AutoFixApplier:
        return AutoFixApplier()

    def test_fix_missing_import_known_module(self, applier: AutoFixApplier) -> None:
        code = "wf = workflow('test', 'desc')"
        result = applier.fix_missing_import(code, "dan.builder")
        assert result is not None
        assert result.startswith("from dan.builder import workflow")
        assert code in result

    def test_fix_missing_import_unknown_module(self, applier: AutoFixApplier) -> None:
        result = applier.fix_missing_import("import xyz", "nonexistent_pkg")
        assert result is None

    def test_fix_missing_import_already_present(self, applier: AutoFixApplier) -> None:
        code = "from dan.builder import workflow\nwf = workflow('test', 'desc')"
        result = applier.fix_missing_import(code, "dan.builder")
        assert result is None

    def test_fix_missing_import_engine(self, applier: AutoFixApplier) -> None:
        code = "engine = Engine(graph)"
        result = applier.fix_missing_import(code, "dan.engine")
        assert result is not None
        assert "from dan.engine import Engine" in result

    def test_fix_port_name_replaces(self, applier: AutoFixApplier) -> None:
        code = 'a >> b.port("wrong_output")'
        result = applier.fix_port_name(code, "wrong_output", "text")
        assert result is not None
        assert "text" in result
        assert "wrong_output" not in result

    def test_fix_port_name_not_found(self, applier: AutoFixApplier) -> None:
        code = 'a >> b.port("correct")'
        result = applier.fix_port_name(code, "nonexistent", "text")
        assert result is None

    def test_fix_port_name_does_not_replace_unrelated_literals(
        self, applier: AutoFixApplier
    ) -> None:
        code = (
            'prompt = "wrong_output should stay in prompt"\n'
            'a >> b.port("wrong_output")\n'
        )
        result = applier.fix_port_name(code, "wrong_output", "text")
        assert result is not None
        assert 'prompt = "wrong_output should stay in prompt"' in result
        assert 'b.port("text")' in result

    def test_fix_port_name_rewrites_source_port_keyword(
        self, applier: AutoFixApplier
    ) -> None:
        code = "wf.connect('a', source_port='outpt', target_port='input')\n"
        result = applier.fix_port_name(code, "outpt", "output")
        assert result is not None
        assert "source_port='output'" in result


class TestDiagnosisLoopPortFixHeuristics:
    def test_guess_port_fix_from_available_ports(self) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.missing_port,
            message=(
                "Edge 'e2': source node 'A' has no output port 'outpt' "
                "(available: ['output', 'result'])"
            ),
        )
        wrong, correct = DiagnosisLoop._guess_port_fix(error)
        assert wrong == "outpt"
        assert correct == "output"

    def test_guess_port_fix_from_double_quoted_available_ports(self) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.missing_port,
            message=(
                'Edge "e2": source node "A" has no output port "outpt" '
                '(available: ["output", "result"])'
            ),
        )
        wrong, correct = DiagnosisLoop._guess_port_fix(error)
        assert wrong == "outpt"
        assert correct == "output"

    def test_apply_auto_fixes_rewrites_wrong_port(self) -> None:
        loop = DiagnosisLoop(max_attempts=1)
        code = "wf.connect('a', 'outpt', 'b', 'input')\n"
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.edge_endpoint,
            message=(
                "Edge 'e2': source node 'A' has no output port 'outpt' "
                "(available: ['output'])"
            ),
        )
        fixed = loop._apply_auto_fixes(code, [error])
        assert fixed is not None
        assert "'output'" in fixed
        assert "'outpt'" not in fixed

    def test_guess_port_fix_falls_back_to_node_type_default(self) -> None:
        error = GenerationError(
            stage=GenerationStage.build,
            error_type=GenerationErrorType.port_conflict,
            message="node type 'llm_operator' has no output port 'output'",
        )
        wrong, correct = DiagnosisLoop._guess_port_fix(error)
        assert wrong == "output"
        assert correct == "text"


# ---------------------------------------------------------------------------
# RePromptComposer
# ---------------------------------------------------------------------------


class TestRePromptComposer:
    @pytest.fixture
    def composer(self) -> RePromptComposer:
        return RePromptComposer()

    def test_includes_error_type_and_snippet(
        self, composer: RePromptComposer
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.syntax_error,
            message="SyntaxError: invalid syntax",
            source_line=3,
        )
        artifact = ErrorArtifact(
            artifact_type=ArtifactType.code_line,
            artifact_id="3",
            context="line2\nline3_broken\nline4",
        )
        prompt = composer.compose(
            error, artifact, "line1\nline2\nline3_broken\nline4", "build a pipeline"
        )
        assert "syntax_error" in prompt
        assert "SyntaxError" in prompt
        assert "line3_broken" in prompt
        assert "build a pipeline" in prompt

    def test_uses_source_line_when_no_artifact_context(
        self, composer: RePromptComposer
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.sandbox,
            error_type=GenerationErrorType.name_error,
            message="NameError: name 'x' is not defined",
            source_line=2,
        )
        code = "import os\nprint(x)\nprint('done')"
        prompt = composer.compose(error, None, code, "run script")
        assert "name_error" in prompt
        assert "print(x)" in prompt
        assert "run script" in prompt

    def test_includes_goal_and_fix_guidance(
        self, composer: RePromptComposer
    ) -> None:
        error = GenerationError(
            stage=GenerationStage.validation,
            error_type=GenerationErrorType.reachability,
            message="Node 'x' is unreachable",
        )
        prompt = composer.compose(error, None, "", "create a pipeline")
        assert "create a pipeline" in prompt
        assert "Fix the error" in prompt


# ---------------------------------------------------------------------------
# ErrorArtifact serialization
# ---------------------------------------------------------------------------


class TestErrorArtifactSerialization:
    def test_roundtrip(self) -> None:
        art = ErrorArtifact(
            artifact_type=ArtifactType.port,
            artifact_id="llm1:query",
            context="Node 'llm1' has required input port 'query'",
        )
        data = art.model_dump()
        restored = ErrorArtifact.model_validate(data)
        assert restored == art
        assert data["artifact_type"] == "port"
