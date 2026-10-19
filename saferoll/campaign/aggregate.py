"""Descriptive campaign tables; invalid runs never enter scientific aggregates."""
from __future__ import annotations

import csv
from io import StringIO
import json
import math
from pathlib import Path
import statistics
from typing import Any

from ..artifacts import canonical_json, write_json

RUN_COLUMNS = ("campaign_id", "run_id", "scenario", "policy", "repetition", "seed", "backend",
               "decision", "ground_truth", "correctness_class", "critical_error", "candidate_error_rate",
               "baseline_error_rate", "candidate_p50_latency", "baseline_p50_latency",
               "candidate_p95_latency", "baseline_p95_latency", "candidate_behavior_metric",
               "baseline_behavior_metric", "behavior_task_type", "candidate_accuracy", "baseline_accuracy",
               "candidate_structure_valid_rate", "baseline_structure_valid_rate",
               "candidate_exact_match_rate", "baseline_exact_match_rate", "disagreement_rate",
               "paired_sample_count", "decision_logical_time", "evaluation_start_logical_time",
               "decision_delay_logical_units", "hold_count", "final_outcome", "rollback_decision_time",
               "rollback_requested_time", "effective_zero_candidate_time", "last_candidate_served_time",
               "candidate_requests", "candidate_fraction", "fault_affected_requests", "run_duration_seconds",
               "validation_duration_seconds", "valid")
DECISION_COLUMNS = ("campaign_id", "run_id", "scenario", "policy", "repetition", "decision_index",
                    "decision", "reason_codes", "ground_truth_state", "correctness_class", "critical_error")
GROUP_COLUMNS = ("scenario", "policy", "run_count", "valid_run_count", "promote_count", "hold_count",
                 "rollback_count", "unsafe_promotion_count", "unnecessary_rollback_count",
                 "unnecessary_hold_count", "critical_error_count", "promote_rate", "hold_rate",
                 "rollback_rate", "critical_error_rate", "candidate_latency_delta_mean_ms",
                 "candidate_behavior_delta_mean", "candidate_p95_latency_variance", "decision_counts")
FAULT_COLUMNS = ("campaign_id", "run_id", "scenario", "policy", "repetition", "expected_faults",
                 "fault_affected_requests", "fault_activated_events", "fault_cleared_events")


def _read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: canonical_json(row.get(key)) if isinstance(row.get(key), (list, dict))
                         else "" if row.get(key) is None else row[key] for key in columns})
    return buffer.getvalue()


def _evidence_for_decision(records: list[dict[str, Any]], window: str, role: str) -> dict[str, Any]:
    return next((r for r in records if r.get("window_id") == window and r.get("backend_role") == role), {})


def _metric(record: dict[str, Any]) -> float | None:
    if record.get("task_type") == "classification":
        return record.get("accuracy")
    return record.get("exact_match_rate")


