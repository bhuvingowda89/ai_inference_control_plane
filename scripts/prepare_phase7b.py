#!/usr/bin/env python3
"""Build final-v2 from frozen final-v1 inputs without reusing final-v1 observations."""
from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saferoll.artifacts import canonical_json, config_hash, write_json
from saferoll.campaign.final_freeze import file_hash, source_hash
from saferoll.campaign.plan import derived_seed
from saferoll.config import config_from_dict
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

V1 = ROOT / "campaigns/final-v1"
V2 = ROOT / "campaigns/final-v2"
CAL = ROOT / "campaigns/phase7b-error-calibration-v1/calibration.json"
LABELS = {"control_0pct": "control_every_100th_nontriggering",
          "mild_9_5_to_12pct": "mild_every_8th_candidate",
          "moderate_21_1_to_23_8pct": "moderate_every_4th_candidate",
          "severe_47_4_to_48pct": "severe_every_2nd_candidate"}


def save(path: Path, value: object) -> None:
    content = canonical_json(value) + "\n"
    if path.exists() and path.read_text() != content:
        raise ValueError(f"existing final-v2 artifact differs: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def history(seed_design: dict) -> dict:
    old = json.loads((V1 / "history_index.json").read_text())
    entries = {}
    old_severity = "epoch1_v2_to_epoch2_v3"
    for repetition in range(seed_design["routing_repetitions"]):
        seed = derived_seed(7300, "historical_attribution", "paired", repetition, {"severity": old_severity})
        source = ROOT / old["entries"][str(seed)]["run_dir"] / "config.resolved.json"
        raw = json.loads(source.read_text())
        name = f"final-v2-history-source-{repetition}"
        raw["experiment"]["name"] = name
        raw["rollout"]["rollout_id"] = name
        target = V2 / "history" / f"seed-{seed}"
        existing = list(target.glob("run-*")) if target.exists() else []
        if existing:
            if len(existing) != 1:
                raise ValueError("ambiguous final-v2 historical source")
            run = existing[0]
            if json.loads((run / "config.resolved.json").read_text()) != raw:
                raise ValueError("historical source config mutation")
        else:
            run = run_experiment(config_from_dict(raw), target)
        if not validate_run(run)["valid"]:
            raise ValueError("invalid final-v2 historical source")
        entries[str(seed)] = {"run_dir": run.relative_to(ROOT).as_posix(),
            "behavior_evidence_sha256": file_hash(run / "behavior_evidence.jsonl"),
            "source_config_sha256": file_hash(run / "config.resolved.json"),
            "rollout_id": name, "epoch": 1, "candidate_revision": "candidate-v2",
            "workload_sha256": file_hash(run / "behavior_workload.json"),
            "matching_rule": "same prespecified seed and workload; source generated before current run"}
    result = {"schema_version": "1.0", "campaign_id": "final-v2", "entries": entries}
    save(V2 / "history_index.json", result)
    return result


def build() -> dict:
    if not CAL.is_file():
        raise ValueError("run phase7b error calibration first")
    calibration = json.loads(CAL.read_text())
    if len(calibration["rows"]) != 80 or calibration["levels"] != {"CONTROL": 100, "MILD": 8, "MODERATE": 4, "SEVERE": 2}:
        raise ValueError("calibration design mismatch")
    old = json.loads((V1 / "campaign_freeze.json").read_text())
    old_plan = json.loads((V1 / "campaign_plan.json").read_text())
    if old["freeze_sha256"] != "4e4bae666ab2f87e3ae723a384ccd17fedb950fbb8b5bceb8d76121a99b32c5f":
        raise ValueError("unexpected final-v1 freeze")
    if file_hash(V1 / "campaign_plan.json") != "74cec8a151e082e44c61742b847b9953daddcef821ab45b86b2a8c9080cffe3a":
        raise ValueError("unexpected final-v1 plan")
    V2.mkdir(parents=True, exist_ok=True)
    seed_design = deepcopy(json.loads((V1 / "seed_design.json").read_text()))
    seed_design["campaign_id"] = "final-v2"
    seed_design["error_severity_seed_note"] = "same SHA-256 rule and repetition indices; revised prespecified severity labels give new error-condition seeds"
    save(V2 / "seed_design.json", seed_design)
    history(seed_design)
    hist_hash = file_hash(V2 / "history_index.json")
    scenarios = deepcopy(old["scenarios"])
    for item in scenarios:
        if item["scenario"] != "candidate_error":
            continue
        original = item["severity"]
        item["severity"] = LABELS[original]
        item["scenario_version"] = "2.0"
        cadence = item["setting"][1]
        item["intervention"] = {"injector_mode": "periodic_candidate_failure",
             "every_nth_candidate": cadence, "eligible_role": "candidate",
             "eligible_request_indices_inclusive": [45, 84], "routing_candidate_weight": 0.5,
             "evaluation_request_budget": 40,
             "seed_semantics": "one seeded routing draw per request; fault counter increments only on eligible candidate requests",
             "control_note": "cadence 100 cannot trigger within the 40-request eligibility window" if cadence == 100 else None}
        item["measured_outcome"] = "candidate error rate in the decision evaluation window; calibration distribution is descriptive, never a validity gate"
        item["calibration_summary"] = calibration["summary"][original.split("_")[0].upper() if cadence != 100 else "CONTROL"]
        item["note"] = "Severity is the configured cadence, not an observed-rate interval."
    by_condition = {(x["scenario"], x["severity"]): x for x in scenarios}
    rows = []
    for prior in old_plan["rows"]:
        row = deepcopy(prior)
        old_label = row["severity"]
        row["severity"] = LABELS.get(old_label, old_label) if row["scenario"] == "candidate_error" else old_label
        item = by_condition[(row["scenario"], row["severity"])]
        row["scenario_version"] = item["scenario_version"]
        row["seed"] = derived_seed(7300, row["scenario"], "paired", row["repetition"], {"severity": row["severity"]})
        row["pairing_key"] = f"{row['scenario']}:{row['severity']}:{row['repetition']}"
        raw = json.loads((V1 / prior["config_path"]).read_text())
        name = f"final-v2-{row['scenario']}-{row['severity']}-{row['policy']}-r{row['repetition']}"
        raw["experiment"].update(name=name, seed=row["seed"])
        raw["rollout"]["rollout_id"] = name
        if row["scenario"] == "historical_attribution":
            fault = raw["phase4e"]["observation_faults"][0]
            fault["historical_index_path"] = "campaigns/final-v2/history_index.json"
            fault["historical_index_sha256"] = hist_hash
            row["history_seed"] = row["seed"]
        cfg = config_from_dict(raw)
        cid = config_hash(cfg)
        run_id = hashlib.sha256(canonical_json([row["scenario"], row["severity"], row["policy"],
                          row["repetition"], row["seed"], cid]).encode()).hexdigest()[:24]
        row.update(run_id=run_id, config_path=f"configs/{run_id}.json", config_sha256=cid,
                   threshold_signature=hashlib.sha256(canonical_json(raw["policy"]).encode()).hexdigest())
        save(V2 / row["config_path"], raw)
        rows.append(row)
    if len(rows) != 650 or len({r["run_id"] for r in rows}) != 650:
        raise ValueError("final-v2 plan count or uniqueness changed")
    plan = {"schema_version": old_plan["schema_version"], "campaign_id": "final-v2",
            "policy_order": old_plan["policy_order"], "rows": rows,
            "source_history_sha256": hist_hash, "seed_design_sha256": file_hash(V2 / "seed_design.json")}
    save(V2 / "campaign_plan.json", plan)
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    csv_path = V2 / "campaign_plan.csv"
    if csv_path.exists() and csv_path.read_text() != output.getvalue():
        raise ValueError("plan CSV mutation")
    csv_path.write_text(output.getvalue())
    real = deepcopy(json.loads((V1 / "real_campaign_spec.json").read_text()))
    for final in real["final_runs"]:
        raw = json.loads((V1 / final["config_path"]).read_text())
        name = raw["experiment"]["name"].replace("final-v1", "final-v2")
        raw["experiment"]["name"] = name
        raw["rollout"]["rollout_id"] = name
        final["config_sha256"] = config_hash(config_from_dict(raw))
        save(V2 / final["config_path"], raw)
    real["campaign_id"] = "final-v2"
    real["pilot_provenance_note"] = "Phase 7 real timing pilot is repetition calibration only; no pilot or final-v1 run enters final-v2 evidence."
    save(V2 / "real_campaign_spec.json", real)
    freeze = deepcopy(old)
    freeze.update(campaign_id="final-v2", status="PROPOSED", freeze_sha256=None, scenarios=scenarios,
                  seed_design_sha256=file_hash(V2 / "seed_design.json"), history_index_sha256=hist_hash,
                  real_campaign_spec_sha256=file_hash(V2 / "real_campaign_spec.json"),
                  plan_sha256=file_hash(V2 / "campaign_plan.json"),
                  error_severity_rule="Intervention cadence and eligibility define severity; realized rate is measured, never an inclusion gate.",
                  error_calibration_id=calibration["campaign_id"],
                  error_calibration_sha256=file_hash(CAL),
                  final_v1_usage="Methodological stop evidence only; zero final-v1 scientific runs reused.")
    freeze["seed_design_review_note"] = "Final-v1 paired SHA-256 seed rule and repetition counts retained; error labels changed before final-v2 execution."
    freeze["threshold_evidence_by_value"]["max_error_rate"] += "; Phase 7B cadence calibration retained threshold 0.05 without retuning"
    freeze["analysis_plan"]["error_severity_reporting"] = "Group by configured cadence; report realized error-rate distribution and policy outcomes without post-hoc severity reassignment."
    freeze["analysis_plan"]["realized_error_rate_dose_response"] = "Optional secondary descriptive covariate analysis only; no post-hoc primary groups."
    freeze["source_git_revision"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    freeze["git_dirty"] = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    source_paths = list((ROOT / "saferoll").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")) + list((ROOT / "configs").rglob("*.yaml"))
    analysis_paths = list((ROOT / "analysis").rglob("*.py")) + [ROOT / "saferoll/research/oracle.py"]
    freeze["source_tree_sha256"] = source_hash(source_paths)
    freeze["analysis_source_sha256"] = source_hash(analysis_paths)
    save(V2 / "campaign_freeze.json", freeze)
    md = ["# SafeRoll final-v2 freeze review", "", "Status: **PROPOSED**.", "",
          f"Source Git revision: `{freeze['source_git_revision']}`; source tree: `{freeze['source_tree_sha256']}`.",
          f"Plan: {len(scenarios)} conditions, 5 policies, {len(rows)} mock runs; SHA-256 `{freeze['plan_sha256']}`.",
          "", "## Scientific change", "", "Candidate-error severity is defined by every Nth eligible candidate request in request indices 45–84. The realized rate is an outcome, never a run-validity gate.",
          "Control uses cadence 100; at most 40 requests are eligible, so it cannot trigger.",
          "", "| Level | Cadence | Calibration mean | Calibration min–max |", "|---|---:|---:|---:|"]
    for label in ("CONTROL", "MILD", "MODERATE", "SEVERE"):
        s = calibration["summary"][label]
        rate = s["realized_error_rate"]
        md.append(f"| {label} | {s['every_nth_candidate']} | {rate['mean']:.3%} | {rate['min']:.3%}–{rate['max']:.3%} |")
    md += ["", "These pilot ranges are descriptive and do not bound valid final-v2 outcomes.",
           "", "## Unchanged design", "", "Six research questions, five policy versions, thresholds, oracle version, 20 scenario conditions, ten routing-sensitive and three fixed-signal repetitions, paired seed rule, planned analyses, and six real CPU runs remain as in final-v1.",
           "No final-v1 run is a final-v2 scientific observation. Historical source evidence is regenerated under final-v2 before current runs.",
           "", "## Known limits", "", "The error condition uses 40 evaluation requests at 0.50 candidate routing, while the separate 60-request sufficiency result applies at 0.20 routing. Finite denominators cause discrete realized rates. Calibration min/max are not guarantees.", ""]
    review = V2 / "campaign_freeze.md"
    if review.exists() and review.read_text() != "\n".join(md):
        raise ValueError("freeze review mutation")
    review.write_text("\n".join(md))
    return freeze


if __name__ == "__main__":
    f = build()
    print(json.dumps({"status": f["status"], "planned_mock_runs": f["planned_mock_runs"], "plan_sha256": f["plan_sha256"]}, sort_keys=True))
