#!/usr/bin/env python3
"""Prepare final-v3 from final-v2 scientific inputs; never execute final runs."""
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

from saferoll.artifacts import canonical_json, config_hash
from saferoll.campaign.final_freeze import file_hash, source_hash
from saferoll.campaign.plan import derived_seed
from saferoll.config import config_from_dict
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

V2 = ROOT / "campaigns/final-v2"
V3 = ROOT / "campaigns/final-v3"


def save(path: Path, value: object) -> None:
    content = canonical_json(value) + "\n"
    if path.exists() and path.read_text() != content:
        raise ValueError(f"existing final-v3 artifact differs: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def build_history(seed_design: dict) -> dict:
    old = json.loads((V2 / "history_index.json").read_text())
    entries = {}
    severity = "epoch1_v2_to_epoch2_v3"
    for repetition in range(seed_design["routing_repetitions"]):
        seed = derived_seed(7300, "historical_attribution", "paired", repetition, {"severity": severity})
        original = ROOT / old["entries"][str(seed)]["run_dir"] / "config.resolved.json"
        raw = json.loads(original.read_text())
        name = f"final-v3-history-source-{repetition}"
        raw["experiment"]["name"] = name
        raw["rollout"]["rollout_id"] = name
        slot = V3 / "history" / f"seed-{seed}"
        prior = list(slot.glob("run-*")) if slot.exists() else []
        if prior:
            if len(prior) != 1 or json.loads((prior[0] / "config.resolved.json").read_text()) != raw:
                raise ValueError("final-v3 historical source mutation")
            run = prior[0]
        else:
            run = run_experiment(config_from_dict(raw), slot)
        if not validate_run(run)["valid"]:
            raise ValueError("invalid final-v3 historical source")
        rel = run.relative_to(ROOT).as_posix()
        evidence = run / "behavior_evidence.jsonl"
        workload = json.loads((run / "behavior_workload.json").read_text())
        entries[str(seed)] = {"run_dir": rel, "source_run_id": run.name,
            "seed": seed, "pairing_key": f"historical_attribution:{severity}:{repetition}",
            "identity_relation": "DISTINCT_SOURCE_CURRENT", "source_epoch": 1,
            "source_revision": "candidate-v2", "epoch": 1, "candidate_revision": "candidate-v2",
            "rollout_id": name, "source_artifact_path": rel + "/behavior_evidence.jsonl",
            "source_sha256": file_hash(evidence), "behavior_evidence_sha256": file_hash(evidence),
            "source_config_sha256": file_hash(run / "config.resolved.json"),
            "workload_sha256": file_hash(run / "behavior_workload.json"),
            "workload_id": workload["workload_id"],
            "matching_rule": "prespecified seed, repetition and exact workload; source created before current run",
            "source_role": "SETUP_ARTIFACT_NOT_FINAL_POLICY_EVALUATION"}
    result = {"schema_version": "2.0", "campaign_id": "final-v3", "entries": entries,
              "source_role": "setup artifacts; excluded from 650 final policy-evaluation runs"}
    save(V3 / "history_index.json", result)
    return result


def main() -> None:
    old = json.loads((V2 / "campaign_freeze.json").read_text())
    old_plan = json.loads((V2 / "campaign_plan.json").read_text())
    if old["freeze_sha256"] != "3194901ad8cea389ea12136dda1bafb2c87b822fbd7a62a8436ef27590a21490":
        raise ValueError("unexpected final-v2 freeze")
    if file_hash(V2 / "campaign_plan.json") != "67c8605996cda9c23221e3ab718d18b4c2ef19b5b34549ade716b3eb046d55d4":
        raise ValueError("unexpected final-v2 plan")
    V3.mkdir(parents=True, exist_ok=True)
    seed_design = deepcopy(json.loads((V2 / "seed_design.json").read_text()))
    seed_design["campaign_id"] = "final-v3"
    save(V3 / "seed_design.json", seed_design)
    history = build_history(seed_design)
    history_hash = file_hash(V3 / "history_index.json")
    rows = []
    for previous in old_plan["rows"]:
        row = deepcopy(previous)
        raw = json.loads((V2 / row["config_path"]).read_text())
        name = raw["experiment"]["name"].replace("final-v2", "final-v3")
        raw["experiment"]["name"] = name
        raw["rollout"]["rollout_id"] = name
        if row["scenario"] == "historical_attribution":
            fault = raw["phase4e"]["observation_faults"][0]
            fault["historical_index_path"] = "campaigns/final-v3/history_index.json"
            fault["historical_index_sha256"] = history_hash
        cfg = config_from_dict(raw)
        cid = config_hash(cfg)
        run_id = hashlib.sha256(canonical_json([row["scenario"], row["severity"], row["policy"],
                   row["repetition"], row["seed"], cid]).encode()).hexdigest()[:24]
        row.update(run_id=run_id, config_path=f"configs/{run_id}.json", config_sha256=cid,
                   threshold_signature=hashlib.sha256(canonical_json(raw["policy"]).encode()).hexdigest())
        save(V3 / row["config_path"], raw)
        rows.append(row)
    if len(rows) != 650 or len({r["run_id"] for r in rows}) != 650:
        raise ValueError("final-v3 plan size or uniqueness changed")
    plan = {"schema_version": old_plan["schema_version"], "campaign_id": "final-v3",
            "policy_order": old_plan["policy_order"], "rows": rows,
            "source_history_sha256": history_hash, "seed_design_sha256": file_hash(V3 / "seed_design.json")}
    save(V3 / "campaign_plan.json", plan)
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    csv_path = V3 / "campaign_plan.csv"
    if csv_path.exists() and csv_path.read_text() != output.getvalue():
        raise ValueError("final-v3 plan CSV mutation")
    csv_path.write_text(output.getvalue())
    real = deepcopy(json.loads((V2 / "real_campaign_spec.json").read_text()))
    for row in real["final_runs"]:
        raw = json.loads((V2 / row["config_path"]).read_text())
        name = raw["experiment"]["name"].replace("final-v2", "final-v3")
        raw["experiment"]["name"] = name
        raw["rollout"]["rollout_id"] = name
        row["config_sha256"] = config_hash(config_from_dict(raw))
        save(V3 / row["config_path"], raw)
    real["campaign_id"] = "final-v3"
    real["pilot_provenance_note"] = "Prior calibration is setup only; no final-v1/v2 run is final-v3 evidence."
    save(V3 / "real_campaign_spec.json", real)
    freeze = deepcopy(old)
    freeze.update(campaign_id="final-v3", status="PROPOSED", freeze_sha256=None,
                  plan_sha256=file_hash(V3 / "campaign_plan.json"),
                  seed_design_sha256=file_hash(V3 / "seed_design.json"),
                  history_index_sha256=history_hash,
                  real_campaign_spec_sha256=file_hash(V3 / "real_campaign_spec.json"),
                  history_contract_version="2.0",
                  history_source_role="setup artifacts, excluded from 650 final policy-evaluation runs",
                  final_v2_usage="methodological runtime stop evidence only; zero final-v2 scientific runs reused")
    freeze["seed_design_review_note"] = "Final-v2 seed derivation and repetition counts retained unchanged; only campaign namespace changes."
    freeze["source_git_revision"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    freeze["git_dirty"] = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    paths = list((ROOT / "saferoll").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")) + list((ROOT / "configs").rglob("*.yaml"))
    analysis = list((ROOT / "analysis").rglob("*.py")) + [ROOT / "saferoll/research/oracle.py"]
    freeze["source_tree_sha256"] = source_hash(paths)
    freeze["analysis_source_sha256"] = source_hash(analysis)
    save(V3 / "campaign_freeze.json", freeze)
    lines = ["# SafeRoll final-v3 freeze review", "", "Status: **PROPOSED**.", "",
             "Scientific design: identical to final-v2. The runtime history contract and provenance namespace are corrected.",
             f"Source revision: `{freeze['source_git_revision']}`. Source-tree SHA-256: `{freeze['source_tree_sha256']}`.",
             f"Plan: 20 conditions, 13 scenarios, 5 policies, {len(rows)} mock runs; SHA-256 `{freeze['plan_sha256']}`.",
             "REAL_CPU: 3 healthy and 3 structured regression runs. No final-v1/v2 observation enters final-v3.",
             "Historical source runs are setup artifacts, generated before current runs and excluded from the 650 policy-evaluation identities.",
             "", "## Runtime contract", "", "The frozen index pins source run, seed, repetition, workload, artifact hash, epoch, revision and distinct source/current rollout identities. Runtime and preflight use the same historical_source function.",
             "", "## Research questions", ""]
    lines += [f"- {k}: {v}" for k,v in freeze["research_questions"].items()]
    lines += ["", "## Policies and thresholds", "", ", ".join(freeze["policies"])+" (version 1.0).", ""]
    lines += [f"- {k}: {v}" for k,v in freeze["thresholds"].items()]
    lines += ["", "## Conditions", "", "| Category | Scenario | Severity | Repetitions |", "|---|---|---|---:|"]
    lines += [f"| {x['category']} | {x['scenario']} | {x['severity']} | {10 if x['routing_sensitive'] else 3} |" for x in freeze["scenarios"]]
    lines += ["", "## Endpoints and constraints", "", "Primary: "+", ".join(freeze["primary_metrics"])+".",
              "Secondary: "+", ".join(freeze["secondary_metrics"])+".",
              "Paired key: "+freeze["analysis_plan"]["paired_key"]+"; multiplicity: "+freeze["analysis_plan"]["multiplicity"]+".",
              "Exclusions: "+", ".join(freeze["exclusion_codes"])+".",
              "No final-v3 policy-evaluation run or inferential test has been executed.", ""]
    review = V3 / "campaign_freeze.md"
    if review.exists() and review.read_text() != "\n".join(lines):
        raise ValueError("final-v3 review mutation")
    review.write_text("\n".join(lines))
    print(json.dumps({"status": freeze["status"], "planned_mock_runs": len(rows),
                      "plan_sha256": freeze["plan_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