def _run_row(plan: dict[str, Any], index: dict[str, Any], directory: Path) -> dict[str, Any]:
    root = directory / index["run_dir"]
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    decisions = _read_lines(root / "decisions.jsonl")
    evaluations = _read_lines(root / "decision_evaluations.jsonl")
    system = _read_lines(root / "system_evidence.jsonl")
    behavior = _read_lines(root / "behavior_evidence.jsonl")
    comparisons = _read_lines(root / "behavior_comparisons.jsonl")
    decision = decisions[0] if decisions else {}
    evaluation = evaluations[0] if evaluations else {}
    window = evaluation.get("source_window_id")
    baseline_system = _evidence_for_decision(system, window, "baseline")
    candidate_system = _evidence_for_decision(system, window, "candidate")
    baseline_behavior = _evidence_for_decision(behavior, window, "baseline")
    candidate_behavior = _evidence_for_decision(behavior, window, "candidate")
    comparison = next((r for r in comparisons if r.get("window_id") == window), {})
    window_start = candidate_system.get("window_start_logical_time")
    decision_time = decision.get("logical_time")
    row = {"campaign_id": plan["campaign_id"], "run_id": plan["run_id"],
           "scenario": plan["scenario"], "policy": plan["policy"], "repetition": plan["repetition"],
           "seed": plan["seed"], "backend": plan["expected_backend"],
           "decision": decision.get("decision"), "ground_truth": evaluation.get("ground_truth_state"),
           "correctness_class": evaluation.get("correctness_class"), "critical_error": evaluation.get("critical_error"),
           "candidate_error_rate": candidate_system.get("error_rate"),
           "baseline_error_rate": baseline_system.get("error_rate"),
           "candidate_p50_latency": candidate_system.get("latency_p50_ms"),
           "baseline_p50_latency": baseline_system.get("latency_p50_ms"),
           "candidate_p95_latency": candidate_system.get("latency_p95_ms"),
           "baseline_p95_latency": baseline_system.get("latency_p95_ms"),
           "candidate_behavior_metric": _metric(candidate_behavior),
           "baseline_behavior_metric": _metric(baseline_behavior),
           "behavior_task_type": candidate_behavior.get("task_type"),
           "candidate_accuracy": candidate_behavior.get("accuracy"),
           "baseline_accuracy": baseline_behavior.get("accuracy"),
           "candidate_structure_valid_rate": candidate_behavior.get("structure_valid_rate"),
           "baseline_structure_valid_rate": baseline_behavior.get("structure_valid_rate"),
           "candidate_exact_match_rate": candidate_behavior.get("exact_match_rate"),
           "baseline_exact_match_rate": baseline_behavior.get("exact_match_rate"),
           "disagreement_rate": comparison.get("disagreement_rate"),
           "paired_sample_count": comparison.get("paired_sample_count"),
           "decision_logical_time": decision_time,
           "evaluation_start_logical_time": window_start,
           "decision_delay_logical_units": decision_time - window_start
               if decision_time is not None and window_start is not None else None,
           "hold_count": summary.get("hold_count"), "final_outcome": summary.get("final_outcome"),
           "rollback_decision_time": summary.get("rollback_decision_time"),
           "rollback_requested_time": summary.get("rollback_requested_time"),
           "effective_zero_candidate_time": summary.get("effective_zero_candidate_time"),
           "last_candidate_served_time": summary.get("last_candidate_served_time"),
           "candidate_requests": summary.get("candidate_requests"),
           "candidate_fraction": summary.get("candidate_fraction"),
           "fault_affected_requests": summary.get("fault_affected_requests"),
           "run_duration_seconds": index.get("run_duration_seconds"),
           "validation_duration_seconds": index.get("validation_duration_seconds"), "valid": True}
    return row


def descriptive(values: list[float]) -> dict[str, float | int | None]:
    """Descriptive repetition summary, no inferential statistics."""
    if not values:
        return {"count": 0, "mean": None, "median": None, "std": None, "iqr": None, "min": None, "max": None}
    ordered = sorted(values)
    def quartile(percent: float) -> float:
        position = (len(ordered) - 1) * percent / 100
        low, high = math.floor(position), math.ceil(position)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return {"count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "std": statistics.stdev(values) if len(values) > 1 else 0.0,
            "iqr": quartile(75) - quartile(25),
            "min": ordered[0], "max": ordered[-1]}


