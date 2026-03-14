"""Shared concierge runtime modules."""

from .command_registry import CommandDescriptor, CommandRegistry, SubcommandDescriptor, get_default_registry
from .dispatcher import ConcurrentDispatcher
from .fan_out import fan_out, fan_out_dict
from .identity import format_bare_prefix, format_prefix, get_bot_name, starts_with_prefix, strip_prefix
from .models import (
    ConciergeGoal,
    ConciergeState,
    IntentCategory,
    PendingAction,
    Project,
    ResolvedContext,
    RouteDecision,
    RouteMode,
    SurfaceMessage,
    Task,
    TaskTurn,
)
from .policy import (
    ActionPolicy,
    BehaviorPolicy,
    ClarificationRequest,
    ClarificationResponse,
    ExecutionPolicy,
    resolve_policy,
)
from .progress import ProgressReporter
from .project_store import ProjectStore
from .resources import MessagePriority, PriorityQueue, ResourceBudget, ResourceTracker, classify_priority
from .runtime import AutonomyLevel, Concierge, build_concierge
from .session import Session, SessionManager, SessionResult, SessionState, SessionTier, SessionTrace
from .triage import EntityRef, TriageResult, triage
from .tiered_dispatch import TieredDispatcher
from .tier_executors import InstantExecutor, MultiStepExecutor, SingleShotExecutor, TierExecutor
from .pii_tokenizer import (
    PIISession,
    SensitiveWord,
    SensitiveWordRegistry,
    TokenizingProviderWrapper,
    detokenize,
    handle_pii_command,
    is_pii_enabled,
    tokenize,
)
from .computer_policy import (
    ActionType,
    AuditEntry,
    AuditLog,
    BrowserDomainRule,
    ChunkPolicies,
    ChunkPolicy,
    ComputerControlConfig,
    SessionOverride,
    classify_action,
    is_app_allowed,
    is_domain_allowed,
    requires_approval,
)
from .computer_use import (
    ComputerUseController,
    ComputerUseLeaseManager,
    ObservedElement,
    UIObservation,
    handle_computer_command,
)
from .progress_ux import (
    CheckpointOption,
    CheckpointOptions,
    CLIProgressRenderer,
    EditorProgressRenderer,
    InteractionRequest,
    ProgressPhase,
    ProgressRenderer,
    ProgressSession,
    TelegramProgressRenderer,
    VerbosityLevel,
    WhatsAppProgressRenderer,
    generate_preflight_questions,
    handle_progress_command,
    resolve_verbosity,
)
from .scheduler import (
    DeliveryTarget,
    ScheduleEntry,
    ScheduleHistoryStore,
    ScheduleRunRecord,
    ScheduleStore,
    TaskScheduler,
    TriggerContext,
    compute_next_run,
    handle_schedule_command,
    parse_schedule_add,
    parse_trigger,
)

__all__ = [
    # models
    "ConciergeGoal",
    "ConciergeState",
    "IntentCategory",
    "PendingAction",
    "Project",
    "ResolvedContext",
    "RouteDecision",
    "RouteMode",
    "SurfaceMessage",
    "Task",
    "TaskTurn",
    # project_store
    "ProjectStore",
    # command_registry
    "CommandDescriptor",
    "CommandRegistry",
    "SubcommandDescriptor",
    "get_default_registry",
    # dispatcher
    "ConcurrentDispatcher",
    # session
    "Session",
    "SessionManager",
    "SessionResult",
    "SessionState",
    "SessionTier",
    "SessionTrace",
    # triage
    "EntityRef",
    "TriageResult",
    "triage",
    # tiered_dispatch
    "TieredDispatcher",
    # tier_executors
    "InstantExecutor",
    "MultiStepExecutor",
    "SingleShotExecutor",
    "TierExecutor",
    # runtime
    "AutonomyLevel",
    "Concierge",
    "build_concierge",
    # policy
    "ActionPolicy",
    "BehaviorPolicy",
    "ClarificationRequest",
    "ClarificationResponse",
    "ExecutionPolicy",
    "resolve_policy",
    # identity
    "format_bare_prefix",
    "format_prefix",
    "get_bot_name",
    "starts_with_prefix",
    "strip_prefix",
    # progress
    "ProgressReporter",
    # fan_out
    "fan_out",
    "fan_out_dict",
    # resources
    "MessagePriority",
    "PriorityQueue",
    "ResourceBudget",
    "ResourceTracker",
    "classify_priority",
    # pii_tokenizer
    "PIISession",
    "SensitiveWord",
    "SensitiveWordRegistry",
    "TokenizingProviderWrapper",
    "detokenize",
    "handle_pii_command",
    "is_pii_enabled",
    "tokenize",
    # computer_policy
    "ActionType",
    "AuditEntry",
    "AuditLog",
    "BrowserDomainRule",
    "ChunkPolicies",
    "ChunkPolicy",
    "ComputerControlConfig",
    "SessionOverride",
    "classify_action",
    "is_app_allowed",
    "is_domain_allowed",
    "requires_approval",
    # computer_use
    "ComputerUseController",
    "ComputerUseLeaseManager",
    "ObservedElement",
    "UIObservation",
    "handle_computer_command",
    # progress_ux
    "CheckpointOption",
    "CheckpointOptions",
    "CLIProgressRenderer",
    "EditorProgressRenderer",
    "InteractionRequest",
    "ProgressPhase",
    "ProgressRenderer",
    "ProgressSession",
    "TelegramProgressRenderer",
    "VerbosityLevel",
    "WhatsAppProgressRenderer",
    "generate_preflight_questions",
    "handle_progress_command",
    "resolve_verbosity",
    # scheduler
    "DeliveryTarget",
    "ScheduleEntry",
    "ScheduleHistoryStore",
    "ScheduleRunRecord",
    "ScheduleStore",
    "TaskScheduler",
    "TriggerContext",
    "compute_next_run",
    "handle_schedule_command",
    "parse_schedule_add",
    "parse_trigger",
]
