"""Regression coverage for the final-v2 historical runtime freeze defect."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from saferoll.artifacts import canonical_json
from saferoll.config import load_config
from saferoll.faults.phase4e_config import BehaviorFault, ObservationFault, Phase4EConfig
from saferoll.observations import historical_source
from saferoll.campaign.final_v3 import DIRECTORY, preflight
from saferoll.config import config_from_dict
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT = Path(__file__).parents[1]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pair(work: Path, *, namespace: str = "independent-campaign"):
    base = load_config(ROOT / "configs/phase6b-latency_severity.yaml")
    source_name = "prior-source-r0"
    prior = replace(base, experiment=replace(base.experiment, name=source_name),
                    rollout=replace(base.rollout, rollout_id=source_name, epoch=1),
                    candidate=replace(base.candidate, revision="candidate-v2"))
    source = run_experiment(prior, work / "prior")
    current_name = "current-historical-r0"
    current = replace(base, experiment=replace(base.experiment, name=current_name),
        rollout=replace(base.rollout, rollout_id=current_name, epoch=2),
        candidate=replace(base.candidate, revision="candidate-v3"))
    rel = source.relative_to(ROOT).as_posix()
    workload = json.loads((source / "behavior_workload.json").read_text())
    seed = base.experiment.seed
    entry = {"run_dir": rel, "source_run_id": source.name, "seed": seed,
        "pairing_key": "historical_attribution:epoch1_v2_to_epoch2_v3:0",
        "identity_relation": "DISTINCT_SOURCE_CURRENT", "source_epoch": 1,
        "source_revision": "candidate-v2", "epoch": 1, "candidate_revision": "candidate-v2",
        "rollout_id": source_name, "source_artifact_path": rel + "/behavior_evidence.jsonl",
        "source_sha256": _digest(source / "behavior_evidence.jsonl"),
        "behavior_evidence_sha256": _digest(source / "behavior_evidence.jsonl"),
        "source_config_sha256": _digest(source / "config.resolved.json"),
        "workload_sha256": _digest(source / "behavior_workload.json"),
        "workload_id": workload["workload_id"]}
    index = work / "history_index.json"
    data = {"schema_version": "2.0", "campaign_id": namespace, "entries": {str(seed): entry}}
    _write(index, data)
    fault = ObservationFault("real-history", "historical_evidence_replay", "behavior", "candidate", 2, 1,
        source_window=2, historical_index_path=index.relative_to(ROOT).as_posix(),
        historical_index_sha256=_digest(index))
    current = replace(current, phase4e=Phase4EConfig(True,
        behavior_faults=(BehaviorFault("v3-regression", "classification_regression", "candidate", 2, 1, 4),),
        observation_faults=(fault,)))
    return current, source, index, data


def _write(path: Path, data: dict) -> None:
    path.write_text(canonical_json(data) + "\n")


def _with_index(current, index: Path, data: dict):
    _write(index, data)
    fault = replace(current.phase4e.observation_faults[0], historical_index_sha256=_digest(index))
    return replace(current, phase4e=replace(current.phase4e, observation_faults=(fault,)))


def test_different_campaign_namespace_and_cross_epoch_runtime_smoke():
    with TemporaryDirectory(dir=ROOT / "runs", prefix="v3-history-test-") as directory:
        current, source, _, _ = _pair(Path(directory), namespace="unrelated-valid-namespace")
        record, _, path, digest = historical_source(current, current.phase4e.observation_faults[0])
        assert path == source.relative_to(ROOT).as_posix()
        assert digest == _digest(source / "behavior_evidence.jsonl")
        assert (record["epoch"], record["revision"], record["rollout_id"]) == (1, "candidate-v2", "prior-source-r0")
        run = run_experiment(current, Path(directory) / "current")
        assert validate_run(run)["valid"]
        observations = [json.loads(x) for x in (run / "observations.jsonl").read_text().splitlines()]
        replay = next(x for x in observations if "real-history" in x["fault_ids"])
        assert replay["ground_truth_reference"]["source_run_dir"] == path
        assert replay["observed_value"]["epoch"] == 1
        assert replay["observed_value"]["revision"] == "candidate-v2"
        assert replay["expected_ground_truth_reference"]["epoch"] == 2
        evaluations = [json.loads(x) for x in (run / "decision_evaluations.jsonl").read_text().splitlines()]
        assert evaluations[0]["ground_truth_state"] == "UNSAFE"


@pytest.mark.parametrize("change", ["wrong_hash", "wrong_workload", "wrong_seed", "wrong_pairing",
                                    "mutated_revision", "mutated_epoch", "equal_rollout", "arbitrary_source"])
def test_corrupt_historical_source_is_rejected(change: str):
    with TemporaryDirectory(dir=ROOT / "runs", prefix="v3-history-test-") as directory:
        current, source, index, data = _pair(Path(directory))
        entry = data["entries"][str(current.experiment.seed)]
        if change == "wrong_hash":
            entry["behavior_evidence_sha256"] = "0" * 64
        elif change == "wrong_workload":
            entry["workload_sha256"] = "0" * 64
        elif change == "wrong_seed":
            entry["seed"] += 1
        elif change == "wrong_pairing":
            entry["pairing_key"] = "historical_attribution:epoch1_v2_to_epoch2_v3:9"
        elif change == "mutated_revision":
            entry["source_revision"] = "candidate-v3"
        elif change == "mutated_epoch":
            entry["source_epoch"] = 2
        elif change == "equal_rollout":
            entry["identity_relation"] = "SAME_SOURCE_CURRENT"
        elif change == "arbitrary_source":
            entry["run_dir"] = "campaigns/final-v2/history/not-the-pinned-source"
        current = _with_index(current, index, data)
        with pytest.raises((ValueError, KeyError, OSError)):
            historical_source(current, current.phase4e.observation_faults[0])


def test_index_sha_mismatch_is_rejected():
    with TemporaryDirectory(dir=ROOT / "runs", prefix="v3-history-test-") as directory:
        current, _, index, data = _pair(Path(directory))
        data["campaign_id"] = "changed-after-freeze"
        _write(index, data)
        with pytest.raises(ValueError, match="index SHA-256 mismatch"):
            historical_source(current, current.phase4e.observation_faults[0])


def test_final_v3_preflight_and_runtime_accept_same_frozen_references():
    if not (DIRECTORY / "campaign_plan.json").is_file():
        return
    result = preflight(require_frozen=False, check_clean=False)
    assert result["valid"], result["issues"]
    assert result["historical_runtime_seeds_checked"] == 10
    plan = json.loads((DIRECTORY / "campaign_plan.json").read_text())
    for row in plan["rows"]:
        if row["scenario"] != "historical_attribution":
            continue
        config = config_from_dict(json.loads((DIRECTORY / row["config_path"]).read_text()))
        source, _, _, _ = historical_source(config, config.phase4e.observation_faults[0])
        assert source["epoch"] < config.rollout.epoch
        assert source["revision"] != config.candidate.revision
