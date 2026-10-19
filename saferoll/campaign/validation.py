"""Independent campaign coverage, attribution, and aggregate integrity checks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..artifacts import canonical_json, config_hash, write_json
from ..config import config_from_dict
from ..validator import validate_run
from .aggregate import build_aggregates
from .plan import PLAN_COLUMNS, _csv_text, derived_seed
from .spec import CAMPAIGN_SCHEMA_VERSION


def _lines(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def validate_campaign(directory: str | Path) -> dict[str, Any]:
    """Reject missing, duplicate, unplanned, invalid, or aggregate-contaminating runs."""
    root = Path(directory)
    issues: list[str] = []
    required = ("campaign_plan.json", "campaign_plan.csv", "campaign_manifest.json",
                "run_index.jsonl", "failures.jsonl", "pilot_report.json", "pilot_report.md")
    required_aggregates = ("run_summary.csv", "decision_summary.csv", "policy_scenario_summary.csv",
                           "fault_summary.csv", "campaign_summary.json")
    for name in required:
        if not (root / name).is_file():
            issues.append(f"missing campaign artifact: {name}")
    for name in required_aggregates:
        if not (root / "aggregate" / name).is_file():
            issues.append(f"missing campaign aggregate: {name}")
    if not issues:
        try:
            plan = json.loads((root / "campaign_plan.json").read_text(encoding="utf-8"))
            manifest = json.loads((root / "campaign_manifest.json").read_text(encoding="utf-8"))
            index = _lines(root / "run_index.jsonl")
            failures = _lines(root / "failures.jsonl")
            rows = plan["rows"]
            if plan.get("schema_version") != CAMPAIGN_SCHEMA_VERSION or manifest.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
                issues.append("campaign schema version mismatch")
            if plan.get("campaign_id") != manifest.get("campaign_id") or plan.get("campaign_config_sha256") != manifest.get("campaign_config_sha256"):
                issues.append("campaign manifest identity mismatch")
            for field in ("source_git_revision", "source_tree_sha256", "policy_versions", "scenario_versions",
                          "artifact_schema_version", "python_version", "host", "started_at", "ended_at", "backend_versions"):
                if field not in manifest:
                    issues.append(f"campaign manifest missing {field}")
            if (root / "campaign_plan.csv").read_text(encoding="utf-8") != _csv_text(rows):
                issues.append("campaign plan CSV differs from JSON plan")
            ids = [r["run_id"] for r in rows]
            seeds = [r["seed"] for r in rows]
            if len(set(ids)) != len(ids) or len(set(seeds)) != len(seeds):
                issues.append("duplicate planned run or seed identity")
            if len({(r["scenario"], r["policy"], r["repetition"], canonical_json(r["sweep"])) for r in rows}) != len(rows):
                issues.append("duplicate scenario/policy/repetition/sweep identity")
            if len(set(plan["scenario_order"])) != len(plan["scenario_order"]) or len(set(plan["policy_order"])) != len(plan["policy_order"]):
                issues.append("duplicate scenario or policy declared")
            if {r["scenario"] for r in rows} != set(plan["scenario_order"]) or {r["policy"] for r in rows} != set(plan["policy_order"]):
                issues.append("declared scenario/policy coverage mismatch")
            for scenario in plan["scenario_order"]:
                sweeps = {canonical_json(r["sweep"]) for r in rows if r["scenario"] == scenario}
                for sweep in sweeps:
                    for policy in plan["policy_order"]:
                        for repetition in range(plan["repetitions"]):
                            matches = [r for r in rows if r["scenario"] == scenario and r["policy"] == policy
                                       and r["repetition"] == repetition and canonical_json(r["sweep"]) == sweep]
                            if len(matches) != 1:
                                issues.append(f"scenario/policy/repetition coverage mismatch: {scenario}/{policy}/{repetition}/{sweep}")
            if len({r["run_id"] for r in index}) != len(index):
                issues.append("duplicate run index identity")
            if set(r["run_id"] for r in index) != set(ids):
                issues.append("campaign coverage incomplete or unplanned run indexed")
            if [r["run_id"] for r in index] != ids:
                issues.append("run index order differs from plan")
            slots = {p.name for p in (root / "runs").iterdir() if p.is_dir()} if (root / "runs").exists() else set()
            if slots - set(ids):
                issues.append("unplanned run slot found")
            indexed = {r["run_id"]: r for r in index}
            for row in rows:
                if row.get("campaign_id") != plan.get("campaign_id"):
                    issues.append(f"plan row campaign mismatch: {row.get('run_id')}")
                if row["seed"] != derived_seed(plan["seed_base"], row["scenario"], row["policy"],
                                               row["repetition"], row["sweep"]):
                    issues.append(f"seed derivation mismatch: {row['run_id']}")
                config_path = root / row["config_path"]
                try:
                    config = config_from_dict(json.loads(config_path.read_text(encoding="utf-8")))
                    if config_hash(config) != row["config_sha256"] or config.experiment.seed != row["seed"]:
                        issues.append(f"planned config mismatch: {row['run_id']}")
                    if config.policy.type != row["policy"] or (config.baseline.backend_type == "mock") != (row["expected_backend"] == "MOCK"):
                        issues.append(f"planned policy/backend mismatch: {row['run_id']}")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    issues.append(f"invalid planned config {row['run_id']}: {exc}")
                indexed_run = indexed.get(row["run_id"])
                if not indexed_run:
                    continue
                for field in ("campaign_id", "scenario", "policy", "repetition", "seed"):
                    if indexed_run.get(field) != row.get(field):
                        issues.append(f"run index {field} mismatch: {row['run_id']}")
                if indexed_run.get("status") != "VALID":
                    issues.append(f"planned run not valid: {row['run_id']} ({indexed_run.get('status')})")
                    continue
                run_dir = indexed_run.get("run_dir")
                if not isinstance(run_dir, str) or Path(run_dir).is_absolute() or ".." in Path(run_dir).parts:
                    issues.append(f"invalid run directory reference: {row['run_id']}")
                    continue
                candidate = root / run_dir
                if not candidate.is_dir() or candidate.parent != root / "runs" / row["run_id"]:
                    issues.append(f"run outside planned slot: {row['run_id']}")
                    continue
                manifest_run = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
                if manifest_run.get("config_sha256") != row["config_sha256"]:
                    issues.append(f"run config hash mismatch: {row['run_id']}")
                if manifest_run.get("source_tree_sha256") != manifest.get("source_tree_sha256"):
                    issues.append(f"run source-tree hash differs from campaign: {row['run_id']}")
                if manifest_run.get("git_revision") != manifest.get("source_git_revision"):
                    issues.append(f"run Git revision differs from campaign: {row['run_id']}")
                check = validate_run(candidate)
                if not check["valid"]:
                    issues.append(f"individual run invalid: {row['run_id']}: {check['issues']}")
                decision = _lines(candidate / "decisions.jsonl")
                evaluations = _lines(candidate / "decision_evaluations.jsonl")
                for field, actual in (("decision", decision[0]["decision"] if decision else None),
                                      ("ground_truth_state", evaluations[0]["ground_truth_state"] if evaluations else None),
                                      ("correctness_class", evaluations[0]["correctness_class"] if evaluations else None),
                                      ("critical_error", evaluations[0]["critical_error"] if evaluations else None)):
                    if indexed_run.get(field) != actual:
                        issues.append(f"run index {field} differs from run: {row['run_id']}")
            expected_failures = [r for r in index if r["status"] != "VALID"]
            if canonical_json(failures) != canonical_json(expected_failures):
                issues.append("failure report differs from run index")
            if not issues:
                outputs = build_aggregates(root, rows, index)
                for name in required_aggregates:
                    if (root / "aggregate" / name).read_text(encoding="utf-8") != outputs[name]:
                        issues.append(f"aggregate mismatch: {name}")
                report = json.loads((root / "pilot_report.json").read_text(encoding="utf-8"))
                if report.get("valid_runs") != outputs["_summary"]["valid_runs"] or report.get("planned_runs") != len(rows):
                    issues.append("pilot report coverage mismatch")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            issues.append(f"campaign artifact parse/integrity failure: {exc}")
    result = {"valid": not issues, "issues": issues}
    (root / "validation").mkdir(parents=True, exist_ok=True)
    write_json(root / "validation" / "campaign_validation.json", result)
    return result
