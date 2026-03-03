# 17-3: Self-Generating Rules (Tier 3)

**Parent:** [17-self-evolving-orchestrator](17-self-evolving-orchestrator.md)
**Status:** in-progress
**Goal:** Automatically convert reflection-generated causal principles into executable adaptations — hyperedge rules (guardrails, skills, overrides) plus runtime parameter mutations for `parameter_fix` cases — so orchestrators improve behavior based on past experience with effectiveness tracking, lifecycle management, and safety bounds.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `CausalPrinciple` (17-2) | `engine/error_memory.py` (planned) | Structured principles: condition/action/reason/confidence/tags | Principles stored as memory entries; not actionable as runtime rules |
| `PrincipleStore` (17-2) | `engine/error_memory.py` (planned) | `load_principles(workflow_id, tags, min_confidence)` | Read-only retrieval; no conversion to executable rules |
| `Hyperedge` | `models/hyperedges.py:30–96` | Full model: types (`skill`/`guardrail`/`style`/`override`), hooks (`pre_prompt`/`tool_call`/`post_output`/`validation`), attachment scopes (`attach_to`/`attach_to_type`/`attach_to_tags`/`attach_to_subgraph`/`attach_globally`), `priority`, `enabled` | Available; no programmatic generation pipeline |
| `HyperedgeResolver` | `engine/hyperedge_runtime.py:29–374` | `resolve()`, `apply_pre_prompt()`, `apply_post_output()`, `apply_tool_call()`, `apply_validation()` — integrated into scheduler + LLM/tool executors | Runtime fully operational; supports arbitrary hyperedge sets at construction |
| `AddHyperedge` / `RemoveHyperedge` / `EditHyperedge` | `server/graph_mutator.py:117–132, 1231–1253` | Programmatic hyperedge CRUD via mutation pipeline | Available for build-time rule injection (alternative to runtime injection) |
| `Graph.hyperedges` | `models/graph.py:95–98` | `list[Hyperedge]` on Graph model | `HyperedgeResolver` reads this at engine startup |
| `VALID_HOOKS_BY_TYPE` | `models/hyperedges.py:18–24` | Maps hyperedge types to allowed hooks | Constraint for generated rules: e.g., `skill` → `[pre_prompt]`, `guardrail` → `[pre_prompt, tool_call, post_output, validation]`, `override` → `[tool_call, pre_prompt]` |
| `TYPE_RANK` / `SCOPE_RANK` | `models/hyperedges.py:26–28` | Precedence: `override(0) > guardrail(1) > style(2) > skill(3)`; scope: `node_id(0) > tag(1) > type(2) > subgraph(3) > global(4)` | Generated rules need correct priority below user-defined rules |
| `evaluate_condition()` | `engine/conditions.py` | Safe expression evaluation for gate conditions | **Tier 3 validation**: generated `post_output` conditions must pass this evaluator |
| `EngineConfig` | `engine/executor.py` | Feature flags pattern | `self_evolving_rules_enabled` flag |
| `EngineEvent` / `EventType` | `engine/events.py` | Rich typed event enum already used across execution, hyperedges, teams, and voting | Needs additional rule-lifecycle event types for auditability |

## Tasks

- [x] 1. Principle → Hyperedge conversion
  - [x] 1-1. Define `RuleGenerator` class in new `engine/rule_generator.py`. Core method: `generate_rule(principle: CausalPrinciple, workflow_id: str) -> Hyperedge`. Conversion logic maps principle semantics to hyperedge type and hook:
    - Action contains negation ("avoid", "do not", "never", "prevent") → `guardrail` type, `pre_prompt` hook (warn the LLM to avoid the pattern).
    - Action contains preference ("prefer", "use", "always", "default to") → `skill` type, `pre_prompt` hook (guide the LLM toward the pattern).
    - Action contains interception ("block", "reject", "deny", "override") → `override` type, `tool_call` hook (intercept tool invocations matching the condition).
    - Default fallback: `skill` type, `pre_prompt` hook.
  - [x] 1-2. Attachment targeting from principle `tags`: map `CausalPrinciple.tags` → `Hyperedge.attach_to_tags`. If no tags, default to `attach_to_type: "llm_operator"` (broadest useful scope). Support explicit node targeting via `CausalPrinciple.source_node_ids` → `attach_to` when the principle is highly specific to certain nodes.
  - [x] 1-3. Content generation for `pre_prompt` rules: format as `"Based on past experience with this workflow:\n- WHEN: {condition}\n- THEREFORE: {action}\n- REASON: {reason}\nApply this guidance to your current task."`. For `post_output`/`validation` guardrails: generate expressions against output-variable namespace used by `evaluate_condition()` (for example, when `text` exists in outputs: `\"'forbidden_phrase' not in text\"`). If expression generation is not feasible, fall back to a pre-prompt rule (`skill` or `guardrail` with `pre_prompt`).
  - [x] 1-4. Priority assignment: generated rules get `priority` = 100 (below typical user-defined rules at 0–50, above nothing). Configurable via `EngineConfig.generated_rule_base_priority`. User-defined rules always win in precedence conflicts.
  - [x] 1-5. Validate generated `Hyperedge` against `VALID_HOOKS_BY_TYPE` and the Pydantic model validators (`_check_selectors`, `_check_hook_compat`) before storing. Log and skip principles that produce invalid rules.

