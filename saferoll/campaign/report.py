"""Pilot diagnostics and a nonfinal freeze proposal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..artifacts import canonical_json, write_json
from .plan import read_plan


def write_pilot_report(directory: Path, plan: dict[str, Any], outputs: dict[str, Any],
                       index: list[dict[str, Any]]) -> None:
    groups = outputs["_groups"]
    table = {f"{row['scenario']}/{row['policy']}": row["decision_counts"] for row in groups}
    report = {"schema_version": "1.0", "campaign_id": plan["campaign_id"],
              "planned_runs": len(plan["rows"]), "valid_runs": outputs["_summary"]["valid_runs"],
              "invalid_run_count": sum(r["status"] != "VALID" for r in index),
              "unattempted_runs": len(plan["rows"]) - len(index),
              "per_policy_decisions": {policy: {decision: sum(r["policy"] == policy and r["decision"] == decision
                  for r in outputs["_decisions"]) for decision in ("PROMOTE", "HOLD", "ROLLBACK")}
                  for policy in plan["policy_order"]},
              "per_policy_hold_rate": {policy: (
                  sum(r["policy"] == policy and r["decision"] == "HOLD" for r in outputs["_decisions"])
                  / sum(r["policy"] == policy for r in outputs["_decisions"])
                  if any(r["policy"] == policy for r in outputs["_decisions"]) else None)
                  for policy in plan["policy_order"]},
              "per_scenario_critical_errors": {scenario: sum(r["scenario"] == scenario and r["critical_error"]
                  for r in outputs["_decisions"]) for scenario in plan["scenario_order"]},
              "decision_matrix": table,
              "scenario_policy_diagnostics": {f"{r['scenario']}/{r['policy']}": {
                  "hold_rate": r["hold_rate"],
                  "candidate_latency_delta_mean_ms": r["candidate_latency_delta_mean_ms"],
                  "candidate_behavior_delta_mean": r["candidate_behavior_delta_mean"],
                  "critical_error_count": r["critical_error_count"]} for r in groups},
              "runtime_seconds": outputs["_summary"]["engineering_run_duration_seconds"],
              "repetition_descriptives": outputs["_summary"]["repetition_descriptives"]}
    write_json(directory / "pilot_report.json", report)
    lines = [f"# Pilot report: {plan['campaign_id']}", "",
             f"Coverage: {report['valid_runs']}/{report['planned_runs']} valid; "
             f"{report['invalid_run_count']} invalid/failed; {report['unattempted_runs']} unattempted.", "",
             "| Scenario | " + " | ".join(plan["policy_order"]) + " |",
             "| --- | " + " | ".join("---" for _ in plan["policy_order"]) + " |"]
    for scenario in plan["scenario_order"]:
        cells = []
        for policy in plan["policy_order"]:
            counts = table[f"{scenario}/{policy}"]
            cells.append(", ".join(f"{letter}={counts[name]}" for name, letter in
                                   (("PROMOTE", "P"), ("HOLD", "H"), ("ROLLBACK", "R"))))
        lines.append("| " + scenario + " | " + " | ".join(cells) + " |")
    lines.extend(["", "Descriptive pilot diagnostics only. No thresholds or campaign design are frozen.", ""])
    (directory / "pilot_report.md").write_text("\n".join(lines), encoding="utf-8")


def propose_freeze(directory: str | Path) -> Path:
    """Emit a reviewable proposal without declaring or modifying a campaign freeze."""
    root = Path(directory)
    plan = read_plan(root)
    from .validation import validate_campaign
    validation = validate_campaign(root)
    if not validation["valid"]:
        raise ValueError("freeze proposal requires a valid completed pilot")
    manifest = json.loads((root / "campaign_manifest.json").read_text(encoding="utf-8"))
    configs = [json.loads((root / row["config_path"]).read_text(encoding="utf-8")) for row in plan["rows"]]
    proposal = {"schema_version": "1.0", "status": "PROPOSED_NOT_FROZEN",
                "campaign_id": plan["campaign_id"], "campaign_config_sha256": plan["campaign_config_sha256"],
                "source_git_revision": manifest["source_git_revision"],
                "source_tree_sha256": manifest["source_tree_sha256"],
                "scenario_order": plan["scenario_order"], "policy_order": plan["policy_order"],
                "repetitions": plan["repetitions"],
                "threshold_signatures": sorted({r["threshold_signature"] for r in plan["rows"]}),
                "policy_thresholds": {r["threshold_signature"]: c["policy"]
                                      for r, c in zip(plan["rows"], configs)},
                "fault_severities": {f"{r['scenario']}/{canonical_json(r['sweep'])}": {
                    "request_fault": c["fault"], "phase4e": c["phase4e"],
                    "candidate_behavior_profile": c["candidate"]["behavior_profile"],
                    "candidate_regression_every_nth_item": c["candidate"]["regression_every_nth_item"]}
                    for r, c in zip(plan["rows"], configs)},
                "backend_versions": manifest["backend_versions"],
                "real_model_hashes": sorted({c["real_runtime"]["model_sha256"] for c in configs
                                             if c["baseline"]["backend_type"] == "llama_cpp"})}
    output = root / "proposed_campaign_freeze.json"
    write_json(output, proposal)
    return output
