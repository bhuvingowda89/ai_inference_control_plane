"""Validate artifact structure, attribution, accounting and deterministic replay."""
from __future__ import annotations
import csv
from dataclasses import asdict, fields
import json
from pathlib import Path
from typing import Any
from .artifacts import canonical_json, config_hash, write_json
from .config import config_from_dict
from .research.validation import validate_policy_artifacts
from .phase4e_validation import validate_phase4e_artifacts
from .behavior.workload import BEHAVIOR_SCHEMA_VERSION
from .behavior.validation import validate_behavior
from .runner import simulate
from .lifecycle import STATE_MACHINE_VERSION
from .evidence import EVIDENCE_SCHEMA_VERSION
from .evidence_validation import validate_system_evidence
from .lifecycle_validation import validate_lifecycle
from .schemas import Event, EVENT_TYPES, REQUEST_COLUMNS, SCHEMA_VERSION
from .real_validation import validate_real_artifacts


def validate_run(run_dir: str | Path) -> dict[str, Any]:
    """Report corrupt/missing artifacts rather than crashing; persist every result."""
    root = Path(run_dir)
    issues: list[str] = []
    required = ["manifest.json", "config.resolved.json", "requests.csv", "events.jsonl", "summary.json", "rollout_transitions.jsonl", "system_evidence.jsonl", "behavior_workload.json", "behavior_probes.jsonl", "behavior_evidence.jsonl", "behavior_comparisons.jsonl", "routing_states.jsonl", "observations.jsonl", "fault_events.jsonl", "policy_inputs.jsonl", "decisions.jsonl", "decision_evaluations.jsonl"]
    for name in required:
        if not (root / name).is_file():
            issues.append(f"missing artifact: {name}")
    if not issues:
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            config = config_from_dict(json.loads((root / "config.resolved.json").read_text(encoding="utf-8")))
            real = config.baseline.backend_type == "llama_cpp"
            if real:
                for name in ("preflight.json", "backend_runs.jsonl", "real_provenance.json", "llama-server.log"):
                    if not (root / name).is_file():
                        issues.append(f"missing artifact: {name}")
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            with (root / "requests.csv").open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != REQUEST_COLUMNS:
                    issues.append("requests.csv schema fields/order mismatch")
                rows = list(reader)
            events = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            transitions = [json.loads(line) for line in (root / "rollout_transitions.jsonl").read_text(encoding="utf-8").splitlines()]
            evidence = [json.loads(line) for line in (root / "system_evidence.jsonl").read_text(encoding="utf-8").splitlines()]
            workload = json.loads((root / "behavior_workload.json").read_text(encoding="utf-8"))
            behavior_artifacts = [[json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines()]
                                  for name in ("behavior_probes.jsonl", "behavior_evidence.jsonl", "behavior_comparisons.jsonl")]
            phase_artifacts = [[json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines()]
                               for name in ("routing_states.jsonl", "observations.jsonl", "fault_events.jsonl")]
            policy_artifacts = [[json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines()]
                                for name in ("policy_inputs.jsonl", "decisions.jsonl", "decision_evaluations.jsonl")]
            issues.extend(validate_policy_artifacts(config, *policy_artifacts, phase_artifacts[1], phase_artifacts[0],
                                                   evidence, rows, events, summary, probes=behavior_artifacts[0]))
            issues.extend(validate_phase4e_artifacts(config, *phase_artifacts, rows, events, evidence, behavior_artifacts[1], summary))
            issues.extend(validate_behavior(config, workload, *behavior_artifacts, evidence, events, summary))
            issues.extend(validate_system_evidence(config, evidence, rows, events))
            issues.extend(validate_lifecycle(config, transitions, events, rows, summary))
            if real:
                issues.extend(validate_real_artifacts(root, config, manifest, rows, behavior_artifacts[0], events, workload))
            else:
                expected_responses, expected_events, expected_summary = simulate(config)
            expected_manifest = {"schema_version": SCHEMA_VERSION, "behavior_schema_version": BEHAVIOR_SCHEMA_VERSION, "evidence_schema_version": EVIDENCE_SCHEMA_VERSION, "state_machine_version": STATE_MACHINE_VERSION,
                                 "backend_type": config.baseline.backend_type,
                                 "timing_mode": "MEASURED" if real else "SIMULATED",
                                 "decision_policy_type": config.decision_policy.type, "initial_state": "STABLE", "experiment_name": config.experiment.name,
                                 "seed": config.experiment.seed, **asdict(config.identity),
                                 "request_count": config.experiment.request_count, "config_sha256": config_hash(config)}
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be an object")
            for key, value in expected_manifest.items():
                if key not in manifest or canonical_json(manifest[key]) != canonical_json(value):
                    issues.append(f"manifest mismatch: {key}")
            for key in ("source_tree_sha256", "python_version", "platform", "git_revision", "git_dirty"):
                if key not in manifest:
                    issues.append(f"manifest missing field: {key}")
            if len(rows) != (config.experiment.request_count if config.policy.type == "dummy" else summary.get("planned_requests")):
                issues.append("planned request count differs from rows")
            ids = [r.get("request_id") for r in rows]
            if len(set(ids)) != len(ids):
                issues.append("duplicate request ID")
            for index, row in enumerate(rows):
                if (set(row) != set(REQUEST_COLUMNS) or any(v is None or
                        (v == "" and k not in ({"request_start_monotonic", "request_end_monotonic", "output"}
                                              if real else {"request_start_monotonic", "request_end_monotonic"}))
                        for k, v in row.items())):
                    issues.append(f"request {index}: missing or extra fields")
                if row.get("rollout_id") != config.rollout.rollout_id or row.get("epoch") != str(config.rollout.epoch):
                    issues.append(f"request {index}: rollout/epoch mismatch")
                if row.get("served_revision") not in (config.baseline.revision, config.candidate.revision):
                    issues.append(f"request {index}: unknown served revision")
            if real:
                from .real_validation import validate_real_summary
                issues.extend(validate_real_summary(config, rows, summary))
            else:
                expected_rows = [{key: "" if value is None else str(value) for key, value in asdict(r).items()}
                                 for r in expected_responses]
                if rows != expected_rows:
                    issues.append("request records differ from deterministic replay (identity/routing/fault/output/latency)")
                compared_summary = {k: v for k, v in summary.items() if config.policy.type == "dummy" or
                                    k not in {"decision_metrics", "unsafe_promotion_count", "unnecessary_rollback_count", "unnecessary_hold_count"}}
                if canonical_json(compared_summary) != canonical_json(expected_summary):
                    issues.append("summary schema/counts/metrics differ from raw deterministic records")
            event_fields = {f.name for f in fields(Event)}
            for index, event in enumerate(events):
                if not isinstance(event, dict) or set(event) != event_fields:
                    issues.append(f"event {index}: schema mismatch")
                    continue
                if event["event_index"] != index or event["logical_time"] != index:
                    issues.append(f"event {index}: invalid ordering")
                if event["event_type"] not in EVENT_TYPES:
                    issues.append(f"event {index}: unknown type")
                if event["rollout_id"] != config.rollout.rollout_id or event["epoch"] != config.rollout.epoch:
                    issues.append(f"event {index}: rollout/epoch mismatch")
            if not events or not isinstance(events[0], dict) or events[0].get("event_type") != "RUN_STARTED":
                issues.append("RUN_STARTED must be first lifecycle event")
            if not events or not isinstance(events[-1], dict) or events[-1].get("event_type") != "RUN_COMPLETED":
                issues.append("RUN_COMPLETED must be last lifecycle event")
            if not real and canonical_json(events) != canonical_json([asdict(e) for e in expected_events]):
                issues.append("logical event sequence differs from deterministic replay")
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, csv.Error) as error:
            issues.append(f"unreadable or malformed artifact: {error}")
    result = {"valid": not issues, "issues": issues}
    if root.is_dir():
        write_json(root / "validation.json", result)
    return result
