"""Strict, resolved YAML configuration with no implicit defaults."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any, get_type_hints
import yaml
from .ids import RolloutIdentity


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    seed: int
    request_count: int


@dataclass(frozen=True)
class RolloutConfig:
    rollout_id: str
    epoch: int


@dataclass(frozen=True)
class BackendConfig:
    revision: str
    fixed_latency_ms: float
    response_prefix: str


@dataclass(frozen=True)
class RoutingConfig:
    baseline_weight: float
    candidate_weight: float


@dataclass(frozen=True)
class FaultConfig:
    type: str
    target: str
    start_request: int
    end_request: int
    added_latency_ms: float


@dataclass(frozen=True)
class Config:
    experiment: ExperimentConfig
    rollout: RolloutConfig
    baseline: BackendConfig
    candidate: BackendConfig
    routing: RoutingConfig
    fault: FaultConfig

    @property
    def identity(self) -> RolloutIdentity:
        return RolloutIdentity(self.rollout.rollout_id, self.rollout.epoch,
                               self.baseline.revision, self.candidate.revision)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_from_dict(data: Any) -> Config:
    """Reject missing/unknown fields, coercion, nonfinite numbers, and invalid bounds."""
    sections = {"experiment": ExperimentConfig, "rollout": RolloutConfig,
                "baseline": BackendConfig, "candidate": BackendConfig,
                "routing": RoutingConfig, "fault": FaultConfig}
    if not isinstance(data, dict) or set(data) != set(sections):
        raise ValueError("config requires exactly: " + ", ".join(sections))
    resolved = {}
    for section, cls in sections.items():
        value = data[section]
        if not isinstance(value, dict) or set(value) != set(cls.__annotations__):
            raise ValueError(f"{section}: missing or unknown fields")
        normalized = {}
        for key, kind in get_type_hints(cls).items():
            item = value[key]
            if kind is str and (not isinstance(item, str) or not item.strip()):
                raise ValueError(f"{section}.{key} must be a nonempty string")
            if kind is int and type(item) is not int:
                raise ValueError(f"{section}.{key} must be an integer")
            if kind is float:
                if type(item) not in (int, float) or not math.isfinite(item) or item < 0:
                    raise ValueError(f"{section}.{key} must be finite and nonnegative")
                item = float(item)
            normalized[key] = item
        resolved[section] = cls(**normalized)
    config = Config(**resolved)
    if config.experiment.request_count < 0 or config.rollout.epoch < 1:
        raise ValueError("request_count must be nonnegative and epoch must be positive")
    if config.baseline.revision == config.candidate.revision:
        raise ValueError("backend revisions must be distinct")
    weights = config.routing
    if max(weights.baseline_weight, weights.candidate_weight) > 1 or not math.isclose(
            weights.baseline_weight + weights.candidate_weight, 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError("routing weights must be in [0,1] and sum to 1")
    fault = config.fault
    if fault.type != "latency" or fault.target != "candidate":
        raise ValueError("only latency fault with candidate target is supported")
    if not math.isfinite(config.candidate.fixed_latency_ms + fault.added_latency_ms):
        raise ValueError("candidate latency plus fault must be finite")
    # Empty runs have no interval; [0,0] is the sole accepted inert configuration.
    limit = max(1, config.experiment.request_count)
    if not 0 <= fault.start_request <= fault.end_request < limit:
        raise ValueError("fault bounds must satisfy 0 <= start <= end < request_count")
    return config


def load_config(path: str | Path) -> Config:
    with Path(path).open(encoding="utf-8") as stream:
        return config_from_dict(yaml.safe_load(stream))
