"""Focused regression checks for the refinement observation and state scenarios."""
from __future__ import annotations

import json
from pathlib import Path

from saferoll.config import load_config
from saferoll.research.matrix import with_policy
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT = Path(__file__).parents[1]


def records(path: Path, name: str) -> list[dict]:
    return [json.loads(line) for line in (path / name).read_text(encoding="utf-8").splitlines()]


def test_replayed_attribution_is_visible_fault_not_ground_truth_corruption(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/phase6b-revision_attribution_mismatch.yaml")
    results = {}
    for policy in ("ai_gate", "saferoll"):
        run = run_experiment(with_policy(config, policy), tmp_path)
        assert validate_run(run) == {"valid": True, "issues": []}
        results[policy] = (records(run, "decisions.jsonl")[0],
                           records(run, "decision_evaluations.jsonl")[0])
        fault = next(o for o in records(run, "observations.jsonl") if
                     o["observation_type"] == "behavior" and "replayed-attribution" in o["fault_ids"])
        assert fault["source_revision"] == config.candidate.revision
        assert fault["observed_value"]["revision"] == "candidate-A"
        assert fault["ground_truth_reference"]["window_id"] != fault["expected_ground_truth_reference"]["window_id"]
        assert fault["observed_value"]["accuracy"] == 1.0
    assert results["ai_gate"][0]["decision"] == "PROMOTE"
    assert results["ai_gate"][1]["correctness_class"] == "UNSAFE_PROMOTION"
    assert results["saferoll"][0]["decision"] == "HOLD"
    assert results["saferoll"][0]["reason_codes"] == ["IDENTITY_UNCERTAINTY"]
    assert results["saferoll"][1]["ground_truth_state"] == "UNSAFE"


def test_replay_corruption_is_rejected(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/phase6b-revision_attribution_mismatch.yaml")
    run = run_experiment(config, tmp_path)
    observations = records(run, "observations.jsonl")
    fault = next(o for o in observations if "replayed-attribution" in o["fault_ids"])
    fault["observed_value"]["accuracy"] = 0.33
    (run / "observations.jsonl").write_text("".join(json.dumps(o) + "\n" for o in observations), encoding="utf-8")
    assert not validate_run(run)["valid"]


def test_hidden_effective_state_remains_outside_policy_inputs(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/phase6b-rollout_realization_mismatch.yaml")
    for policy in ("ai_gate", "saferoll"):
        run = run_experiment(with_policy(config, policy), tmp_path)
        assert validate_run(run)["valid"]
        visible = records(run, "policy_inputs.jsonl")[0]
        decision = records(run, "decisions.jsonl")[0]
        assert "effective_candidate_weight" not in visible
        assert decision["decision"] == ("PROMOTE" if policy == "ai_gate" else "HOLD")
