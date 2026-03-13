# 34-1: Session Model & Lifecycle

**Parent:** [34-tiered-async-dispatcher](34-tiered-async-dispatcher.md)
**Status:** completed
**Goal:** Define the `Session` data model that represents a unit of work at any depth, plus the `SessionManager` that tracks the session tree.

## Models

### SessionTier

```python
class SessionTier(int, Enum):
    INSTANT = 0   # No async work, inline response
    SINGLE = 1    # One LLM call, no children
    MULTI = 2     # Multi-step, can decompose into children
```

### SessionState

```python
class SessionState(str, Enum):
    PENDING = "pending"       # Created but not started
    RUNNING = "running"       # Actively executing
    WAITING = "waiting"       # Waiting for children to complete
    PAUSED = "paused"         # User-requested pause
    COMPLETED = "completed"   # Finished successfully
    FAILED = "failed"         # Finished with error
    CANCELLED = "cancelled"   # User-cancelled
```

### SessionResult

```python
class SessionResult(BaseModel):
    content: str = ""
    attachments: list[str] = Field(default_factory=list)
    events: list[ChatStreamEvent] | None = None  # for streaming back
    metadata: dict[str, Any] = Field(default_factory=dict)
    child_results: dict[str, SessionResult] = Field(default_factory=dict)
    error: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    duration_ms: float = 0
    tools_used: list[str] = Field(default_factory=list)   # tool_ids invoked
    model_used: str | None = None                          # LLM model for this session
```

### SessionTrace

Every session — root and child alike — produces a trace record for telemetry and history. The full session tree is preserved as a structured execution log.

```python
class SessionTrace(BaseModel):
    session_id: str
    parent_id: str | None
    root_id: str
    tier: SessionTier
    depth: int
    task: str                                  # what was assigned
    state: SessionState                        # terminal state
    token_usage: dict[str, int] = {}
    estimated_cost: float = 0
    duration_ms: float = 0
    model_used: str | None = None
    tools_used: list[str] = []
    error: str | None = None
    children: list[str] = []                   # child session IDs
    created_at: float = 0
    started_at: float | None = None
    completed_at: float | None = None
```

### Session

```python
class Session(BaseModel):
    id: str                              # unique session ID
    parent_id: str | None = None         # None = root session
    root_id: str                         # always points to the top
    tier: SessionTier
    depth: int = 0                       # 0 = concierge-spawned
    state: SessionState = SessionState.PENDING

    # Task description
    task: str                            # what this session should do
    task_context: dict[str, Any] = {}    # structured context from parent

    # Execution context (populated by executor)
    msg: SurfaceMessage | None = None    # original or synthetic message
    context: ResolvedContext | None = None
    triage: TriageResult | None = None   # only on root sessions

    # Tree structure
    children: list[str] = []             # child session IDs
    child_execution: Literal["parallel", "serial"] = "parallel"

    # Results
    result: SessionResult | None = None

    # Lifecycle
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None

    # Limits (inherited from root, decremented per level)
    max_depth: int = 4
    max_children: int = 8
    max_total_sessions: int = 32
```

### SessionManager

In-memory session tree tracker. One per `ConciergeRuntime` instance.

```python
class SessionManager:
    _sessions: dict[str, Session]         # all sessions by ID
    _root_sessions: dict[str, str]        # surface_id → active root session ID
    _session_counts: dict[str, int]       # root_id → total descendant count

    def create_root(msg, triage, tier) -> Session
    def create_child(parent_id, task, tier, task_context) -> Session
    def get(session_id) -> Session | None
    def get_root(surface_id) -> Session | None
    def children_of(session_id) -> list[Session]
    def update_state(session_id, new_state) -> None
    def set_result(session_id, result) -> None
    def can_spawn_child(parent_id) -> bool  # depth + total checks
    def cancel_tree(root_id) -> None        # cancel root + all descendants
    def prune_completed(max_age_seconds) -> int  # cleanup
    def build_trace(root_id) -> list[SessionTrace]  # full tree trace for telemetry
    def export_tree(root_id) -> dict[str, Any]      # JSON-serializable tree snapshot
```

## Tasks

- [x] 1. `Session`, `SessionTier`, `SessionState`, `SessionResult` Pydantic models in `src/dan/server/concierge/session.py`
- [x] 2. `SessionManager` with tree operations: `create_root`, `create_child`, `get`, `children_of`
- [x] 3. State machine enforcement: valid transitions only (pending→running, running→waiting/completed/failed, waiting→running/completed)
- [x] 4. Depth + total session budget enforcement in `can_spawn_child`
- [x] 5. `cancel_tree` for user interrupts — cancels root and all descendants
- [x] 6. `prune_completed` for memory cleanup after configurable TTL
- [x] 7. `build_trace` — walk the tree and produce a `SessionTrace` per session (for telemetry)
- [x] 8. `export_tree` — JSON-serializable snapshot of the full session tree (for history/debug)
- [x] 9. Unit tests for tree operations, state transitions, budget enforcement, trace generation

## Decisions

- In-memory during execution; the full session tree is persisted as a trace after completion (via `build_trace` → telemetry store, and `export_tree` → turn metadata).
- `max_depth`, `max_children`, `max_total_sessions` are configurable via env vars but have sane defaults.
- A Tier 1 session cannot have children (enforced in `create_child`).
- Every session — root and child — produces a `SessionTrace`. The trace tree is the execution log: what was planned, what ran, what each step cost. This is essential for debugging, cost attribution, and learning.

## Notes

- The `task_context` dict on child sessions carries structured info from the parent: relevant memory, artifacts, partial results from siblings. This is how context flows down the tree.
- `child_execution` = "parallel" means independent children run concurrently. "serial" means they run in order, each seeing previous results.
- Focused unit coverage now lives in `tests/test_concierge/test_session_manager.py`; the full `SessionManager` behavior passed without requiring a follow-up production patch in `session.py`.