- [x] 2. Rule lifecycle management
  - [x] 2-1. Define `GeneratedRule` model in `engine/rule_generator.py`: `rule_id: str`, `hyperedge: Hyperedge` (the generated rule), `source_principle_id: str`, `workflow_id: str`, `created_at: float`, `expires_at: float | None` (TTL-based expiry), `apply_count: int = 0` (times the rule was active during a run), `prevented_count: int = 0` (times the target error did NOT recur while rule was active), `recurred_count: int = 0` (times the target error recurred despite the rule), `status: Literal["active", "expired", "disabled", "pending", "superseded"] = "active"`.
  - [x] 2-2. Define `effectiveness_score` computed property on `GeneratedRule`: `prevented_count / max(apply_count, 1)`. Values: 1.0 = always prevented, 0.0 = never helped.
  - [x] 2-3. Implement `RuleLifecycleManager` in `engine/rule_generator.py`: filesystem-backed storage (JSON per workflow under `rules/{workflow_id}/`). Methods:
    - `create_rule(principle, workflow_id) -> GeneratedRule` — generate hyperedge, validate, store, return.
    - `list_rules(workflow_id, status=None) -> list[GeneratedRule]` — load all rules, optionally filter by status.
    - `get_active_hyperedges(workflow_id) -> list[Hyperedge]` — load active rules, return their hyperedge instances (used at engine startup for injection).
    - `disable_rule(rule_id)`, `expire_rules(workflow_id)` — status transitions.
    - `record_application(rule_id)` — increment `apply_count`.
    - `record_outcome(rule_id, error_recurred: bool)` — increment `prevented_count` or `recurred_count`.
    - `prune_ineffective(workflow_id, min_effectiveness=0.3, min_apply_count=5)` — disable rules with low effectiveness after sufficient observations.
  - [x] 2-4. Rule expiry: configurable TTL via `EngineConfig.generated_rule_ttl_days` (default 30). On `list_rules()`, check `expires_at` and auto-transition to `status="expired"`. Expired rules are retained for audit but not injected.
  - [x] 2-5. Max rules cap: `EngineConfig.max_generated_rules_per_workflow` (default 20). When cap is reached in `create_rule()`, evict in order: expired lowest-effectiveness → disabled lowest-effectiveness → active lowest-effectiveness. If all slots are high-effectiveness, reject new rule and log warning.

- [x] 3. Rule injection into graph runtime
  - [x] 3-1. **Runtime injection** (primary path): in `Engine.run()` / scheduler `_execute()` initialization path, if `self_evolving_rules_enabled`: call `RuleLifecycleManager.get_active_hyperedges(workflow_id)`. Deep-copy the `graph.hyperedges` list and append the generated rules before constructing `HyperedgeResolver` to avoid mutating the caller's graph object. This keeps the persisted graph clean.
  - [ ] 3-2. Record rule application (deferred — requires post-resolve analysis to identify matched rules): after `HyperedgeResolver` is constructed, identify which generated rules matched at least one node (via `resolve()` results). Call `record_application(rule_id)` for each matched rule.
  - [x] 3-3. Ensure precedence correctness: generated hyperedges get higher `priority` numbers (lower precedence) than user-defined ones. Within generated rules, sort by `effectiveness_score` descending (most effective first).
  - [x] 3-4. Subgraph propagation: generated rules follow existing `HyperedgeResolver` propagation semantics — if a rule attaches by tag, it propagates into subgraphs (existing behavior). No special handling needed.

