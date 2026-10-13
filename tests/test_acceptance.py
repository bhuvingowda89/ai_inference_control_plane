import csv
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from saferoll.runner import run_experiment
from saferoll.validator import validate_run


def test_determinism_across_reruns(config, tmp_path):
    first = run_experiment(config, tmp_path)
    second = run_experiment(config, tmp_path)
    assert first != second
    for name in ("config.resolved.json", "requests.csv", "events.jsonl", "summary.json", "validation.json", "rollout_transitions.jsonl", "system_evidence.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    diagnostic = {"python_version", "platform", "git_revision", "git_dirty", "git_metadata_status"}
    manifests = [json.loads((p / "manifest.json").read_text()) for p in (first, second)]
    assert {k: v for k, v in manifests[0].items() if k not in diagnostic} == {
        k: v for k, v in manifests[1].items() if k not in diagnostic}


def test_epoch_isolation_and_contamination(config, tmp_path):
    one = run_experiment(config, tmp_path)
    two = run_experiment(replace(config, rollout=replace(config.rollout, epoch=2)), tmp_path)
    datasets = []
    for epoch, path in ((1, one), (2, two)):
        with (path / "requests.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        datasets.append(rows)
        assert all(r["epoch"] == str(epoch) for r in rows)
        assert all(json.loads(line)["epoch"] == epoch for line in (path / "events.jsonl").read_text().splitlines())
        for artifact in ("manifest.json", "summary.json"):
            assert json.loads((path / artifact).read_text())["epoch"] == epoch
        assert json.loads((path / "config.resolved.json").read_text())["rollout"]["epoch"] == epoch
    assert {r["request_id"] for r in datasets[0]}.isdisjoint(r["request_id"] for r in datasets[1])
    datasets[1][0] = datasets[0][0]
    with (two / "requests.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, datasets[1][0].keys())
        writer.writeheader()
        writer.writerows(datasets[1])
    assert not validate_run(two)["valid"]


def test_cli_and_hash_randomization(tmp_path):
    root = Path(__file__).parents[1]
    outputs = []
    for hash_seed in ("1", "999"):
        result = subprocess.run([sys.executable, "-m", "saferoll.cli", "run", "--config",
                                 str(root / "configs/phase4a-smoke.yaml"), "--output-root", str(tmp_path)],
                                env={**os.environ, "PYTHONHASHSEED": hash_seed}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        path = Path(json.loads(result.stdout)["run_dir"])
        outputs.append(path)
        validation = subprocess.run([sys.executable, "-m", "saferoll.cli", "validate", "--run-dir", str(path)],
                                    capture_output=True, text=True)
        assert validation.returncode == 0
        assert json.loads(validation.stdout) == {"valid": True, "issues": []}
    for name in ("requests.csv", "events.jsonl", "summary.json"):
        assert (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes()
    (outputs[0] / "summary.json").unlink()
    assert subprocess.run([sys.executable, "-m", "saferoll.cli", "validate", "--run-dir", str(outputs[0])],
                          capture_output=True).returncode == 1


def test_zero_requests(config, tmp_path):
    empty = replace(config, experiment=replace(config.experiment, request_count=0),
                    fault=replace(config.fault, start_request=0, end_request=0),
                    lifecycle=replace(config.lifecycle, canary_requests=0))
    path = run_experiment(empty, tmp_path)
    summary = json.loads((path / "summary.json").read_text())
    assert summary["completed_requests"] == 0 and summary["candidate_fraction"] == 0
    assert summary["mean_candidate_latency_ms"] is None
    assert sum(json.loads(line)["event_type"].startswith("REQUEST_") for line in
               (path / "events.jsonl").read_text().splitlines()) == 0


@pytest.mark.parametrize("candidate_weight", [0.0, 1.0])
def test_routing_extremes_and_run_end_fault(config, tmp_path, candidate_weight):
    full = replace(config, routing=replace(config.routing, candidate_weight=candidate_weight,
                                          baseline_weight=1 - candidate_weight),
                   fault=replace(config.fault, start_request=0, end_request=19))
    path = run_experiment(full, tmp_path)
    summary = json.loads((path / "summary.json").read_text())
    assert summary["candidate_requests"] == int(20 * candidate_weight)
    assert summary["fault_affected_requests"] == int(20 * candidate_weight)
    assert summary["mean_candidate_latency_ms"] == (62 if candidate_weight else None)
    events = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines()]
    assert next(e for e in events if e["event_type"] == "FAULT_ACTIVATED")["details"]["request_index"] == 0
    assert events[-2]["event_type"] == "FAULT_CLEARED"
    assert events[-2]["details"]["request_index"] == 20
