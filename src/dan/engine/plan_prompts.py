"""LLM planning prompts, few-shot examples, and experience-based estimation.

Provides the prompt templates that instruct an LLM to decompose a goal into
a PlanDAG, validate an existing DAG, and estimate durations from past runs.
Also includes template-based dependency patterns and experience-based
dependency prediction.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

from dan.engine.plan_scheduler import PlanDAG, PlanTask

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 5-1: Decomposition prompt
# ---------------------------------------------------------------------------

DECOMPOSITION_PROMPT = """\
You are a planning assistant.  Given a high-level goal, decompose it into
concrete, independently executable tasks suitable for parallel scheduling.

Return a JSON array of task objects.  Each object has:
  - "id": short snake_case identifier (unique)
  - "name": human-readable task name
  - "description": one-sentence description of the deliverable
  - "estimated_duration_minutes": numeric estimate (be realistic)
  - "dependencies": list of task IDs this task must wait for (use [] when independent)
  - "required_inputs": list of {{"name": ..., "artifact_type": ...}} this task needs
  - "expected_outputs": list of {{"name": ..., "artifact_type": ...}} this task produces

Guidelines:
1. **Bias toward independence** — only add a dependency when the output of one task
   is literally required as input to another.  Two tasks that merely relate to the
   same topic do NOT need a dependency.
2. Keep task granularity moderate: 3-15 tasks for a typical goal.
3. Use descriptive artifact names so dependency inference can match them.
4. If a task could run while another is in progress, make them independent.
5. Every leaf task (no dependents) should produce a user-visible artifact.

Goal: {goal}

{experience_hint}

