"""Phase 4H campaign planning, resume, aggregation, and integrity gates."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from saferoll.artifacts import canonical_json
from saferoll.campaign.aggregate import descriptive
from saferoll.campaign.plan import derived_seed, expand_campaign, read_plan, write_plan
from saferoll.campaign.report import propose_freeze
from saferoll.campaign.runtime import run_campaign
from saferoll.campaign.spec import load_campaign_spec
from saferoll.campaign.validation import validate_campaign

REPO = Path(__file__).resolve().parents[1]
SMOKE = REPO / "configs/campaigns/phase4h-smoke.yaml"
PILOT = REPO / "configs/campaigns/phase6-pilot-v1.yaml"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_plan_expansion_is_deterministic_and_unique(tmp_path: Path) -> None:
    smoke = load_campaign_spec(SMOKE)
    rows_a, configs_a = expand_campaign(smoke)
    rows_b, configs_b = expand_campaign(smoke)
    assert canonical_json(rows_a) == canonical_json(rows_b)
    assert canonical_json(configs_a) == canonical_json(configs_b)
    assert len(rows_a) == len({r["run_id"] for r in rows_a}) == len({r["seed"] for r in rows_a}) == 8
    assert rows_a[0]["seed"] == derived_seed(smoke.seed_base, "healthy", "ops_gate", 0, {})
    directory = write_plan(smoke, tmp_path)
    first = (directory / "campaign_plan.json").read_bytes()
    assert write_plan(smoke, tmp_path) == directory
    assert (directory / "campaign_plan.json").read_bytes() == first
    assert len(read_plan(directory)["rows"]) == 8
    with pytest.raises(ValueError, match="differs"):
        write_plan(replace(smoke, seed_base=999), tmp_path)


def test_pilot_is_105_mock_runs_without_execution() -> None:
    spec = load_campaign_spec(PILOT)
    rows, _ = expand_campaign(spec)
    assert len(rows) == 105
    assert len(spec.scenarios) == 7 and len(spec.policies) == 5 and spec.repetitions == 3
    assert {r["expected_backend"] for r in rows} == {"MOCK"}
    assert len({r["threshold_signature"] for r in rows}) == 5
    assert all(len({r["threshold_signature"] for r in rows if r["policy"] == policy}) == 1
               for policy in spec.policies)


def test_optional_real_pilot_is_small_and_explicit() -> None:
    spec = load_campaign_spec(REPO / "configs/campaigns/phase4h-real-pilot-optional.yaml")
    rows, _ = expand_campaign(spec)
    assert len(rows) == 4
    assert {r["expected_backend"] for r in rows} == {"REAL_CPU"}


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(policies=["saferoll", "saferoll"]),
    lambda x: x["campaign"].update(repetitions=0),
    lambda x: x["scenarios"][0].update(backend="REAL_CPU"),
    lambda x: x["scenarios"][0].update(id="../unsafe"),
    lambda x: x["scenarios"][0].update(fault_domains=["UNKNOWN"]),
])
def test_campaign_spec_rejects_invalid_fields(tmp_path: Path, mutation) -> None:
    import yaml
    data = yaml.safe_load(SMOKE.read_text())
    mutation(data)
    campaign_dir = tmp_path / "configs/campaigns"
    campaign_dir.mkdir(parents=True)
    for scenario in data["scenarios"]:
        original = REPO / scenario["template"]
        copy = tmp_path / scenario["template"]
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(original.read_bytes())
    path = campaign_dir / "bad.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        load_campaign_spec(path)


def test_sweep_seed_and_plan_expansion() -> None:
    spec = load_campaign_spec(PILOT)
    scenario = replace(spec.scenarios[1], sweep=(("fault.added_latency_ms", (10, 20, 40)),))
    short = replace(spec, id="sweep-test", scenarios=(scenario,), policies=("saferoll",), repetitions=2)
    rows, configs = expand_campaign(short)
    assert len(rows) == 6
    assert len({r["seed"] for r in rows}) == 6
    assert {configs[r["run_id"]]["fault"]["added_latency_ms"] for r in rows} == {10.0, 20.0, 40.0}


@pytest.fixture(scope="module")
def completed(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("campaign")
    spec = replace(load_campaign_spec(SMOKE), id="phase4h-test-smoke")
    directory, result = run_campaign(spec, root)
    assert result == {"valid": True, "issues": []}
    return directory


def test_eight_run_smoke_and_aggregates(completed: Path) -> None:
    index = _rows(completed / "run_index.jsonl")
    assert len(index) == 8
    assert all(r["status"] == "VALID" for r in index)
    assert validate_campaign(completed) == {"valid": True, "issues": []}
    summary = json.loads((completed / "aggregate/campaign_summary.json").read_text())
    assert summary["planned_runs"] == summary["valid_runs"] == 8
    assert summary["total_decisions"] == 8
    runs_csv = (completed / "aggregate/run_summary.csv").read_text().splitlines()
    decisions_csv = (completed / "aggregate/decision_summary.csv").read_text().splitlines()
    assert len(runs_csv) == len(decisions_csv) == 9
    assert "candidate_p95_latency" in runs_csv[0]
    report = json.loads((completed / "pilot_report.json").read_text())
    assert report["per_policy_hold_rate"] == {"ops_gate": 0.0, "saferoll": 0.0}
    assert "candidate_behavior_delta_mean" in report["scenario_policy_diagnostics"]["behavior_regression/saferoll"]
    assert report["decision_matrix"]["behavior_regression/saferoll"] == {
        "PROMOTE": 0, "HOLD": 0, "ROLLBACK": 2}


def test_resume_reuses_valid_artifacts(completed: Path) -> None:
    before = _rows(completed / "run_index.jsonl")
    run_files = [completed / r["run_dir"] / "summary.json" for r in before]
    bytes_before = [p.read_bytes() for p in run_files]
    spec = replace(load_campaign_spec(SMOKE), id="phase4h-test-smoke")
    path, result = run_campaign(spec, completed.parent, resume=True)
    assert path == completed and result["valid"]
    assert _rows(completed / "run_index.jsonl") == before or all(r["status"] == "VALID" for r in _rows(completed / "run_index.jsonl"))
    assert [p.read_bytes() for p in run_files] == bytes_before
    assert len(_rows(completed / "resume_history.jsonl")) == 2
    with pytest.raises(ValueError, match="--resume"):
        run_campaign(spec, completed.parent)


def test_freeze_proposal_is_not_final(completed: Path) -> None:
    output = propose_freeze(completed)
    proposal = json.loads(output.read_text())
    assert proposal["status"] == "PROPOSED_NOT_FROZEN"
    assert proposal["repetitions"] == 2
    assert proposal["real_model_hashes"] == []
    assert len(proposal["policy_thresholds"]) == 2
    assert proposal["fault_severities"]["behavior_regression/{}"]["candidate_behavior_profile"] == "classification_regression"


def test_resume_rejects_changed_source(completed: Path, monkeypatch) -> None:
    from saferoll.campaign import runtime
    spec = replace(load_campaign_spec(SMOKE), id="phase4h-test-smoke")
    monkeypatch.setattr(runtime, "_source_hash", lambda: "changed")
    with pytest.raises(ValueError, match="source/config/schema changed"):
        run_campaign(spec, completed.parent, resume=True)


def test_negative_deltas_are_valid_descriptive_data() -> None:
    assert descriptive([-4, -2, 0])["iqr"] == 2
    assert descriptive([])["mean"] is None


def test_failed_run_excluded_then_resume_recovers(tmp_path: Path, monkeypatch) -> None:
    from saferoll.campaign import runtime
    from saferoll.campaign.aggregate import build_aggregates
    original = runtime.run_experiment
    spec = load_campaign_spec(SMOKE)
    spec = replace(spec, id="phase4h-failure-test", scenarios=spec.scenarios[:1],
                   policies=("saferoll",), repetitions=1)

    def fail_once(config, slot):
        marker = Path(slot) / "run-failed"
        marker.mkdir()
        (marker / "diagnostic.txt").write_text("preserved attempt")
        raise RuntimeError("injected executor failure")

    monkeypatch.setattr(runtime, "run_experiment", fail_once)
    directory, result = run_campaign(spec, tmp_path)
    assert not result["valid"]
    index = _rows(directory / "run_index.jsonl")
    assert index[0]["status"] == "EXECUTION_FAILED"
    assert _rows(directory / "failures.jsonl")[0]["error_type"] == "RuntimeError"
    outputs = build_aggregates(directory, read_plan(directory)["rows"], index)
    assert outputs["_summary"]["valid_runs"] == 0
    assert (directory / "runs" / index[0]["run_id"] / "run-failed/diagnostic.txt").is_file()
    monkeypatch.setattr(runtime, "run_experiment", original)
    _, recovered = run_campaign(spec, tmp_path, resume=True)
    assert recovered["valid"]
    assert (directory / "runs" / index[0]["run_id"] / "run-failed/diagnostic.txt").is_file()


@pytest.mark.parametrize("corruption", ["aggregate", "duplicate_index", "unplanned_slot", "run_artifact", "missing_index"])
def test_campaign_validator_rejects_corruption(completed: Path, tmp_path: Path, corruption: str) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(completed, copy)
    if corruption == "aggregate":
        path = copy / "aggregate/run_summary.csv"
        path.write_text(path.read_text() + "fabricated,row\n")
    elif corruption == "duplicate_index":
        path = copy / "run_index.jsonl"
        path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    elif corruption == "unplanned_slot":
        (copy / "runs/unplanned").mkdir()
    elif corruption == "run_artifact":
        first = _rows(copy / "run_index.jsonl")[0]
        (copy / first["run_dir"] / "summary.json").write_text("{}\n")
    elif corruption == "missing_index":
        path = copy / "run_index.jsonl"
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    result = validate_campaign(copy)
    assert not result["valid"], corruption


def test_invalid_attempt_preserved_and_replaced_on_resume(completed: Path, tmp_path: Path) -> None:
    root = tmp_path / "root"
    copy = root / "phase4h-test-smoke"
    shutil.copytree(completed, copy)
    first = _rows(copy / "run_index.jsonl")[0]
    invalid = copy / first["run_dir"]
    (invalid / "summary.json").write_text("{}\n")
    spec = replace(load_campaign_spec(SMOKE), id="phase4h-test-smoke")
    path, result = run_campaign(spec, root, resume=True)
    assert path == copy and result["valid"]
    new = _rows(copy / "run_index.jsonl")[0]
    assert new["run_dir"] != first["run_dir"]
    assert (invalid / "summary.json").read_text() == "{}\n"
    assert (copy / new["run_dir"] / "summary.json").is_file()


def test_cli_campaign_plan_and_validate(completed: Path, tmp_path: Path) -> None:
    plan = subprocess.run([sys.executable, "-m", "saferoll.cli", "campaign-plan", "--config", str(SMOKE),
                           "--output-root", str(tmp_path)], cwd=REPO, capture_output=True, text=True)
    assert plan.returncode == 0 and json.loads(plan.stdout)["planned_runs"] == 8
    validation = subprocess.run([sys.executable, "-m", "saferoll.cli", "campaign-validate",
                                 "--campaign-dir", str(completed)], cwd=REPO, capture_output=True, text=True)
    assert validation.returncode == 0 and json.loads(validation.stdout)["valid"]
