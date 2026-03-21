"""Generation-time error extraction and classification.

Parses raw errors from sandbox execution, builder compilation, and graph
validation into a uniform ``GenerationError`` model.  This handles
*generation-time* failures only — runtime failures are handled by
``meta/repair.py``.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from dan.executors.code import _ALLOWED_BUILTINS
from dan.sandbox import SandboxResult

logger = logging.getLogger(__name__)


def generation_repair_attempt_budget(default: int = 4) -> int:
    """Return the bounded repair budget for workflow generation retries."""
    raw = os.environ.get("DAN_GENERATION_REPAIR_ATTEMPTS", str(default)).strip()
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(1, min(parsed, 5))


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GenerationStage(str, Enum):
    sandbox = "sandbox"
    build = "build"
    validation = "validation"


class GenerationErrorType(str, Enum):
    syntax_error = "syntax_error"
    import_error = "import_error"
    runtime_error = "runtime_error"
    no_output = "no_output"  # sandbox returned None (timeout, crash, no graph)
    name_error = "name_error"
    build_error = "build_error"
    port_conflict = "port_conflict"
    missing_edge_target = "missing_edge_target"
    duplicate_node = "duplicate_node"
    schema_mismatch = "schema_mismatch"
    reachability = "reachability"
    missing_port = "missing_port"
    edge_endpoint = "edge_endpoint"
    cycle = "cycle"
    hyperedge = "hyperedge"
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------


class GenerationError(BaseModel):
    """Structured representation of a generation-time failure."""

    stage: GenerationStage
    error_type: GenerationErrorType
    message: str
    source_line: int | None = None
    artifact_id: str | None = None
    recoverable: bool = True


# ---------------------------------------------------------------------------
# Regex patterns for Python traceback parsing
# ---------------------------------------------------------------------------

_RE_TB_LINE = re.compile(r'File ".*?", line (\d+)')
_RE_EXCEPTION = re.compile(r"^(\w+(?:Error|Exception|Warning))\b", re.MULTILINE)

_SANDBOX_EXCEPTION_MAP: dict[str, GenerationErrorType] = {
    "SyntaxError": GenerationErrorType.syntax_error,
    "IndentationError": GenerationErrorType.syntax_error,
    "TabError": GenerationErrorType.syntax_error,
    "ModuleNotFoundError": GenerationErrorType.import_error,
    "ImportError": GenerationErrorType.import_error,
    "NameError": GenerationErrorType.name_error,
}

_SANDBOX_RECOVERABLE: frozenset[GenerationErrorType] = frozenset({
    GenerationErrorType.syntax_error,
    GenerationErrorType.import_error,
    GenerationErrorType.name_error,
})

# ---------------------------------------------------------------------------
# Keyword rules for build-error classification
# ---------------------------------------------------------------------------

_BUILD_RULES: list[tuple[list[str], GenerationErrorType]] = [
    (["duplicate"], GenerationErrorType.duplicate_node),
    (["port"], GenerationErrorType.port_conflict),
    (["schema"], GenerationErrorType.schema_mismatch),
    (["edge", "target"], GenerationErrorType.missing_edge_target),
    (["edge"], GenerationErrorType.missing_edge_target),
    (["context_key", "missing"], GenerationErrorType.build_error),
    (["unknown node_type"], GenerationErrorType.build_error),
    (["unknown edge_type"], GenerationErrorType.build_error),
]

# ---------------------------------------------------------------------------
# Keyword rules for validation-error classification
# ---------------------------------------------------------------------------

_VALIDATION_RULES: list[tuple[re.Pattern[str], GenerationErrorType, bool]] = [
    (re.compile(r"unreachable|orphan", re.IGNORECASE), GenerationErrorType.reachability, True),
    (re.compile(r"required input port.*not connected", re.IGNORECASE), GenerationErrorType.missing_port, True),
    (re.compile(r"has no (output|input) port", re.IGNORECASE), GenerationErrorType.edge_endpoint, True),
    (re.compile(r"source node.*not found|target node.*not found", re.IGNORECASE), GenerationErrorType.edge_endpoint, True),
    (re.compile(r"entry point.*does not match", re.IGNORECASE), GenerationErrorType.edge_endpoint, True),
    (re.compile(r"cycle", re.IGNORECASE), GenerationErrorType.cycle, False),
    (re.compile(r"hyperedge", re.IGNORECASE), GenerationErrorType.hyperedge, True),
    (re.compile(r"schema", re.IGNORECASE), GenerationErrorType.schema_mismatch, True),
    (re.compile(r"sub-graph.*not in", re.IGNORECASE), GenerationErrorType.missing_edge_target, True),
    (re.compile(r"context", re.IGNORECASE), GenerationErrorType.build_error, True),
]


# ---------------------------------------------------------------------------
# ErrorClassifier
# ---------------------------------------------------------------------------


class ErrorClassifier:
    """Parses raw errors from sandbox/build/validation into GenerationError list."""

    @staticmethod
    def _match_sandbox_error_type(error_type_name: str | None) -> str:
        """Map a Python exception name to a ``GenerationErrorType`` value string."""
        if not error_type_name:
            return GenerationErrorType.runtime_error.value
        matched = _SANDBOX_EXCEPTION_MAP.get(error_type_name)
        return matched.value if matched else GenerationErrorType.runtime_error.value

    # -- Sandbox ------------------------------------------------------------

    def classify_sandbox_result(
        self, result: SandboxResult, code: str = ""
    ) -> list[GenerationError]:
        """Parse ``SandboxResult`` stderr for Python errors."""
        if result.exit_code == 0:
            return []

        stderr = result.stderr.strip()
        if not stderr:
            if result.exit_code != 0:
                return [
                    GenerationError(
                        stage=GenerationStage.sandbox,
                        error_type=GenerationErrorType.runtime_error,
                        message=f"Process exited with code {result.exit_code} (no stderr)",
                        recoverable=False,
                    )
                ]
            return []

        line_matches = _RE_TB_LINE.findall(stderr)
        source_line: int | None = int(line_matches[-1]) if line_matches else None

        exc_matches = _RE_EXCEPTION.findall(stderr)
        exc_name = exc_matches[-1] if exc_matches else None

        error_type = _SANDBOX_EXCEPTION_MAP.get(
            exc_name or "", GenerationErrorType.runtime_error
        )
        recoverable = error_type in _SANDBOX_RECOVERABLE

        last_line = stderr.rstrip().rsplit("\n", 1)[-1]

        return [
            GenerationError(
                stage=GenerationStage.sandbox,
                error_type=error_type,
                message=last_line,
                source_line=source_line,
                recoverable=recoverable,
            )
        ]

    # -- Build --------------------------------------------------------------

    def classify_build_errors(self, errors: list[str]) -> list[GenerationError]:
        """Parse ``BuildError`` error strings."""
        result: list[GenerationError] = []
        for msg in errors:
            error_type = self._match_build_rule(msg)
            result.append(
                GenerationError(
                    stage=GenerationStage.build,
                    error_type=error_type,
                    message=msg,
                    artifact_id=self._extract_artifact_id(msg),
                    recoverable=True,
                )
            )
        return result

    @staticmethod
    def _match_build_rule(msg: str) -> GenerationErrorType:
        lower = msg.lower()
        for keywords, etype in _BUILD_RULES:
            if all(kw in lower for kw in keywords):
                return etype
        return GenerationErrorType.build_error

    @staticmethod
    def _extract_artifact_id(msg: str) -> str | None:
        m = _RE_QUOTED_IDS.search(msg)
        return m.group(1) if m else None

    # -- Validation ---------------------------------------------------------

    def classify_validation_errors(self, errors: list[str]) -> list[GenerationError]:
        """Parse ``validate_graph()`` error strings."""
        result: list[GenerationError] = []
        for msg in errors:
            error_type, recoverable = self._match_validation_rule(msg)
            result.append(
                GenerationError(
                    stage=GenerationStage.validation,
                    error_type=error_type,
                    message=msg,
                    artifact_id=self._extract_artifact_id(msg),
                    recoverable=recoverable,
                )
            )
        return result

    @staticmethod
    def _match_validation_rule(msg: str) -> tuple[GenerationErrorType, bool]:
        for pattern, etype, recoverable in _VALIDATION_RULES:
            if pattern.search(msg):
                return etype, recoverable
        return GenerationErrorType.unknown, True

    # -- Unified entry point ------------------------------------------------

    def classify(self, raw_error: Any) -> list[GenerationError]:
        """Single entry point.

        Accepts ``SandboxResult``, ``list[str]`` (build or validation
        errors — dispatched via heuristic), or a plain ``str``.
        """
        if isinstance(raw_error, SandboxResult):
            return self.classify_sandbox_result(raw_error)

        if isinstance(raw_error, list):
            return self._classify_string_list(raw_error)

        if isinstance(raw_error, str):
            return self._classify_string_list([raw_error])

        return [
            GenerationError(
                stage=GenerationStage.build,
                error_type=GenerationErrorType.unknown,
                message=str(raw_error),
            )
        ]

    def _classify_string_list(self, errors: list[str]) -> list[GenerationError]:
        """Heuristic dispatch: if the error looks like a validation message
        (references nodes/edges/graph structure), route to validation;
        otherwise treat as build error.
        """
        build_msgs: list[str] = []
        validation_msgs: list[str] = []
        for msg in errors:
            if self._looks_like_validation(msg):
                validation_msgs.append(msg)
            else:
                build_msgs.append(msg)

        result: list[GenerationError] = []
        if build_msgs:
            result.extend(self.classify_build_errors(build_msgs))
        if validation_msgs:
            result.extend(self.classify_validation_errors(validation_msgs))
        return result

    @staticmethod
    def _looks_like_validation(msg: str) -> bool:
        lower = msg.lower()
        validation_signals = (
            "unreachable",
            "entry point",
            "exit point",
            "required input port",
            "not connected",
            "not found",
            "has no output port",
            "has no input port",
            "cycle",
            "hyperedge",
            "sub-graph",
            "context key",
            "schema incompatibility",
            "schema safety bypassed",
            "orphan",
        )
        return any(sig in lower for sig in validation_signals)


# ---------------------------------------------------------------------------
# Artifact mapping
# ---------------------------------------------------------------------------


class ArtifactType(str, Enum):
    code_line = "code_line"
    node = "node"
    edge = "edge"
    port = "port"
    config = "config"


class ErrorArtifact(BaseModel):
    """Smallest responsible artifact for a generation-time failure."""

    artifact_type: ArtifactType
    artifact_id: str
    context: str = ""


_RE_NODE_PORT = re.compile(
    r"""port\s+['"]([^'"]+)['"]\s+(?:on\s+)?node\s+['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_RE_NODE_INPUT_PORT = re.compile(
    r"""Node\s+['"]([^'"]+)['"]\s+.*?(?:input|output)\s+port\s+['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_RE_QUOTED_IDS = re.compile(r"""['"]([^'"]+)['"]""")
_RE_ANY_QUOTED_IDS = re.compile(r"""['"]([^'"]+)['"]""")
_RE_HAS_NO_PORT = re.compile(
    r"""has no (?:output|input) port\s+['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_RE_PORT_ON_NODE = re.compile(
    r"""port\s+['"]([^'"]+)['"]\s+on\s+node\s+['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_RE_GENERIC_PORT = re.compile(
    r"""(?:required\s+input|input|output)\s+port\s+['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_RE_AVAILABLE_PORTS = re.compile(
    r"available(?:\s+ports)?\s*:\s*\[([^\]]*)\]",
    re.IGNORECASE,
)
_RE_NODE_TYPE = re.compile(r"""node[_\s]type\s+['"]([^'"]+)['"]""", re.IGNORECASE)


class ArtifactMapper:
    """Maps GenerationError to the smallest responsible artifact."""

    def map_to_artifact(
        self,
        error: GenerationError,
        code: str | None = None,
        graph_dict: dict | None = None,
    ) -> ErrorArtifact | None:
        """Map error to artifact.  Prioritises source code over graph."""
        if error.stage == GenerationStage.sandbox:
            return self._map_sandbox(error, code)
        elif error.stage == GenerationStage.build:
            return self._map_build(error)
        else:
            return self._map_validation(error)

    # -- Sandbox ---------------------------------------------------------------

    def _map_sandbox(
        self, error: GenerationError, code: str | None
    ) -> ErrorArtifact | None:
        if error.source_line is None:
            return None
        context = (
            self._extract_surrounding_lines(code, error.source_line)
            if code
            else ""
        )
        return ErrorArtifact(
            artifact_type=ArtifactType.code_line,
            artifact_id=str(error.source_line),
            context=context,
        )

    # -- Build -----------------------------------------------------------------

    def _map_build(self, error: GenerationError) -> ErrorArtifact | None:
        msg = error.message

        if error.error_type == GenerationErrorType.port_conflict:
            m = _RE_NODE_PORT.search(msg)
            if m:
                port_name, node_id = m.group(1), m.group(2)
                return ErrorArtifact(
                    artifact_type=ArtifactType.port,
                    artifact_id=f"{node_id}:{port_name}",
                    context=msg,
                )

        if error.error_type == GenerationErrorType.missing_edge_target:
            if error.artifact_id:
                return ErrorArtifact(
                    artifact_type=ArtifactType.edge,
                    artifact_id=error.artifact_id,
                    context=msg,
                )

        if error.error_type == GenerationErrorType.duplicate_node:
            if error.artifact_id:
                return ErrorArtifact(
                    artifact_type=ArtifactType.node,
                    artifact_id=error.artifact_id,
                    context=msg,
                )

        if error.artifact_id:
            return ErrorArtifact(
                artifact_type=ArtifactType.node,
                artifact_id=error.artifact_id,
                context=msg,
            )
        return None

    # -- Validation ------------------------------------------------------------

    def _map_validation(self, error: GenerationError) -> ErrorArtifact | None:
        msg = error.message

        if error.error_type == GenerationErrorType.reachability:
            if error.artifact_id:
                return ErrorArtifact(
                    artifact_type=ArtifactType.node,
                    artifact_id=error.artifact_id,
                    context=msg,
                )

        if error.error_type == GenerationErrorType.cycle:
            ids = _RE_QUOTED_IDS.findall(msg)
            artifact_id = ",".join(ids) if ids else (error.artifact_id or "")
            if artifact_id:
                return ErrorArtifact(
                    artifact_type=ArtifactType.node,
                    artifact_id=artifact_id,
                    context=msg,
                )

        if error.error_type == GenerationErrorType.missing_port:
            m = _RE_NODE_INPUT_PORT.search(msg)
            if m:
                node_id, port_name = m.group(1), m.group(2)
                return ErrorArtifact(
                    artifact_type=ArtifactType.port,
                    artifact_id=f"{node_id}:{port_name}",
                    context=msg,
                )

        if error.error_type == GenerationErrorType.edge_endpoint:
            if error.artifact_id:
                return ErrorArtifact(
                    artifact_type=ArtifactType.edge,
                    artifact_id=error.artifact_id,
                    context=msg,
                )

        if error.artifact_id:
            return ErrorArtifact(
                artifact_type=ArtifactType.node,
                artifact_id=error.artifact_id,
                context=msg,
            )
        return None

    # -- Helpers ---------------------------------------------------------------

    @staticmethod
    def _extract_surrounding_lines(
        code: str | None, line: int, radius: int = 3
    ) -> str:
        """Return *radius* lines before and after 1-indexed *line*."""
        if not code:
            return ""
        lines = code.splitlines()
        start = max(0, line - 1 - radius)
        end = min(len(lines), line + radius)
        return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Correction strategies
# ---------------------------------------------------------------------------


class CorrectionStrategy(str, Enum):
    auto_fix = "auto_fix"
    re_prompt = "re_prompt"
    suggest_to_user = "suggest_to_user"


class CorrectionStrategySelector:
    """Select the right correction strategy for an error+artifact pair."""

    SAFE_IMPORTS: frozenset[str] = frozenset(
        {"dan.builder", "dan.models", "dan.engine"}
    )

    def select(
        self, error: GenerationError, artifact: ErrorArtifact | None
    ) -> CorrectionStrategy:
        """Pick strategy.  auto_fix > re_prompt > suggest_to_user."""
        if error.error_type == GenerationErrorType.import_error:
            if any(mod in error.message for mod in self.SAFE_IMPORTS):
                return CorrectionStrategy.auto_fix
            return CorrectionStrategy.re_prompt

        if error.error_type == GenerationErrorType.syntax_error:
            return CorrectionStrategy.re_prompt

        if error.error_type in (
            GenerationErrorType.port_conflict,
            GenerationErrorType.missing_port,
        ):
            if self._can_auto_fix_port_error(error.message):
                return CorrectionStrategy.auto_fix
            return CorrectionStrategy.re_prompt

        if error.error_type == GenerationErrorType.edge_endpoint:
            if self._can_auto_fix_port_error(error.message):
                return CorrectionStrategy.auto_fix
            return CorrectionStrategy.re_prompt

        if error.error_type == GenerationErrorType.reachability:
            return CorrectionStrategy.re_prompt

        if error.error_type == GenerationErrorType.cycle:
            return CorrectionStrategy.suggest_to_user

        return CorrectionStrategy.re_prompt

    @staticmethod
    def _can_auto_fix_port_error(message: str) -> bool:
        """Return True only when we can deterministically infer a port rename."""
        lower = message.lower()
        return (
            "available:" in lower
            or "available ports:" in lower
            or "conflicts with existing port" in lower
            or ("port '" in lower and " on node '" in lower and " conflict" in lower)
            or "has no output port" in lower
            or "has no input port" in lower
        )


class AutoFixApplier:
    """Apply deterministic fixes to code without LLM."""

    SAFE_IMPORT_LINES: dict[str, str] = {
        "dan.builder": "from dan.builder import workflow",
        "dan.models": "from dan.models.graph import Graph",
        "dan.engine": "from dan.engine import Engine",
    }

    def fix_missing_import(self, code: str, module: str) -> str | None:
        """Add import line if *module* is on the allowlist.

        Returns the fixed code or ``None`` if the module is not allowlisted
        or the import is already present.
        """
        for key, import_line in self.SAFE_IMPORT_LINES.items():
            if key in module:
                if import_line in code:
                    return None
                return import_line + "\n" + code
        return None

    def fix_port_name(
        self, code: str, wrong_port: str, correct_port: str
    ) -> str | None:
        """Replace a wrong port name in known port-wiring contexts only."""
        if wrong_port not in code:
            return None
        escaped = re.escape(wrong_port)
        context_patterns = (
            # NodeRef.port("name")
            re.compile(
                rf"(?P<prefix>\.port\(\s*)(?P<quote>['\"]){escaped}"
                rf"(?P=quote)(?P<suffix>\s*\))"
            ),
            # source_port="name", target_port="name"
            re.compile(
                rf"(?P<prefix>\b(?:source_port|target_port)\s*=\s*)"
                rf"(?P<quote>['\"]){escaped}(?P=quote)"
                rf"(?P<suffix>\s*(?:,|\)|\]|\}}))"
            ),
            # {"source_port": "name"} or {"target_port": "name"}
            re.compile(
                rf"(?P<prefix>['\"](?:source_port|target_port)['\"]\s*:\s*)"
                rf"(?P<quote>['\"]){escaped}(?P=quote)"
                rf"(?P<suffix>\s*(?:,|\)|\]|\}}))"
            ),
            # wf.connect(<node>, <source_port>, ...)
            re.compile(
                rf"(?P<prefix>\.connect\(\s*[^,]+,\s*)(?P<quote>['\"]){escaped}"
                rf"(?P=quote)(?P<suffix>\s*,)",
                re.DOTALL,
            ),
            # wf.connect(..., <target_port>)
            re.compile(
                rf"(?P<prefix>\.connect\(\s*[^,]+,\s*['\"][^'\"]+['\"]\s*,\s*[^,]+,\s*)"
                rf"(?P<quote>['\"]){escaped}(?P=quote)"
                rf"(?P<suffix>\s*(?:,|\)))",
                re.DOTALL,
            ),
        )

        fixed_code = code
        replaced_total = 0

        def _replace(m: re.Match[str]) -> str:
            quote = m.group("quote")
            return (
                f"{m.group('prefix')}{quote}{correct_port}{quote}{m.group('suffix')}"
            )

        for pattern in context_patterns:
            fixed_code, replaced = pattern.subn(_replace, fixed_code)
            replaced_total += replaced

        return fixed_code if replaced_total > 0 else None


class RePromptComposer:
    """Compose focused error context for LLM re-prompt."""

    def compose(
        self,
        error: GenerationError,
        artifact: ErrorArtifact | None,
        original_code: str,
        goal: str,
        *,
        prior_attempts: list["DiagnosisAttempt"] | None = None,
        learning_points: list[str] | None = None,
        attempt_number: int | None = None,
        max_attempts: int | None = None,
    ) -> str:
        """Return a focused error message for the LLM.

        Includes: error type, message, relevant code snippet (not full
        code), and fix guidance.
        """
        parts: list[str] = []
        if attempt_number is not None and max_attempts is not None:
            parts.append(f"## Repair attempt {attempt_number}/{max_attempts}")
        parts.extend([
            f"## Error during {error.stage.value}",
            f"**Type:** {error.error_type.value}",
            f"**Message:** {error.message}",
        ])

        if artifact and artifact.context:
            parts.append(
                f"\n### Relevant snippet\n```\n{artifact.context}\n```"
            )
        elif error.source_line and original_code:
            snippet = ArtifactMapper._extract_surrounding_lines(
                original_code, error.source_line, radius=3
            )
            if snippet:
                parts.append(
                    f"\n### Relevant snippet (around line {error.source_line})\n"
                    f"```\n{snippet}\n```"
                )

        if learning_points:
            parts.append(
                "\n### Lessons from prior failed attempts\n"
                + "\n".join(f"- {point}" for point in learning_points[:6])
            )

        if prior_attempts:
            prior_lines: list[str] = []
            for attempt in prior_attempts[-3:]:
                strategy = attempt.strategy_used.value if attempt.strategy_used else "unknown"
                corrections = ", ".join(attempt.corrections_applied) or "no patch landed"
                prior_lines.append(
                    f"- Attempt {attempt.attempt_number}: strategy={strategy}, "
                    f"result={attempt.result}, corrections={corrections}"
                )
            if prior_lines:
                parts.append("\n### Prior attempts\n" + "\n".join(prior_lines))

        parts.append(f"\n### Goal\n{goal}")
        parts.append(
            "\nFix the error above and regenerate the code.  "
            "Only fix the error — do not change unrelated parts. "
            "Return only executable Python builder code with no markdown fences."
        )
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Diagnosis loop dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DiagnosisAttempt:
    attempt_number: int
    errors_found: list[GenerationError]
    corrections_applied: list[str]
    learning_points: list[str] = field(default_factory=list)
    strategy_used: CorrectionStrategy | None = None
    result: str = "pending"  # "fixed" | "failed" | "partial"


@dataclass
class DiagnosisResult:
    success: bool
    final_graph: dict | None = None
    final_code: str = ""
    attempts: list[DiagnosisAttempt] = field(default_factory=list)
    final_errors: list[GenerationError] | None = None


# ---------------------------------------------------------------------------
# DiagnosisLoop
# ---------------------------------------------------------------------------


class DiagnosisLoop:
    """Bounded repair loop for failed workflow generation.

    Runs only at generation time.  Tries auto-fixes first, then bounded
    LLM re-prompts with accumulated failure context.
    """

    def __init__(self, max_attempts: int | None = None):
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else generation_repair_attempt_budget()
        )
        self.classifier = ErrorClassifier()
        self.mapper = ArtifactMapper()
        self.selector = CorrectionStrategySelector()
        self.fixer = AutoFixApplier()
        self.composer = RePromptComposer()

    async def diagnose_and_repair(
        self,
        goal: str,
        generated_code: str,
        errors: list[GenerationError],
        sandbox_runner: Any = None,
        llm_complete: Any = None,
        graph_validator: Any = None,
    ) -> DiagnosisResult:
        """Run bounded diagnosis.  Returns :class:`DiagnosisResult`."""
        attempts: list[DiagnosisAttempt] = []
        current_code = generated_code
        current_errors = errors
        learned_points: list[str] = []

        for attempt_num in range(1, self.max_attempts + 1):
            attempt = DiagnosisAttempt(
                attempt_number=attempt_num,
                errors_found=list(current_errors),
                corrections_applied=[],
            )

            if not current_errors:
                graph_dict = await self._try_build(current_code, sandbox_runner)
                current_errors = self._validate_candidate_graph(
                    graph_dict,
                    graph_validator,
                )
                if not current_errors:
                    attempt.result = "fixed"
                    attempts.append(attempt)
                    return DiagnosisResult(
                        success=True,
                        final_graph=graph_dict,
                        final_code=current_code,
                        attempts=attempts,
                    )
                attempt.errors_found = list(current_errors)

            attempt.learning_points = self._extract_learning_points(current_errors)
            for point in attempt.learning_points:
                if point not in learned_points:
                    learned_points.append(point)

            _STRATEGY_PRIORITY = {
                CorrectionStrategy.auto_fix: 0,
                CorrectionStrategy.re_prompt: 1,
                CorrectionStrategy.suggest_to_user: 2,
            }
            best_strategy: CorrectionStrategy | None = None
            best_priority = 999
            for error in current_errors:
                artifact = self.mapper.map_to_artifact(error, code=current_code)
                strategy = self.selector.select(error, artifact)
                p = _STRATEGY_PRIORITY.get(strategy, 999)
                if p < best_priority:
                    best_strategy = strategy
                    best_priority = p

            attempt.strategy_used = best_strategy

            logger.debug(
                "diagnosis attempt=%d strategy=%s errors=%d",
                attempt_num,
                best_strategy,
                len(current_errors),
            )

            if best_strategy == CorrectionStrategy.auto_fix:
                fixed_code = self._apply_auto_fixes(current_code, current_errors)
                if fixed_code and fixed_code != current_code:
                    attempt.corrections_applied.append("auto-fix applied")
                    current_code = fixed_code
                    new_errors = await self._revalidate(current_code, sandbox_runner)
                    candidate_graph = None
                    if not new_errors:
                        candidate_graph = await self._try_build(
                            current_code, sandbox_runner
                        )
                        new_errors = self._validate_candidate_graph(
                            candidate_graph,
                            graph_validator,
                        )
                    if not new_errors:
                        attempt.result = "fixed"
                        attempts.append(attempt)
                        return DiagnosisResult(
                            success=True,
                            final_graph=candidate_graph,
                            final_code=current_code,
                            attempts=attempts,
                        )
                    current_errors = new_errors
                    attempt.result = "partial"
                else:
                    # Auto-fix was selected but no deterministic patch was
                    # possible for this concrete message; degrade to re-prompt.
                    if llm_complete:
                        best_strategy = CorrectionStrategy.re_prompt
                        attempt.strategy_used = best_strategy
                    else:
                        attempt.result = "failed"

            if (
                best_strategy == CorrectionStrategy.re_prompt
                and llm_complete
                and attempt.result == "pending"
            ):
                prompt = self.composer.compose(
                    current_errors[0],
                    self.mapper.map_to_artifact(current_errors[0], code=current_code),
                    current_code,
                    goal,
                    prior_attempts=attempts,
                    learning_points=learned_points,
                    attempt_number=attempt_num,
                    max_attempts=self.max_attempts,
                )
                try:
                    new_code = await llm_complete(
                        "Fix the following builder code error. "
                        "Return ONLY the corrected Python code.",
                        prompt,
                    )
                    if new_code and new_code.strip():
                        attempt.corrections_applied.append("LLM re-prompt fix")
                        current_code = self._normalize_llm_code(new_code)
                        new_errors = await self._revalidate(
                            current_code, sandbox_runner
                        )
                        candidate_graph = None
                        if not new_errors:
                            candidate_graph = await self._try_build(
                                current_code, sandbox_runner
                            )
                            new_errors = self._validate_candidate_graph(
                                candidate_graph,
                                graph_validator,
                            )
                        if not new_errors:
                            attempt.result = "fixed"
                            attempts.append(attempt)
                            return DiagnosisResult(
                                success=True,
                                final_graph=candidate_graph,
                                final_code=current_code,
                                attempts=attempts,
                            )
                        current_errors = new_errors
                        attempt.result = "partial"
                    else:
                        attempt.result = "failed"
                except Exception:
                    logger.exception("LLM re-prompt failed on attempt %d", attempt_num)
                    attempt.result = "failed"

            if attempt.result == "pending":
                attempt.result = "failed"

            attempts.append(attempt)

        return DiagnosisResult(
            success=False,
            final_code=current_code,
            attempts=attempts,
            final_errors=current_errors,
        )

    @staticmethod
    def _normalize_llm_code(text: str) -> str:
        """Strip markdown fences from repaired code when models add them."""
        match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
        return (match.group(1) if match else text).strip()

    @staticmethod
    def _extract_learning_points(
        errors: list[GenerationError],
    ) -> list[str]:
        """Convert repeated failures into compact constraints for the next retry."""
        points: list[str] = []
        for error in errors:
            if error.error_type == GenerationErrorType.syntax_error:
                points.append("Return executable Python builder code that parses cleanly.")
            elif error.error_type == GenerationErrorType.import_error:
                points.append("Use only supported DAN builder/runtime imports available in this repo.")
            elif error.error_type == GenerationErrorType.name_error:
                if "no graph variable found" in error.message.lower():
                    points.append("Assign the final .build() result to graph, wf, workflow, or g.")
                else:
                    points.append("Define every referenced symbol before it is used.")
            elif error.error_type in (
                GenerationErrorType.port_conflict,
                GenerationErrorType.missing_port,
                GenerationErrorType.edge_endpoint,
            ):
                points.append("Use declared port names only and connect matching input/output ports.")
            elif error.error_type == GenerationErrorType.duplicate_node:
                points.append("Keep every node id unique and update references consistently.")
            elif error.error_type == GenerationErrorType.reachability:
                points.append("Ensure every node is reachable from an entry point and contributes to an exit path.")
            elif error.error_type == GenerationErrorType.cycle:
                points.append("Avoid cycles unless using explicit loop constructs supported by DAN.")
            elif error.error_type in (
                GenerationErrorType.schema_mismatch,
                GenerationErrorType.build_error,
            ):
                points.append("Produce a workflow that compiles to a valid DAN graph schema.")
            elif error.error_type == GenerationErrorType.no_output:
                points.append("Make sure the code actually builds a workflow graph and returns it via graph/wf/workflow/g.")
        return list(dict.fromkeys(points))

    @staticmethod
    def _validate_candidate_graph(
        graph_dict: dict | None,
        graph_validator: Any = None,
    ) -> list[GenerationError]:
        """Run optional structural validation on a built graph candidate."""
        if graph_dict is None:
            return [
                GenerationError(
                    stage=GenerationStage.build,
                    error_type=GenerationErrorType.no_output,
                    message="Code executed but no workflow graph was produced",
                    recoverable=True,
                )
            ]
        if graph_validator is None:
            return []
        try:
            return list(graph_validator(graph_dict))
        except Exception as exc:
            logger.exception("Graph validator callback failed during diagnosis")
            return [
                GenerationError(
                    stage=GenerationStage.validation,
                    error_type=GenerationErrorType.unknown,
                    message=f"Graph validator callback failed: {exc}",
                    recoverable=False,
                )
            ]

    def _apply_auto_fixes(
        self, code: str, errors: list[GenerationError]
    ) -> str | None:
        """Apply all applicable auto-fixes from the allowlist."""
        fixed = code
        for error in errors:
            if error.error_type == GenerationErrorType.import_error:
                result = self.fixer.fix_missing_import(fixed, error.message)
                if result:
                    fixed = result
            elif error.error_type in (
                GenerationErrorType.port_conflict,
                GenerationErrorType.missing_port,
                GenerationErrorType.edge_endpoint,
            ):
                wrong, correct = self._guess_port_fix(error)
                if wrong and correct:
                    result = self.fixer.fix_port_name(fixed, wrong, correct)
                    if result:
                        fixed = result
        return fixed if fixed != code else None

    @staticmethod
    def _guess_port_fix(
        error: GenerationError,
    ) -> tuple[str | None, str | None]:
        """Extract wrong/correct port from error message heuristics.

        Prefers explicit ``available: [...]`` hints from validation/build errors,
        then falls back to node-type defaults when the message includes a
        ``node type '<type>'`` hint.
        """
        wrong = DiagnosisLoop._extract_wrong_port(error.message)
        if not wrong:
            return None, None

        available_ports = DiagnosisLoop._extract_available_ports(error.message)
        if available_ports:
            correct = DiagnosisLoop._choose_best_port_candidate(
                wrong, available_ports
            )
            if correct and correct != wrong:
                return wrong, correct

        node_type = DiagnosisLoop._extract_node_type(error.message)
        default_port = DiagnosisLoop._default_output_port_for_node(node_type)
        if default_port and default_port != wrong:
            return wrong, default_port

        return None, None

    @staticmethod
    def _extract_wrong_port(message: str) -> str | None:
        for pattern in (_RE_HAS_NO_PORT, _RE_PORT_ON_NODE, _RE_GENERIC_PORT):
            match = pattern.search(message)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _extract_available_ports(message: str) -> list[str]:
        match = _RE_AVAILABLE_PORTS.search(message)
        if not match:
            return []
        return _RE_ANY_QUOTED_IDS.findall(match.group(1))

    @staticmethod
    def _extract_node_type(message: str) -> str | None:
        match = _RE_NODE_TYPE.search(message)
        return match.group(1) if match else None

    @staticmethod
    def _default_output_port_for_node(node_type: str | None) -> str | None:
        if not node_type:
            return None
        try:
            from dan.builder.compiler import DEFAULT_OUTPUT_PORTS
        except Exception:
            return None
        return DEFAULT_OUTPUT_PORTS.get(node_type)

    @staticmethod
    def _choose_best_port_candidate(
        wrong: str, candidates: list[str]
    ) -> str | None:
        if not candidates:
            return None
        if wrong in candidates:
            return wrong

        synonym_map = {
            "output": "text",
            "response": "text",
            "answer": "text",
            "result": "text",
            "items": "results",
            "item": "result",
            "chunk": "chunks",
        }
        mapped = synonym_map.get(wrong.lower())
        if mapped and mapped in candidates:
            return mapped

        matches = difflib.get_close_matches(wrong, candidates, n=1, cutoff=0.6)
        if matches:
            return matches[0]

        # If validator provided exactly one candidate, treat it as deterministic.
        if len(candidates) == 1:
            return candidates[0]
        return None

    async def _revalidate(
        self, code: str, sandbox_runner: Any = None
    ) -> list[GenerationError]:
        """Re-run the code and check for errors.

        Uses the sandbox when available; falls back to a compile() check.
        """
        if sandbox_runner is not None:
            try:
                result = await sandbox_runner.run_code(code)
                return self.classifier.classify_sandbox_result(result, code)
            except Exception:
                pass

        try:
            compile(code, "<diagnosis>", "exec")
        except SyntaxError as e:
            return [
                GenerationError(
                    stage=GenerationStage.sandbox,
                    error_type=GenerationErrorType.syntax_error,
                    message=str(e),
                    source_line=e.lineno,
                )
            ]

        try:
            ns: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS}
            exec(code, ns)  # noqa: S102
            graph_obj = None
            for var_name in ("graph", "wf", "workflow", "g"):
                obj = ns.get(var_name)
                if obj is not None and hasattr(obj, "model_dump"):
                    graph_obj = obj
                    break
            if graph_obj is None:
                return [
                    GenerationError(
                        stage=GenerationStage.build,
                        error_type=GenerationErrorType.name_error,
                        message="Code executed but no graph variable found",
                        recoverable=False,
                    )
                ]
            return []
        except NameError as e:
            return [
                GenerationError(
                    stage=GenerationStage.sandbox,
                    error_type=GenerationErrorType.name_error,
                    message=str(e),
                )
            ]
        except ImportError as e:
            return [
                GenerationError(
                    stage=GenerationStage.sandbox,
                    error_type=GenerationErrorType.import_error,
                    message=str(e),
                )
            ]
        except Exception as e:
            return [
                GenerationError(
                    stage=GenerationStage.sandbox,
                    error_type=GenerationErrorType.runtime_error,
                    message=str(e),
                    recoverable=False,
                )
            ]

    async def _try_build(
        self, code: str, sandbox_runner: Any = None
    ) -> dict | None:
        """Try to execute the code and extract the graph dict.

        When *sandbox_runner* is provided, uses it for isolation; otherwise
        falls back to in-process ``exec()`` (acceptable only for
        deterministic intent-compiled code).
        """
        if sandbox_runner is not None:
            try:
                import pathlib, json as _json  # noqa: E401
                from dan.sandbox import SandboxConfig
                from dan.meta.planner import _BUILDER_CODE_HARNESS

                config = SandboxConfig(timeout_seconds=30, memory_mb=256)
                inputs = {
                    "user_code": code,
                    "src_path": str(
                        pathlib.Path(__file__).resolve().parents[2]
                    ),
                }
                result, structured = await sandbox_runner.run(
                    _BUILDER_CODE_HARNESS, config, inputs
                )
                if (
                    isinstance(structured, dict)
                    and "graph" in structured
                ):
                    return structured["graph"]
                return None
            except Exception:
                pass

        try:
            ns: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS}
            exec(code, ns)  # noqa: S102
            for var_name in ("graph", "wf", "workflow", "g"):
                if var_name in ns and hasattr(ns[var_name], "model_dump"):
                    return ns[var_name].model_dump(mode="json")
            return None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# DiagnosisMetrics
# ---------------------------------------------------------------------------


class DiagnosisMetrics:
    """Lightweight in-process counters for diagnosis observability."""

    def __init__(self) -> None:
        self.total_invocations: int = 0
        self.successes: int = 0
        self.failures: int = 0
        self.total_attempts: int = 0
        self.error_type_counts: dict[str, int] = {}
        self.strategy_counts: dict[str, int] = {}

    def record(self, result: DiagnosisResult) -> None:
        self.total_invocations += 1
        if result.success:
            self.successes += 1
        else:
            self.failures += 1
        self.total_attempts += len(result.attempts)
        for attempt in result.attempts:
            for error in attempt.errors_found:
                key = error.error_type.value
                self.error_type_counts[key] = self.error_type_counts.get(key, 0) + 1
            if attempt.strategy_used:
                key = attempt.strategy_used.value
                self.strategy_counts[key] = self.strategy_counts.get(key, 0) + 1

    @property
    def success_rate(self) -> float:
        return self.successes / self.total_invocations if self.total_invocations else 0.0
