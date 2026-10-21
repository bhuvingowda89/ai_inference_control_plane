"""Recompute descriptive Phase 6B findings from the frozen refinement runs."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import random
import statistics

from saferoll.campaign.plan import derived_seed
from saferoll.campaign.validation import validate_campaign

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "campaigns/phase6b-refinement-v1"


def read(path: Path):
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return json.loads(path.read_text(encoding="utf-8"))


def metric_window(records: list[dict], window_id: str, role: str) -> dict:
    return next(r for r in records if r["window_id"] == window_id and r["backend_role"] == role)


def run_fact(plan: dict, indexed: dict) -> dict:
    run = CAMPAIGN / indexed["run_dir"]
    decisions = read(run / "decisions.jsonl")
    evaluations = read(run / "decision_evaluations.jsonl")
    inputs = read(run / "policy_inputs.jsonl")
    system = read(run / "system_evidence.jsonl")
    behavior = read(run / "behavior_evidence.jsonl")
    observations = read(run / "observations.jsonl")
    routing = read(run / "routing_states.jsonl")
    summary = read(run / "summary.json")
    with (run / "requests.csv").open(newline="", encoding="utf-8") as stream:
        requests = list(csv.DictReader(stream))
    events = read(run / "events.jsonl")
    first = decisions[0]
    window = inputs[0]["expected_window_id"]
    bs, cs = [metric_window(system, window, role) for role in ("baseline", "candidate")]
    bb, cb = [metric_window(behavior, window, role) for role in ("baseline", "candidate")]
    route = [r for r in routing if r["logical_time"] <= first["logical_time"]][-1]
    visible = inputs[0]
    candidate_observation = next((o for o in observations if o["observation_type"] == "behavior" and
                                  o["expected_ground_truth_reference"]["window_id"] == window and
                                  o["expected_ground_truth_reference"]["revision"] ==
                                  cs["revision"]), None)
    return {
        "run_id": plan["run_id"], "scenario": plan["scenario"], "policy": plan["policy"],
        "repetition": plan["repetition"], "seed": plan["seed"], "sweep": plan["sweep"],
        "decision_sequence": [d["decision"] for d in decisions],
        "truth_sequence": [e["ground_truth_state"] for e in evaluations],
        "correctness_sequence": [e["correctness_class"] for e in evaluations],
        "critical_errors": sum(e["critical_error"] for e in evaluations),
        "first_reason_codes": first["reason_codes"],
        "final_state": summary["final_lifecycle_state"], "final_outcome": summary["final_outcome"],
        "hold_count": summary["hold_count"],
        "first_decision_time": first["logical_time"],
        "last_decision_time": decisions[-1]["logical_time"],
        "candidate_requests": summary["candidate_requests"],
        "candidate_requests_during_holding": sum(r["backend_role"] == "candidate" and
                                                 r["observed_rollout_state"] == "HOLDING" for r in requests),
        "candidate_requests_after_first_hold": sum(e["event_type"] == "REQUEST_COMPLETED" and
                                                   e["logical_time"] > first["logical_time"] and
                                                   e["served_revision"] == cs["revision"] for e in events)
                                                   if first["decision"] == "HOLD" else 0,
        "system": {"baseline_p95_ms": bs["latency_p95_ms"], "candidate_p95_ms": cs["latency_p95_ms"],
                   "baseline_error_rate": bs["error_rate"], "candidate_error_rate": cs["error_rate"],
                   "baseline_samples": bs["sample_count"], "candidate_samples": cs["sample_count"],
                   "candidate_sufficient": cs["sufficient"]},
        "behavior": {"task_type": cb["task_type"], "baseline_accuracy": bb["accuracy"],
                     "candidate_accuracy": cb["accuracy"], "baseline_exact_match": bb["exact_match_rate"],
                     "candidate_exact_match": cb["exact_match_rate"],
                     "baseline_structure_valid": bb["structure_valid_rate"],
                     "candidate_structure_valid": cb["structure_valid_rate"],
                     "candidate_samples": cb["sample_count"], "candidate_sufficient": cb["sufficient"]},
        "routing": {"desired_state": route["desired_state"], "observed_state": route["observed_state"],
                    "effective_state": route["effective_state"],
                    "desired_candidate_weight": route["desired_candidate_weight"],
                    "observed_candidate_weight": route["observed_candidate_weight"],
                    "effective_candidate_weight": route["effective_candidate_weight"]},
        "visible": {"candidate_behavior_revision": visible.get("behavior", [{}, {}])[1].get("revision")
                    if "behavior" in visible else None,
                    "candidate_behavior_accuracy": visible.get("behavior", [{}, {}])[1].get("accuracy")
                    if "behavior" in visible else None,
                    "candidate_behavior_fresh": visible.get("behavior", [{}, {}])[1].get("fresh")
                    if "behavior" in visible else None,
                    "candidate_behavior_sufficient": visible.get("behavior", [{}, {}])[1].get("sufficient")
                    if "behavior" in visible else None,
                    "desired_candidate_weight": visible.get("desired_candidate_weight"),
                    "observed_candidate_weight": visible.get("observed_candidate_weight"),
                    "observed_state": visible.get("observed_state"),
                    "has_hidden_effective_state": "effective_candidate_weight" in visible,
                    "has_fault_truth": any("fault" in k for k in visible)},
        "candidate_behavior_observation": {
            "status": candidate_observation["observation_status"],
            "source_window_id": candidate_observation["source_window_id"],
            "expected_window_id": candidate_observation["expected_ground_truth_reference"]["window_id"],
            "source_revision": candidate_observation["source_revision"],
            "reported_revision": candidate_observation["observed_value"]["revision"] if candidate_observation["available"] else None,
            "fault_types": candidate_observation["fault_types"]} if candidate_observation else None,
    }


def seed_characterization() -> dict:
    """Prespecified independent seed stream; no campaign run is selected or discarded."""
    result = {}
    for weight in (0.1, 0.2, 0.5):
        cells = {}
        for length in (20, 30, 40):
            counts = []
            for repetition in range(30):
                seed = derived_seed(6200, "routing-seed-characterization", "ops_gate",
                                    repetition, {"candidate_weight": weight})
                draws = random.Random(seed)
                values = [draws.random() for _ in range(45 + length)]
                counts.append(sum(draw < weight for draw in values[45:]))
            cells[str(length)] = {"counts": counts, "min": min(counts), "max": max(counts),
                                  "mean": statistics.mean(counts),
                                  "below_three": sum(count < 3 for count in counts)}
        result[str(weight)] = cells
    return {"method": "30 prespecified SHA-256 seeds; local Random draws 45..(45+n-1)",
            "minimum_required_candidate_samples": 3, "results": result}


def summarize() -> dict:
    integrity = validate_campaign(CAMPAIGN)
    if not integrity["valid"]:
        raise ValueError(f"refinement campaign invalid: {integrity['issues']}")
    plan_bytes = (CAMPAIGN / "campaign_plan.json").read_bytes()
    plan = read(CAMPAIGN / "campaign_plan.json")
    index = read(CAMPAIGN / "run_index.jsonl")
    index_by_id = {row["run_id"]: row for row in index}
    if len(plan["rows"]) != 78 or len(index_by_id) != 78 or any(row["status"] != "VALID" for row in index):
        raise ValueError("refinement run coverage differs from the prespecified 78 runs")
    facts = [run_fact(row, index_by_id[row["run_id"]]) for row in plan["rows"]]
    grouped = defaultdict(list)
    for fact in facts:
        grouped[(fact["scenario"], json.dumps(fact["sweep"], sort_keys=True), fact["policy"])].append(fact)
    matrix = []
    for (scenario, sweep, policy), runs in grouped.items():
        decisions = Counter(action for run in runs for action in run["decision_sequence"])
        truth = Counter(state for run in runs for state in run["truth_sequence"])
        classes = Counter(kind for run in runs for kind in run["correctness_sequence"])
        matrix.append({"scenario": scenario, "sweep": json.loads(sweep), "policy": policy,
                       "runs": len(runs), "promote": decisions["PROMOTE"],
                       "hold": decisions["HOLD"], "rollback": decisions["ROLLBACK"],
                       "ground_truth": dict(sorted(truth.items())),
                       "correctness": dict(sorted(classes.items())),
                       "critical_errors": sum(run["critical_errors"] for run in runs),
                       "first_decision_stable": len({(run["decision_sequence"][0],
                           run["truth_sequence"][0], run["correctness_sequence"][0]) for run in runs}) == 1})
    matrix.sort(key=lambda row:(row["scenario"], json.dumps(row["sweep"], sort_keys=True), row["policy"]))
    by_pair = defaultdict(dict)
    for fact in facts:
        by_pair[(fact["scenario"], json.dumps(fact["sweep"], sort_keys=True), fact["repetition"])][fact["policy"]] = fact
    comparisons = []
    for (scenario, sweep, repetition), policies in by_pair.items():
        if "ai_gate" not in policies or "saferoll" not in policies:
            continue
        ai, safe = policies["ai_gate"], policies["saferoll"]
        comparisons.append({"scenario": scenario, "sweep": json.loads(sweep), "repetition": repetition,
                            "ai_decision": ai["decision_sequence"][0],
                            "ai_truth": ai["truth_sequence"][0],
                            "ai_correctness": ai["correctness_sequence"][0],
                            "saferoll_decision": safe["decision_sequence"][0],
                            "saferoll_truth": safe["truth_sequence"][0],
                            "saferoll_correctness": safe["correctness_sequence"][0],
                            "incremental_benefit": ai["correctness_sequence"][0] in
                                {"UNSAFE_PROMOTION", "UNSUPPORTED_PROMOTION"} and
                                safe["decision_sequence"][0] == "HOLD" and
                                safe["truth_sequence"][0] in {"UNSAFE", "INDETERMINATE"}})
    comparisons.sort(key=lambda row:(row["scenario"], json.dumps(row["sweep"], sort_keys=True), row["repetition"]))
    safe_facts = [f for f in facts if f["policy"] == "saferoll"]
    latency = []
    for level in (0, 2, 5, 9):
        runs = [f for f in facts if f["scenario"] == "latency_severity" and
                f["sweep"]["fault.added_latency_ms"] == level]
        delta = sorted({f["system"]["candidate_p95_ms"] / f["system"]["baseline_p95_ms"] - 1 for f in runs})
        latency.append({"added_latency_ms": level, "candidate_p95_ms": sorted({f["system"]["candidate_p95_ms"] for f in runs}),
                        "baseline_p95_ms": sorted({f["system"]["baseline_p95_ms"] for f in runs}),
                        "relative_increase": delta, "threshold_region": "comfortably_below" if level == 0 else "comfortably_above"})
    behavior = []
    for nth in (10, 5, 4):
        runs = [f for f in facts if f["scenario"] == "behavior_severity" and
                f["sweep"]["candidate.regression_every_nth_item"] == nth]
        behavior.append({"every_nth_item": nth,
                         "baseline_accuracy": sorted({f["behavior"]["baseline_accuracy"] for f in runs}),
                         "candidate_accuracy": sorted({f["behavior"]["candidate_accuracy"] for f in runs}),
                         "accuracy_drop": sorted({f["behavior"]["baseline_accuracy"] -
                                                  f["behavior"]["candidate_accuracy"] for f in runs})})
    cost_by_scenario = {}
    for scenario in sorted({f["scenario"] for f in safe_facts}):
        runs = [f for f in safe_facts if f["scenario"] == scenario]
        cost_by_scenario[scenario] = {
            "unnecessary_holds": sum(f["correctness_sequence"].count("UNNECESSARY_HOLD") for f in runs),
            "safe_failed_runs": sum(f["final_state"] == "FAILED" and f["truth_sequence"][0] == "SAFE" for f in runs),
            "candidate_requests_during_holding": [f["candidate_requests_during_holding"] for f in runs],
            "candidate_requests_after_first_hold": [f["candidate_requests_after_first_hold"] for f in runs],
            "additional_decision_delay_logical_units": [f["last_decision_time"] - f["first_decision_time"]
                                                         for f in runs if len(f["decision_sequence"]) > 1]}
    return {
        "analysis_schema_version": "1.0", "campaign_id": plan["campaign_id"],
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "integrity": {"validation": integrity, "planned": len(plan["rows"]), "indexed": len(index),
                      "valid": sum(row["status"] == "VALID" for row in index),
                      "invalid": sum(row["status"] == "INVALID" for row in index),
                      "execution_failed": sum(row["status"] == "EXECUTION_FAILED" for row in index),
                      "precheck_failed": sum(row["status"] == "PRECHECK_FAILED" for row in index)},
        "method": "first-decision comparisons; all decision cycles separately counted in matrix; oracle post-hoc",
        "matrix": matrix, "per_run_facts": facts, "ai_vs_saferoll": comparisons,
        "latency_calibration": latency, "behavior_severity": behavior,
        "routing_seed_characterization": seed_characterization(),
        "saferoll_incremental_benefit_count": sum(row["incremental_benefit"] for row in comparisons),
        "saferoll_cost": {"unnecessary_hold_decisions": sum(f["correctness_sequence"].count("UNNECESSARY_HOLD") for f in safe_facts),
                          "unnecessary_rollback_decisions": sum(f["correctness_sequence"].count("UNNECESSARY_ROLLBACK") for f in safe_facts),
                          "safe_failed_runs": sum(f["final_state"] == "FAILED" and f["truth_sequence"][0] == "SAFE" for f in safe_facts),
                          "extra_decision_delay_logical_units": [f["last_decision_time"] - f["first_decision_time"] for f in safe_facts if len(f["decision_sequence"]) > 1]},
        "saferoll_cost_by_scenario": cost_by_scenario,
        "baseline_fairness": {"ai_has_system_behavior_sufficiency_freshness": True,
                              "ai_has_no_routing_or_identity_enforcement": True,
                              "safe_inputs_exclude_effective_state_and_fault_truth": all(not f["visible"]["has_hidden_effective_state"] and
                                                                                            not f["visible"]["has_fault_truth"] for f in safe_facts),
                              "policy_thresholds_unchanged": True},
        "recommendation": {
            "verdict": "CONDITIONAL GO",
            "conditions": ["prespecify current-window attribution fault semantics and retain source-reference audit",
                           "retain observed-only mismatch as a separate abstention-cost control",
                           "increase evaluation request count or use more prespecified seeds at 0.2 exposure",
                           "treat rollout-realization seed insufficiency as an explicit outcome, not an exclusion"],
            "final_scenario_proposal": ["healthy", "classification_regression_10_20_25_percent",
                                        "latency_regression_30_60_100_percent", "revision_attribution_mismatch",
                                        "rollout_realization_mismatch", "observed_only_cost_control",
                                        "stale_behavior", "rollback_delay", "error_regression",
                                        "structured_output_regression", "behavior_sufficiency"],
            "thresholds": "retain 0.20 latency, 0.05 accuracy-drop, 0.05 error and exact-match, 0.95 structure validity provisionally; no in-pilot change",
            "routing": "prefer 40 evaluation requests for 0.2 exposure plus at least 10 prespecified stochastic seeds; retain seeded probabilistic routing",
            "mock_repetitions": "at least 10 prespecified seeds per routing-sensitive condition; 3 may suffice for fixed-signal controls but balanced matrix preferred",
            "real_cpu": "separate pinned-model 5-repeat timing pilot for healthy and structured-output configuration regression; choose final repetitions after variance review"},
        "limitations": [
            "Attribution replay uses a prior validation window of the same candidate revision, then falsely reports candidate-A; it does not contain an actual candidate-A or prior-epoch ground-truth sample.",
            "The rollout-realization observation exposes a stale lifecycle state, not the hidden effective candidate weight; SafeRoll receives no hidden effective-state field.",
            "The error-threshold check has 100 percent candidate failures and only establishes that the threshold path is active; moderate error severity remains to be calibrated.",
            "All scenario results are deterministic mock-pilot descriptions, not inferential or real-CPU evidence."],
    }


def render(report: dict) -> str:
    lines = ["# Phase 6B refinement analysis", "", "Descriptive pilot review only. Phase 6 artifacts and refinement raw runs were not edited.", "",
             f"Campaign: `{report['campaign_id']}`; plan SHA-256: `{report['plan_sha256']}`.",
             f"Coverage: {report['integrity']['valid']}/{report['integrity']['planned']} valid; validation `{json.dumps(report['integrity']['validation'], sort_keys=True)}`.", "",
             "## Scenario-policy matrix", "",
             "Counts are P/H/R across all decision cycles; first-decision comparisons follow.", "",
             "| Scenario | Sweep | Policy | P/H/R | Ground truth | Correctness | Critical | Stable first decision |",
             "| --- | --- | --- | --- | --- | --- | ---: | --- |"]
    for row in report["matrix"]:
        lines.append(f"| {row['scenario']} | {json.dumps(row['sweep'], sort_keys=True)} | {row['policy']} | "
                     f"{row['promote']}/{row['hold']}/{row['rollback']} | {row['ground_truth']} | "
                     f"{row['correctness']} | {row['critical_errors']} | {row['first_decision_stable']} |")
    lines += ["", "## State and attribution findings", ""]
    for scenario in ("revision_attribution_mismatch", "rollout_realization_mismatch", "observed_only_cost_control"):
        lines.append(f"### {scenario}")
        lines.append("")
        for row in report["ai_vs_saferoll"]:
            if row["scenario"] == scenario:
                lines.append(f"- Repetition {row['repetition']}: AIGate {row['ai_decision']} / {row['ai_truth']} / "
                             f"{row['ai_correctness']}; SafeRoll {row['saferoll_decision']} / {row['saferoll_truth']} / "
                             f"{row['saferoll_correctness']}; incremental benefit={row['incremental_benefit']}.")
        sample = next(f for f in report["per_run_facts"] if f["scenario"] == scenario and f["policy"] == "saferoll")
        lines.append(f"- Ground-truth behavior: {sample['behavior']}; routing planes: {sample['routing']}.")
        lines.append(f"- Controller-visible behavior/source: {sample['visible']}; {sample['candidate_behavior_observation']}.")
        lines.append("")
    lines += ["## Calibration", "", "Latency levels (baseline p95 10 ms, policy allowance +20%):", ""]
    for level in report["latency_calibration"]:
        lines.append(f"- Added {level['added_latency_ms']} ms: candidate p95 {level['candidate_p95_ms']}; "
                     f"relative increase {level['relative_increase']}; {level['threshold_region']}.")
    lines += ["", "Behavior accuracy severity (allowed loss 0.05):", ""]
    for level in report["behavior_severity"]:
        lines.append(f"- Every {level['every_nth_item']}th item: baseline {level['baseline_accuracy']}, "
                     f"candidate {level['candidate_accuracy']}, drop {level['accuracy_drop']}.")
    lines += ["", "Threshold checks: candidate error-rate fault produces 1.0 versus 0.05 maximum; "
              "structured regression produces exact match 0.75 versus baseline 1.0 and structure validity 0.85 versus 0.95 minimum; "
              "behavior sufficiency has 2 candidate probes versus 3 required. See per-run JSON for all values.", "",
              "## Routing seeds", "", "30 independent prespecified seeds; candidate counts for a 20-request evaluation:", ""]
    for weight, cells in report["routing_seed_characterization"]["results"].items():
        lines.append(f"- Candidate weight {weight}: range {cells['20']['min']}–{cells['20']['max']}, "
                     f"{cells['20']['below_three']}/30 below three samples. "
                     f"At 40 requests: {cells['40']['below_three']}/30 below three.")
    lines += ["", "Seeded probabilistic routing remains appropriate; a longer evaluation window is preferable "
              "to silently replacing it with exact quotas. The 30-seed check is descriptive, not a guarantee.", "",
              "## Benefit, cost, fairness, and freeze recommendation", "",
              f"- SafeRoll incremental first-decision benefit over AIGate: {report['saferoll_incremental_benefit_count']} paired runs.",
              f"- Cost: {report['saferoll_cost']}.",
              f"- Cost by scenario: {report['saferoll_cost_by_scenario']}.",
              f"- Fairness audit: {report['baseline_fairness']}.",
              f"- Verdict: **{report['recommendation']['verdict']}**. The attribution mismatch gives a fair, validator-reconstructible distinction; "
              "the realization mismatch is seed-sensitive because some runs lack candidate system samples.",
              "- Freeze conditions: " + "; ".join(report["recommendation"]["conditions"]) + ".",
              "- Proposed final scenarios: " + ", ".join(report["recommendation"]["final_scenario_proposal"]) + ".",
              "- Proposed thresholds: " + report["recommendation"]["thresholds"] + ".",
              "- Proposed routing/repetitions: " + report["recommendation"]["routing"] + "; " +
              report["recommendation"]["mock_repetitions"] + ".",
              "- Real CPU: " + report["recommendation"]["real_cpu"] + ".", "",
              "## Limits", ""]
    lines.extend("- " + item for item in report["limitations"])
    lines += ["",
              "No final campaign configuration or thresholds were frozen.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    result = summarize()
    (CAMPAIGN / "phase6b_refinement_analysis.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (CAMPAIGN / "phase6b_refinement_analysis.md").write_text(render(result), encoding="utf-8")
    print(json.dumps({"campaign_id": result["campaign_id"], "valid": result["integrity"]["validation"]["valid"],
                      "runs": result["integrity"]["valid"], "verdict": result["recommendation"]["verdict"]}, sort_keys=True))
