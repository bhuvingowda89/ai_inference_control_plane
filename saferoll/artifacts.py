"""Stable UTF-8 artifact serialization and diagnostic provenance."""
from __future__ import annotations
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Any
from .config import Config
from .research.oracle import evaluate_decisions, decision_metrics
from .observations import build_observations, build_fault_events
from .behavior.workload import BEHAVIOR_SCHEMA_VERSION, build_workload
from .behavior.evidence import collect_behavior_evidence
from .lifecycle import STATE_MACHINE_VERSION
from .evidence import EVIDENCE_SCHEMA_VERSION, collect_system_evidence
from .schemas import Event, Response, REQUEST_COLUMNS, SCHEMA_VERSION


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def config_hash(config: Config) -> str:
    return hashlib.sha256(canonical_json(config.to_dict()).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def build_manifest(config: Config) -> dict[str, Any]:
    package = Path(__file__).parent
    source = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        source.update(path.relative_to(package).as_posix().encode("utf-8") + b"\0")
        source.update(path.read_bytes() + b"\0")
    manifest = {"schema_version": SCHEMA_VERSION, "behavior_schema_version": BEHAVIOR_SCHEMA_VERSION, "evidence_schema_version": EVIDENCE_SCHEMA_VERSION, "state_machine_version": STATE_MACHINE_VERSION,
                "backend_type": config.baseline.backend_type,
                "timing_mode": "MEASURED" if config.baseline.backend_type == "llama_cpp" else "SIMULATED",
                "decision_policy_type": config.decision_policy.type, "initial_state": "STABLE", "experiment_name": config.experiment.name,
                "seed": config.experiment.seed, **asdict(config.identity),
                "request_count": config.experiment.request_count,
                "config_sha256": config_hash(config), "source_tree_sha256": source.hexdigest(),
                "python_version": platform.python_version(), "platform": platform.platform(),
                "git_revision": None, "git_dirty": None}
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=package,
                                  capture_output=True, text=True, check=False)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=package,
                                capture_output=True, text=True, check=False)
    except FileNotFoundError:
        manifest["git_metadata_status"] = "git executable unavailable"
    else:
        if revision.returncode == 0 and status.returncode == 0:
            manifest.update(git_revision=revision.stdout.strip(), git_dirty=bool(status.stdout))
        else:
            manifest["git_metadata_status"] = "Git metadata unavailable"
    return manifest


def write_artifacts(run_dir: Path, config: Config, responses: list[Response],
                    events: list[Event], summary: dict[str, Any],
                    real_provenance: dict[str, Any] | None = None) -> None:
    """Write only to a new directory; never overwrite an earlier experiment."""
    if real_provenance is None:
        run_dir.mkdir(parents=True, exist_ok=False)
    elif not run_dir.is_dir() or not (run_dir / "preflight.json").is_file():
        raise ValueError("real run requires completed preflight")
    write_json(run_dir / "manifest.json", build_manifest(config))
    write_json(run_dir / "config.resolved.json", config.to_dict())
    with (run_dir / "requests.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REQUEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(response) for response in responses)
    with (run_dir / "events.jsonl").open("w", encoding="utf-8", newline="") as stream:
        for event in events:
            stream.write(canonical_json(asdict(event)) + "\n")
    with (run_dir / "rollout_transitions.jsonl").open("w", encoding="utf-8", newline="") as stream:
        for event in events:
            if event.event_type == "STATE_TRANSITION":
                stream.write(canonical_json(event.details) + "\n")
    system_windows = [asdict(r) for r in collect_system_evidence(config, responses, events)]
    with (run_dir / "system_evidence.jsonl").open("w", encoding="utf-8", newline="") as stream:
        for record in system_windows:
            stream.write(canonical_json(record) + "\n")
    workload = build_workload(config)
    probes = [e.details for e in events if e.event_type == "BEHAVIOR_PROBE_COMPLETED"]
    behavior, comparisons = collect_behavior_evidence(config, workload, probes, system_windows)
    write_json(run_dir / "behavior_workload.json", workload)
    for name, records in (("behavior_probes.jsonl", probes), ("behavior_evidence.jsonl", behavior),
                          ("behavior_comparisons.jsonl", comparisons)):
        with (run_dir / name).open("w", encoding="utf-8", newline="") as stream:
            for record in records:
                stream.write(canonical_json(record) + "\n")
    routing = [e.details for e in events if e.event_type == "ROUTING_STATE"]
    observations = build_observations(config, system_windows, behavior, routing)
    fault_events = build_fault_events(config, routing, system_windows, [asdict(e) for e in events])
    for name, records in (("routing_states.jsonl", routing), ("observations.jsonl", observations),
                          ("fault_events.jsonl", fault_events)):
        with (run_dir / name).open("w", encoding="utf-8", newline="") as stream:
            for record in records:
                stream.write(canonical_json(record) + "\n")
    inputs = [e.details["policy_input"] for e in events if e.event_type == "DECISION_MADE" and "policy_input" in e.details]
    decisions = [e.details["policy_result"] for e in events if e.event_type == "DECISION_MADE" and "policy_result" in e.details]
    evaluations = evaluate_decisions(config, inputs, decisions, system_windows, routing,
                                     [asdict(r) for r in responses], probes=probes)
    for name, records in (("policy_inputs.jsonl", inputs), ("decisions.jsonl", decisions),
                          ("decision_evaluations.jsonl", evaluations)):
        with (run_dir / name).open("w", encoding="utf-8", newline="") as stream:
            for record in records:
                stream.write(canonical_json(record) + "\n")
    if config.policy.type != "dummy":
        summary["decision_metrics"] = decision_metrics(decisions, evaluations)
        summary.update({key: summary["decision_metrics"][key] for key in
                        ("unsafe_promotion_count", "unnecessary_rollback_count", "unnecessary_hold_count")})
    write_json(run_dir / "summary.json", summary)
    if real_provenance is not None:
        from .behavior.real import prompt_template_sha256
        from .backends.llama_cpp import sha256_file
        write_json(run_dir / "backend_runs.jsonl", real_provenance)
        write_json(run_dir / "real_provenance.json", {
            "model_sha256": real_provenance["model_sha256"],
            "workload_sha256": sha256_file(run_dir / "behavior_workload.json"),
            "prompt_template_sha256": prompt_template_sha256(),
            "runtime_version": real_provenance["llama_cpp_version"],
            "runtime_revision": real_provenance["llama_cpp_git_revision"],
            "saferoll_source_revision": build_manifest(config)["git_revision"],
            "config_sha256": config_hash(config),
            "host_architecture": real_provenance["host_architecture"],
            "operating_system": real_provenance["operating_system"],
            "logical_cpu_count": real_provenance["logical_cpu_count"]})
