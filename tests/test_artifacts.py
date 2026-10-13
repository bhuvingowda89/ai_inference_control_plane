import csv
import json
from dataclasses import replace
import pytest
from saferoll.artifacts import config_hash, write_artifacts
from saferoll.runner import simulate, summarize


def test_complete_artifacts_and_summary(run_dir, config):
    assert {p.name for p in run_dir.iterdir()} == {
        "manifest.json", "config.resolved.json", "requests.csv", "events.jsonl", "summary.json", "validation.json"}
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["config_sha256"] == config_hash(config)
    with (run_dir / "requests.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    summary = json.loads((run_dir / "summary.json").read_text())
    candidate = [r for r in rows if r["backend_role"] == "candidate"]
    assert summary["completed_requests"] == len(rows) == 20
    assert summary["candidate_requests"] == len(candidate)
    assert summary["candidate_fraction"] == len(candidate) / 20
    assert summary["mean_candidate_latency_ms"] == sum(float(r["latency_ms"]) for r in candidate) / len(candidate)
    assert summary["fault_affected_requests"] == sum(r["fault_active"] == "True" for r in rows)
    assert json.loads((run_dir / "config.resolved.json").read_text()) == config.to_dict()
    responses, events, expected_summary = simulate(config)
    with pytest.raises(FileExistsError):
        write_artifacts(run_dir, config, responses, events, expected_summary)


def test_summary_failure_accounting(config):
    responses, _, _ = simulate(config)
    responses[0] = replace(responses[0], success=False)
    summary = summarize(config, responses)
    assert summary["successful_requests"] == 19
    assert summary["failed_requests"] == 1