Return ONLY the JSON array, no commentary.
"""


# ---------------------------------------------------------------------------
# 5-2: Few-shot examples
# ---------------------------------------------------------------------------

FEW_SHOT_EXAMPLES: list[dict[str, Any]] = [
    {
        "goal": "Write a comprehensive research report on renewable energy trends",
        "tasks": [
            {
                "id": "lit_review",
                "name": "Literature Review",
                "description": "Survey recent papers and reports on renewable energy",
                "estimated_duration_minutes": 30,
                "dependencies": [],
                "expected_outputs": [{"name": "literature_notes", "artifact_type": "text"}],
            },
            {
                "id": "data_collection",
                "name": "Collect Statistical Data",
                "description": "Gather energy production and investment statistics",
                "estimated_duration_minutes": 20,
                "dependencies": [],
                "expected_outputs": [{"name": "energy_data", "artifact_type": "csv"}],
            },
            {
                "id": "expert_interviews",
                "name": "Summarize Expert Opinions",
                "description": "Compile expert perspectives from published interviews",
                "estimated_duration_minutes": 15,
                "dependencies": [],
                "expected_outputs": [{"name": "expert_summaries", "artifact_type": "text"}],
            },
            {
                "id": "analysis",
                "name": "Trend Analysis",
                "description": "Analyze data and literature for key trends",
                "estimated_duration_minutes": 25,
                "dependencies": ["lit_review", "data_collection"],
                "required_inputs": [
                    {"name": "literature_notes", "artifact_type": "text"},
                    {"name": "energy_data", "artifact_type": "csv"},
                ],
                "expected_outputs": [{"name": "trend_analysis", "artifact_type": "text"}],
            },
            {
                "id": "draft_report",
                "name": "Draft Report",
                "description": "Write the full report combining all sources",
                "estimated_duration_minutes": 40,
                "dependencies": ["analysis", "expert_interviews"],
                "required_inputs": [
                    {"name": "trend_analysis", "artifact_type": "text"},
                    {"name": "expert_summaries", "artifact_type": "text"},
                ],
                "expected_outputs": [{"name": "report_draft", "artifact_type": "document"}],
            },
        ],
    },
    {
        "goal": "Refactor authentication module to use JWT tokens",
        "tasks": [
            {
                "id": "audit_current",
                "name": "Audit Current Auth",
                "description": "Map existing session-based auth flow and identify all call sites",
                "estimated_duration_minutes": 15,
                "dependencies": [],
                "expected_outputs": [{"name": "auth_audit", "artifact_type": "text"}],
            },
            {
                "id": "design_jwt",
                "name": "Design JWT Schema",
                "description": "Define token claims, expiry, and refresh strategy",
                "estimated_duration_minutes": 10,
                "dependencies": [],
                "expected_outputs": [{"name": "jwt_design", "artifact_type": "text"}],
            },
            {
                "id": "implement_jwt",
                "name": "Implement JWT Module",
                "description": "Create token generation, validation, and refresh logic",
                "estimated_duration_minutes": 30,
                "dependencies": ["design_jwt"],
                "required_inputs": [{"name": "jwt_design", "artifact_type": "text"}],
                "expected_outputs": [{"name": "jwt_module", "artifact_type": "code"}],
            },
            {
                "id": "migrate_endpoints",
                "name": "Migrate API Endpoints",
                "description": "Replace session auth with JWT middleware in all endpoints",
                "estimated_duration_minutes": 25,
                "dependencies": ["implement_jwt", "audit_current"],
                "required_inputs": [
                    {"name": "jwt_module", "artifact_type": "code"},
                    {"name": "auth_audit", "artifact_type": "text"},
                ],
                "expected_outputs": [{"name": "migrated_endpoints", "artifact_type": "code"}],
            },
            {
                "id": "write_tests",
                "name": "Write Auth Tests",
                "description": "Add integration tests for JWT auth flow",
                "estimated_duration_minutes": 20,
                "dependencies": ["migrate_endpoints"],
                "expected_outputs": [{"name": "auth_tests", "artifact_type": "code"}],
            },
        ],
    },
    {
        "goal": "Build an ETL data pipeline for sales analytics",
        "tasks": [
            {
                "id": "source_connectors",
                "name": "Build Source Connectors",
                "description": "Create extractors for CRM, billing, and web analytics",
                "estimated_duration_minutes": 25,
                "dependencies": [],
                "expected_outputs": [{"name": "raw_extracts", "artifact_type": "data"}],
            },
            {
                "id": "schema_design",
                "name": "Design Target Schema",
                "description": "Define star-schema for sales analytics warehouse",
                "estimated_duration_minutes": 15,
                "dependencies": [],
                "expected_outputs": [{"name": "target_schema", "artifact_type": "sql"}],
            },
            {
                "id": "transform_logic",
                "name": "Implement Transformations",
                "description": "Clean, normalize, and join extracted data",
                "estimated_duration_minutes": 30,
                "dependencies": ["source_connectors", "schema_design"],
                "required_inputs": [
                    {"name": "raw_extracts", "artifact_type": "data"},
                    {"name": "target_schema", "artifact_type": "sql"},
                ],
                "expected_outputs": [{"name": "transformed_data", "artifact_type": "data"}],
            },
            {
                "id": "load_warehouse",
                "name": "Load into Warehouse",
                "description": "Insert transformed data into target schema",
                "estimated_duration_minutes": 10,
                "dependencies": ["transform_logic"],
                "expected_outputs": [{"name": "loaded_tables", "artifact_type": "data"}],
            },
            {
                "id": "build_dashboards",
                "name": "Build Dashboards",
                "description": "Create sales analytics dashboards",
                "estimated_duration_minutes": 20,
                "dependencies": ["load_warehouse"],
                "expected_outputs": [{"name": "dashboards", "artifact_type": "document"}],
            },
        ],
    },
    {
        "goal": "Prepare a Kaggle competition submission for house price prediction",
        "tasks": [
            {
                "id": "eda",
                "name": "Exploratory Data Analysis",
                "description": "Analyze distributions, correlations, and missing values",
                "estimated_duration_minutes": 20,
                "dependencies": [],
                "expected_outputs": [{"name": "eda_report", "artifact_type": "document"}],
            },
            {
                "id": "feature_eng",
                "name": "Feature Engineering",
                "description": "Create derived features, handle categoricals and missings",
                "estimated_duration_minutes": 25,
                "dependencies": ["eda"],
                "required_inputs": [{"name": "eda_report", "artifact_type": "document"}],
                "expected_outputs": [{"name": "feature_matrix", "artifact_type": "data"}],
            },
            {
                "id": "model_lgbm",
                "name": "Train LightGBM Model",
                "description": "Train and tune LightGBM with cross-validation",
                "estimated_duration_minutes": 15,
                "dependencies": ["feature_eng"],
                "required_inputs": [{"name": "feature_matrix", "artifact_type": "data"}],
                "expected_outputs": [{"name": "lgbm_oof", "artifact_type": "data"}],
            },
            {
                "id": "model_xgb",
                "name": "Train XGBoost Model",
                "description": "Train and tune XGBoost with cross-validation",
                "estimated_duration_minutes": 15,
                "dependencies": ["feature_eng"],
                "required_inputs": [{"name": "feature_matrix", "artifact_type": "data"}],
                "expected_outputs": [{"name": "xgb_oof", "artifact_type": "data"}],
            },
            {
                "id": "ensemble",
                "name": "Ensemble and Submit",
                "description": "Blend OOF predictions and create submission file",
                "estimated_duration_minutes": 10,
                "dependencies": ["model_lgbm", "model_xgb"],
                "required_inputs": [
                    {"name": "lgbm_oof", "artifact_type": "data"},
                    {"name": "xgb_oof", "artifact_type": "data"},
                ],
                "expected_outputs": [{"name": "submission", "artifact_type": "csv"}],
            },
        ],
    },
    {
        "goal": "Equity analysis of AAPL for an investment memo",
        "tasks": [
            {
                "id": "financial_data",
                "name": "Collect Financial Data",
                "description": "Pull income statements, balance sheet, and cash flows",
                "estimated_duration_minutes": 10,
                "dependencies": [],
                "expected_outputs": [{"name": "financial_statements", "artifact_type": "data"}],
            },
            {
                "id": "market_context",
                "name": "Market Context Research",
                "description": "Analyze sector trends, competitors, and macro environment",
                "estimated_duration_minutes": 15,
                "dependencies": [],
                "expected_outputs": [{"name": "market_context", "artifact_type": "text"}],
            },
            {
                "id": "dcf_model",
                "name": "Build DCF Model",
                "description": "Construct discounted cash flow valuation model",
                "estimated_duration_minutes": 25,
                "dependencies": ["financial_data"],
                "required_inputs": [{"name": "financial_statements", "artifact_type": "data"}],
                "expected_outputs": [{"name": "dcf_valuation", "artifact_type": "data"}],
            },
            {
                "id": "comps_analysis",
                "name": "Comparable Companies Analysis",
                "description": "Compute relative valuation multiples vs peers",
                "estimated_duration_minutes": 15,
                "dependencies": ["financial_data", "market_context"],
                "required_inputs": [
                    {"name": "financial_statements", "artifact_type": "data"},
                    {"name": "market_context", "artifact_type": "text"},
                ],
                "expected_outputs": [{"name": "comps_table", "artifact_type": "data"}],
            },
            {
                "id": "investment_memo",
                "name": "Write Investment Memo",
                "description": "Synthesize all analysis into an investment recommendation",
                "estimated_duration_minutes": 20,
                "dependencies": ["dcf_model", "comps_analysis"],
                "required_inputs": [
                    {"name": "dcf_valuation", "artifact_type": "data"},
                    {"name": "comps_table", "artifact_type": "data"},
                ],
                "expected_outputs": [{"name": "memo", "artifact_type": "document"}],
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# 5-3: Validation prompt
# ---------------------------------------------------------------------------

VALIDATION_PROMPT = """\
You are reviewing a task DAG for correctness.  The DAG represents a plan
to achieve the stated goal.

