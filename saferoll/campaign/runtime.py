"""Sequential campaign execution with resumable, preserved run attempts."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import time
from typing import Any

from ..artifacts import canonical_json, write_json
from ..config import config_from_dict
from ..runner import run_experiment
from ..schemas import SCHEMA_VERSION
from ..validator import validate_run
from .aggregate import write_aggregates
from .plan import read_plan, write_plan
from .report import write_pilot_report
from .spec import CAMPAIGN_SCHEMA_VERSION, CampaignSpec


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_lines(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def _source_hash() -> str:
    import hashlib
    package = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _git_revision() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _manifest(spec: CampaignSpec, plan: dict[str, Any]) -> dict[str, Any]:
    versions = {p["policy"]: p["policy_version"] for p in plan["rows"]}
    scenarios = {s.id: {"version": s.version, "fault_domains": list(s.fault_domains),
                        "ground_truth_intent": s.ground_truth_intent,
                        "expected_condition": s.expected_condition, "backend": s.backend}
                 for s in spec.scenarios}
    return {"schema_version": CAMPAIGN_SCHEMA_VERSION, "campaign_id": spec.id,
            "campaign_config_sha256": spec.config_sha256, "source_git_revision": _git_revision(),
            "source_tree_sha256": _source_hash(), "artifact_schema_version": SCHEMA_VERSION,
            "policy_versions": versions, "scenario_versions": scenarios,
            "threshold_signatures": sorted({p["threshold_signature"] for p in plan["rows"]}),
            "python_version": platform.python_version(), "host": platform.platform(),
            "started_at": _now(), "ended_at": None,
            "backend_versions": {"MOCK": "saferoll-mock-v1"}}


def _validated_attempt(directory: Path, planned: dict[str, Any]) -> tuple[str | None, str | None, float]:
    """Find a valid attempt by its config hash, never by directory presence alone."""
    slot = directory / "runs" / planned["run_id"]
    if not slot.exists():
        return None, None, 0.0
    for candidate in sorted(slot.glob("run-*")):
        try:
            manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("config_sha256") != planned["config_sha256"]:
                continue
            start = time.monotonic()
            result = validate_run(candidate)
            duration = time.monotonic() - start
            if result["valid"]:
                return str(candidate.relative_to(directory)), "VALID", duration
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return None, None, 0.0


def _index_row(planned: dict[str, Any], status: str, run_dir: str | None,
               error: str | None = None, stage: str | None = None,
               duration: float | None = None, validation_duration: float | None = None) -> dict[str, Any]:
    return {"campaign_id": planned["campaign_id"], "run_id": planned["run_id"],
            "scenario": planned["scenario"], "policy": planned["policy"],
            "repetition": planned["repetition"], "seed": planned["seed"],
            "status": status, "run_dir": run_dir, "validation_status": status == "VALID",
            "decision": None, "ground_truth_state": None, "correctness_class": None,
            "critical_error": None, "error_type": None, "error": error, "stage": stage,
            "run_duration_seconds": duration, "validation_duration_seconds": validation_duration}


def run_campaign(spec: CampaignSpec, root: str | Path = "campaigns", *, resume: bool = False,
                 fail_fast: bool = False) -> tuple[Path, dict[str, Any]]:
    invocation_started = time.monotonic()
    directory = write_plan(spec, root)
    plan = read_plan(directory)
    index_path = directory / "run_index.jsonl"
    previous = _json_lines(index_path)
    if previous and not resume:
        raise ValueError("campaign already attempted; use --resume")
    existing = {r["run_id"]: r for r in previous}
    if len(existing) != len(previous) or set(existing) - {r["run_id"] for r in plan["rows"]}:
        raise ValueError("run index contains duplicate or unplanned identity")
    manifest_path = directory / "campaign_manifest.json"
    if not manifest_path.exists():
        write_json(manifest_path, _manifest(spec, plan))
    else:
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (saved.get("source_tree_sha256") != _source_hash()
                or saved.get("source_git_revision") != _git_revision()
                or saved.get("campaign_config_sha256") != spec.config_sha256
                or saved.get("artifact_schema_version") != SCHEMA_VERSION):
            raise ValueError("campaign source/config/schema changed; use a new campaign ID")
    history = directory / "resume_history.jsonl"
    with history.open("a", encoding="utf-8") as stream:
        stream.write(canonical_json({"at": _now(), "resume": resume, "previous_records": len(previous)}) + "\n")
    records: dict[str, dict[str, Any]] = dict(existing)
    for planned in plan["rows"]:
        run_id = planned["run_id"]
        valid_dir, _, prior_validation_duration = _validated_attempt(directory, planned)
        if valid_dir is not None:
            row = records.get(run_id) or _index_row(planned, "VALID", valid_dir)
            decisions = _json_lines(directory / valid_dir / "decisions.jsonl")
            evaluations = _json_lines(directory / valid_dir / "decision_evaluations.jsonl")
            row.update(status="VALID", run_dir=valid_dir, validation_status=True,
                       validation_duration_seconds=prior_validation_duration,
                       decision=decisions[0]["decision"] if decisions else None,
                       ground_truth_state=evaluations[0]["ground_truth_state"] if evaluations else None,
                       correctness_class=evaluations[0]["correctness_class"] if evaluations else None,
                       critical_error=evaluations[0]["critical_error"] if evaluations else None)
            records[run_id] = row
            _write_lines(index_path, [records[p["run_id"]] for p in plan["rows"] if p["run_id"] in records])
            continue
        config_path = directory / planned["config_path"]
        slot = directory / "runs" / run_id
        slot.mkdir(parents=True, exist_ok=True)
        begun = time.monotonic()
        stage = "precheck"
        run_dir: Path | None = None
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            config = config_from_dict(data)
            from ..artifacts import config_hash
            if config_hash(config) != planned["config_sha256"]:
                raise ValueError("planned run config hash mismatch")
            stage = "execution"
            run_dir = run_experiment(config, slot)
            stage = "validation"
            validated_at = time.monotonic()
            validation = validate_run(run_dir)
            validation_duration = time.monotonic() - validated_at
            if not validation["valid"]:
                status = "INVALID"
                error = "; ".join(validation["issues"])
            else:
                status = "VALID"
                error = None
            row = _index_row(planned, status, str(run_dir.relative_to(directory)), error,
                             stage, time.monotonic() - begun, validation_duration)
        except (OSError, ValueError, RuntimeError) as exc:
            status = "PRECHECK_FAILED" if stage == "precheck" else "EXECUTION_FAILED"
            error = str(exc)
            attempts = sorted(slot.glob("run-*"))
            path = run_dir or (attempts[-1] if attempts else None)
            if path and (path / "preflight.json").is_file():
                try:
                    preflight = json.loads((path / "preflight.json").read_text(encoding="utf-8"))
                    if preflight.get("valid") is False:
                        status, stage = "PRECHECK_FAILED", "precheck"
                except (ValueError, OSError):
                    pass
            if path and (path / "validation.json").is_file() and stage == "execution":
                try:
                    if not json.loads((path / "validation.json").read_text(encoding="utf-8")).get("valid", True):
                        status = "INVALID"
                except (ValueError, OSError):
                    pass
            row = _index_row(planned, status, str(path.relative_to(directory)) if path else None,
                             error, stage, time.monotonic() - begun)
            row["error_type"] = type(exc).__name__
        if row["status"] == "VALID":
            root_run = directory / row["run_dir"]
            decisions = _json_lines(root_run / "decisions.jsonl")
            evaluations = _json_lines(root_run / "decision_evaluations.jsonl")
            row.update(decision=decisions[0]["decision"] if decisions else None,
                       ground_truth_state=evaluations[0]["ground_truth_state"] if evaluations else None,
                       correctness_class=evaluations[0]["correctness_class"] if evaluations else None,
                       critical_error=evaluations[0]["critical_error"] if evaluations else None)
        records[run_id] = row
        _write_lines(index_path, [records[p["run_id"]] for p in plan["rows"] if p["run_id"] in records])
        if fail_fast and row["status"] != "VALID":
            break
    ordered = [records[p["run_id"]] for p in plan["rows"] if p["run_id"] in records]
    failures = [r for r in ordered if r["status"] != "VALID"]
    _write_lines(directory / "failures.jsonl", failures)
    outputs = write_aggregates(directory, plan["rows"], ordered)
    write_pilot_report(directory, plan, outputs, ordered)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ended_at"] = _now()
    manifest["campaign_duration_seconds"] = (
        datetime.fromisoformat(manifest["ended_at"]) - datetime.fromisoformat(manifest["started_at"])).total_seconds()
    manifest["last_invocation_duration_seconds"] = time.monotonic() - invocation_started
    manifest["recorded_run_duration_seconds"] = sum(r.get("run_duration_seconds") or 0 for r in ordered)
    real_versions = sorted({json.loads((directory / r["run_dir"] / "real_provenance.json").read_text())["runtime_version"]
                            for r in ordered if r["status"] == "VALID" and (directory / r["run_dir"] / "real_provenance.json").exists()})
    if real_versions:
        manifest["backend_versions"]["REAL_CPU"] = real_versions
    write_json(manifest_path, manifest)
    from .validation import validate_campaign
    result = validate_campaign(directory)
    return directory, result