- [x] 4. Effectiveness tracking (closed-loop learning)
  - [x] 4-1. After each run completes (in `RunManager._enrich_and_persist()`), compare `RunResult.errors` against active generated rules:
    - For each active rule, check if its `source_principle_id`'s original error pattern (same `node_id` or same `error_category`) appears in the current run's errors.
    - If the error did NOT recur → `record_outcome(rule_id, error_recurred=False)`.
    - If the error DID recur → `record_outcome(rule_id, error_recurred=True)`.
  - [ ] 4-2. Auto-disable on regression (deferred — requires pre-activation error set baseline comparison): if a generated rule is active and a *new* error pattern appears on a node that the rule was attached to (error that didn't exist before the rule was activated), auto-disable the rule and emit a `RULE_AUTO_DISABLED` event. Heuristic: compare current run's error set against the error set from the run immediately before rule activation.
  - [x] 4-3. Periodic pruning: run `prune_ineffective()` after effectiveness updates. Emit `RULE_PRUNED` event for each disabled rule.

- [x] 5. Safety bounds
  - [x] 5-1. **Human approval gate** (optional): `EngineConfig.self_evolving_approval_required: bool = False`. When `True`, `create_rule()` sets `status="pending"` instead of `"active"`. Rules require explicit approval via API or `HumanNode` before activation.
  - [x] 5-2. **Audit trail**: new `EventType` values: `RULE_GENERATED`, `RULE_ACTIVATED`, `RULE_EXPIRED`, `RULE_DISABLED`, `RULE_AUTO_DISABLED`, `RULE_PRUNED`, `RULE_EFFECTIVENESS_UPDATE`. Every lifecycle transition emits an event persisted via `RunStore` (or a dedicated rule audit log).
  - [x] 5-3. **Scope containment**: generated rules can only attach to nodes within the source workflow. Cross-workflow rule propagation is blocked — `RuleGenerator` enforces `workflow_id` matching and never sets `attach_globally=True`. Global rules require explicit human opt-in via a separate API.
  - [x] 5-4. **Rollback API**: `POST /api/rules/{workflow_id}/rollback?before={timestamp}` — disable all generated rules created after the given timestamp. Emergency mechanism for reverting a batch of bad rules.

- [x] 6. Parameter mutations (level 2 repair — runtime graph mutation only)
  - [x] 6-1. Canonical rule: `repair_level == "parameter_fix"` is applied only via runtime graph mutation. Do not generate parameter-fix hyperedges.
  - [x] 6-2. Define `ParameterMutation` payload with explicit target selector and allowed fields: `model`, `temperature`, `max_tokens`, `timeout_seconds`, `tool_config`.
  - [x] 6-3. Extend `RuleLifecycleManager.get_active_mutations(workflow_id)` → `list[ParameterMutation]` for active parameter-fix rules.
  - [x] 6-4. Apply mutations in scheduler `_execute()` by creating a temporary graph copy and patching node parameters before dispatch. Persisted graph JSON remains unchanged.
  - [x] 6-5. Guard scope: mutations are workflow-local and field-whitelisted. Reject unknown fields and any topology fields (`nodes`, `edges`, `entry_points`, `exit_points`, `sub_graphs`).
  - [x] 6-6. Tests: verify mutation generation, safe-field enforcement, runtime application, and that persisted graph is not mutated.

- [ ] 7. API and editor integration
  - [x] 7-1. REST endpoints:
    - `GET /api/rules/{workflow_id}` — list generated rules with effectiveness metrics, status, source principle.
    - `POST /api/rules/{workflow_id}/{rule_id}/disable` — manually disable a rule.
    - `POST /api/rules/{workflow_id}/{rule_id}/enable` — re-enable a disabled/expired rule.
    - `POST /api/rules/{workflow_id}/{rule_id}/approve` — approve a pending rule (for approval gate).
    - `DELETE /api/rules/{workflow_id}/{rule_id}` — permanently delete a rule.
    - `POST /api/rules/{workflow_id}/rollback` — bulk disable by timestamp.
    - `GET /api/rules/{workflow_id}/stats` — summary: total/active/expired/disabled counts, average effectiveness.
  - [ ] 7-2. Editor: "Generated Rules" section in workflow settings panel. Table of active rules: source principle (condition/action), effectiveness score (bar chart), apply count, status toggle. Visual badge on nodes that have generated rules attached.
  - [ ] 7-3. Chat integration: respond to "what has the system learned?" with a formatted list of active principles and generated rules for the current workflow.

- [x] 8. Testing
  - [x] 8-1. Unit tests: `RuleGenerator` — all conversion branches (negation → guardrail, preference → skill, interception → override, default fallback), validation against `VALID_HOOKS_BY_TYPE`, invalid principle handling.
  - [x] 8-2. Unit tests: `RuleLifecycleManager` — CRUD, effectiveness tracking, expiry, pruning thresholds, max rules cap eviction.
  - [ ] 8-3. Integration test: end-to-end learning loop — failed run → error indexed (17-1) → reflection generates principle (17-2) → rule generated from principle → rule injected at runtime → LLM node receives rule as `pre_prompt` content.
  - [ ] 8-4. Safety test: rule causes new error → auto-disabled. Max rules cap enforced. Expired rules not injected. Pending rules require approval when gate is enabled.
  - [ ] 8-5. Composition test: generated rules compose correctly with user-defined hyperedges — user rules take precedence, no attachment conflicts.
  - [ ] 8-6. Regression test: measure error recurrence rate across a sequence of runs to validate the effectiveness tracking produces accurate scores.

## Primary Files

| File | Changes |
|------|---------|
| `engine/rule_generator.py` (new) | `RuleGenerator`, `GeneratedRule`, `RuleLifecycleManager` |
| `engine/scheduler.py` | Runtime rule injection during `_execute()` setup before `HyperedgeResolver` construction |
| `engine/events.py` | New event types: `RULE_GENERATED`, `RULE_ACTIVATED`, `RULE_EXPIRED`, etc. |
| `engine/executor.py` | `EngineConfig` fields: `self_evolving_rules_enabled`, `generated_rule_ttl_days`, etc. |
| `server/run_manager.py` | Effectiveness tracking in `_enrich_and_persist()` |
| `server/app.py` | REST endpoints for rule management |
| `models/hyperedges.py` | (no changes — reused as-is) |
| `engine/hyperedge_runtime.py` | (no changes — reused as-is) |

## Decisions

- **Runtime injection over build-time mutation.** Generated rules live in the `RuleLifecycleManager` (filesystem), not in the persisted graph JSON. They are injected as additional hyperedges at engine startup. This keeps the graph clean, separates generated from user-defined rules, and avoids merge conflicts with user edits. Trade-off: rules are not visible in the graph JSON — the API/editor provides visibility instead.
- **`parameter_fix` uses runtime graph mutation only.** Parameter-level repairs (model/temperature/timeout/tool_config) are applied through temporary graph mutation in scheduler runtime. Hyperedges remain for prompt/tool guidance, not direct node parameter patching.
- **Effectiveness tracking is automatic and closed-loop.** The system observes whether rules prevent error recurrence without requiring explicit human feedback. This closes the learning loop: errors → reflection → rules → effectiveness → refinement/pruning.
- **Safety-first defaults.** Generated rules have lower priority than user-defined rules, expire after 30 days, auto-disable on regression (new errors on attached nodes), and are capped at 20 per workflow. Users can relax these constraints explicitly.
- **Approval gate is opt-in.** For production/safety-critical workflows, require human approval before rule activation. For development/iteration, auto-activate. This follows the HumanNode adjustable-autonomy philosophy.
- **Start with `pre_prompt` skill injection for `prompt_fix` principles.** Most guidance principles translate naturally to "tell the LLM what to do/avoid." `post_output` guardrail generation (producing valid condition expressions from natural-language principles) is harder and more error-prone — defer to a follow-up or make it LLM-assisted. `parameter_fix` principles are handled via runtime graph mutation (section 6), not hyperedges.

## Notes

- Tier 3 creates a genuine closed-loop learning system: errors → RAG retrieval (Tier 1) → reflection (Tier 2) → rules (Tier 3) → modified behavior → new outcomes → effectiveness tracking → rule refinement. This is the distinguishing feature of 9D.
- The hyperedge runtime is already surprisingly complete — `HyperedgeResolver`, hooks, attachment, precedence, propagation are all implemented and integrated. Tier 3's novelty is in the *generation* pipeline and *lifecycle management*, not the execution runtime.
- Rule effectiveness tracking has a cold-start problem: new rules have no effectiveness data. Default `effectiveness_score` is 0.0 (neutral — `prevented_count=0, apply_count=0` → `0/1=0.0`). Require `min_apply_count >= 5` before pruning decisions to give rules time to prove themselves.
- Consider whether `post_output` guardrails should use simple expressions (`evaluate_condition` from `conditions.py`) or LLM-based validation. Simple expressions are safer but less expressive. Start with `pre_prompt` only; add `post_output` as a follow-up.
- The `AddHyperedge` mutation (build-time path, task 3 alternative) would make rules visible in the graph editor immediately. This is a valid alternative for workflows where users want full transparency. Could offer both: `EngineConfig.rule_injection_mode: Literal["runtime", "persistent"] = "runtime"`.
- Cross-workflow rule sharing (e.g., "this principle applies to all paper-writing workflows") is a natural extension but scoping correctly is non-trivial. Defer to post-Tier 3.