Goal: {goal}

Task DAG (JSON):
{dag_json}

Check for:
1. **Missing dependencies** — does any task require an artifact that no
   predecessor produces?  If so, which edge is missing?
2. **Redundant edges** — is there a dependency A→C that is already implied
   by A→B→C?  If so, which edge is redundant?
3. **Parallelism opportunities** — are there tasks with unnecessary
   sequential dependencies that could run in parallel?
4. **Missing tasks** — is a sub-task needed that isn't represented?
5. **Unrealistic estimates** — any duration that looks clearly wrong?

Return a JSON object:
{{
  "missing_edges": [["producer_id", "consumer_id"], ...],
  "redundant_edges": [["source_id", "target_id"], ...],
  "parallelism_suggestions": ["description", ...],
  "missing_tasks": ["description", ...],
  "estimate_concerns": ["description", ...],
  "is_valid": true/false
}}

Return ONLY the JSON, no commentary.
"""


# ---------------------------------------------------------------------------
# 5-4: Experience-based estimation
# ---------------------------------------------------------------------------

_DURATION_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:min(?:ute)?s?|m)\b", re.IGNORECASE)


def estimate_from_experience(
    task_description: str, experience_store: Any
) -> float | None:
    """Look up similar past tasks for duration estimation.

    *experience_store* should expose ``search(query, top_k)`` returning items
    with an ``avg_elapsed_seconds`` field.  Falls back to ``None`` (caller
    should use LLM heuristic) when no similar experience is found.
    """
    if experience_store is None:
        return None

    search = getattr(experience_store, "search", None)
    if search is None:
        return None

    try:
        results = search(task_description, top_k=3)
    except Exception:
        logger.debug("Experience store search failed", exc_info=True)
        return None

    if not results:
        return None

    durations: list[float] = []
    for item in results:
        avg = None
        if isinstance(item, dict):
            avg = item.get("avg_elapsed_seconds")
        else:
            avg = getattr(item, "avg_elapsed_seconds", None)

        if avg is not None and avg > 0:
            durations.append(avg / 60.0)

    if not durations:
        return None

    return sum(durations) / len(durations)


# ---------------------------------------------------------------------------
# 5B-4: Template-based dependency patterns
# ---------------------------------------------------------------------------

DEPENDENCY_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "research_report": [
        ("literature_review", "methodology"),
        ("literature_review", "analysis"),
        ("data_collection", "data_cleaning"),
        ("data_cleaning", "analysis"),
        ("analysis", "writing"),
        ("analysis", "draft_report"),
        ("writing", "review"),
        ("draft_report", "review"),
    ],
    "code_refactor": [
        ("audit", "design"),
        ("design", "implement"),
        ("implement", "test"),
        ("test", "review"),
        ("review", "deploy"),
    ],
    "data_pipeline": [
        ("extract", "transform"),
        ("transform", "load"),
        ("load", "validate"),
        ("schema_design", "transform"),
        ("source_connectors", "transform"),
    ],
    "ml_experiment": [
        ("eda", "feature_engineering"),
        ("feature_engineering", "model_training"),
        ("model_training", "evaluation"),
        ("evaluation", "ensemble"),
        ("ensemble", "submission"),
    ],
    "equity_analysis": [
        ("financial_data", "dcf_model"),
        ("financial_data", "comps_analysis"),
        ("market_context", "comps_analysis"),
        ("dcf_model", "investment_memo"),
        ("comps_analysis", "investment_memo"),
    ],
}


def _fuzzy_match_name(task_name: str, template_name: str) -> bool:
    """Check if a task name is a fuzzy match for a template step name."""
    norm_task = task_name.lower().replace("-", "_").replace(" ", "_")
    norm_tmpl = template_name.lower().replace("-", "_").replace(" ", "_")
    if norm_tmpl in norm_task or norm_task in norm_tmpl:
        return True
    return SequenceMatcher(None, norm_task, norm_tmpl).ratio() > 0.7


def apply_template_deps(dag: PlanDAG, template_key: str | None = None) -> PlanDAG:
    """Overlay template dependency edges when task names match known patterns.

    If *template_key* is None, tries all templates and applies the one with
    the most matching edges.  Skips edges that would create cycles.
    """
    from dan.engine.plan_scheduler import _has_cycle

    task_map = {t.id: t for t in dag.tasks}
    name_to_id: dict[str, str] = {}
    for t in dag.tasks:
        norm = t.name.lower().replace("-", "_").replace(" ", "_")
        name_to_id[norm] = t.id
        name_to_id[t.id.lower().replace("-", "_")] = t.id

    def _match_id(template_name: str) -> str | None:
        if template_name in name_to_id:
            return name_to_id[template_name]
        for task_key, task_id in name_to_id.items():
            if _fuzzy_match_name(task_key, template_name):
                return task_id
        return None

    templates_to_try = (
        [(template_key, DEPENDENCY_TEMPLATES[template_key])]
        if template_key and template_key in DEPENDENCY_TEMPLATES
        else list(DEPENDENCY_TEMPLATES.items())
    )

    best_edges: list[tuple[str, str]] = []
    for _key, patterns in templates_to_try:
        matched: list[tuple[str, str]] = []
        for src_name, tgt_name in patterns:
            src_id = _match_id(src_name)
            tgt_id = _match_id(tgt_name)
            if src_id and tgt_id and src_id != tgt_id:
                matched.append((src_id, tgt_id))
        if len(matched) > len(best_edges):
            best_edges = matched

    if not best_edges:
        return dag

    existing_edges: set[tuple[str, str]] = set()
    for t in dag.tasks:
        for dep in t.dependencies:
            existing_edges.add((dep, t.id))

    updated_tasks = [t.model_copy() for t in dag.tasks]
    updated_map = {t.id: t for t in updated_tasks}

    for src, tgt in best_edges:
        if (src, tgt) in existing_edges:
            continue
        t = updated_map[tgt]
        new_deps = list(t.dependencies) + [src]
        candidate = t.model_copy(update={"dependencies": new_deps})
        updated_map[tgt] = candidate
        test_tasks = list(updated_map.values())
        if _has_cycle(test_tasks):
            updated_map[tgt] = t
            logger.debug("Skipped template edge %s→%s (would create cycle)", src, tgt)
        else:
            existing_edges.add((src, tgt))

    return PlanDAG(
        tasks=list(updated_map.values()),
        goal=dag.goal,
        constraints=dag.constraints,
    )


# ---------------------------------------------------------------------------
# 5B-5: Experience-based dependency prediction
# ---------------------------------------------------------------------------


def predict_deps_from_experience(
    dag: PlanDAG, experience_store: Any
) -> list[tuple[str, str]]:
    """Learn which task-type pairs frequently co-occur with dependency edges.

    Queries *experience_store* for past DAGs, tallies (task_name_A, task_name_B)
    pair frequencies, and returns predicted edges whose tasks appear in *dag*.
    """
    if experience_store is None:
        return []

    get_edges = getattr(experience_store, "get_dependency_pairs", None)
    if get_edges is None:
        return []

    try:
        pair_counts: dict[tuple[str, str], int] = get_edges()
    except Exception:
        logger.debug("Experience store get_dependency_pairs failed", exc_info=True)
        return []

    task_names = {t.name.lower(): t.id for t in dag.tasks}
    task_ids_by_alias = dict(task_names)
    for t in dag.tasks:
        task_ids_by_alias[t.id.lower()] = t.id

    existing_edges: set[tuple[str, str]] = set()
    for t in dag.tasks:
        for dep in t.dependencies:
            existing_edges.add((dep, t.id))

    predicted: list[tuple[str, str]] = []
    threshold = 2

    for (src_name, tgt_name), count in pair_counts.items():
        if count < threshold:
            continue
        src_id = task_ids_by_alias.get(src_name.lower())
        tgt_id = task_ids_by_alias.get(tgt_name.lower())
        if src_id and tgt_id and src_id != tgt_id:
            if (src_id, tgt_id) not in existing_edges:
                predicted.append((src_id, tgt_id))

    return predicted
