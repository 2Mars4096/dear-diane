# 34-2: Triage LLM Call

**Parent:** [34-tiered-async-dispatcher](34-tiered-async-dispatcher.md)
**Status:** in-progress
**Goal:** Single "triage" LLM call that understands the message, identifies entities, decides the tier, and routes — all in one shot. No backward compatibility with the old classifier. No heuristic fallback beyond a minimal safe default.

## What the Triage Replaces

Everything in the old routing pipeline:

| Old module/step | Lines deleted | Triage output field |
|-----------------|---------------|---------------------|
| `classifier.py` (heuristic + LLM classification) | 1,281 | `tier`, `intent`, `route`, `confidence` |
| `entity_grounding.py` (heuristic entity scan) | 445 | `entities` |
| `resume.py` (resume check heuristic) | 371 | `is_resume`, `resume_task_id` |
| `context_resolver.py` (context needs inference) | 327 | `context_needs` |
| `solver.py` (goal/plan extraction) | 406 | `goal`, `deliverable`, `subtasks` |
| `continuity.py` (surface-switch detection) | 425 | `is_resume` + triage context |
| Simple message fast path (~50 lines in runtime) | — | `tier` (0 or 1) |
| Guard 1: classification coherence (~30 lines) | — | `confidence` |

**Net effect:** ~3,300 lines of routing code → 1 triage function (~200 lines).

## TriageResult Model

```python
class EntityRef(BaseModel):
    kind: Literal["project", "task", "workflow", "file", "person"]
    label: str
    id: str | None = None
    confidence: float = 0.8

class TriageResult(BaseModel):
    tier: SessionTier                  # 0=instant, 1=single, 2=multi

    intent: Literal["ask", "agent", "plan"]
    route: RouteDecision               # target + action_hints
    confidence: float

    goal: str                          # what the user wants
    deliverable: str                   # concrete expected output
    entities: list[EntityRef] = []
    rationale: str = ""

    is_resume: bool = False
    resume_task_id: str | None = None

    is_social: bool = False
    social_response: str | None = None

    context_needs: list[str] = []      # ["memory", "file:/path", "domain:finance"]

    subtasks: list[str] = []           # decomposition hints for Tier 2
    execution_order: Literal["parallel", "serial", "mixed"] = "parallel"
```

## Triage Prompt Design

The triage prompt receives:
- User message
- Recent conversation turns (last 4)
- Active project/task summary (1-2 lines)
- Available capability summary (tool families from registry)
- Pending actions summary (if any)

Returns structured JSON matching `TriageResult`.

### Tier Assignment Rules (in prompt)

```
Tier 0 (instant): greetings, thanks, confirmations (yes/no/number), slash commands that slipped through
Tier 1 (single-shot): questions with clear answers, status checks, file lookups, experience queries,
    simple explanations, short translations, quick calculations — anything answerable in one response
Tier 2 (multi-step): research tasks, report writing, code projects, workflow builds, anything requiring
    multiple tool calls, file reads + writes, web searches + synthesis, or iterative refinement
```

### Decomposition Hints

For Tier 2, the triage suggests how to break the task down. These are **hints** — the Tier 2 executor makes the final decomposition decision. Having them from triage avoids a second "planning" LLM call in simple cases.

## Deterministic Pre-Filter

Minimal — only truly zero-LLM cases:

```python
def pre_filter(msg: SurfaceMessage) -> TriageResult | None:
    text = msg.text.strip()
    if text.startswith("/"):
        return None  # slash commands handled upstream
    if not text:
        return TriageResult(tier=0, is_social=True, social_response="How can I help?", ...)
    return None  # everything else goes to LLM
```

## Fallback (on LLM failure)

If the triage LLM call fails (timeout, parse error, empty response):
- Default to `tier=1`, `intent="ask"`, `confidence=0.5`
- Empty entities, no decomposition hints
- Log the failure for debugging

No heuristic fallback. No `classify_intent()` rescue. The old classifier is deleted.

## Entity Resolution (post-triage)

After the LLM returns entities, resolve them against `ProjectStore`:
- Match project/task/workflow labels to known IDs
- Fill `EntityRef.id` where matched
- Set active project context based on matched entities

## Current Entanglements in `triage.py`

These imports from deleted modules must be resolved before deletion:

| Line | Import | Resolution |
|------|--------|------------|
| 11-17 | `from .classifier import ClassificationResult, IntentCategory, RouteDecision, RouteMode, classify_intent` | Delete `ClassificationResult`, `classify_intent`. Import `IntentCategory`, `RouteDecision`, `RouteMode` from `models.py` (relocated there). |
| 18 | `from .context_resolver import ResolvedContext` | Import from `models.py` (relocated there). |
| 25 | `from .resume import ResumeProtocol` (TYPE_CHECKING) | Delete — no backward compat. |
| 431 | `from .resume import ResumeProtocol as _ResumeProtocol` (runtime) | Delete. Inline the 5-line task-ID lookup if still needed for `is_resume` detection. |

## Tasks

- [x] 1. `TriageResult`, `EntityRef` models *(exists in `triage.py`)*
- [x] 2. Triage system prompt with intent catalog, tier rules, decomposition guidance *(exists)*
- [x] 3. `triage()` async function: build messages → LLM call → parse *(exists)*
- [x] 4. Entity resolution against `ProjectStore` *(exists)*
- [ ] 5. **Remove `ClassificationResult` production** — triage no longer produces backward-compat `ClassificationResult`; all downstream code uses `TriageResult` directly
- [ ] 6. **Remove heuristic fallback** — delete the `classify_intent()` rescue path; LLM failure → safe default only
- [ ] 7. **Remove resume.py dependency** — delete the `ResumeProtocol` import and `_post_process_resume` function. Inline the minimal task-ID lookup (5 lines: search active tasks by name/ID match) directly in triage post-processing.
- [ ] 8. **Remove entity_grounding.py dependency** — entity extraction is fully in the triage LLM output; delete `entity_grounding.py`
- [ ] 9. **Clean imports** — change `IntentCategory`, `RouteDecision`, `RouteMode` imports from `.classifier` → `.models`; change `ResolvedContext` import from `.context_resolver` → `.models`
- [ ] 10. Update tests: remove classifier-compat tests, add triage-only coverage

## Decisions

- The triage uses `DAN_CLASSIFIER_MODEL` or the default model. It's a micro-tier call.
- No backward compat with `ClassificationResult`. That type is deleted along with `classifier.py`.
- The triage prompt includes the intent catalog from `intent_catalog.py`.
- On failure, the triage returns a safe default (tier=1, ask), never falls back to heuristics.

## Notes

- The `context_needs` field drives targeted context gathering. If triage says `["memory", "file:/path"]`, the `ContextGatherer` only fetches those — no speculative domain/reuse/artifact resolution.
- For Tier 0 social turns, `social_response` is included directly — no further LLM call.
