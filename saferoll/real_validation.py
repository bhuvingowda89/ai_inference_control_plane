"""Independent checks for measured runs; never rerun a nondeterministic model."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .behavior.real import prompt_template_sha256


def check_measured_timing(record: dict[str, Any]) -> None:
    start, end, latency = (record.get("request_start_monotonic"),
                           record.get("request_end_monotonic"), record.get("latency_ms"))
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (start, end, latency)):
        raise ValueError("measured timing must contain finite numbers")
    if start < 0 or end < start or latency < 0 or not math.isclose(latency, (end - start) * 1000,
                                                                  rel_tol=1e-8, abs_tol=1e-5):
        raise ValueError("measured start/end/latency mismatch")


def validate_real_artifacts(root: Path, config: Any, manifest: dict[str, Any],
                            rows: list[dict[str, str]], probes: list[dict[str, Any]],
                            events: list[dict[str, Any]], workload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    try:
        preflight = json.loads((root / "preflight.json").read_text(encoding="utf-8"))
        provenance = json.loads((root / "real_provenance.json").read_text(encoding="utf-8"))
        backend = json.loads((root / "backend_runs.jsonl").read_text(encoding="utf-8"))
        if not (root / "llama-server.log").is_file():
            raise ValueError("missing backend log")
        if (preflight.get("valid") is not True or not all(preflight.get("checks", {}).values())
                or preflight.get("expected_model_sha256") != config.real_runtime.model_sha256):
            raise ValueError("preflight did not verify the pinned model")
        if (backend.get("backend_type") != "llama_cpp" or backend.get("model_sha256") != config.real_runtime.model_sha256
                or backend.get("runtime_binary") != preflight.get("binary")
                or backend.get("model_path") != preflight.get("model_path")
                or backend.get("startup_result") != "READY" or backend.get("shutdown_result") != "STOPPED"
                or backend.get("backend_exit_status") is None or backend.get("llama_cpp_version") is None
                or backend.get("process_exit_status") != backend.get("backend_exit_status")
                or backend.get("process_exited") is not True or backend.get("listener_closed") is not True
                or backend.get("shutdown_success") is not True
                or type(backend.get("cleanup_attempts")) is not int or backend["cleanup_attempts"] < 1):
            raise ValueError("backend process/provenance mismatch")
        model_path = Path(preflight['model_path'])
        if model_path.is_file():
            from .backends.llama_cpp import sha256_file
            if (sha256_file(model_path) != config.real_runtime.model_sha256
                    or backend.get('model_file_size') != model_path.stat().st_size):
                raise ValueError('local model file changed after preflight')
        if (backend.get("thread_count") != config.real_runtime.thread_count
                or backend.get("context_size") != config.real_runtime.context_size
                or backend.get("batch_size") != config.real_runtime.batch_size
                or backend.get("temperature") != config.real_runtime.temperature
                or backend.get("seed") != config.experiment.seed
                or backend.get("runtime_arguments", [])[-4:-2] != ["--n-gpu-layers", "0"]):
            raise ValueError("CPU runtime parameters mismatch")
        expected_counts = {role: sum(r['backend_role'] == role for r in rows) +
                           sum(p['backend_role'] == role for p in probes) for role in ('baseline', 'candidate')}
        if backend.get("request_count_by_role") != expected_counts:
            raise ValueError("backend request accounting mismatch")
        workload_hash = hashlib.sha256((root / "behavior_workload.json").read_bytes()).hexdigest()
        if (provenance.get("model_sha256") != config.real_runtime.model_sha256
                or provenance.get("workload_sha256") != workload_hash
                or provenance.get("prompt_template_sha256") != prompt_template_sha256()
                or provenance.get("config_sha256") != manifest.get("config_sha256")
                or provenance.get("runtime_version") != backend.get("llama_cpp_version")
                or provenance.get("runtime_revision") != backend.get("llama_cpp_git_revision")):
            raise ValueError("real reproducibility manifest mismatch")
        completions = {e['request_id']: e for e in events if e['event_type'] == 'REQUEST_COMPLETED'}
        creations = {e['request_id']: e for e in events if e['event_type'] == 'REQUEST_CREATED'}
        if len(completions) != len(rows):
            raise ValueError("serving completion count mismatch")
        if len(creations) != len(rows):
            raise ValueError("serving creation count mismatch")
        for row in rows:
            parsed = dict(row)
            parsed['request_start_monotonic'] = float(row['request_start_monotonic'])
            parsed['request_end_monotonic'] = float(row['request_end_monotonic'])
            parsed['latency_ms'] = float(row['latency_ms'])
            if row['timing_mode'] != 'MEASURED' or row['success'] not in {'True', 'False'}:
                raise ValueError("real request timing/success mode mismatch")
            check_measured_timing(parsed)
            from .behavior.real import serving_prompt
            expected_prompt = serving_prompt(config.experiment.seed, int(row['request_index']),
                                             config.behavior_evidence.task_type,
                                             getattr(config, row['backend_role']).prompt_variant)
            if creations[row['request_id']]['details']['payload'] != expected_prompt:
                raise ValueError('serving prompt revision mismatch')
            event = completions[row['request_id']]
            if (event['served_revision'] != row['served_revision'] or event['details']['output'] != row['output']
                    or event['details']['latency_ms'] != parsed['latency_ms']
                    or event['details']['success'] != (row['success'] == 'True')):
                raise ValueError("real response differs from event trace")
        for probe in probes:
            check_measured_timing(probe)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, json.JSONDecodeError) as exc:
        issues.append(f"invalid real backend artifacts: {exc}")
    return issues


def validate_real_summary(config: Any, rows: list[dict[str, str]], summary: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    try:
        by_role = {role: [r for r in rows if r['backend_role'] == role] for role in ('baseline', 'candidate')}
        n = len(rows)
        expected = {'planned_requests': n, 'completed_requests': n,
                    'successful_requests': sum(r['success'] == 'True' for r in rows),
                    'failed_requests': sum(r['success'] == 'False' for r in rows),
                    'baseline_requests': len(by_role['baseline']), 'candidate_requests': len(by_role['candidate']),
                    'candidate_fraction': len(by_role['candidate']) / n if n else 0.0,
                    'fault_affected_requests': 0}
        for role in ('baseline', 'candidate'):
            subset = by_role[role]
            expected[f'mean_{role}_latency_ms'] = (sum(float(r['latency_ms']) for r in subset) / len(subset)
                                                   if subset else None)
        for key, value in expected.items():
            supplied = summary.get(key)
            if isinstance(value, float):
                match = type(supplied) in (int, float) and math.isclose(value, supplied, rel_tol=1e-12, abs_tol=1e-9)
            else:
                match = supplied == value and type(supplied) is type(value)
            if not match:
                issues.append(f'real summary mismatch: {key}')
        if config.policy.type != 'dummy' and summary.get('request_budget') != config.experiment.request_count:
            issues.append('real request budget mismatch')
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        issues.append(f'invalid real summary: {exc}')
    return issues
