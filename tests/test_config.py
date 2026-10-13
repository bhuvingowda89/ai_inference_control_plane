import copy
import pytest
from saferoll.config import config_from_dict


@pytest.mark.parametrize("section,key,value", [
    ("experiment", "request_count", -1), ("experiment", "request_count", True),
    ("experiment", "seed", "101"), ("rollout", "epoch", 0),
    ("rollout", "rollout_id", ""), ("candidate", "revision", "baseline-v1"),
    ("baseline", "fixed_latency_ms", -1), ("candidate", "fixed_latency_ms", float("nan")),
    ("routing", "baseline_weight", 0.9), ("routing", "candidate_weight", -0.2),
    ("routing", "candidate_weight", 1.2), ("routing", "candidate_weight", float("inf")),
    ("fault", "type", "error"), ("fault", "target", "baseline"),
    ("fault", "start_request", -1), ("fault", "start_request", 13),
    ("fault", "end_request", 20), ("fault", "added_latency_ms", -1),
])
def test_invalid_values(config, section, key, value):
    data = copy.deepcopy(config.to_dict())
    data[section][key] = value
    with pytest.raises(ValueError):
        config_from_dict(data)


def test_missing_unknown_and_tolerance(config):
    data = config.to_dict()
    data["experiment"]["unknown"] = 1
    with pytest.raises(ValueError):
        config_from_dict(data)
    data = config.to_dict()
    del data["fault"]
    with pytest.raises(ValueError):
        config_from_dict(data)
    data = config.to_dict()
    data["routing"]["baseline_weight"] += 1e-10
    assert config_from_dict(data).experiment.seed == 101


def test_round_trip(config):
    assert config_from_dict(config.to_dict()) == config
