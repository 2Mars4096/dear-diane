"""Report generator for workflow generation quality evaluation.

Reads JSONL evaluation records and produces summary reports with
per-tier, per-lane, generation-path, and failure-mode breakdowns.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from tests.eval import EvalRecord, PROMPTS_FILE, TokenInfo
from tests.eval.metrics import EvalLogger

try:
    from rich.console import Console
    from rich.table import Table

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


_SMALL_BATTERY_TIERS = frozenset({"T1", "T2", "T2R", "T3", "T5"})


class ReportGenerator:
    def __init__(
        self,
        records: list[EvalRecord],
        graphs_dir: Path | None = None,
        runs: int = 1,
        prompts_path: Path | None = None,
    ):
        self._records = records
        self._graphs_dir = graphs_dir
        self._runs = runs
        self._prompts_path = prompts_path or PROMPTS_FILE
        self._prompt_catalog = _load_prompt_catalog(self._prompts_path)

    @classmethod
    def from_jsonl(
        cls,
        path: Path,
        graphs_dir: Path | None = None,
        runs: int = 1,
        prompts_path: Path | None = None,
    ) -> ReportGenerator:
        records = EvalLogger.load_records(path)
        gd = graphs_dir or path.parent / f"{path.stem}_graphs"
        return cls(
            records,
            graphs_dir=gd if gd.exists() else None,
            runs=runs,
            prompts_path=prompts_path,
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        recs = self._records
        total = len(recs)
        passed = sum(1 for r in recs if r.status == "passed")
        failed = sum(1 for r in recs if r.status == "failed")
        error = total - passed - failed

        total_build_tokens = sum(
            r.build_tokens.total_tokens for r in recs if r.build_tokens
        )
        total_build_cost = sum(
            r.build_tokens.estimated_cost for r in recs if r.build_tokens
        )
        total_run_tokens = sum(
            r.run_tokens.total_tokens for r in recs if r.run_tokens
        )
        total_time_ms = sum(r.timing.total_ms for r in recs)

        result = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "error": error,
            "pass_rate": passed / total if total else 0.0,
            "total_build_tokens": total_build_tokens,
            "total_build_cost": total_build_cost,
            "total_run_tokens": total_run_tokens,
            "total_time_ms": total_time_ms,
            "by_tier": self._by_tier(recs),
            "by_lane": self._by_lane(recs),
            "named_subsets": self._named_subsets(recs),
            "execution_friendly_subset": self._execution_friendly_subset(recs),
            "benchmark_prep": self._benchmark_prep(recs),
            "lane_comparison": self._lane_comparison(recs),
            "generation_path": self._generation_path(recs),
            "failure_modes": self._failure_modes(recs),
            "expectation_mismatches": self._expectation_mismatches(recs),
            "judge_worst_examples": self._judge_worst_examples(recs),
            "top_token_consumers": self._top_token_consumers(recs),
            "top_slowest": self._top_slowest(recs),
            "quality_by_tier": self._quality_by_tier(recs),
            "top_quality_concerns": self._top_quality_concerns(recs),
            "guard_check_summary": self._guard_check_summary(recs),
            "telemetry_completeness": self._telemetry_completeness(recs),
            "workflow_contract_variants": self._workflow_contract_variants(recs),
            "workflow_guidance_summary": self._workflow_guidance_summary(recs),
            "workflow_guidance_correlation": self._workflow_guidance_correlation(recs),
            "workflow_success_metrics": self._workflow_success_metrics(recs),
            "domain_profiles": self._domain_profiles(recs),
            "coverage_32_6": self._coverage_32_6(recs),
            "smart_defaults": self._smart_defaults(recs),
            "reuse_adaptation": self._reuse_adaptation(recs),
            "multi_turn_summary": self._multi_turn_summary(recs),
            "t4_domain": self._t4_domain(recs),
            "path_distribution": self._path_distribution(recs),
            "t5_latency": self._t5_latency(recs),
        }
        result["judge_summary"] = self._judge_summary(recs)
        if self._runs > 1:
            result["flakiness"] = self._flakiness(recs, self._runs)
        return result

    def _record_catalog_entry(self, record: EvalRecord) -> dict[str, Any] | None:
        return self._prompt_catalog.get(_base_record_id(record.id))

    def _record_tags(self, record: EvalRecord) -> set[str]:
        entry = self._record_catalog_entry(record)
        if not entry:
            return set()
        return set(entry.get("tags", []))

    def _record_matches_subset(self, record: EvalRecord, subset: str) -> bool:
        entry = self._record_catalog_entry(record)
        return _prompt_catalog_entry_matches_subset(entry, subset)

    def _sorted_prompt_ids(
        self,
        records: list[EvalRecord],
        *,
        prefer_lr2: bool = False,
    ) -> list[str]:
        prompt_ids = {_base_record_id(record.id) for record in records}
        return _ordered_prompt_ids(
            self._prompt_catalog,
            prompt_ids,
            prefer_lr2=prefer_lr2,
        )

    @staticmethod
    def _by_contract_variant(recs: list[EvalRecord]) -> dict[str, Any]:
        by_variant: dict[str, dict[str, Any]] = {}
        for record in recs:
            variant = record.workflow_contract_variant or "unspecified"
            bucket = by_variant.setdefault(
                variant,
                {"total": 0, "passed": 0, "failed": 0, "error": 0},
            )
            bucket["total"] += 1
            if record.status == "passed":
                bucket["passed"] += 1
            elif record.status == "failed":
                bucket["failed"] += 1
            else:
                bucket["error"] += 1
        for bucket in by_variant.values():
            total = bucket["total"]
            bucket["pass_rate"] = bucket["passed"] / total if total else 0.0
        return by_variant

    def _summarize_subset(
        self,
        recs: list[EvalRecord],
        *,
        prefer_lr2: bool = False,
    ) -> dict[str, Any]:
        total = len(recs)
        passed = sum(1 for record in recs if record.status == "passed")
        failed = sum(1 for record in recs if record.status == "failed")
        error = total - passed - failed
        return {
            "total": total,
            "unique_prompts": len({_base_record_id(record.id) for record in recs}),
            "prompt_ids": self._sorted_prompt_ids(recs, prefer_lr2=prefer_lr2),
            "passed": passed,
            "failed": failed,
            "error": error,
            "pass_rate": passed / total if total else 0.0,
            "by_contract_variant": self._by_contract_variant(recs),
        }

    def _named_subsets(self, recs: list[EvalRecord]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for subset in (
            "small",
            "complex",
            "execution-friendly",
            "benchmark-prep",
            "lr2-first",
        ):
            subset_records = [
                record for record in recs
                if self._record_matches_subset(record, subset)
            ]
            if subset_records:
                result[subset] = self._summarize_subset(
                    subset_records,
                    prefer_lr2=(subset == "benchmark-prep"),
                )
        return result

    def _execution_friendly_subset(self, recs: list[EvalRecord]) -> dict[str, Any]:
        subset_records = [
            record for record in recs
            if self._record_matches_subset(record, "execution-friendly")
        ]
        if not subset_records:
            return {}

        validated = [record for record in subset_records if record.validation is not None]
        run_ready = [
            record
            for record in validated
            if record.validation is not None and bool(record.validation.run_ready)
        ]
        execution_attempted = [
            record for record in subset_records if record.execution is not None
        ]
        execution_completed = [
            record
            for record in execution_attempted
            if record.execution is not None and record.execution.status == "completed"
        ]
        return {
            **self._summarize_subset(subset_records, prefer_lr2=True),
            "validated": len(validated),
            "run_ready": len(run_ready),
            "run_ready_rate": len(run_ready) / len(validated) if validated else 0.0,
            "execution_attempted": len(execution_attempted),
            "execution_completed": len(execution_completed),
            "execution_completion_rate": (
                len(execution_completed) / len(execution_attempted)
                if execution_attempted else 0.0
            ),
        }

    def _benchmark_prep(self, recs: list[EvalRecord]) -> dict[str, Any]:
        catalog_order = _ordered_prompt_ids(
            self._prompt_catalog,
            {
                prompt_id
                for prompt_id, entry in self._prompt_catalog.items()
                if _prompt_catalog_entry_matches_subset(entry, "benchmark-prep")
            },
            prefer_lr2=True,
        )
        if not catalog_order:
            return {}

        subset_records = [
            record for record in recs
            if self._record_matches_subset(record, "benchmark-prep")
        ]
        observed_prompt_ids = self._sorted_prompt_ids(
            subset_records,
            prefer_lr2=True,
        )
        lr2_prompt_ids = [
            prompt_id for prompt_id in catalog_order
            if "lr2_first" in set(self._prompt_catalog.get(prompt_id, {}).get("tags", []))
        ]
        return {
            **self._summarize_subset(subset_records, prefer_lr2=True),
            "catalog_prompt_order": catalog_order,
            "catalog_lr2_first_prompt_ids": lr2_prompt_ids,
            "catalog_remaining_prompt_ids": [
                prompt_id for prompt_id in catalog_order
                if prompt_id not in lr2_prompt_ids
            ],
            "observed_prompt_ids": observed_prompt_ids,
        }

    # ------------------------------------------------------------------
    # Per-tier breakdown
    # ------------------------------------------------------------------

    @staticmethod
    def _quality_by_tier(recs: list[EvalRecord]) -> dict[str, Any]:
        """Quality score distribution per tier (min/avg/max)."""
        by_tier: dict[str, list[int]] = {}
        for r in recs:
            if r.quality_score is not None:
                by_tier.setdefault(r.tier, []).append(r.quality_score)
        out: dict[str, Any] = {}
        for tier in sorted(by_tier):
            scores = by_tier[tier]
            out[tier] = {
                "min": min(scores),
                "avg": sum(scores) / len(scores),
                "max": max(scores),
                "count": len(scores),
            }
        return out

    @staticmethod
    def _top_quality_concerns(recs: list[EvalRecord], n: int = 10) -> list[tuple[str, int]]:
        """Top N most frequent quality concerns."""
        from collections import Counter

        all_concerns: list[str] = []
        for r in recs:
            all_concerns.extend(r.quality_concerns or [])
        return Counter(all_concerns).most_common(n)

    @staticmethod
    def _guard_check_summary(recs: list[EvalRecord]) -> dict[str, Any]:
        """33-1 task 2-6: agent lane records with guard_check events."""
        agent_recs = [r for r in recs if r.lane == "agent"]
        with_guard = sum(1 for r in agent_recs if r.guard_events)
        return {
            "agent_lane_total": len(agent_recs),
            "with_guard_events": with_guard,
            "guard_intervention_rate": with_guard / len(agent_recs) if agent_recs else 0.0,
        }

    @staticmethod
    def _telemetry_completeness(recs: list[EvalRecord]) -> dict[str, Any]:
        """33-5 task 3: Verify telemetry has tokens, cost, duration for each turn."""
        with_tokens = sum(1 for r in recs if r.build_tokens and r.build_tokens.total_tokens > 0)
        with_cost = sum(1 for r in recs if r.build_tokens and r.build_tokens.estimated_cost > 0)
        with_timing = sum(1 for r in recs if r.timing and r.timing.total_ms > 0)
        with_telemetry = sum(1 for r in recs if r.telemetry)
        return {
            "total": len(recs),
            "with_tokens": with_tokens,
            "with_cost": with_cost,
            "with_timing": with_timing,
            "with_telemetry": with_telemetry,
            "tokens_complete_rate": with_tokens / len(recs) if recs else 0.0,
        }

    @staticmethod
    def _workflow_contract_variants(recs: list[EvalRecord]) -> dict[str, Any]:
        by_variant: dict[str, dict[str, Any]] = {}
        for r in recs:
            variant = r.workflow_contract_variant or "unspecified"
            bucket = by_variant.setdefault(
                variant,
                {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "error": 0,
                    "with_audit": 0,
                    "with_guidance": 0,
                },
            )
            bucket["total"] += 1
            if r.status == "passed":
                bucket["passed"] += 1
            elif r.status == "failed":
                bucket["failed"] += 1
            else:
                bucket["error"] += 1
            if r.audit_found:
                bucket["with_audit"] += 1
            if r.workflow_guidance_injected:
                bucket["with_guidance"] += 1
        for bucket in by_variant.values():
            total = bucket["total"] or 0
            bucket["pass_rate"] = bucket["passed"] / total if total else 0.0
            bucket["audit_found_rate"] = bucket["with_audit"] / total if total else 0.0
            bucket["guidance_injection_rate"] = bucket["with_guidance"] / total if total else 0.0
        return by_variant

    @staticmethod
    def _workflow_guidance_summary(recs: list[EvalRecord]) -> dict[str, Any]:
        audited = [r for r in recs if r.audit_found]
        injected = [r for r in audited if r.workflow_guidance_injected]
        by_surface: dict[str, int] = {}
        for r in injected:
            surface = r.workflow_guidance_surface or "unknown"
            by_surface[surface] = by_surface.get(surface, 0) + 1
        return {
            "total": len(recs),
            "with_audit": len(audited),
            "with_guidance": len(injected),
            "audit_found_rate": len(audited) / len(recs) if recs else 0.0,
            "guidance_injection_rate": len(injected) / len(audited) if audited else 0.0,
            "by_surface": by_surface,
        }

    @staticmethod
    def _workflow_guidance_correlation(recs: list[EvalRecord]) -> dict[str, Any]:
        audited = [r for r in recs if r.audit_found]

        def _bucket(rows: list[EvalRecord]) -> dict[str, Any]:
            total = len(rows)
            passed = sum(1 for r in rows if r.status == "passed")
            run_ready = sum(
                1
                for r in rows
                if r.validation is not None and bool(r.validation.run_ready)
            )
            return {
                "total": total,
                "passed": passed,
                "pass_rate": passed / total if total else 0.0,
                "run_ready": run_ready,
                "run_ready_rate": run_ready / total if total else 0.0,
            }

        injected = [r for r in audited if r.workflow_guidance_injected]
        not_injected = [r for r in audited if not r.workflow_guidance_injected]
        return {
            "audited_total": len(audited),
            "injected": _bucket(injected),
            "not_injected": _bucket(not_injected),
        }

    @staticmethod
    def _workflow_success_metrics(recs: list[EvalRecord]) -> dict[str, Any]:
        from collections import Counter

        validated = [r for r in recs if r.graph_created and r.validation is not None]
        structural_valid = sum(
            1 for r in validated
            if r.validation is not None and bool(r.validation.passed)
        )
        run_ready = sum(
            1 for r in validated
            if r.validation is not None and bool(r.validation.run_ready)
        )

        repair_turns = [
            r for r in recs
            if (r.workflow_guidance_surface == "repair")
            or _looks_like_repair_turn(r.prompt)
        ]
        repair_sequences = {
            _base_record_id(r.id)
            for r in repair_turns
        }

        mechanical_counts = Counter(
            r.failure_mode
            for r in recs
            if r.failure_mode in {
                "validation_error",
                "not_run_ready",
                "expectation_mismatch",
                "codegen_failed",
            }
        )
        repeated_mechanical = {
            mode: count
            for mode, count in mechanical_counts.items()
            if count > 1
        }

        over_claims = [
            r for r in recs
            if _claims_run_ready(r.response_text)
            and (
                r.validation is None
                or not bool(r.validation.run_ready)
                or r.status != "passed"
            )
        ]

        leakage_candidates = [
            r for r in recs
            if r.audit_found and _looks_like_non_workflow_turn(r)
        ]
        leakage_hits = sum(
            1 for r in leakage_candidates
            if r.workflow_guidance_injected
        )

        return {
            "validated_total": len(validated),
            "structural_validity_rate": (
                structural_valid / len(validated) if validated else 0.0
            ),
            "run_ready_rate": (
                run_ready / len(validated) if validated else 0.0
            ),
            "repair_turns_total": len(repair_turns),
            "repair_sequences": len(repair_sequences),
            "avg_repair_turns_per_sequence": (
                len(repair_turns) / len(repair_sequences)
                if repair_sequences else 0.0
            ),
            "repeated_mechanical_mistakes": repeated_mechanical,
            "top_mechanical_failure_modes": mechanical_counts.most_common(5),
            "false_confidence_count": len(over_claims),
            "false_confidence_rate": len(over_claims) / len(recs) if recs else 0.0,
            "workflow_guidance_leakage_candidates": len(leakage_candidates),
            "workflow_guidance_leakage_count": leakage_hits,
            "workflow_guidance_leakage_rate": (
                leakage_hits / len(leakage_candidates)
                if leakage_candidates else 0.0
            ),
        }

    @staticmethod
    def _domain_profiles(recs: list[EvalRecord]) -> dict[str, Any]:
        """33-3 task 7: Domain detection activation by domain_detected."""
        by_domain: dict[str, int] = {}
        for r in recs:
            d = r.domain_detected or "none"
            by_domain[d] = by_domain.get(d, 0) + 1
        return {"by_domain": by_domain, "total_with_domain": sum(c for k, c in by_domain.items() if k != "none")}

    @staticmethod
    def _coverage_32_6(recs: list[EvalRecord]) -> dict[str, Any]:
        """33-3 task 9: 32-6 coverage — t1-03 (review criteria), t1-09 (conditional branch)."""
        targets = {"t1-03", "t1-09"}
        entries: list[dict[str, Any]] = []
        for r in recs:
            if r.id in targets or (r.id.startswith("t1-03") or r.id.startswith("t1-09")):
                entries.append({
                    "id": r.id,
                    "lane": r.lane,
                    "status": r.status,
                    "generation_path": r.generation_path or "unknown",
                    "failure_mode": r.failure_mode,
                })
        return {"targets": list(targets), "entries": entries}

    def _smart_defaults(self, recs: list[EvalRecord]) -> dict[str, Any]:
        """33-3 task 6: Smart defaults (retry policies, validation gates) from stored graphs."""
        if not self._graphs_dir:
            return {"available": False, "reason": "no_graphs_dir"}
        passed = [r for r in recs if r.status == "passed" and r.graph_id]
        has_retry_llm = 0
        has_retry_tool = 0
        has_validator = 0
        checked = 0
        for r in passed:
            safe_id = r.id.replace("/", "_").replace(" ", "_")
            path = self._graphs_dir / f"{safe_id}_{r.lane}.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            nodes = data.get("nodes", [])
            checked += 1
            for n in nodes:
                nt = n.get("node_type", n.get("type", ""))
                cfg = n.get("config", {})
                if nt in ("llm_operator", "llm") and "retry_policy" in cfg:
                    has_retry_llm += 1
                    break
            for n in nodes:
                nt = n.get("node_type", n.get("type", ""))
                cfg = n.get("config", {})
                if nt in ("tool_operator", "tool") and "retry_policy" in cfg:
                    has_retry_tool += 1
                    break
            if any(n.get("node_type", n.get("type")) == "validator" for n in nodes):
                has_validator += 1
        return {
            "available": True,
            "checked": checked,
            "with_llm_retry": has_retry_llm,
            "with_tool_retry": has_retry_tool,
            "with_validator": has_validator,
            "llm_retry_rate": has_retry_llm / checked if checked else 0.0,
            "validator_rate": has_validator / checked if checked else 0.0,
        }

    @staticmethod
    def _reuse_adaptation(recs: list[EvalRecord]) -> dict[str, Any]:
        """33-3 task 8: Reuse/adaptation behavior from T2R prompts."""
        t2r_ids = {"t2r-01", "t2r-02", "t2r-03"}
        entries: list[dict[str, Any]] = []
        for r in recs:
            base_id = r.id.split("-follow")[0] if "-follow" in r.id else r.id
            if base_id not in t2r_ids:
                continue
            mutation_signal = any(
                e.get("type") == "chat_mutation"
                for e in (r.observed_events or [])
            )
            content_signal = any(
                kw in str(e.get("content", "")).lower()
                for e in (r.observed_events or [])
                for kw in ("reuse", "adapt", "existing", "similar")
            )
            entries.append({
                "id": r.id,
                "lane": r.lane,
                "status": r.status,
                "generation_path": r.generation_path or "unknown",
                "mutation_signal": mutation_signal,
                "reuse_adapt_signal": content_signal or mutation_signal,
            })
        return {"targets": list(t2r_ids), "entries": entries}

    @staticmethod
    def _multi_turn_summary(recs: list[EvalRecord]) -> dict[str, Any]:
        """33-4 task 9: Multi-turn progressive refinement — structural mutations vs full rebuild."""
        multi_base = {"m1", "m2", "m3"}
        by_base: dict[str, list[EvalRecord]] = {}
        for r in recs:
            if "-follow" in r.id:
                base = r.id.split("-follow")[0]
            elif r.id in multi_base:
                base = r.id
            else:
                continue
            if base in multi_base:
                by_base.setdefault(base, []).append(r)
        mutation_count = 0
        entries: list[dict[str, Any]] = []
        for base, group in sorted(by_base.items()):
            for r in group:
                has_mutation = any(
                    e.get("type") == "chat_mutation"
                    for e in (r.observed_events or [])
                )
                if has_mutation:
                    mutation_count += 1
                entries.append({
                    "id": r.id,
                    "lane": r.lane,
                    "status": r.status,
                    "generation_path": r.generation_path or "unknown",
                    "structural_mutation": has_mutation,
                })
        total = len(entries)
        return {
            "sequences": list(by_base.keys()),
            "total_turns": total,
            "with_structural_mutation": mutation_count,
            "mutation_rate": mutation_count / total if total else 0.0,
            "entries": entries,
        }

    @staticmethod
    def _t4_domain(recs: list[EvalRecord]) -> dict[str, Any]:
        """33-4 task 8: Domain profiles for T4 prompts (equity, research, Kaggle)."""
        t4_recs = [r for r in recs if r.id.startswith("t4-")]
        by_domain: dict[str, int] = {}
        entries: list[dict[str, Any]] = []
        for r in t4_recs:
            d = r.domain_detected or "none"
            by_domain[d] = by_domain.get(d, 0) + 1
            entries.append({
                "id": r.id,
                "lane": r.lane,
                "domain_detected": d,
                "status": r.status,
            })
        return {
            "total": len(t4_recs),
            "by_domain": by_domain,
            "entries": entries,
        }

    @staticmethod
    def _path_distribution(recs: list[EvalRecord]) -> dict[str, Any]:
        """Per-tier distribution of generation paths from ChatGenerationSummaryEvent."""
        by_tier: dict[str, dict[str, Any]] = {}
        for r in recs:
            path = r.generation_path_taken or r.generation_path or "none"
            td = by_tier.setdefault(r.tier, {
                "paths": {},
                "wall_clock_by_path": {},
                "fallback_count": 0,
                "total": 0,
            })
            td["total"] += 1
            td["paths"][path] = td["paths"].get(path, 0) + 1
            if r.generation_wall_clock_ms is not None:
                td["wall_clock_by_path"].setdefault(path, []).append(
                    r.generation_wall_clock_ms,
                )
            if r.fallback_chain and len(r.fallback_chain) > 1:
                td["fallback_count"] += 1

        result: dict[str, Any] = {}
        for tier in sorted(by_tier):
            td = by_tier[tier]
            total = td["total"]
            paths_pct = {
                p: {"count": c, "pct": c / total if total else 0}
                for p, c in sorted(td["paths"].items())
            }
            avg_wall = {
                p: sum(vs) / len(vs)
                for p, vs in td["wall_clock_by_path"].items()
            }
            result[tier] = {
                "total": total,
                "paths": paths_pct,
                "avg_wall_clock_ms": avg_wall,
                "fallback_rate": td["fallback_count"] / total if total else 0,
            }
        return result

    @staticmethod
    def _t5_latency(recs: list[EvalRecord]) -> dict[str, Any]:
        """T5 prompt latency stats for non-build fast-rejection measurement."""
        t5_recs = [r for r in recs if r.tier.upper() == "T5"]
        if not t5_recs:
            return {}
        times = sorted(r.timing.total_ms for r in t5_recs)
        n = len(times)
        median = (
            times[n // 2]
            if n % 2 == 1
            else (times[n // 2 - 1] + times[n // 2]) / 2
        )
        p90_idx = min(int(n * 0.9), n - 1)
        return {
            "count": n,
            "median_ms": median,
            "p90_ms": times[p90_idx],
            "max_ms": times[-1],
            "target_median_ms": 15_000,
            "meets_target": median <= 15_000,
        }

    @staticmethod
    def _by_tier(recs: list[EvalRecord]) -> dict[str, Any]:
        tiers: dict[str, list[EvalRecord]] = {}
        for r in recs:
            tiers.setdefault(r.tier, []).append(r)

        out: dict[str, Any] = {}
        for tier in sorted(tiers):
            group = tiers[tier]
            t_total = len(group)
            t_passed = sum(1 for r in group if r.status == "passed")
            t_failed = t_total - t_passed
            tokens = [r.build_tokens.total_tokens for r in group if r.build_tokens]
            times = [r.timing.total_ms for r in group]
            fm: dict[str, int] = {}
            for r in group:
                if r.status != "passed" and r.failure_mode:
                    fm[r.failure_mode] = fm.get(r.failure_mode, 0) + 1
            out[tier] = {
                "total": t_total,
                "passed": t_passed,
                "failed": t_failed,
                "pass_rate": t_passed / t_total if t_total else 0.0,
                "avg_tokens": sum(tokens) / len(tokens) if tokens else 0.0,
                "avg_time_ms": sum(times) / len(times) if times else 0.0,
                "failure_modes": fm,
            }
        return out

    # ------------------------------------------------------------------
    # Per-lane breakdown
    # ------------------------------------------------------------------

    @staticmethod
    def _by_lane(recs: list[EvalRecord]) -> dict[str, Any]:
        lanes: dict[str, list[EvalRecord]] = {}
        for r in recs:
            lanes.setdefault(r.lane, []).append(r)
        out: dict[str, Any] = {}
        for lane in sorted(lanes):
            group = lanes[lane]
            l_total = len(group)
            l_passed = sum(1 for r in group if r.status == "passed")
            out[lane] = {
                "total": l_total,
                "passed": l_passed,
                "failed": l_total - l_passed,
                "pass_rate": l_passed / l_total if l_total else 0.0,
            }
        return out

    # ------------------------------------------------------------------
    # Lane comparison (agent-only vs build-only failures)
    # ------------------------------------------------------------------

    @staticmethod
    def _lane_comparison(recs: list[EvalRecord]) -> dict[str, Any]:
        by_id: dict[str, dict[str, EvalRecord]] = {}
        for r in recs:
            by_id.setdefault(r.id, {})[r.lane] = r

        agent_only: list[dict[str, str]] = []
        build_only: list[dict[str, str]] = []
        both_fail: list[dict[str, str]] = []

        for pid, lane_map in sorted(by_id.items()):
            a = lane_map.get("agent")
            b = lane_map.get("build")
            if a is None or b is None:
                continue
            a_fail = a.status != "passed"
            b_fail = b.status != "passed"
            if a_fail and not b_fail:
                agent_only.append({"id": pid, "failure_mode": a.failure_mode or "unknown"})
            elif b_fail and not a_fail:
                build_only.append({"id": pid, "failure_mode": b.failure_mode or "unknown"})
            elif a_fail and b_fail:
                both_fail.append({
                    "id": pid,
                    "agent_failure_mode": a.failure_mode or "unknown",
                    "build_failure_mode": b.failure_mode or "unknown",
                })

        return {
            "agent_only_failures": agent_only,
            "build_only_failures": build_only,
            "both_fail": both_fail,
        }

    # ------------------------------------------------------------------
    # Generation path (intent_compiler vs codegen)
    # ------------------------------------------------------------------

    @staticmethod
    def _generation_path(recs: list[EvalRecord]) -> dict[str, Any]:
        counts: dict[str, int] = {
            "intent_compiler": 0,
            "structured_generation": 0,
            "codegen": 0,
            "unknown": 0,
        }
        by_tier: dict[str, dict[str, Any]] = {}
        for r in recs:
            gp = (
                r.generation_path
                if r.generation_path in ("intent_compiler", "structured_generation", "codegen")
                else "unknown"
            )
            counts[gp] += 1
            tier_entry = by_tier.setdefault(
                r.tier,
                {
                    "intent_compiler": 0,
                    "structured_generation": 0,
                    "codegen": 0,
                    "unknown": 0,
                },
            )
            tier_entry[gp] += 1
        # Add intent compiler activation rate per tier (33-6)
        for tier, tc in by_tier.items():
            t_total = (
                tc["intent_compiler"]
                + tc["structured_generation"]
                + tc["codegen"]
                + tc["unknown"]
            )
            tc["activation_rate"] = (
                (tc["intent_compiler"] + tc["structured_generation"]) / t_total if t_total else 0.0
            )
        return {**counts, "by_tier": by_tier}

    # ------------------------------------------------------------------
    # Failure modes with examples
    # ------------------------------------------------------------------

    @staticmethod
    def _expectation_mismatches(recs: list[EvalRecord]) -> dict[str, Any]:
        matched = [r for r in recs if r.failure_mode == "expectation_mismatch"]
        examples = [
            {"id": r.id, "errors": r.expectation_errors}
            for r in matched[:10]
        ]
        return {"count": len(matched), "examples": examples}

    @staticmethod
    def _judge_summary(recs: list[EvalRecord]) -> dict[str, Any]:
        """LLM-as-judge score summary by tier."""
        by_tier: dict[str, list[dict[str, int]]] = {}
        for r in recs:
            if r.judge_scores:
                by_tier.setdefault(r.tier, []).append(r.judge_scores)
        if not by_tier:
            return {}
        result: dict[str, Any] = {}
        for tier in sorted(by_tier):
            scores_list = by_tier[tier]
            dims = ["prompt_faithfulness", "node_specificity", "data_flow_correctness", "executability"]
            avgs: dict[str, Any] = {}
            for dim in dims:
                vals = [s[dim] for s in scores_list if dim in s]
                avgs[dim] = sum(vals) / len(vals) if vals else 0
            avgs["count"] = len(scores_list)
            result[tier] = avgs
        return result

    @staticmethod
    def _judge_worst_examples(recs: list[EvalRecord], n: int = 5) -> list[dict[str, Any]]:
        """Lowest-scoring judged graphs across all tiers."""
        scored: list[tuple[float, EvalRecord]] = []
        for record in recs:
            if not record.judge_scores:
                continue
            values = list(record.judge_scores.values())
            if not values:
                continue
            scored.append((sum(values) / len(values), record))
        scored.sort(key=lambda item: item[0])
        return [
            {
                "id": record.id,
                "tier": record.tier,
                "avg_score": avg,
                "scores": record.judge_scores,
            }
            for avg, record in scored[:n]
        ]

    @staticmethod
    def _failure_modes(recs: list[EvalRecord]) -> dict[str, Any]:
        modes: dict[str, dict[str, Any]] = {}
        for r in recs:
            if r.status == "passed" or not r.failure_mode:
                continue
            fm = r.failure_mode
            entry = modes.setdefault(fm, {"count": 0, "examples": []})
            entry["count"] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append({"id": r.id, "prompt": r.prompt[:200]})
        return modes

    # ------------------------------------------------------------------
    # Top-N helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _top_token_consumers(recs: list[EvalRecord], n: int = 5) -> list[dict[str, Any]]:
        scored = [
            (r, r.build_tokens.total_tokens)
            for r in recs
            if r.build_tokens
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [{"id": r.id, "tokens": t, "tier": r.tier} for r, t in scored[:n]]

    @staticmethod
    def _top_slowest(recs: list[EvalRecord], n: int = 5) -> list[dict[str, Any]]:
        scored = [(r, r.timing.total_ms) for r in recs]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [{"id": r.id, "time_ms": t, "tier": r.tier} for r, t in scored[:n]]

    # ------------------------------------------------------------------
    # Flakiness (Task 8)
    # ------------------------------------------------------------------

    @staticmethod
    def _flakiness(recs: list[EvalRecord], runs: int) -> dict[str, Any]:
        """Compute per-prompt pass/fail/flaky across runs."""
        by_key: dict[tuple[str, str, str], list[EvalRecord]] = {}
        for r in recs:
            key = (r.id, r.lane, r.workflow_contract_variant or "unspecified")
            by_key.setdefault(key, []).append(r)
        stable_pass = 0
        stable_fail = 0
        stable_error = 0
        flaky = 0
        flaky_prompts: list[dict[str, Any]] = []
        by_contract_variant: dict[str, dict[str, int]] = {}
        for (pid, lane, variant), group in sorted(by_key.items()):
            if len(group) < runs:
                continue
            status_counts = Counter((r.status or "unknown") for r in group)
            variant_bucket = by_contract_variant.setdefault(
                variant,
                {
                    "stable_pass": 0,
                    "stable_fail": 0,
                    "stable_error": 0,
                    "flaky": 0,
                },
            )
            if status_counts == Counter({"passed": runs}):
                stable_pass += 1
                variant_bucket["stable_pass"] += 1
            elif len(status_counts) == 1 and "error" in status_counts:
                stable_error += 1
                variant_bucket["stable_error"] += 1
            elif len(status_counts) == 1:
                stable_fail += 1
                variant_bucket["stable_fail"] += 1
            else:
                flaky += 1
                variant_bucket["flaky"] += 1
                flaky_prompts.append({
                    "id": pid,
                    "lane": lane,
                    "workflow_contract_variant": variant,
                    "statuses": dict(sorted(status_counts.items())),
                    "runs": len(group),
                })
        total = stable_pass + stable_fail + stable_error + flaky
        for bucket in by_contract_variant.values():
            variant_total = sum(bucket.values())
            bucket["flaky_rate"] = bucket["flaky"] / variant_total if variant_total else 0.0
        return {
            "runs": runs,
            "stable_pass": stable_pass,
            "stable_fail": stable_fail,
            "stable_error": stable_error,
            "flaky": flaky,
            "flaky_rate": flaky / total if total else 0.0,
            "by_contract_variant": by_contract_variant,
            "flaky_prompts": flaky_prompts,
        }

    def print_flakiness_report(
        self, records: list[EvalRecord], runs: int
    ) -> None:
        """Print flakiness summary when --runs > 1 (Task 8)."""
        if runs < 2:
            return
        data = self._flakiness(records, runs)
        if _HAS_RICH:
            from rich.console import Console
            from rich.table import Table
            console = Console()
            console.rule("[bold]Flakiness Report[/bold]")
            console.print(
                f"  Stable pass: {data['stable_pass']}  |  "
                f"Stable fail: {data['stable_fail']}  |  "
                f"Stable error: {data['stable_error']}  |  "
                f"Flaky: [yellow]{data['flaky']}[/yellow]  |  "
                f"Flakiness rate: {data['flaky_rate'] * 100:.1f}%"
            )
            if data.get("by_contract_variant"):
                contract_bits = ", ".join(
                    f"{variant}={stats['flaky']} flaky ({stats['flaky_rate'] * 100:.1f}%)"
                    for variant, stats in sorted(data["by_contract_variant"].items())
                )
                console.print(f"  By contract: {contract_bits}")
            if data["flaky_prompts"]:
                tbl = Table(title="Flaky Prompts")
                tbl.add_column("ID")
                tbl.add_column("Lane")
                tbl.add_column("Variant")
                tbl.add_column("Statuses")
                tbl.add_column("Runs", justify="right")
                for p in data["flaky_prompts"][:20]:
                    statuses = ", ".join(
                        f"{status}={count}"
                        for status, count in sorted((p.get("statuses") or {}).items())
                    )
                    tbl.add_row(
                        p["id"],
                        p["lane"],
                        p["workflow_contract_variant"],
                        statuses,
                        str(p["runs"]),
                    )
                console.print(tbl)
            console.print()
        else:
            print("--- Flakiness ---")
            print(
                f"Stable pass: {data['stable_pass']}, "
                f"Stable fail: {data['stable_fail']}, "
                f"Stable error: {data['stable_error']}, "
                f"Flaky: {data['flaky']}, "
                f"Rate: {data['flaky_rate'] * 100:.1f}%"
            )
            if data.get("by_contract_variant"):
                for variant, stats in sorted(data["by_contract_variant"].items()):
                    print(
                        f"  {variant}: stable_pass={stats['stable_pass']} "
                        f"stable_fail={stats['stable_fail']} "
                        f"stable_error={stats['stable_error']} "
                        f"flaky={stats['flaky']} "
                        f"rate={stats['flaky_rate'] * 100:.1f}%"
                    )
            for p in data["flaky_prompts"][:10]:
                statuses = ", ".join(
                    f"{status}={count}"
                    for status, count in sorted((p.get("statuses") or {}).items())
                )
                print(
                    f"  {p['id']}/{p['lane']}/{p['workflow_contract_variant']}: "
                    f"{statuses}"
                )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def print_report(self) -> None:
        s = self.summary()
        if _HAS_RICH:
            _print_rich(s)
        else:
            _print_plain(s)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summary(), indent=2))


# ======================================================================
# Rich output
# ======================================================================


def _load_prompt_catalog(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}

    catalog: dict[str, dict[str, Any]] = {}
    for item in data.get("prompts", []):
        prompt_id = str(item.get("id") or "").strip()
        if not prompt_id:
            continue
        catalog[prompt_id] = {
            "tier": str(item.get("tier") or ""),
            "tags": [
                str(tag).strip().lower()
                for tag in (item.get("tags") or [])
                if str(tag).strip()
            ],
            "multi_turn": bool(item.get("multi_turn_follow_ups")),
        }
    return catalog


def _prompt_catalog_entry_matches_subset(
    entry: dict[str, Any] | None,
    subset: str,
) -> bool:
    if not entry:
        return False

    normalized = str(subset or "").strip().lower().replace("_", "-")
    tags = set(entry.get("tags", []))
    tier = str(entry.get("tier") or "").upper()
    if normalized == "small":
        return tier in _SMALL_BATTERY_TIERS
    if normalized == "complex":
        return tier == "T4" or bool(entry.get("multi_turn"))
    if normalized == "execution-friendly":
        return "execution_friendly" in tags
    if normalized == "benchmark-prep":
        return "benchmark_prep" in tags
    if normalized == "lr2-first":
        return "lr2_first" in tags
    return False


def _ordered_prompt_ids(
    catalog: dict[str, dict[str, Any]],
    prompt_ids: set[str],
    *,
    prefer_lr2: bool = False,
) -> list[str]:
    ordered = [prompt_id for prompt_id in catalog if prompt_id in prompt_ids]
    if prefer_lr2:
        positions = {prompt_id: idx for idx, prompt_id in enumerate(ordered)}
        ordered.sort(
            key=lambda prompt_id: (
                0 if "lr2_first" in set(catalog.get(prompt_id, {}).get("tags", [])) else 1,
                positions[prompt_id],
            ),
        )
    for prompt_id in sorted(prompt_ids):
        if prompt_id not in ordered:
            ordered.append(prompt_id)
    return ordered


def _pct(val: float) -> str:
    return f"{val * 100:.1f}%"


def _base_record_id(record_id: str) -> str:
    return record_id.split("-follow")[0] if "-follow" in record_id else record_id


def _looks_like_repair_turn(prompt: str) -> bool:
    lower = (prompt or "").lower()
    return any(
        token in lower
        for token in (
            "repair",
            "fix the workflow",
            "fix it so",
            "make it runnable",
            "keep the external behavior the same",
        )
    )


def _claims_run_ready(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        token in lower
        for token in (
            "run-ready",
            "ready to run",
            "runnable",
            "successfully ran",
            "successfully run",
            "known to run",
            "it can run",
        )
    )


def _looks_like_non_workflow_turn(record: EvalRecord) -> bool:
    from dan.workflow_generation_guidance import infer_workflow_generation_surface

    return infer_workflow_generation_surface(
        mode=record.lane or "agent",
        user_message=record.prompt or "",
        allow_mutation_tool=(record.lane == "build"),
        graph_is_empty=(record.lane == "build"),
    ) is None


def _print_rich(s: dict[str, Any]) -> None:
    console = Console()

    # 1 — Overall stats
    console.rule("[bold]Eval Summary[/bold]")
    console.print(
        f"  Total: {s['total']}  |  "
        f"Passed: [green]{s['passed']}[/green]  |  "
        f"Failed: [red]{s['failed']}[/red]  |  "
        f"Error: [yellow]{s['error']}[/yellow]  |  "
        f"Pass rate: [bold]{_pct(s['pass_rate'])}[/bold]"
    )
    console.print(
        f"  Build tokens: {s['total_build_tokens']:,}  |  "
        f"Build cost: ${s['total_build_cost']:.4f}  |  "
        f"Run tokens: {s['total_run_tokens']:,}  |  "
        f"Total time: {s['total_time_ms']:,.0f} ms"
    )
    console.print()

    # 2 — Per-tier table
    tier_tbl = Table(title="Per-Tier Breakdown", show_lines=True)
    for col in ("Tier", "Total", "Passed", "Failed", "Rate", "Avg Tokens", "Avg Time (ms)"):
        tier_tbl.add_column(col, justify="right" if col != "Tier" else "left")
    for tier, ts in sorted(s["by_tier"].items()):
        tier_tbl.add_row(
            tier,
            str(ts["total"]),
            str(ts["passed"]),
            str(ts["failed"]),
            _pct(ts["pass_rate"]),
            f"{ts['avg_tokens']:,.0f}",
            f"{ts['avg_time_ms']:,.0f}",
        )
    console.print(tier_tbl)
    console.print()

    # 3 — Per-lane table
    lane_tbl = Table(title="Per-Lane Breakdown", show_lines=True)
    for col in ("Lane", "Total", "Passed", "Failed", "Rate"):
        lane_tbl.add_column(col, justify="right" if col != "Lane" else "left")
    for lane, ls in sorted(s["by_lane"].items()):
        lane_tbl.add_row(
            lane, str(ls["total"]), str(ls["passed"]), str(ls["failed"]), _pct(ls["pass_rate"])
        )
    console.print(lane_tbl)
    console.print()

    named_subsets = s.get("named_subsets", {})
    if named_subsets:
        console.rule("[bold]Named Subsets[/bold]")
        subset_tbl = Table(show_lines=True)
        subset_tbl.add_column("Subset")
        subset_tbl.add_column("Records", justify="right")
        subset_tbl.add_column("Prompts", justify="right")
        subset_tbl.add_column("Passed", justify="right")
        subset_tbl.add_column("Failed", justify="right")
        subset_tbl.add_column("Error", justify="right")
        subset_tbl.add_column("Rate", justify="right")
        for subset, stats in sorted(named_subsets.items()):
            subset_tbl.add_row(
                subset,
                str(stats["total"]),
                str(stats["unique_prompts"]),
                str(stats["passed"]),
                str(stats["failed"]),
                str(stats["error"]),
                _pct(stats["pass_rate"]),
            )
        console.print(subset_tbl)
        console.print()

    efs = s.get("execution_friendly_subset", {})
    if efs.get("total", 0) > 0:
        console.rule("[bold]Execution-Friendly Subset[/bold]")
        console.print(
            f"  Records: {efs['total']}  |  "
            f"Prompts: {efs['unique_prompts']}  |  "
            f"Run-ready: {efs['run_ready']}/{efs['validated']} "
            f"({_pct(efs['run_ready_rate'])})  |  "
            f"Execution completed: {efs['execution_completed']}/{efs['execution_attempted']} "
            f"({_pct(efs['execution_completion_rate'])})"
        )
        console.print()

    bp = s.get("benchmark_prep", {})
    if bp.get("catalog_prompt_order"):
        console.rule("[bold]Frozen-Input Benchmark Prep[/bold]")
        console.print(
            f"  Catalog prompts: {len(bp['catalog_prompt_order'])}  |  "
            f"Observed in this report: {bp.get('unique_prompts', 0)}"
        )
        if bp.get("catalog_lr2_first_prompt_ids"):
            console.print(
                "  LR2-first: " + ", ".join(bp["catalog_lr2_first_prompt_ids"]),
            )
        if bp.get("catalog_remaining_prompt_ids"):
            console.print(
                "  Then: " + ", ".join(bp["catalog_remaining_prompt_ids"]),
            )
        console.print()

    # 4 — Lane comparison
    lc = s["lane_comparison"]
    if lc["agent_only_failures"] or lc["build_only_failures"] or lc["both_fail"]:
        console.rule("[bold]Lane Comparison[/bold]")
        if lc["agent_only_failures"]:
            console.print(f"  [red]Agent-only failures ({len(lc['agent_only_failures'])}):[/red]")
            for f in lc["agent_only_failures"]:
                console.print(f"    {f['id']}: {f['failure_mode']}")
        if lc["build_only_failures"]:
            console.print(f"  [red]Build-only failures ({len(lc['build_only_failures'])}):[/red]")
            for f in lc["build_only_failures"]:
                console.print(f"    {f['id']}: {f['failure_mode']}")
        if lc["both_fail"]:
            console.print(f"  [yellow]Both-lane failures ({len(lc['both_fail'])}):[/yellow]")
            for f in lc["both_fail"]:
                console.print(
                    f"    {f['id']}: agent={f['agent_failure_mode']}, build={f['build_failure_mode']}"
                )
        console.print()

    # 5 — Generation path (intent compiler activation rate per tier, 33-6)
    gp = s["generation_path"]
    gp_total = gp["intent_compiler"] + gp["structured_generation"] + gp["codegen"] + gp["unknown"]
    if gp_total:
        console.rule("[bold]Generation Path / Intent Compiler Activation[/bold]")
        gp_tbl = Table(show_lines=True)
        gp_tbl.add_column("Tier")
        gp_tbl.add_column("Intent Compiler", justify="right")
        gp_tbl.add_column("Structured", justify="right")
        gp_tbl.add_column("Codegen", justify="right")
        gp_tbl.add_column("Unknown", justify="right")
        gp_tbl.add_column("Activation %", justify="right")
        gp_tbl.add_row(
            "[bold]Overall[/bold]",
            f"{gp['intent_compiler']} ({_pct(gp['intent_compiler'] / gp_total)})",
            f"{gp['structured_generation']} ({_pct(gp['structured_generation'] / gp_total)})",
            f"{gp['codegen']} ({_pct(gp['codegen'] / gp_total)})",
            f"{gp['unknown']} ({_pct(gp['unknown'] / gp_total)})",
            _pct((gp["intent_compiler"] + gp["structured_generation"]) / gp_total),
        )
        for tier, tc in sorted(gp.get("by_tier", {}).items()):
            t_total = tc["intent_compiler"] + tc["structured_generation"] + tc["codegen"] + tc["unknown"]
            if not t_total:
                continue
            gp_tbl.add_row(
                tier,
                f"{tc['intent_compiler']} ({_pct(tc['intent_compiler'] / t_total)})",
                f"{tc['structured_generation']} ({_pct(tc['structured_generation'] / t_total)})",
                f"{tc['codegen']} ({_pct(tc['codegen'] / t_total)})",
                f"{tc['unknown']} ({_pct(tc['unknown'] / t_total)})",
                _pct(tc.get("activation_rate", 0.0)),
            )
        console.print(gp_tbl)
        console.print()

    # 6 — Quality by tier
    qbt = s.get("quality_by_tier", {})
    if qbt:
        console.rule("[bold]Quality Score by Tier[/bold]")
        q_tbl = Table(show_lines=True)
        q_tbl.add_column("Tier", justify="left")
        q_tbl.add_column("Min", justify="right")
        q_tbl.add_column("Avg", justify="right")
        q_tbl.add_column("Max", justify="right")
        q_tbl.add_column("Count", justify="right")
        for tier, q in sorted(qbt.items()):
            q_tbl.add_row(
                tier,
                str(q["min"]),
                f"{q['avg']:.1f}",
                str(q["max"]),
                str(q["count"]),
            )
        console.print(q_tbl)
        top_qc = s.get("top_quality_concerns", [])
        if top_qc:
            console.print("\nTop quality concerns:")
            for concern, count in top_qc[:5]:
                console.print(f"  [dim]{count}x[/dim] {concern[:80]}")
        console.print()

    # 6b — Guard check (33-1 task 2-6)
    gcs = s.get("guard_check_summary", {})
    if gcs.get("agent_lane_total", 0) > 0:
        console.rule("[bold]Guard Check (Agent Lane)[/bold]")
        console.print(
            f"  Agent lane: {gcs['agent_lane_total']} records  |  "
            f"With guard events: [yellow]{gcs['with_guard_events']}[/yellow]  |  "
            f"Intervention rate: {_pct(gcs['guard_intervention_rate'])}"
        )
        console.print()

    # 6c — Telemetry completeness (33-5 task 3)
    tc = s.get("telemetry_completeness", {})
    if tc.get("total", 0) > 0:
        console.rule("[bold]Telemetry Completeness[/bold]")
        console.print(
            f"  With tokens: {tc['with_tokens']}/{tc['total']}  |  "
            f"With cost: {tc['with_cost']}/{tc['total']}  |  "
            f"Tokens complete rate: {_pct(tc['tokens_complete_rate'])}"
        )
        console.print()

    # 6d — Workflow contract compare / audit
    wcv = s.get("workflow_contract_variants", {})
    if wcv:
        console.rule("[bold]Workflow Contract Variants[/bold]")
        vc_tbl = Table(show_lines=True)
        vc_tbl.add_column("Variant", justify="left")
        vc_tbl.add_column("Total", justify="right")
        vc_tbl.add_column("Passed", justify="right")
        vc_tbl.add_column("Rate", justify="right")
        vc_tbl.add_column("Audit", justify="right")
        vc_tbl.add_column("Guidance", justify="right")
        for variant, stats in sorted(wcv.items()):
            vc_tbl.add_row(
                variant,
                str(stats["total"]),
                str(stats["passed"]),
                _pct(stats["pass_rate"]),
                _pct(stats["audit_found_rate"]),
                _pct(stats["guidance_injection_rate"]),
            )
        console.print(vc_tbl)
        console.print()

    wgs = s.get("workflow_guidance_summary", {})
    if wgs.get("with_audit", 0) > 0:
        console.rule("[bold]Workflow Guidance Audit[/bold]")
        console.print(
            f"  Audited turns: {wgs['with_audit']}/{wgs['total']}  |  "
            f"Guidance injected: {wgs['with_guidance']}  |  "
            f"Injection rate: {_pct(wgs['guidance_injection_rate'])}"
        )
        if wgs.get("by_surface"):
            surfaces = ", ".join(
                f"{surface}={count}"
                for surface, count in sorted(wgs["by_surface"].items())
            )
            console.print(f"  Surfaces: {surfaces}")
        wgc = s.get("workflow_guidance_correlation", {})
        injected = wgc.get("injected", {})
        not_injected = wgc.get("not_injected", {})
        console.print(
            f"  Pass rate injected/not: {_pct(injected.get('pass_rate', 0.0))} / "
            f"{_pct(not_injected.get('pass_rate', 0.0))}"
        )
        console.print(
            f"  Run-ready injected/not: {_pct(injected.get('run_ready_rate', 0.0))} / "
            f"{_pct(not_injected.get('run_ready_rate', 0.0))}"
        )
        console.print()

    wsm = s.get("workflow_success_metrics", {})
    if wsm.get("validated_total", 0) > 0 or wsm.get("repair_turns_total", 0) > 0:
        console.rule("[bold]Workflow Success Metrics[/bold]")
        console.print(
            f"  Structural validity: {_pct(wsm.get('structural_validity_rate', 0.0))}  |  "
            f"Run-ready: {_pct(wsm.get('run_ready_rate', 0.0))}"
        )
        console.print(
            f"  Repair turns: {wsm.get('repair_turns_total', 0)} across "
            f"{wsm.get('repair_sequences', 0)} sequences  |  "
            f"Avg per sequence: {wsm.get('avg_repair_turns_per_sequence', 0.0):.2f}"
        )
        console.print(
            f"  False-confidence: {wsm.get('false_confidence_count', 0)}  |  "
            f"Leakage: {_pct(wsm.get('workflow_guidance_leakage_rate', 0.0))}"
        )
        top_mechanical = wsm.get("top_mechanical_failure_modes", [])
        if top_mechanical:
            console.print(
                "  Top mechanical failures: "
                + ", ".join(f"{mode}={count}" for mode, count in top_mechanical[:3])
            )
        console.print()

    # 6d — Domain profiles (33-3 task 7)
    dp = s.get("domain_profiles", {})
    if dp.get("by_domain"):
        console.rule("[bold]Domain Profiles[/bold]")
        for dom, cnt in sorted(dp["by_domain"].items(), key=lambda x: -x[1]):
            console.print(f"  {dom}: {cnt}")
        console.print()

    # 6e — 32-6 coverage (33-3 task 9)
    c32 = s.get("coverage_32_6", {})
    if c32.get("entries"):
        console.rule("[bold]32-6 Coverage (t1-03, t1-09)[/bold]")
        for e in c32["entries"]:
            console.print(f"  {e['id']}/{e['lane']}: {e['status']}  path={e['generation_path']}" + (f"  ({e['failure_mode']})" if e.get("failure_mode") else ""))
        console.print()

    # 6f — Smart defaults (33-3 task 6)
    sd = s.get("smart_defaults", {})
    if sd.get("available") and sd.get("checked", 0) > 0:
        console.rule("[bold]Smart Defaults (32-3)[/bold]")
        console.print(
            f"  Checked: {sd['checked']} graphs  |  "
            f"LLM retry: {sd['with_llm_retry']} ({_pct(sd['llm_retry_rate'])})  |  "
            f"Validator: {sd['with_validator']} ({_pct(sd['validator_rate'])})"
        )
        console.print()

    # 6g — Reuse/adaptation (33-3 task 8)
    ra = s.get("reuse_adaptation", {})
    if ra.get("entries"):
        console.rule("[bold]Reuse / Adaptation (T2R)[/bold]")
        for e in ra["entries"]:
            sig = "reuse/adapt" if e.get("reuse_adapt_signal") else "—"
            console.print(f"  {e['id']}/{e['lane']}: {e['status']}  path={e['generation_path']}  signal={sig}")
        console.print()

    # 6h — Multi-turn (33-4 task 9)
    mt = s.get("multi_turn_summary", {})
    if mt.get("entries"):
        console.rule("[bold]Multi-Turn Progressive Refinement[/bold]")
        console.print(
            f"  Sequences: {mt.get('sequences', [])}  |  "
            f"Turns: {mt.get('total_turns', 0)}  |  "
            f"Structural mutation: {mt.get('with_structural_mutation', 0)} ({_pct(mt.get('mutation_rate', 0))})"
        )
        for e in mt["entries"][:10]:
            mut = "mutation" if e.get("structural_mutation") else "rebuild"
            console.print(f"  {e['id']}/{e['lane']}: {e['status']}  path={e['generation_path']}  [{mut}]")
        if len(mt["entries"]) > 10:
            console.print(f"  ... and {len(mt['entries']) - 10} more")
        console.print()

    # 6i — T4 domain (33-4 task 8)
    t4d = s.get("t4_domain", {})
    if t4d.get("total", 0) > 0:
        console.rule("[bold]T4 Domain Profiles[/bold]")
        for dom, cnt in sorted(t4d.get("by_domain", {}).items(), key=lambda x: -x[1]):
            console.print(f"  {dom}: {cnt}")
        for e in t4d.get("entries", [])[:8]:
            console.print(f"  {e['id']}/{e['lane']}: domain={e['domain_detected']}  {e['status']}")
        console.print()

    # 6j — Path distribution (33-9 A.1-3)
    pd = s.get("path_distribution", {})
    if pd:
        console.rule("[bold]Path Distribution (Generation Summary)[/bold]")
        pd_tbl = Table(show_lines=True)
        pd_tbl.add_column("Tier")
        pd_tbl.add_column("Total", justify="right")
        pd_tbl.add_column("Paths")
        pd_tbl.add_column("Avg Wall Clock (ms)")
        pd_tbl.add_column("Fallback %", justify="right")
        for tier, td in sorted(pd.items()):
            paths_str = ", ".join(
                f"{p}={info['count']} ({_pct(info['pct'])})"
                for p, info in sorted(td["paths"].items())
            )
            wall_str = ", ".join(
                f"{p}={ms:,.0f}" for p, ms in sorted(td.get("avg_wall_clock_ms", {}).items())
            )
            pd_tbl.add_row(
                tier,
                str(td["total"]),
                paths_str,
                wall_str or "—",
                _pct(td["fallback_rate"]),
            )
        console.print(pd_tbl)
        console.print()

    # 6k — T5 latency (D.10-2)
    t5l = s.get("t5_latency", {})
    if t5l:
        console.rule("[bold]T5 Latency (Non-Build Fast Rejection)[/bold]")
        target_status = (
            "[green]MEETS TARGET[/green]"
            if t5l.get("meets_target")
            else "[red]ABOVE TARGET[/red]"
        )
        console.print(
            f"  T5 prompts: {t5l['count']}  |  "
            f"Median: {t5l['median_ms']:,.0f} ms  |  "
            f"p90: {t5l['p90_ms']:,.0f} ms  |  "
            f"Max: {t5l['max_ms']:,.0f} ms  |  "
            f"Target: <{t5l['target_median_ms']:,} ms median  |  "
            f"{target_status}"
        )
        console.print()

    # 6l — Expectation mismatches (33-10 B)
    em = s.get("expectation_mismatches", {})
    if em.get("count", 0) > 0:
        console.rule("[bold]Expectation Mismatches[/bold]")
        console.print(f"  Count: [red]{em['count']}[/red]")
        for ex in em.get("examples", []):
            errs = "; ".join(ex["errors"][:5])
            console.print(f"    {ex['id']}: {errs}")
        console.print()

    # 6m — LLM-as-Judge scores (33-10 E)
    js = s.get("judge_summary", {})
    if js:
        console.rule("[bold]LLM-as-Judge Scores[/bold]")
        j_tbl = Table(show_lines=True)
        j_tbl.add_column("Tier")
        j_tbl.add_column("Faithfulness", justify="right")
        j_tbl.add_column("Specificity", justify="right")
        j_tbl.add_column("Data Flow", justify="right")
        j_tbl.add_column("Executability", justify="right")
        j_tbl.add_column("Count", justify="right")
        for tier, scores in sorted(js.items()):
            j_tbl.add_row(
                tier,
                f"{scores.get('prompt_faithfulness', 0):.1f}",
                f"{scores.get('node_specificity', 0):.1f}",
                f"{scores.get('data_flow_correctness', 0):.1f}",
                f"{scores.get('executability', 0):.1f}",
                str(scores.get("count", 0)),
            )
        console.print(j_tbl)
        worst = s.get("judge_worst_examples", [])
        if worst:
            console.print("\nLowest-scoring judged graphs:")
            for item in worst:
                scores = item.get("scores") or {}
                console.print(
                    "  "
                    f"{item['id']} ({item['tier']}): avg={item['avg_score']:.1f} "
                    f"[faithfulness={scores.get('prompt_faithfulness', 0)}, "
                    f"specificity={scores.get('node_specificity', 0)}, "
                    f"data_flow={scores.get('data_flow_correctness', 0)}, "
                    f"executability={scores.get('executability', 0)}]"
                )
        console.print()

    # 7 — Failure modes
    fm = s["failure_modes"]
    if fm:
        console.rule("[bold]Failure Modes[/bold]")
        fm_tbl = Table(show_lines=True)
        fm_tbl.add_column("Mode")
        fm_tbl.add_column("Count", justify="right")
        fm_tbl.add_column("Example Prompt")
        for mode, info in sorted(fm.items(), key=lambda x: x[1]["count"], reverse=True):
            example = info["examples"][0]["prompt"] if info["examples"] else ""
            fm_tbl.add_row(mode, str(info["count"]), example[:80])
        console.print(fm_tbl)
        console.print()

    # 8 — Top token consumers and slowest
    if s["top_token_consumers"]:
        console.rule("[bold]Top-5 Token Consumers[/bold]")
        tk_tbl = Table(show_lines=True)
        tk_tbl.add_column("ID")
        tk_tbl.add_column("Tier")
        tk_tbl.add_column("Tokens", justify="right")
        for e in s["top_token_consumers"]:
            tk_tbl.add_row(e["id"], e["tier"], f"{e['tokens']:,}")
        console.print(tk_tbl)
        console.print()

    if s["top_slowest"]:
        console.rule("[bold]Top-5 Slowest[/bold]")
        sl_tbl = Table(show_lines=True)
        sl_tbl.add_column("ID")
        sl_tbl.add_column("Tier")
        sl_tbl.add_column("Time (ms)", justify="right")
        for e in s["top_slowest"]:
            sl_tbl.add_row(e["id"], e["tier"], f"{e['time_ms']:,.0f}")
        console.print(sl_tbl)


# ======================================================================
# Plain text fallback
# ======================================================================


def _print_plain(s: dict[str, Any]) -> None:
    print("=" * 60)
    print("EVAL SUMMARY")
    print("=" * 60)
    print(
        f"Total: {s['total']}  Passed: {s['passed']}  "
        f"Failed: {s['failed']}  Error: {s['error']}  "
        f"Pass rate: {_pct(s['pass_rate'])}"
    )
    print(
        f"Build tokens: {s['total_build_tokens']:,}  "
        f"Build cost: ${s['total_build_cost']:.4f}  "
        f"Run tokens: {s['total_run_tokens']:,}  "
        f"Total time: {s['total_time_ms']:,.0f} ms"
    )
    print()

    print("--- Per-Tier ---")
    for tier, ts in sorted(s["by_tier"].items()):
        print(
            f"  {tier}: {ts['total']} total, {ts['passed']} passed, "
            f"{ts['failed']} failed, {_pct(ts['pass_rate'])}, "
            f"avg {ts['avg_tokens']:,.0f} tok, {ts['avg_time_ms']:,.0f} ms"
        )
    print()

    print("--- Per-Lane ---")
    for lane, ls in sorted(s["by_lane"].items()):
        print(
            f"  {lane}: {ls['total']} total, {ls['passed']} passed, "
            f"{ls['failed']} failed, {_pct(ls['pass_rate'])}"
        )
    print()

    named_subsets = s.get("named_subsets", {})
    if named_subsets:
        print("--- Named Subsets ---")
        for subset, stats in sorted(named_subsets.items()):
            print(
                f"  {subset}: {stats['total']} records, {stats['unique_prompts']} prompts, "
                f"{stats['passed']} passed, {stats['failed']} failed, "
                f"{stats['error']} error, {_pct(stats['pass_rate'])}"
            )
        print()

    efs = s.get("execution_friendly_subset", {})
    if efs.get("total", 0) > 0:
        print("--- Execution-Friendly Subset ---")
        print(
            f"  Records: {efs['total']}  Prompts: {efs['unique_prompts']}  "
            f"Run-ready: {efs['run_ready']}/{efs['validated']} "
            f"({_pct(efs['run_ready_rate'])})  "
            f"Execution completed: {efs['execution_completed']}/{efs['execution_attempted']} "
            f"({_pct(efs['execution_completion_rate'])})"
        )
        print()

    bp = s.get("benchmark_prep", {})
    if bp.get("catalog_prompt_order"):
        print("--- Frozen-Input Benchmark Prep ---")
        print(
            f"  Catalog prompts: {len(bp['catalog_prompt_order'])}  "
            f"Observed in this report: {bp.get('unique_prompts', 0)}"
        )
        if bp.get("catalog_lr2_first_prompt_ids"):
            print("  LR2-first: " + ", ".join(bp["catalog_lr2_first_prompt_ids"]))
        if bp.get("catalog_remaining_prompt_ids"):
            print("  Then: " + ", ".join(bp["catalog_remaining_prompt_ids"]))
        print()

    lc = s["lane_comparison"]
    if lc["agent_only_failures"]:
        print(f"Agent-only failures: {len(lc['agent_only_failures'])}")
        for f in lc["agent_only_failures"]:
            print(f"  {f['id']}: {f['failure_mode']}")
    if lc["build_only_failures"]:
        print(f"Build-only failures: {len(lc['build_only_failures'])}")
        for f in lc["build_only_failures"]:
            print(f"  {f['id']}: {f['failure_mode']}")
    print()

    gcs = s.get("guard_check_summary", {})
    if gcs.get("agent_lane_total", 0) > 0:
        print("--- Guard Check (Agent Lane) ---")
        print(
            f"  Agent lane: {gcs['agent_lane_total']} records, "
            f"with guard events: {gcs['with_guard_events']}, "
            f"intervention rate: {_pct(gcs['guard_intervention_rate'])}"
        )
        print()

    tc = s.get("telemetry_completeness", {})
    if tc.get("total", 0) > 0:
        print("--- Telemetry Completeness ---")
        print(
            f"  With tokens: {tc['with_tokens']}/{tc['total']}, "
            f"tokens complete rate: {_pct(tc['tokens_complete_rate'])}"
        )
        print()

    wcv = s.get("workflow_contract_variants", {})
    if wcv:
        print("--- Workflow Contract Variants ---")
        for variant, stats in sorted(wcv.items()):
            print(
                f"  {variant}: {stats['total']} total, {stats['passed']} passed, "
                f"rate={_pct(stats['pass_rate'])}, audit={_pct(stats['audit_found_rate'])}, "
                f"guidance={_pct(stats['guidance_injection_rate'])}"
            )
        print()

    wgs = s.get("workflow_guidance_summary", {})
    if wgs.get("with_audit", 0) > 0:
        print("--- Workflow Guidance Audit ---")
        print(
            f"  Audited turns: {wgs['with_audit']}/{wgs['total']}, "
            f"guidance injected: {wgs['with_guidance']}, "
            f"injection rate: {_pct(wgs['guidance_injection_rate'])}"
        )
        if wgs.get("by_surface"):
            surfaces = ", ".join(
                f"{surface}={count}"
                for surface, count in sorted(wgs["by_surface"].items())
            )
            print(f"  Surfaces: {surfaces}")
        wgc = s.get("workflow_guidance_correlation", {})
        print(
            f"  Pass rate injected/not: "
            f"{_pct(wgc.get('injected', {}).get('pass_rate', 0.0))} / "
            f"{_pct(wgc.get('not_injected', {}).get('pass_rate', 0.0))}"
        )
        print(
            f"  Run-ready injected/not: "
            f"{_pct(wgc.get('injected', {}).get('run_ready_rate', 0.0))} / "
            f"{_pct(wgc.get('not_injected', {}).get('run_ready_rate', 0.0))}"
        )
        print()

    wsm = s.get("workflow_success_metrics", {})
    if wsm.get("validated_total", 0) > 0 or wsm.get("repair_turns_total", 0) > 0:
        print("--- Workflow Success Metrics ---")
        print(
            f"  Structural validity: {_pct(wsm.get('structural_validity_rate', 0.0))}, "
            f"run-ready: {_pct(wsm.get('run_ready_rate', 0.0))}"
        )
        print(
            f"  Repair turns: {wsm.get('repair_turns_total', 0)} across "
            f"{wsm.get('repair_sequences', 0)} sequences, "
            f"avg per sequence: {wsm.get('avg_repair_turns_per_sequence', 0.0):.2f}"
        )
        print(
            f"  False-confidence: {wsm.get('false_confidence_count', 0)}, "
            f"leakage: {_pct(wsm.get('workflow_guidance_leakage_rate', 0.0))}"
        )
        top_mechanical = wsm.get("top_mechanical_failure_modes", [])
        if top_mechanical:
            print(
                "  Top mechanical failures: "
                + ", ".join(f"{mode}={count}" for mode, count in top_mechanical[:3])
            )
        print()

    dp = s.get("domain_profiles", {})
    if dp.get("by_domain"):
        print("--- Domain Profiles ---")
        for dom, cnt in sorted(dp["by_domain"].items(), key=lambda x: -x[1]):
            print(f"  {dom}: {cnt}")
        print()

    c32 = s.get("coverage_32_6", {})
    if c32.get("entries"):
        print("--- 32-6 Coverage (t1-03, t1-09) ---")
        for e in c32["entries"]:
            print(f"  {e['id']}/{e['lane']}: {e['status']} path={e['generation_path']}" + (f" ({e['failure_mode']})" if e.get("failure_mode") else ""))
        print()

    sd = s.get("smart_defaults", {})
    if sd.get("available") and sd.get("checked", 0) > 0:
        print("--- Smart Defaults (32-3) ---")
        print(f"  Checked: {sd['checked']}  LLM retry: {sd['with_llm_retry']} ({_pct(sd['llm_retry_rate'])})  Validator: {sd['with_validator']} ({_pct(sd['validator_rate'])})")
        print()

    ra = s.get("reuse_adaptation", {})
    if ra.get("entries"):
        print("--- Reuse / Adaptation (T2R) ---")
        for e in ra["entries"]:
            sig = "reuse/adapt" if e.get("reuse_adapt_signal") else "—"
            print(f"  {e['id']}/{e['lane']}: {e['status']} path={e['generation_path']} signal={sig}")
        print()

    mt = s.get("multi_turn_summary", {})
    if mt.get("entries"):
        print("--- Multi-Turn Progressive Refinement ---")
        print(f"  Sequences: {mt.get('sequences', [])}  Turns: {mt.get('total_turns', 0)}  Mutation rate: {_pct(mt.get('mutation_rate', 0))}")
        for e in mt["entries"][:8]:
            mut = "mutation" if e.get("structural_mutation") else "rebuild"
            print(f"  {e['id']}/{e['lane']}: {e['status']} [{mut}]")
        print()

    t4d = s.get("t4_domain", {})
    if t4d.get("total", 0) > 0:
        print("--- T4 Domain Profiles ---")
        for dom, cnt in sorted(t4d.get("by_domain", {}).items(), key=lambda x: -x[1]):
            print(f"  {dom}: {cnt}")
        print()

    qbt = s.get("quality_by_tier", {})
    if qbt:
        print("--- Quality by Tier ---")
        for tier, q in sorted(qbt.items()):
            print(f"  {tier}: min={q['min']}, avg={q['avg']:.1f}, max={q['max']}, n={q['count']}")
        top_qc = s.get("top_quality_concerns", [])
        if top_qc:
            print("Top quality concerns:")
            for concern, count in top_qc[:5]:
                print(f"  {count}x {concern[:60]}")
        print()

    pd = s.get("path_distribution", {})
    if pd:
        print("--- Path Distribution (Generation Summary) ---")
        for tier, td in sorted(pd.items()):
            paths_str = ", ".join(
                f"{p}={info['count']} ({_pct(info['pct'])})"
                for p, info in sorted(td["paths"].items())
            )
            wall_str = ", ".join(
                f"{p}={ms:,.0f}ms" for p, ms in sorted(td.get("avg_wall_clock_ms", {}).items())
            )
            fb = _pct(td["fallback_rate"])
            print(f"  {tier} (n={td['total']}): {paths_str}  wall={wall_str or '—'}  fallback={fb}")
        print()

    t5l = s.get("t5_latency", {})
    if t5l:
        print("--- T5 Latency (Non-Build Fast Rejection) ---")
        status = "MEETS TARGET" if t5l.get("meets_target") else "ABOVE TARGET"
        print(
            f"  T5 prompts: {t5l['count']}  "
            f"Median: {t5l['median_ms']:,.0f}ms  "
            f"p90: {t5l['p90_ms']:,.0f}ms  "
            f"Max: {t5l['max_ms']:,.0f}ms  "
            f"Target: <{t5l['target_median_ms']:,}ms  [{status}]"
        )
        print()

    em = s.get("expectation_mismatches", {})
    if em.get("count", 0) > 0:
        print("--- Expectation Mismatches ---")
        print(f"  Count: {em['count']}")
        for ex in em.get("examples", []):
            errs = "; ".join(ex["errors"][:5])
            print(f"    {ex['id']}: {errs}")
        print()

    js = s.get("judge_summary", {})
    if js:
        print("--- LLM-as-Judge Scores ---")
        for tier, scores in sorted(js.items()):
            print(
                f"  {tier}: faithfulness={scores.get('prompt_faithfulness', 0):.1f}, "
                f"specificity={scores.get('node_specificity', 0):.1f}, "
                f"data_flow={scores.get('data_flow_correctness', 0):.1f}, "
                f"executability={scores.get('executability', 0):.1f}, "
                f"n={scores.get('count', 0)}"
            )
        worst = s.get("judge_worst_examples", [])
        if worst:
            print("Lowest-scoring judged graphs:")
            for item in worst:
                scores = item.get("scores") or {}
                print(
                    "  "
                    f"{item['id']} ({item['tier']}): avg={item['avg_score']:.1f} "
                    f"[faithfulness={scores.get('prompt_faithfulness', 0)}, "
                    f"specificity={scores.get('node_specificity', 0)}, "
                    f"data_flow={scores.get('data_flow_correctness', 0)}, "
                    f"executability={scores.get('executability', 0)}]"
                )
        print()

    fm = s["failure_modes"]
    if fm:
        print("--- Failure Modes ---")
        for mode, info in sorted(fm.items(), key=lambda x: x[1]["count"], reverse=True):
            ex = info["examples"][0]["prompt"][:60] if info["examples"] else ""
            print(f"  {mode}: {info['count']}  e.g. \"{ex}\"")
    print()

    gp = s.get("generation_path", {})
    gp_total = (
        gp.get("intent_compiler", 0)
        + gp.get("structured_generation", 0)
        + gp.get("codegen", 0)
        + gp.get("unknown", 0)
    )
    if gp_total:
        print("--- Generation Path / Intent Compiler Activation ---")
        print(
            f"  Overall: intent_compiler={gp.get('intent_compiler', 0)}, "
            f"structured_generation={gp.get('structured_generation', 0)}, "
            f"codegen={gp.get('codegen', 0)}, "
            f"activation={_pct((gp.get('intent_compiler', 0) + gp.get('structured_generation', 0)) / gp_total)}"
        )
        for tier, tc in sorted(gp.get("by_tier", {}).items()):
            t_total = (
                tc.get("intent_compiler", 0)
                + tc.get("structured_generation", 0)
                + tc.get("codegen", 0)
                + tc.get("unknown", 0)
            )
            if t_total:
                print(
                    f"  {tier}: intent_compiler={tc.get('intent_compiler', 0)}, "
                    f"structured_generation={tc.get('structured_generation', 0)}, "
                    f"codegen={tc.get('codegen', 0)}, activation={_pct(tc.get('activation_rate', 0.0))}"
                )
        print()

    if s["top_token_consumers"]:
        print("--- Top-5 Token Consumers ---")
        for e in s["top_token_consumers"]:
            print(f"  {e['id']} ({e['tier']}): {e['tokens']:,} tokens")

    if s["top_slowest"]:
        print("--- Top-5 Slowest ---")
        for e in s["top_slowest"]:
            print(f"  {e['id']} ({e['tier']}): {e['time_ms']:,.0f} ms")
