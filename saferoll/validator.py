"""Validate artifact structure, attribution, accounting and deterministic replay."""
from __future__ import annotations
import csv
from dataclasses import asdict, fields
import json
from pathlib import Path
from typing import Any
from .artifacts import canonical_json, config_hash, write_json
from .config import config_from_dict
from .runner import simulate
from .lifecycle import STATE_MACHINE_VERSION
from .evidence import EVIDENCE_SCHEMA_VERSION
from .evidence_validation import validate_system_evidence
from .lifecycle_validation import validate_lifecycle
from .schemas import Event, EVENT_TYPES, REQUEST_COLUMNS, SCHEMA_VERSION


def validate_run(run_dir: str | Path) -> dict[str, Any]:
    """Report corrupt/missing artifacts rather than crashing; persist every result."""
    root = Path(run_dir)
    issues: list[str] = []
    required = ["manifest.json", "config.resolved.json", "requests.csv", "events.jsonl", "summary.json", "rollout_transitions.jsonl", "system_evidence.jsonl"]
    for name in required:
        if not (root / name).is_file():
            issues.append(f"missing artifact: {name}")
    if not issues:
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            config = config_from_dict(json.loads((root / "config.resolved.json").read_text(encoding="utf-8")))
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            with (root / "requests.csv").open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != REQUEST_COLUMNS:
                    issues.append("requests.csv schema fields/order mismatch")
                rows = list(reader)
            events = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            transitions = [json.loads(line) for line in (root / "rollout_transitions.jsonl").read_text(encoding="utf-8").splitlines()]
            evidence = [json.loads(line) for line in (root / "system_evidence.jsonl").read_text(encoding="utf-8").splitlines()]
            issues.extend(validate_system_evidence(config, evidence, rows, events))
            issues.extend(validate_lifecycle(config, transitions, events, rows, summary))
            expected_responses, expected_events, expected_summary = simulate(config)
            expected_manifest = {"schema_version": SCHEMA_VERSION, "evidence_schema_version": EVIDENCE_SCHEMA_VERSION, "state_machine_version": STATE_MACHINE_VERSION,
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
            if len(rows) != config.experiment.request_count:
                issues.append("planned request count differs from rows")
            ids = [r.get("request_id") for r in rows]
            if len(set(ids)) != len(ids):
                issues.append("duplicate request ID")
            for index, row in enumerate(rows):
                if set(row) != set(REQUEST_COLUMNS) or any(v is None or v == "" for v in row.values()):
                    issues.append(f"request {index}: missing or extra fields")
                if row.get("rollout_id") != config.rollout.rollout_id or row.get("epoch") != str(config.rollout.epoch):
                    issues.append(f"request {index}: rollout/epoch mismatch")
                if row.get("served_revision") not in (config.baseline.revision, config.candidate.revision):
                    issues.append(f"request {index}: unknown served revision")
            expected_rows = [{key: str(value) for key, value in asdict(r).items()} for r in expected_responses]
            if rows != expected_rows:
                issues.append("request records differ from deterministic replay (identity/routing/fault/output/latency)")
            if canonical_json(summary) != canonical_json(expected_summary):
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
            if canonical_json(events) != canonical_json([asdict(e) for e in expected_events]):
                issues.append("logical event sequence differs from deterministic replay")
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, csv.Error) as error:
            issues.append(f"unreadable or malformed artifact: {error}")
    result = {"valid": not issues, "issues": issues}
    if root.is_dir():
        write_json(root / "validation.json", result)
    return result
