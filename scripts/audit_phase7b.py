#!/usr/bin/env python3
"""Document the final-v1 scientific stop and audit every frozen severity definition."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from saferoll.artifacts import write_json
from saferoll.campaign.aggregate import descriptive

V1 = ROOT / "campaigns/final-v1"
OUT = ROOT / "campaigns/phase7b-error-calibration-v1"


def read_lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def audit() -> None:
    freeze = json.loads((V1 / "campaign_freeze.json").read_text())
    plan = json.loads((V1 / "campaign_plan.json").read_text())
    index = {r["run_id"]: r for r in read_lines(V1 / "run_index.jsonl")}
    stop = json.loads((V1 / "phase8_stop_audit.json").read_text())
    rows = []
    for p in plan["rows"]:
        if p["scenario"] != "candidate_error" or p["run_id"] not in index:
            continue
        run = V1 / index[p["run_id"]]["run_dir"]
        config = json.loads((run / "config.resolved.json").read_text())
        evidence = [x for x in read_lines(run / "system_evidence.jsonl")
                    if x["backend_role"] == "candidate" and x["lifecycle_state"] == "EVALUATING"]
        if not evidence:
            continue
        first = evidence[0]
        cadence = config["phase4e"]["request_faults"][0]["every_nth_candidate"]
        candidate_count = first["request_count"]
        failure_count = first["failure_count"]
        rows.append({"run_id": p["run_id"], "severity": p["severity"], "policy": p["policy"],
            "seed": p["seed"], "eligible_candidate_count": candidate_count,
            "fault_count": failure_count, "cadence": cadence,
            "expected_fault_count": candidate_count // cadence,
            "realized_rate": first["error_rate"],
            "discrete_rate_step": 1 / candidate_count if candidate_count else None})
    if not rows or any(r["fault_count"] != r["expected_fault_count"] for r in rows):
        raise ValueError("final-v1 fault mechanism does not match cadence model")
    by_level = {}
    for severity in sorted({r["severity"] for r in rows}):
        subset = [r for r in rows if r["severity"] == severity]
        by_level[severity] = {"run_count": len(subset),
            "candidate_counts": descriptive([r["eligible_candidate_count"] for r in subset]),
            "fault_counts": descriptive([r["fault_count"] for r in subset]),
            "rates": descriptive([r["realized_rate"] for r in subset])}
    root = {"status": "STOPPED_SCIENTIFIC_DESIGN_MISMATCH",
            "frozen_freeze_sha256": freeze["freeze_sha256"],
            "frozen_plan_sha256": freeze["plan_sha256"],
            "planned": 650, "attempted": len(index), "valid": sum(x["status"] == "VALID" for x in index.values()),
            "unattempted": 650 - len(index), "invalid": sum(x["status"] != "VALID" for x in index.values()),
            "final_v1_artifacts_preserved": True,
            "mechanism": {"routing": "Python random.Random(seed) draws once per request; candidate iff draw is below configured 0.50 weight in candidate-error conditions",
                          "request_window": [45, 84], "evaluation_request_budget": 40,
                          "eligibility": "only candidate responses with inclusive request index 45..84 increment the fault counter",
                          "placement": "eligible candidate counts 1..N; cadence n fails counts n,2n,...,floor(N/n)*n",
                          "formula": "fault_count=floor(eligible_candidate_count/cadence); realized_rate=fault_count/eligible_candidate_count",
                          "rounding": "integer fault counts produce rate steps of 1/candidate_count",
                          "finite_count": "40 evaluation requests at 0.50 candidate weight give variable candidate denominators; no 60-request/0.20 guarantee applies to this condition"},
            "observed_by_level": by_level, "error_runs_audited": len(rows),
            "outlier_count_in_stop_artifact": len(json.loads((V1 / "severity_integrity.json").read_text()).get("outliers", [])),
            "policy_outcomes_used_for_redesign": False,
            "final_v1_result_usage": "methodological stop evidence only; no final-v1 run enters final-v2 scientific dataset"}
    if root["attempted"] != 316 or root["valid"] != 316:
        raise ValueError("final-v1 preservation count changed")
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "final_v1_stop_root_cause.json", root)
    md = ["# final-v1 scientific stop: root cause", "",
          f"Status: **{root['status']}**. {root['attempted']} of 650 runs were attempted and valid; 334 remain unattempted.",
          "", "Candidate-error conditions route each request with a seeded 0.50 candidate weight. The fault applies only to candidate requests at serving indices 45–84, a 40-request evaluation window. For cadence N, the injector fails every Nth eligible candidate request. If C candidates were routed there, failures equal floor(C/N) and the observed candidate rate equals floor(C/N)/C. Different seeds change C; the integer denominator produces discrete rate steps.",
          "", "| Frozen severity | Runs audited | Candidate count min–max | Fault count min–max | Realized rate min–max |",
          "|---|---:|---:|---:|---:|"]
    for severity, s in by_level.items():
        md.append(f"| {severity} | {s['run_count']} | {s['candidate_counts']['min']}–{s['candidate_counts']['max']} | {s['fault_counts']['min']}–{s['fault_counts']['max']} | {s['rates']['min']:.2%}–{s['rates']['max']:.2%} |")
    md += ["", "The narrow Phase 6C outcome ranges were therefore unsuitable as validity bounds. Final-v1 observations remain preserved, excluded from final-v2 results, and were not used to choose policy settings.", ""]
    (OUT / "final_v1_stop_root_cause.md").write_text("\n".join(md))
    rows_audit = [
      ["candidate_error", "periodic candidate failure, every 100/8/4/2 eligible candidate request, indices 45..84", "candidate error rate", False, "define severity by cadence; remove outcome range as gate"],
      ["latency", "mock baseline 10 ms, candidate 11 ms plus fixed 0/2/5/9 ms during evaluation", "candidate p95 latency change +10/+30/+60/+100%", True, "none"],
      ["classification", "20-item fixed workload; wrong answer every 10th/5th/4th item", "accuracy loss 10/20/25%", True, "none"],
      ["structured", "fixed 20-item structured workload and fixed corruption profile", "structure-valid 85%, exact-match 75%", True, "none"],
      ["rollback_delay", "routing propagation fault for 12 requests after rollback", "physical containment time and post-rollback exposure", True, "none; measured containment is an outcome"],
      ["routing_realization", "desired/observed/effective routing transformation in frozen config", "candidate count and routing consistency", False, "none; do not gate on candidate count outside minimum sufficiency"],
      ["stale_behavior", "one prior evidence window selected by fixed observation fault", "freshness and policy decision", True, "none"],
      ["dropped_system", "candidate observation dropped by fixed observation fault", "missingness and policy decision", True, "none"],
      ["historical_attribution", "prespecified source seed/workload and original epoch/revision replay", "attribution decision", True, "regenerate sources for final-v2"],
      ["behavior_routing", "fixed behavior corruption plus routing transformation", "accuracy and routing realization", False, "none; validate config and sample sufficiency"],
      ["observed_only_routing", "fixed observed-only routing mismatch", "HOLD and exposure costs", False, "none; candidate count is an outcome"],
      ["insufficient_behavior", "fixed two candidate probes below minimum three", "evidence sufficiency", True, "none"],
    ]
    audit_data = {"completed_before_final_v2_creation": True,
                  "columns": ["condition", "intervention_definition", "measured_outcome", "robust_across_seeds", "change_required"],
                  "rows": [dict(zip(["condition", "intervention_definition", "measured_outcome", "robust_across_seeds", "change_required"], r)) for r in rows_audit],
                  "policy_threshold_oracle_change_required": False,
                  "error_sample_budget": "Retain original 40 evaluation requests at 0.50 candidate weight. The 60-request/0.20 sufficiency finding applies to other routing-sensitive conditions and does not promise precise error-rate estimates. Larger budgets would alter exposure and fault opportunities; cadence-based identity removes the need to force narrow percentages."}
    write_json(OUT / "severity_definition_audit.json", audit_data)
    lines = ["# Phase 7B severity-definition audit", "", "| Condition | Frozen intervention | Measured outcome | Seed-robust? | Required change |", "|---|---|---|---|---|"]
    for r in audit_data["rows"]:
        lines.append(f"| {r['condition']} | {r['intervention_definition']} | {r['measured_outcome']} | {r['robust_across_seeds']} | {r['change_required']} |")
    lines += ["", audit_data["error_sample_budget"], ""]
    (OUT / "severity_definition_audit.md").write_text("\n".join(lines))


if __name__ == "__main__":
    audit()
    print("phase7b root-cause and severity audit written")