def build_aggregates(directory: Path, plan_rows: list[dict[str, Any]],
                     index_rows: list[dict[str, Any]]) -> dict[str, Any]:
    indexed = {r["run_id"]: r for r in index_rows}
    run_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    fault_rows: list[dict[str, Any]] = []
    for planned in plan_rows:
        indexed_run = indexed.get(planned["run_id"])
        if not indexed_run or indexed_run["status"] != "VALID":
            continue
        run = _run_row(planned, indexed_run, directory)
        run_rows.append(run)
        root = directory / indexed_run["run_dir"]
        decisions = _read_lines(root / "decisions.jsonl")
        evaluations = _read_lines(root / "decision_evaluations.jsonl")
        for sequence, (decision, evaluation) in enumerate(zip(decisions, evaluations)):
            decision_rows.append({"campaign_id": planned["campaign_id"], "run_id": planned["run_id"],
                                  "scenario": planned["scenario"], "policy": planned["policy"],
                                  "repetition": planned["repetition"], "decision_index": sequence,
                                  "decision": decision["decision"], "reason_codes": decision["reason_codes"],
                                  "ground_truth_state": evaluation["ground_truth_state"],
                                  "correctness_class": evaluation["correctness_class"],
                                  "critical_error": evaluation["critical_error"]})
        fault_events = _read_lines(root / "fault_events.jsonl")
        fault_rows.append({"campaign_id": planned["campaign_id"], "run_id": planned["run_id"],
                           "scenario": planned["scenario"], "policy": planned["policy"],
                           "repetition": planned["repetition"], "expected_faults": planned["expected_faults"],
                           "fault_affected_requests": run["fault_affected_requests"],
                           "fault_activated_events": sum(e.get("event_type") == "FAULT_ACTIVATED" for e in fault_events),
                           "fault_cleared_events": sum(e.get("event_type") == "FAULT_CLEARED" for e in fault_events)})
    groups = []
    series: dict[str, Any] = {}
    pairs = list(dict.fromkeys((p["scenario"], p["policy"]) for p in plan_rows))
    for scenario, policy in pairs:
        planned_count = sum(p["scenario"] == scenario and p["policy"] == policy for p in plan_rows)
        subset = [r for r in run_rows if r["scenario"] == scenario and r["policy"] == policy]
        relevant_decisions = [r for r in decision_rows if r["scenario"] == scenario and r["policy"] == policy]
        counts = {name: sum(r["decision"] == name for r in relevant_decisions) for name in ("PROMOTE", "HOLD", "ROLLBACK")}
        n = len(relevant_decisions)
        latency_delta = [r["candidate_p95_latency"] - r["baseline_p95_latency"] for r in subset
                         if r["candidate_p95_latency"] is not None and r["baseline_p95_latency"] is not None]
        behavior_delta = [r["candidate_behavior_metric"] - r["baseline_behavior_metric"] for r in subset
                          if r["candidate_behavior_metric"] is not None and r["baseline_behavior_metric"] is not None]
        crit = sum(bool(r["critical_error"]) for r in relevant_decisions)
        groups.append({"scenario": scenario, "policy": policy, "run_count": planned_count,
                       "valid_run_count": len(subset), "promote_count": counts["PROMOTE"],
                       "hold_count": counts["HOLD"], "rollback_count": counts["ROLLBACK"],
                       "unsafe_promotion_count": sum(r["correctness_class"] == "UNSAFE_PROMOTION" for r in relevant_decisions),
                       "unnecessary_rollback_count": sum(r["correctness_class"] == "UNNECESSARY_ROLLBACK" for r in relevant_decisions),
                       "unnecessary_hold_count": sum(r["correctness_class"] == "UNNECESSARY_HOLD" for r in relevant_decisions),
                       "critical_error_count": crit,
                       "promote_rate": counts["PROMOTE"] / n if n else None,
                       "hold_rate": counts["HOLD"] / n if n else None,
                       "rollback_rate": counts["ROLLBACK"] / n if n else None,
                       "critical_error_rate": crit / n if n else None,
                       "candidate_latency_delta_mean_ms": statistics.fmean(latency_delta) if latency_delta else None,
                       "candidate_behavior_delta_mean": statistics.fmean(behavior_delta) if behavior_delta else None,
                       "candidate_p95_latency_variance": statistics.variance([r["candidate_p95_latency"] for r in subset
                           if r["candidate_p95_latency"] is not None]) if sum(r["candidate_p95_latency"] is not None for r in subset) > 1 else None,
                       "decision_counts": counts})
        series[f"{scenario}/{policy}"] = {"candidate_p95_latency_ms": descriptive([r["candidate_p95_latency"] for r in subset
            if r["candidate_p95_latency"] is not None]), "candidate_behavior_metric": descriptive([r["candidate_behavior_metric"] for r in subset
            if r["candidate_behavior_metric"] is not None]), "latency_delta_ms": descriptive(latency_delta),
            "behavior_delta": descriptive(behavior_delta), "hold_count": descriptive([r["hold_count"] for r in subset
            if r["hold_count"] is not None])}
    campaign_summary = {"schema_version": "1.0", "planned_runs": len(plan_rows), "valid_runs": len(run_rows),
                        "invalid_runs": sum(r["status"] == "INVALID" for r in index_rows),
                        "execution_failed_runs": sum(r["status"] == "EXECUTION_FAILED" for r in index_rows),
                        "precheck_failed_runs": sum(r["status"] == "PRECHECK_FAILED" for r in index_rows),
                        "unattempted_runs": len(plan_rows) - len(index_rows),
                        "total_decisions": len(decision_rows),
                        "critical_error_count": sum(bool(r["critical_error"]) for r in decision_rows),
                        "engineering_run_duration_seconds": sum(r.get("run_duration_seconds") or 0 for r in run_rows),
                        "repetition_descriptives": series}
    return {"run_summary.csv": _csv(run_rows, RUN_COLUMNS),
            "decision_summary.csv": _csv(decision_rows, DECISION_COLUMNS),
            "policy_scenario_summary.csv": _csv(groups, GROUP_COLUMNS),
            "fault_summary.csv": _csv(fault_rows, FAULT_COLUMNS),
            "campaign_summary.json": canonical_json(campaign_summary) + "\n",
            "_runs": run_rows, "_decisions": decision_rows, "_groups": groups, "_summary": campaign_summary}


def write_aggregates(directory: Path, plan_rows: list[dict[str, Any]],
                     index_rows: list[dict[str, Any]]) -> dict[str, Any]:
    outputs = build_aggregates(directory, plan_rows, index_rows)
    target = directory / "aggregate"
    target.mkdir(exist_ok=True)
    for name, value in outputs.items():
        if not name.startswith("_"):
            (target / name).write_text(value, encoding="utf-8")
    return outputs
