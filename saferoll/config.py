"""Strict, resolved YAML configuration with explicit legacy migration defaults."""
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
class LifecycleConfig:
    validation_requests: int
    canary_requests: int
    evaluation_requests: int
    hold_requests: int
    completion_requests: int
    max_holds: int


@dataclass(frozen=True)
class DecisionPolicyConfig:
    type: str
    holds_before_decision: int


@dataclass(frozen=True)
class EvidenceConfig:
    minimum_samples_per_revision: int = 3
    max_age_logical_units: int = 10


@dataclass(frozen=True)
class Config:
    experiment: ExperimentConfig
    rollout: RolloutConfig
    baseline: BackendConfig
    candidate: BackendConfig
    routing: RoutingConfig
    fault: FaultConfig
    lifecycle: LifecycleConfig
    decision_policy: DecisionPolicyConfig
    evidence: EvidenceConfig = EvidenceConfig()

    @property
    def identity(self) -> RolloutIdentity:
        return RolloutIdentity(self.rollout.rollout_id, self.rollout.epoch,
                               self.baseline.revision, self.candidate.revision)

    @property
    def planned_requests(self) -> int:
        l = self.lifecycle
        p = self.decision_policy
        desired_holds = l.max_holds + 1 if p.type == "always_hold" else (
            p.holds_before_decision if p.type == "hold_then_promote" else 0)
        holds = min(desired_holds, l.max_holds)
        return (l.validation_requests + l.canary_requests + (1 + holds) * l.evaluation_requests
                + holds * l.hold_requests + (l.completion_requests if desired_holds <= l.max_holds else 0))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_from_dict(data: Any) -> Config:
    """Reject missing/unknown fields, coercion, nonfinite numbers, and invalid bounds."""
    sections = {"experiment": ExperimentConfig, "rollout": RolloutConfig,
                "baseline": BackendConfig, "candidate": BackendConfig,
                "routing": RoutingConfig, "fault": FaultConfig}
    if isinstance(data, dict):
        data = dict(data)
        evidence = data.pop("evidence", asdict(EvidenceConfig()))
    else:
        evidence = None
    if isinstance(data, dict) and set(data) == set(sections):
        # Accepted Phase 4A YAML: preserve every request's canary assignment.
        data = {**data, "lifecycle": {"validation_requests": 0,
                "canary_requests": data["experiment"].get("request_count", 0) if isinstance(data["experiment"], dict) else 0,
                "evaluation_requests": 0, "hold_requests": 0, "completion_requests": 0, "max_holds": 1},
                "decision_policy": {"type": "always_promote", "holds_before_decision": 0}}
    sections.update(lifecycle=LifecycleConfig, decision_policy=DecisionPolicyConfig)
    if isinstance(data, dict):
        data["evidence"] = evidence
    sections["evidence"] = EvidenceConfig
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
    l = config.lifecycle
    if any(value < 0 for value in asdict(l).values()):
        raise ValueError("lifecycle counts and max_holds must be nonnegative")
    p = config.decision_policy
    if p.type not in {"always_promote", "always_rollback", "hold_then_promote", "always_hold"}:
        raise ValueError("unsupported deterministic test policy")
    if p.holds_before_decision < 0 or (p.type != "hold_then_promote" and p.holds_before_decision != 0):
        raise ValueError("holds_before_decision requires hold_then_promote and a nonnegative value")
    if config.experiment.request_count != config.planned_requests:
        raise ValueError("request_count must equal the deterministic lifecycle request budget")
    if config.evidence.minimum_samples_per_revision < 1 or config.evidence.max_age_logical_units < 0:
        raise ValueError("evidence minimum must be positive and maximum age nonnegative")
    fault = config.fault
    if fault.type not in {"latency", "candidate_request_failure"} or fault.target != "candidate":
        raise ValueError("only latency/candidate_request_failure with candidate target is supported")
    if fault.type == "candidate_request_failure" and fault.added_latency_ms != 0:
        raise ValueError("request failure requires added_latency_ms: 0")
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
