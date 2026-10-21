"""Read-only Phase 6 pilot analysis over validated, frozen campaign artifacts.

Writes only phase6_pilot_analysis.{json,md} in the campaign root. No policy,
scenario, threshold, run artifact, or campaign aggregate is changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any


EXPECTED_PLAN_SHA256 = "a36838b4c36f90d0171d0210696be9bc65ab3e241ed8b61f949bbf91bf1b6784"
POLICIES = ("readiness_gate", "ops_gate", "behavior_gate", "ai_gate", "saferoll")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def describe(values: list[float | int | None]) -> dict[str, float | int | None]:
    ordered = sorted(float(v) for v in values if v is not None)
    if not ordered:
        return {"n": 0, "mean": None, "median": None, "std": None, "min": None, "max": None, "iqr": None}
    def quantile(p: float) -> float:
        index = (len(ordered) - 1) * p
        lo = int(index)
        hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)
    return {"n": len(ordered), "mean": statistics.fmean(ordered), "median": statistics.median(ordered),
            "std": statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
            "min": ordered[0], "max": ordered[-1], "iqr": quantile(.75) - quantile(.25)}


def _by_role(records: list[dict[str, Any]], window: str, role: str) -> dict[str, Any]:
    matches = [r for r in records if r.get("window_id") == window and r.get("backend_role") == role]
    if len(matches) != 1:
        raise ValueError(f"expected one {role} evidence record for {window}; got {len(matches)}")
    return matches[0]


def load_run(campaign: Path, index: dict[str, Any]) -> dict[str, Any]:
    root = campaign / index["run_dir"]
    config = json.loads((root / "config.resolved.json").read_text(encoding="utf-8"))
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    decisions = read_jsonl(root / "decisions.jsonl")
    evaluations = read_jsonl(root / "decision_evaluations.jsonl")
    inputs = read_jsonl(root / "policy_inputs.jsonl")
    systems = read_jsonl(root / "system_evidence.jsonl")
    behaviors = read_jsonl(root / "behavior_evidence.jsonl")
    routes = read_jsonl(root / "routing_states.jsonl")
    observations = read_jsonl(root / "observations.jsonl")
    events = read_jsonl(root / "events.jsonl")
    faults = read_jsonl(root / "fault_events.jsonl")
    with (root / "requests.csv").open(encoding="utf-8", newline="") as stream:
        requests = list(csv.DictReader(stream))
    if len(decisions) != len(evaluations) or not decisions:
        raise ValueError(f"run has no aligned policy decisions: {root}")
    first = decisions[0]
    truth = evaluations[0]
    window = truth["source_window_id"]
    system = {role: _by_role(systems, window, role) for role in ("baseline", "candidate")}
    behavior = {role: _by_role(behaviors, window, role) for role in ("baseline", "candidate")}
    route = [r for r in routes if r["logical_time"] <= first["logical_time"]][-1]
    first_window_routes = [r for r in routes if r["transition_index"] == inputs[0]["transition_index"]
                           and r["capture_point"] == "request"]
    candidate_revision = config["candidate"]["revision"]
    current_behavior_observation = next((o for o in observations if o["observation_type"] == "behavior"
        and o["expected_ground_truth_reference"]["window_id"] == window
        and o["expected_ground_truth_reference"]["revision"] == candidate_revision), None)
    rollback_requested = summary["rollback_requested_time"]
    post_rollback = [e for e in events if e["event_type"] == "REQUEST_COMPLETED"
                     and e["served_revision"] == candidate_revision
                     and rollback_requested is not None and e["logical_time"] > rollback_requested]
    first_system = system["baseline"]
    first_candidate = system["candidate"]
    behavior_baseline = behavior["baseline"]
    behavior_candidate = behavior["candidate"]
    task_type = behavior_candidate["task_type"]
    metric_key = "accuracy" if task_type == "classification" else "exact_match_rate"
    latency_fraction = first_candidate["latency_p95_ms"] / first_system["latency_p95_ms"] - 1 if (
        first_candidate["latency_p95_ms"] is not None and first_system["latency_p95_ms"] not in (None, 0)) else None
    visible_behavior = None
    if "behavior" in inputs[0]:
        visible_behavior = next((r for r in inputs[0]["behavior"] if r["scope_role"] == "candidate"), None)
    return {**{k: index[k] for k in ("run_id", "scenario", "policy", "repetition", "seed", "status")},
            "run_dir": index["run_dir"], "config": config,
            "decisions": [d["decision"] for d in decisions],
            "reason_codes": [d["reason_codes"] for d in decisions],
            "correctness_classes": [e["correctness_class"] for e in evaluations],
            "ground_truth_states": [e["ground_truth_state"] for e in evaluations],
            "critical_errors": [e["critical_error"] for e in evaluations],
            "truth_reasons": [e["truth_reasons"] for e in evaluations],
            "decision_times": [d["logical_time"] for d in decisions],
            "final_outcome": summary["final_outcome"],
            "hold_count": summary["hold_count"],
            "candidate_exposure_requests": summary["candidate_requests"],
            "candidate_exposure_fraction": summary["candidate_fraction"],
            "candidate_requests_during_hold": sum(r["backend_role"] == "candidate"
                                                  and r["desired_rollout_state"] == "HOLDING" for r in requests),
            "candidate_requests_after_first_hold": sum(e["event_type"] == "REQUEST_COMPLETED"
                and e["served_revision"] == candidate_revision and e["logical_time"] > first["logical_time"] for e in events)
                if first["decision"] == "HOLD" else 0,
            "candidate_requests_in_post_hold_reevaluation": sum(e["event_type"] == "REQUEST_COMPLETED"
                and e["served_revision"] == candidate_revision and e["logical_time"] > first["logical_time"]
                and e["details"].get("desired_rollout_state") == "EVALUATING" for e in events)
                if first["decision"] == "HOLD" else 0,
            "pre_decision_candidate_requests": sum(e["event_type"] == "REQUEST_COMPLETED"
                and e["served_revision"] == candidate_revision and e["logical_time"] < first["logical_time"] for e in events),
            "post_rollback_candidate_requests": len(post_rollback),
            "post_rollback_candidate_times": [e["logical_time"] for e in post_rollback],
            "rollback_decision_time": summary["rollback_decision_time"],
            "rollback_requested_time": rollback_requested,
            "last_candidate_served_time": summary["last_candidate_served_time"],
            "effective_zero_candidate_time": summary["effective_zero_candidate_time"],
            "containment_delay_logical_units": summary["effective_zero_candidate_time"] - rollback_requested
                if rollback_requested is not None and summary["effective_zero_candidate_time"] is not None else None,
            "decision_delay_logical_units": first["logical_time"] - first_system["window_start_logical_time"],
            "extra_hold_decision_delay_logical_units": decisions[1]["logical_time"] - first["logical_time"]
                if len(decisions) > 1 else 0,
            "system": {role: {k: records[k] for k in ("window_id", "sample_count", "sufficient", "freshness_status",
                "success_rate", "error_rate", "latency_p50_ms", "latency_p95_ms")}
                for role, records in system.items()},
            "behavior": {role: {k: records[k] for k in ("window_id", "sample_count", "sufficient",
                "freshness_status", "task_type", "accuracy", "exact_match_rate", "structure_valid_rate")}
                for role, records in behavior.items()},
            "behavior_metric_name": metric_key,
            "behavior_metric_delta": behavior_candidate[metric_key] - behavior_baseline[metric_key]
                if behavior_candidate[metric_key] is not None and behavior_baseline[metric_key] is not None else None,
            "latency_relative_delta": latency_fraction,
            "latency_absolute_delta_ms": first_candidate["latency_p95_ms"] - first_system["latency_p95_ms"]
                if first_candidate["latency_p95_ms"] is not None and first_system["latency_p95_ms"] is not None else None,
            "routing_at_decision": {k: route[k] for k in ("desired_state", "observed_state", "effective_state",
                "desired_candidate_weight", "observed_candidate_weight", "effective_candidate_weight",
                "desired_observed_match", "desired_effective_match", "observed_effective_match")},
            "evaluation_routing_request_count": len(first_window_routes),
            "evaluation_routing_mismatch_request_count": sum(not r["desired_observed_match"]
                                                               or not r["desired_effective_match"] for r in first_window_routes),
            "candidate_behavior_observation": {
                "status": current_behavior_observation["observation_status"],
                "source_window_id": current_behavior_observation["source_window_id"],
                "expected_window_id": current_behavior_observation["expected_ground_truth_reference"]["window_id"],
                "observed_age_logical_units": current_behavior_observation["observed_age_logical_units"],
                "observed_accuracy": current_behavior_observation["observed_value"]["accuracy"]
                    if current_behavior_observation["observed_value"] else None,
                "visible_fresh": visible_behavior["fresh"] if visible_behavior else None,
                "visible_age_logical_units": visible_behavior["age_logical_units"] if visible_behavior else None}
                if current_behavior_observation else None,
            "fault_events": [{k: f[k] for k in ("event_type", "fault_type", "domain", "logical_time")}
                             for f in faults],
            "rollback_delay_active_request_count": len({r["request_index"] for r in routes
                if r["capture_point"] == "request" and "rollback_propagation_delay" in r["fault_types"]}),
            "visible_input_keys": sorted(inputs[0]),
            "controller_behavior_sufficient": visible_behavior["sufficient"] if visible_behavior else None,
            "controller_behavior_fresh": visible_behavior["fresh"] if visible_behavior else None}


def analyze(campaign: Path) -> dict[str, Any]:
    plan_path = campaign / "campaign_plan.json"
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    if plan_hash != EXPECTED_PLAN_SHA256:
        raise ValueError(f"pilot plan hash changed: {plan_hash}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validation = json.loads((campaign / "validation/campaign_validation.json").read_text(encoding="utf-8"))
    index = read_jsonl(campaign / "run_index.jsonl")
    if not validation["valid"] or len(plan["rows"]) != 105 or len(index) != 105 or any(r["status"] != "VALID" for r in index):
        raise ValueError("analysis requires all 105 individually valid pilot runs")
    runs = [load_run(campaign, row) for row in index]
    scenarios, policies = plan["scenario_order"], plan["policy_order"]
    matrix = []
    stability = []
    continuous = ("candidate_exposure_requests", "candidate_exposure_fraction",
                  "pre_decision_candidate_requests", "post_rollback_candidate_requests",
                  "containment_delay_logical_units", "latency_relative_delta", "latency_absolute_delta_ms",
                  "behavior_metric_delta", "decision_delay_logical_units", "extra_hold_decision_delay_logical_units",
                  "hold_count", "candidate_requests_during_hold", "candidate_requests_after_first_hold",
                  "candidate_requests_in_post_hold_reevaluation")
    variance = {}
    for scenario in scenarios:
        for policy in policies:
            sample = [r for r in runs if r["scenario"] == scenario and r["policy"] == policy]
            if len(sample) != plan["repetitions"]:
                raise ValueError(f"coverage mismatch: {scenario}/{policy}")
            decisions = [d for r in sample for d in r["decisions"]]
            truths = [v for r in sample for v in r["ground_truth_states"]]
            classes = [v for r in sample for v in r["correctness_classes"]]
            matrix.append({"scenario": scenario, "policy": policy, "runs": len(sample),
                           "decision_count": len(decisions),
                           "promote_count": decisions.count("PROMOTE"), "hold_count": decisions.count("HOLD"),
                           "rollback_count": decisions.count("ROLLBACK"),
                           "ground_truth_counts": dict(sorted(Counter(truths).items())),
                           "correctness_counts": dict(sorted(Counter(classes).items())),
                           "critical_error_count": sum(sum(r["critical_errors"]) for r in sample),
                           "per_repetition_sequences": [{"repetition": r["repetition"], "decisions": r["decisions"],
                               "ground_truth_states": r["ground_truth_states"],
                               "correctness_classes": r["correctness_classes"],
                               "critical_errors": r["critical_errors"]} for r in sample]})
            signature = {(tuple(r["decisions"]), tuple(r["correctness_classes"]), tuple(r["critical_errors"])) for r in sample}
            stability.append({"scenario": scenario, "policy": policy, "stable": len(signature) == 1,
                              "signatures": [s["per_repetition_sequences"] for s in matrix[-1:]][0]})
            variance[f"{scenario}/{policy}"] = {metric: describe([r[metric] for r in sample]) for metric in continuous}
    per_policy = {}
    for policy in policies:
        sample = [r for r in runs if r["policy"] == policy]
        first = [(r["decisions"][0], r["ground_truth_states"][0], r["critical_errors"][0]) for r in sample]
        safe = [x for x in first if x[1] == "SAFE"]
        unsafe = [x for x in first if x[1] == "UNSAFE"]
        per_policy[policy] = {"runs": len(sample), "decisions_all_cycles": sum(len(r["decisions"]) for r in sample),
            "first_decision_counts": dict(sorted(Counter(x[0] for x in first).items())),
            "safe_promotion_rate_first_decision": sum(x[0] == "PROMOTE" for x in safe) / len(safe) if safe else None,
            "unsafe_promotion_rate_first_decision": sum(x[0] == "PROMOTE" for x in unsafe) / len(unsafe) if unsafe else None,
            "hold_rate_first_decision": sum(x[0] == "HOLD" for x in first) / len(first),
            "rollback_rate_first_decision": sum(x[0] == "ROLLBACK" for x in first) / len(first),
            "critical_error_rate_first_decision": sum(x[2] for x in first) / len(first),
            "candidate_exposure_requests": describe([r["candidate_exposure_requests"] for r in sample]),
            "post_rollback_candidate_requests": describe([r["post_rollback_candidate_requests"] for r in sample if r["rollback_requested_time"] is not None]),
            "containment_delay_logical_units": describe([r["containment_delay_logical_units"] for r in sample])}
    by_scenario = {scenario: [r for r in runs if r["scenario"] == scenario] for scenario in scenarios}
    saferoll = {r["scenario"]: [x for x in runs if x["scenario"] == r["scenario"] and x["policy"] == "saferoll"]
                for r in runs if r["policy"] == "saferoll"}
    thresholds = runs[0]["config"]["policy"]
    threshold_values = [{k: v for k, v in r["config"]["policy"].items() if k != "type"} for r in runs]
    if any(value != threshold_values[0] for value in threshold_values):
        raise ValueError("policy thresholds changed within the pilot")
    threshold_audit = {
        "configuration": thresholds,
        "latency_fraction": {"configured": thresholds["system"]["max_latency_regression_fraction"],
            "observed_by_scenario": {s: [r["latency_relative_delta"] for r in saferoll[s]] for s in scenarios},
            "assessment": "NEAR_BOUNDARY for healthy and nonlatency cases (exactly 0.20); latency fault at 5.20 is far beyond 0.20"},
        "max_error_rate": {"configured": thresholds["system"]["max_error_rate"],
            "observed_candidate_rates": sorted({r["system"]["candidate"]["error_rate"] for r in runs}),
            "assessment": "NEVER_EXERCISED: all candidate error rates are zero"},
        "min_readiness_success_rate": {"configured": thresholds["system"]["min_readiness_success_rate"],
            "observed_candidate_rates": sorted({r["system"]["candidate"]["success_rate"] for r in runs}),
            "assessment": "NEVER_CROSSED: all candidate success rates are one"},
        "max_accuracy_drop": {"configured": thresholds["behavior"]["max_accuracy_drop"],
            "observed_by_scenario": {s: [-r["behavior_metric_delta"] for r in saferoll[s]] for s in scenarios},
            "assessment": "WELL_SEPARATED: configured behavior regressions drop 0.25, healthy drops zero"},
        "max_exact_match_drop": {"configured": thresholds["behavior"]["max_exact_match_drop"],
            "assessment": "NEVER_EXERCISED: pilot mock workload is classification"},
        "min_structure_valid_rate": {"configured": thresholds["behavior"]["min_structure_valid_rate"],
            "assessment": "NEVER_EXERCISED: pilot mock workload is classification"},
        "numeric_tolerance": {"configured": thresholds["numeric_tolerance"], "assessment": "fixed across all runs"},
        "system_minimum_samples": {"configured": runs[0]["config"]["evidence"]["minimum_samples_per_revision"],
            "observed_rollback_delay_candidate_counts": [r["system"]["candidate"]["sample_count"]
                for r in by_scenario["rollback_delay"]],
            "assessment": "EXERCISED: one OpsGate seed lacks sufficient candidate system samples"},
        "behavior_minimum_samples": {"configured": runs[0]["config"]["behavior_evidence"]["minimum_samples_per_revision"],
            "observed_candidate_counts": sorted({r["behavior"]["candidate"]["sample_count"] for r in runs}),
            "assessment": "NEVER_EXERCISED: 20 candidate behavior probes in each decision window"},
        "system_max_age_logical_units": {"configured": runs[0]["config"]["evidence"]["max_age_logical_units"],
            "assessment": "age limit not crossed by healthy evidence"},
        "behavior_max_age_logical_units": {"configured": runs[0]["config"]["behavior_evidence"]["max_age_logical_units"],
            "stale_observed_ages": [r["candidate_behavior_observation"]["observed_age_logical_units"]
                for r in saferoll["stale_behavior"]],
            "assessment": "stale status comes from prior-window attribution, not exceeding max-age limit"}}
    fairness = {
        "readiness_gate": {"allowed": ["candidate readiness", "candidate success rate"], "forbidden_keys_absent": all(
            "system" not in r["visible_input_keys"] and "behavior" not in r["visible_input_keys"]
            and "observed_candidate_weight" not in r["visible_input_keys"] for r in runs if r["policy"] == "readiness_gate")},
        "ops_gate": {"allowed": ["system evidence", "system sufficiency", "system freshness"], "forbidden_keys_absent": all(
            "behavior" not in r["visible_input_keys"] and "observed_candidate_weight" not in r["visible_input_keys"]
            for r in runs if r["policy"] == "ops_gate")},
        "behavior_gate": {"allowed": ["behavior evidence", "behavior sufficiency", "behavior freshness"], "forbidden_keys_absent": all(
            "system" not in r["visible_input_keys"] and "observed_candidate_weight" not in r["visible_input_keys"]
            for r in runs if r["policy"] == "behavior_gate")},
        "ai_gate": {"allowed": ["system evidence", "behavior evidence", "both sufficiency/freshness"], "forbidden_keys_absent": all(
            "observed_candidate_weight" not in r["visible_input_keys"] for r in runs if r["policy"] == "ai_gate")},
        "saferoll": {"allowed": ["system", "behavior", "observed/desired state and routing", "attribution"],
            "forbidden_keys_absent": all("effective_candidate_weight" not in r["visible_input_keys"]
                and "fault" not in r["visible_input_keys"] for r in runs if r["policy"] == "saferoll")}}
    scenario_checks = {
        "healthy": {"ground_truth_safe": all(r["ground_truth_states"][0] == "SAFE" for r in by_scenario["healthy"]),
            "all_promote": all(r["decisions"][0] == "PROMOTE" for r in by_scenario["healthy"]),
            "evidence_sufficient_fresh": all(all(r[channel][role]["sufficient"] and r[channel][role]["freshness_status"] == "FRESH"
                for channel in ("system", "behavior") for role in ("baseline", "candidate")) for r in by_scenario["healthy"]),
            "routing_consistent": all(r["routing_at_decision"]["desired_observed_match"]
                and r["routing_at_decision"]["desired_effective_match"] for r in by_scenario["healthy"])},
        "latency_regression": {"all_candidate_p95_exceeds_limit": all(r["latency_relative_delta"] >
            r["config"]["policy"]["system"]["max_latency_regression_fraction"] for r in by_scenario["latency_regression"]),
            "per_repetition": [{"policy": r["policy"], "repetition": r["repetition"],
                "baseline_p95_ms": r["system"]["baseline"]["latency_p95_ms"],
                "candidate_p95_ms": r["system"]["candidate"]["latency_p95_ms"],
                "relative_delta": r["latency_relative_delta"], "decision": r["decisions"][0]}
                for r in by_scenario["latency_regression"]]},
        "behavior_regression": {"per_repetition": [{"policy": r["policy"], "repetition": r["repetition"],
            "task_type": r["behavior"]["candidate"]["task_type"],
            "baseline_metric": r["behavior"]["baseline"][r["behavior_metric_name"]],
            "candidate_metric": r["behavior"]["candidate"][r["behavior_metric_name"]],
            "delta": r["behavior_metric_delta"], "candidate_error_rate": r["system"]["candidate"]["error_rate"],
            "latency_relative_delta": r["latency_relative_delta"], "decision": r["decisions"][0]}
            for r in by_scenario["behavior_regression"]]},
        "routing_mismatch": {"per_repetition": [{"policy": r["policy"], "repetition": r["repetition"],
            **r["routing_at_decision"], "evaluation_mismatch_requests": r["evaluation_routing_mismatch_request_count"],
            "ground_truth": r["ground_truth_states"][0], "decisions": r["decisions"],
            "final_outcome": r["final_outcome"]} for r in by_scenario["routing_mismatch"]]},
        "stale_behavior": {"per_repetition": [{"policy": r["policy"], "repetition": r["repetition"],
            "current_candidate_accuracy": r["behavior"]["candidate"]["accuracy"],
            "controller_observation": r["candidate_behavior_observation"],
            "decisions": r["decisions"], "truth": r["ground_truth_states"]}
            for r in by_scenario["stale_behavior"]]},
        "rollback_delay": {"per_repetition": [{"policy": r["policy"], "repetition": r["repetition"],
            "rollback_decision_time": r["rollback_decision_time"],
            "rollback_requested_time": r["rollback_requested_time"],
            "last_candidate_served_time": r["last_candidate_served_time"],
            "effective_zero_candidate_time": r["effective_zero_candidate_time"],
            "post_rollback_candidate_requests": r["post_rollback_candidate_requests"],
            "effective_delay_logical_units": r["containment_delay_logical_units"],
            "rollback_delay_active_request_count": r["rollback_delay_active_request_count"]}
            for r in by_scenario["rollback_delay"]]},
        "behavior_routing_compound": {"per_repetition": [{"policy": r["policy"], "repetition": r["repetition"],
            "candidate_accuracy": r["behavior"]["candidate"]["accuracy"],
            "desired_candidate_weight": r["routing_at_decision"]["desired_candidate_weight"],
            "observed_candidate_weight": r["routing_at_decision"]["observed_candidate_weight"],
            "effective_candidate_weight": r["routing_at_decision"]["effective_candidate_weight"],
            "decision": r["decisions"][0], "reason_codes": r["reason_codes"][0]}
            for r in by_scenario["behavior_routing_compound"]]}}
    severity = {
        "latency_regression": {"configured_added_latency_ms": 50,
            "measured_relative_delta": describe([r["latency_relative_delta"] for r in saferoll["latency_regression"]]),
            "classification": "TOO_EXTREME", "criterion": "5.20 relative increase is 26 times the 0.20 allowed increase"},
        "behavior_regression": {"configured_every_nth_item": 4,
            "measured_accuracy_drop": describe([-r["behavior_metric_delta"] for r in saferoll["behavior_regression"]]),
            "classification": "USEFUL", "criterion": "0.25 loss clearly exceeds 0.05 while candidate accuracy remains 0.75"},
        "routing_mismatch": {"configured_observation_fault": "stale_observed_routing, 99 requests",
            "classification": "TOO_WEAK", "criterion": "observed routing differs, but desired equals effective and oracle marks candidate SAFE; only abstention cost is exposed"},
        "stale_behavior": {"configured_stale_duration_windows": 1,
            "classification": "USEFUL", "criterion": "older healthy observation masks current 0.75 accuracy; both AIGate and SafeRoll HOLD"},
        "rollback_delay": {"configured_duration_requests": 12,
            "measured_post_rollback_candidate_requests": describe([r["post_rollback_candidate_requests"] for r in saferoll["rollback_delay"]]),
            "classification": "USEFUL", "criterion": "desired zero precedes effective zero by 52 logical units; candidate requests continue"},
        "behavior_routing_compound": {"configured_every_nth_item": 4,
            "classification": "USEFUL", "criterion": "0.25 behavior loss and 0.30 observed routing gap coexist; SafeRoll applies reliable-harm precedence"}}
    ai = {(r["scenario"], r["repetition"]): r for r in runs if r["policy"] == "ai_gate"}
    distinct = []
    positive_ai_advantages = 0
    for r in runs:
        if r["policy"] == "saferoll":
            comparator = ai[(r["scenario"], r["repetition"])]
            if r["correctness_classes"][0].startswith("CORRECT_") and not comparator["correctness_classes"][0].startswith("CORRECT_"):
                positive_ai_advantages += 1
            if r["decisions"] != comparator["decisions"]:
                distinct.append({"scenario": r["scenario"], "repetition": r["repetition"],
                                 "saferoll": r["decisions"], "ai_gate": comparator["decisions"],
                                 "saferoll_classes": r["correctness_classes"],
                                 "ai_gate_classes": comparator["correctness_classes"]})
    routing_cost = saferoll["routing_mismatch"]
    stale_cost = saferoll["stale_behavior"]
    benefit_cost = {
        "vs_ops_gate": {"unsafe_first_promotions_avoided": sum(r["critical_errors"][0] for r in runs if r["policy"] == "ops_gate")
            - sum(r["critical_errors"][0] for r in runs if r["policy"] == "saferoll")},
        "vs_ai_gate": {"positive_correctness_advantages": positive_ai_advantages,
            "decision_differences": distinct,
            "finding": "Only routing mismatch differs, and those SafeRoll HOLDs are oracle-classified unnecessary; no positive decision-correctness advantage appears"},
        "saferoll_cost": {"unnecessary_hold_decisions": sum(c == "UNNECESSARY_HOLD" for r in runs if r["policy"] == "saferoll"
                                                       for c in r["correctness_classes"]),
            "failed_routing_mismatch_runs": sum(r["final_outcome"] == "FAILED" for r in routing_cost),
            "routing_hold_candidate_requests": [r["candidate_requests_during_hold"] for r in routing_cost],
            "routing_post_first_hold_candidate_requests": [r["candidate_requests_after_first_hold"] for r in routing_cost],
            "routing_reevaluation_candidate_requests": [r["candidate_requests_in_post_hold_reevaluation"] for r in routing_cost],
            "routing_additional_decision_delay_logical_units": [r["extra_hold_decision_delay_logical_units"] for r in routing_cost],
            "stale_hold_candidate_requests": [r["candidate_requests_during_hold"] for r in stale_cost],
            "stale_post_first_hold_candidate_requests": [r["candidate_requests_after_first_hold"] for r in stale_cost],
            "stale_additional_decision_delay_logical_units": [r["extra_hold_decision_delay_logical_units"] for r in stale_cost]}}
    recommendations = {
        "retain": ["healthy", "behavior_regression", "stale_behavior", "rollback_delay", "behavior_routing_compound"],
        "revise": ["latency_regression: replace one +50 ms extreme with a prespecified severity sweep",
                   "routing_mismatch: retain observed-only mismatch as an abstention-cost control; add a separate desired/effective mismatch visible to SafeRoll"],
        "remove": [],
        "add_candidates": ["insufficient_behavior_evidence: distinct evidence-abstention case",
            "dropped_system_observation: missing operational evidence",
            "routing_propagation_delay plus stale observed routing: desired/effective mismatch with visible state mismatch",
            "partial_rollback: incomplete containment cost",
            "incorrect_observed_routing: distinguish fabricated from stale state",
            "latency_regression plus missing_system_evidence: compound visibility limit"],
        "thresholds": ["Do not change within this pilot. Before freeze, calibrate max_latency_regression_fraction away from the healthy 0.20 equality boundary using an independent rationale/data split.",
                       "Retain behavior max_accuracy_drop 0.05 provisionally; it is crossed clearly by the 0.25 loss.",
                       "Add targeted operational-error and structured-output scenarios before interpreting unexercised thresholds."],
        "severities": ["Consider latency additions +1/+3/+5 ms (candidate base 12 ms, baseline 10 ms) as 30/50/70 percent total relative increases; declare levels before final campaign.",
                       "Consider deterministic behavior every 10th/5th/4th item (10/20/25 percent accuracy loss on 20 items).",
                       "Consider rollback propagation 5/12/20 requests and observation staleness 1/2/3 windows."],
        "mock_repetitions": "Three repetitions establish exact fixed-signal behavior, but one OpsGate rollback-delay pair changed decision because seeded 0.2 routing produced insufficient samples. Recommend at least 10 prespecified seeds for routing-sensitive mock conditions, or a balanced 10-seed matrix across all conditions; no statistical-power claim.",
        "real_repetitions": "Existing Phase 4G has one validated run per real condition, so variance is unknown. Run a separate 5-repetition REAL_CPU timing pilot, then select the final count from observed variability; do not freeze 5 as a power result.",
        "real_cpu_pilot": "Recommended as a small separate external-validity block: pinned-model healthy and structured-output serving-configuration regression; do not run during this mock pilot."}
    verdict = "NO-GO"
    blockers = ["SafeRoll has no positive decision-correctness advantage over strong AIGate in this seven-scenario pilot.",
                "Observed-only routing mismatch gives six unnecessary SafeRoll HOLD decisions and three FAILED runs against SAFE truth.",
                "Latency regression is extreme (+520 percent) while healthy +20 percent sits exactly at the configured boundary.",
                "One rollback-delay OpsGate seed changes first decision because candidate system evidence has too few samples."]
    return {"analysis_schema_version": "1.0", "campaign_id": plan["campaign_id"], "plan_sha256": plan_hash,
        "integrity": {"campaign_validation": validation, "planned": 105, "indexed": len(index),
            "valid": sum(r["status"] == "VALID" for r in index), "invalid": sum(r["status"] == "INVALID" for r in index),
            "execution_failed": sum(r["status"] == "EXECUTION_FAILED" for r in index),
            "precheck_failed": sum(r["status"] == "PRECHECK_FAILED" for r in index),
            "reused": sum(bool(r.get("reused")) for r in index)},
        "decision_matrix": matrix, "stability": stability,
        "instability": [{"scenario": "rollback_delay", "policy": "ops_gate",
            "cause": "seeded 0.20 candidate routing yielded fewer than the configured three candidate system samples in one evaluation window; initial HOLD then PROMOTE instead of immediate PROMOTE"}]
            if any(not r["stable"] for r in stability) else [],
        "scenario_checks": scenario_checks, "baseline_fairness": fairness,
        "threshold_audit": threshold_audit, "fault_severity_audit": severity,
        "variance_by_scenario_policy": variance, "pilot_metrics_by_policy": per_policy,
        "saferoll_benefit_and_cost": benefit_cost, "proposed_final_campaign_changes": recommendations,
        "pilot_verdict": verdict, "blockers_before_freeze": blockers,
        "interpretation_limit": "Pilot descriptive analysis only; no inferential tests or manuscript-level claims. Policy-specific seeds limit paired exposure comparisons.",
        "per_run_facts": [{k: v for k, v in r.items() if k != "config"} for r in runs]}


def markdown(result: dict[str, Any]) -> str:
    i = result["integrity"]
    lines = ["# Phase 6 pilot analysis", "", "Descriptive pilot review only. No thresholds, faults, policies, or raw runs were changed.", "",
        f"Plan SHA-256: `{result['plan_sha256']}`. Coverage: {i['valid']}/{i['planned']} valid; "
        f"{i['invalid']} invalid, {i['execution_failed']} execution failed, {i['precheck_failed']} precheck failed, {i['reused']} reused.",
        f"Campaign validation: `{json.dumps(i['campaign_validation'], sort_keys=True)}`.", "",
        "## Decision matrix", "", "Counts include every decision cycle; HOLD can produce another decision in the same run.", "",
        "| Scenario | Policy | P | H | R | Ground truth | Correctness classes | Critical errors | Stable |", "| --- | --- | ---: | ---: | ---: | --- | --- | ---: | --- |"]
    stability = {(x["scenario"], x["policy"]): x["stable"] for x in result["stability"]}
    for r in result["decision_matrix"]:
        truth = ", ".join(f"{k}={v}" for k, v in r["ground_truth_counts"].items())
        correct = ", ".join(f"{k}={v}" for k, v in r["correctness_counts"].items())
        lines.append(f"| {r['scenario']} | {r['policy']} | {r['promote_count']} | {r['hold_count']} | {r['rollback_count']} | {truth} | {correct} | {r['critical_error_count']} | {stability[(r['scenario'], r['policy'])]} |")
    lines.extend(["", "## Scenario checks", ""])
    checks = result["scenario_checks"]
    healthy = checks["healthy"]
    lines.append(f"Healthy: SAFE={healthy['ground_truth_safe']}, all-promote={healthy['all_promote']}, "
                 f"sufficient/fresh={healthy['evidence_sufficient_fresh']}, state-consistent={healthy['routing_consistent']}.")
    lines.extend(["", "SafeRoll first-evaluation facts by repetition (B/C = baseline/candidate; routing = desired/observed/effective candidate weight):", "",
        "| Scenario | Rep | p95 B/C (ms) | Accuracy B/C | Routing D/O/E | Decision sequence | Truth sequence |", "| --- | ---: | --- | --- | --- | --- | --- |"])
    for r in result["per_run_facts"]:
        if r["policy"] != "saferoll":
            continue
        system, behavior, route = r["system"], r["behavior"], r["routing_at_decision"]
        lines.append(f"| {r['scenario']} | {r['repetition']} | {system['baseline']['latency_p95_ms']}/{system['candidate']['latency_p95_ms']} | "
                     f"{behavior['baseline']['accuracy']}/{behavior['candidate']['accuracy']} | "
                     f"{route['desired_candidate_weight']}/{route['observed_candidate_weight']}/{route['effective_candidate_weight']} | "
                     f"{' → '.join(r['decisions'])} | {' → '.join(r['ground_truth_states'])} |")
    lines.extend(["", "Stale behavior: the current candidate accuracy is 0.75, while the delivered candidate observation reports 1.00 from a different window.", "",
        "| Rep | Expected window | Source window | Observed age | Status | Visible fresh |", "| ---: | --- | --- | ---: | --- | --- |"])
    for r in checks["stale_behavior"]["per_repetition"]:
        if r["policy"] == "saferoll":
            o = r["controller_observation"]
            lines.append(f"| {r['repetition']} | {o['expected_window_id']} | {o['source_window_id']} | "
                         f"{o['observed_age_logical_units']} | {o['status']} | {o['visible_fresh']} |")
    lines.extend(["", "Rollback propagation: the fault holds effective candidate routing nonzero for 12 requests after rollback is requested.", "",
        "| Rep | Decision time | Requested time | Last candidate served | Effective zero | Post-request candidate requests |", "| ---: | ---: | ---: | ---: | ---: | ---: |"])
    for r in checks["rollback_delay"]["per_repetition"]:
        if r["policy"] == "saferoll":
            lines.append(f"| {r['repetition']} | {r['rollback_decision_time']} | {r['rollback_requested_time']} | "
                         f"{r['last_candidate_served_time']} | {r['effective_zero_candidate_time']} | "
                         f"{r['post_rollback_candidate_requests']} |")
    lines.extend(["", "## Baseline fairness", ""])
    for policy, audit in result["baseline_fairness"].items():
        lines.append(f"- {policy}: allowed {', '.join(audit['allowed'])}; forbidden input keys absent={audit['forbidden_keys_absent']}.")
    lines.extend(["", "## Thresholds and fault severity", ""])
    for name, audit in result["threshold_audit"].items():
        if name != "configuration":
            lines.append(f"- {name}: configured {audit.get('configured', 'see config')}; {audit['assessment']}.")
    for name, audit in result["fault_severity_audit"].items():
        lines.append(f"- {name}: **{audit['classification']}** — {audit['criterion']}.")
    lines.extend(["", "## Stability and variance", ""])
    if result["instability"]:
        for issue in result["instability"]:
            lines.append(f"- {issue['scenario']}/{issue['policy']}: {issue['cause']}.")
    else:
        lines.append("All scenario/policy pairs have identical decision, correctness, and critical-error sequences across repetitions.")
    lines.append("Full mean/median/sample-std/min/max/IQR results for every scenario/policy and continuous metric are in the JSON artifact. Selected summaries:")
    lines.extend(["", "| Scenario/policy | Metric | Mean | Median | Sample std | Min | Max | IQR |",
                  "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for key in ("latency_regression/saferoll", "behavior_regression/saferoll", "rollback_delay/saferoll", "routing_mismatch/saferoll"):
        subset = result["variance_by_scenario_policy"][key]
        for metric in ("latency_relative_delta", "behavior_metric_delta", "candidate_exposure_requests",
                       "post_rollback_candidate_requests", "containment_delay_logical_units", "decision_delay_logical_units"):
            values = subset[metric]
            lines.append(f"| {key} | {metric} | {values['mean']} | {values['median']} | {values['std']} | "
                         f"{values['min']} | {values['max']} | {values['iqr']} |")
    lines.extend(["", "## Descriptive pilot metrics (first decision per run)", "",
        "| Policy | Safe promotion rate | Unsafe promotion rate | HOLD rate | ROLLBACK rate | Critical-error rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for name, values in result["pilot_metrics_by_policy"].items():
        lines.append(f"| {name} | {values['safe_promotion_rate_first_decision']} | "
                     f"{values['unsafe_promotion_rate_first_decision']} | {values['hold_rate_first_decision']} | "
                     f"{values['rollback_rate_first_decision']} | {values['critical_error_rate_first_decision']} |")
    lines.extend(["", "## SafeRoll benefit and cost", ""])
    benefit = result["saferoll_benefit_and_cost"]
    lines.append(f"Versus OpsGate, SafeRoll avoided {benefit['vs_ops_gate']['unsafe_first_promotions_avoided']} unsafe first promotions. "
                 f"Versus AIGate, positive correctness advantages: {benefit['vs_ai_gate']['positive_correctness_advantages']}. "
                 f"{benefit['vs_ai_gate']['finding']}.")
    lines.append(f"SafeRoll unnecessary HOLD decisions: {benefit['saferoll_cost']['unnecessary_hold_decisions']}; "
                 f"routing-mismatch FAILED runs: {benefit['saferoll_cost']['failed_routing_mismatch_runs']}; "
                 f"candidate requests during routing HOLD: {benefit['saferoll_cost']['routing_hold_candidate_requests']}; "
                 f"candidate requests after first routing HOLD (including reevaluation): {benefit['saferoll_cost']['routing_post_first_hold_candidate_requests']}; "
                 f"extra decision delay: {benefit['saferoll_cost']['routing_additional_decision_delay_logical_units']} logical units.")
    lines.extend(["", "## Proposed final-campaign changes (not applied)", ""])
    proposals = result["proposed_final_campaign_changes"]
    for key in ("retain", "revise", "remove", "add_candidates", "thresholds", "severities"):
        lines.append(f"- {key}: " + ("; ".join(proposals[key]) if proposals[key] else "none"))
    lines.append(f"- Mock repetitions: {proposals['mock_repetitions']}")
    lines.append(f"- Real repetitions: {proposals['real_repetitions']}")
    lines.append(f"- Real CPU pilot: {proposals['real_cpu_pilot']}")
    lines.extend(["", f"## Verdict: {result['pilot_verdict']}", ""])
    lines.extend(f"- {reason}" for reason in result["blockers_before_freeze"])
    lines.extend(["", result["interpretation_limit"], ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_dir", type=Path)
    args = parser.parse_args()
    result = analyze(args.campaign_dir)
    (args.campaign_dir / "phase6_pilot_analysis.json").write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8")
    (args.campaign_dir / "phase6_pilot_analysis.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"campaign_id": result["campaign_id"], "valid": result["integrity"]["valid"],
                      "verdict": result["pilot_verdict"]}, sort_keys=True))


if __name__ == "__main__":
    main()
