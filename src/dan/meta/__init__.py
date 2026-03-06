"""dan.meta — autonomous workflow planning, execution, and graduated repair."""

from dan.meta.architect import (
    RoutingConfig,
    SystemArchitect,
    SystemManifest,
    SystemPlan,
    SystemValidationResult,
    WorkflowSpec,
)
from dan.meta.authoring import RuntimeAuthor, SkillSpec, ToolSpec, ToolTestResult, ValidationResult
from dan.meta.controller import MetaController, MetaControllerConfig, MetaSession, MetaSessionStatus
from dan.meta.diagnosis import (
    ArtifactMapper,
    ArtifactType,
    AutoFixApplier,
    CorrectionStrategy,
    CorrectionStrategySelector,
    DiagnosisAttempt,
    DiagnosisLoop,
    DiagnosisMetrics,
    DiagnosisResult,
    ErrorArtifact,
    ErrorClassifier,
    GenerationError,
    GenerationErrorType,
    GenerationStage,
    RePromptComposer,
)
from dan.meta.discovery import DiscoveryService, DiscoveryResult
from dan.meta.intent_compiler import COVERAGE_CATALOG, CoverageChecker, CoverageResult, IntentCompiler
from dan.meta.intent_extraction import (
    INTENT_EXTRACTION_SYSTEM_PROMPT,
    INTENT_FEW_SHOT_EXAMPLES,
    build_intent_tool_schema,
)
from dan.meta.intent_schema import StageIntent, StageType, WorkflowIntent
from dan.meta.planner import CodegenDiagnostics, WorkflowPlanner, PlannerOutput
from dan.meta.repair import RepairEscalator, RepairLevel
from dan.meta.self_knowledge import SelfKnowledgeIndex, RetrievedChunk

__all__ = [
    "ArtifactMapper",
    "ArtifactType",
    "AutoFixApplier",
    "CorrectionStrategy",
    "CorrectionStrategySelector",
    "CodegenDiagnostics",
    "COVERAGE_CATALOG",
    "CoverageChecker",
    "CoverageResult",
    "DiagnosisAttempt",
    "DiagnosisLoop",
    "DiagnosisMetrics",
    "DiagnosisResult",
    "DiscoveryResult",
    "DiscoveryService",
    "ErrorArtifact",
    "ErrorClassifier",
    "GenerationError",
    "GenerationErrorType",
    "GenerationStage",
    "INTENT_EXTRACTION_SYSTEM_PROMPT",
    "INTENT_FEW_SHOT_EXAMPLES",
    "IntentCompiler",
    "RePromptComposer",
    "MetaController",
    "MetaControllerConfig",
    "MetaSession",
    "MetaSessionStatus",
    "PlannerOutput",
    "RepairEscalator",
    "RepairLevel",
    "RetrievedChunk",
    "RoutingConfig",
    "RuntimeAuthor",
    "SelfKnowledgeIndex",
    "SkillSpec",
    "StageIntent",
    "StageType",
    "SystemArchitect",
    "SystemManifest",
    "SystemPlan",
    "SystemValidationResult",
    "ToolTestResult",
    "ToolSpec",
    "ValidationResult",
    "WorkflowIntent",
    "WorkflowPlanner",
    "build_intent_tool_schema",
    "WorkflowSpec",
]
