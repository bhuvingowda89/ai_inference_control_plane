"""Strict, resolved YAML configuration with explicit legacy migration defaults."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any, get_type_hints
import yaml
from .ids import RolloutIdentity
from .research.configuration import PolicyConfig, parse_policy
from .faults.phase4e_config import Phase4EConfig, parse_phase4e, validate_phase4e


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
    behavior_profile: str = "correct"
    regression_every_nth_item: int = 4
    backend_type: str = "mock"
    prompt_variant: str = "standard"


@dataclass(frozen=True)
class RealRuntimeConfig:
    binary: str = "llama-server"
    model_path: str = "models/qwen2.5-0.5b-instruct-q4_k_m.gguf"
    model_sha256: str = "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db"
    host: str = "127.0.0.1"
    port: int = 18089
    thread_count: int = 4
    context_size: int = 512
    batch_size: int = 128
    temperature: float = 0.0
    max_tokens: int = 48
    startup_timeout_seconds: float = 90.0
    request_timeout_seconds: float = 30.0
    shutdown_timeout_seconds: float = 10.0


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
class BehaviorConfig:
    enabled: bool = False
    workload_id: str = "deterministic-parity-v1"
    task_type: str = "classification"
    item_count: int = 20
    baseline_items_per_window: int = 20
    candidate_items_per_window: int = 20
    minimum_samples_per_revision: int = 3
    minimum_paired_samples: int = 3
    max_age_logical_units: int = 1000


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
    behavior_evidence: BehaviorConfig = BehaviorConfig()
    phase4e: Phase4EConfig = Phase4EConfig()
    policy: PolicyConfig = PolicyConfig()
    real_runtime: RealRuntimeConfig = RealRuntimeConfig()

    @property
    def identity(self) -> RolloutIdentity:
        return RolloutIdentity(self.rollout.rollout_id, self.rollout.epoch,
                               self.baseline.revision, self.candidate.revision)

    @property
    def planned_requests(self) -> int:
        l = self.lifecycle
        if self.policy.type != "dummy":
            return (l.validation_requests + l.canary_requests + (1 + l.max_holds) * l.evaluation_requests
                    + l.max_holds * l.hold_requests + l.completion_requests)
        p = self.decision_policy
        desired_holds = l.max_holds + 1 if p.type == "always_hold" else (
            p.holds_before_decision if p.type == "hold_then_promote" else 0)
        holds = min(desired_holds, l.max_holds)
        return (l.validation_requests + l.canary_requests + (1 + holds) * l.evaluation_requests
                + holds * l.hold_requests + (l.completion_requests if desired_holds <= l.max_holds else 0))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for name in ("routing_faults", "observation_faults", "behavior_faults", "request_faults"):
            data["phase4e"][name] = list(data["phase4e"][name])
        return data


def config_from_dict(data: Any) -> Config:
    """Reject missing/unknown fields, coercion, nonfinite numbers, and invalid bounds."""
    sections = {"experiment": ExperimentConfig, "rollout": RolloutConfig,
                "baseline": BackendConfig, "candidate": BackendConfig,
                "routing": RoutingConfig, "fault": FaultConfig}
    if isinstance(data, dict):
        data = dict(data)
        research_policy = parse_policy(data.pop("policy", {}))
        real_runtime = data.pop("real_runtime", asdict(RealRuntimeConfig()))
        phase4e = parse_phase4e(data.pop("phase4e", {}))
        behavior = data.pop("behavior_evidence", asdict(BehaviorConfig()))
        for role in ("baseline", "candidate"):
            if isinstance(data.get(role), dict):
                data[role] = {"behavior_profile": "correct", "regression_every_nth_item": 4,
                              "backend_type": "mock", "prompt_variant": "standard", **data[role]}
        evidence = data.pop("evidence", asdict(EvidenceConfig()))
    else:
        evidence = None
        behavior = None
        phase4e = Phase4EConfig()
        research_policy = PolicyConfig()
        real_runtime = asdict(RealRuntimeConfig())
    if isinstance(data, dict) and set(data) == set(sections):
        # Accepted Phase 4A YAML: preserve every request's canary assignment.
        data = {**data, "lifecycle": {"validation_requests": 0,
                "canary_requests": data["experiment"].get("request_count", 0) if isinstance(data["experiment"], dict) else 0,
                "evaluation_requests": 0, "hold_requests": 0, "completion_requests": 0, "max_holds": 1},
                "decision_policy": {"type": "always_promote", "holds_before_decision": 0}}
    sections.update(lifecycle=LifecycleConfig, decision_policy=DecisionPolicyConfig)
    if isinstance(data, dict):
        data["evidence"] = evidence
        data["behavior_evidence"] = behavior
        data["real_runtime"] = real_runtime
    sections["evidence"] = EvidenceConfig
    sections["behavior_evidence"] = BehaviorConfig
    sections["real_runtime"] = RealRuntimeConfig
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
            if kind is bool and type(item) is not bool:
                raise ValueError(f"{section}.{key} must be a boolean")
            if kind is int and type(item) is not int:
                raise ValueError(f"{section}.{key} must be an integer")
            if kind is float:
                if type(item) not in (int, float) or not math.isfinite(item) or item < 0:
                    raise ValueError(f"{section}.{key} must be finite and nonnegative")
                item = float(item)
            normalized[key] = item
        resolved[section] = cls(**normalized)
    config = Config(**resolved, phase4e=phase4e, policy=research_policy)
    real = config.real_runtime
    import re
    if (real.host != "127.0.0.1" or not 1024 <= real.port <= 65535
            or not re.fullmatch(r"[0-9a-f]{64}", real.model_sha256)
            or min(real.thread_count, real.context_size, real.batch_size, real.max_tokens) < 1
            or min(real.startup_timeout_seconds, real.request_timeout_seconds,
                   real.shutdown_timeout_seconds) <= 0):
        raise ValueError("invalid real_runtime CPU/server/model parameters")
    if any(getattr(config, role).backend_type not in {"mock", "llama_cpp"}
           or getattr(config, role).prompt_variant not in {"standard", "weakened_json"}
           for role in ("baseline", "candidate")):
        raise ValueError("unsupported backend_type or prompt_variant")
    if config.baseline.backend_type != config.candidate.backend_type:
        raise ValueError("baseline and candidate backend types must match")
    if config.baseline.backend_type == "llama_cpp":
        if not config.behavior_evidence.enabled or config.phase4e.enabled:
            raise ValueError("real backend requires behavior evidence and no deterministic phase4e faults")
        if config.fault.added_latency_ms != 0 or config.fault.type != "latency":
            raise ValueError("real backend does not accept simulated request faults")
        if config.baseline.prompt_variant != "standard" or (config.candidate.prompt_variant == "weakened_json"
                and config.behavior_evidence.task_type != "structured_output"):
            raise ValueError("invalid real revision prompt configuration")
    validate_phase4e(config)
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
    b = config.behavior_evidence
    if b.task_type not in {"classification", "structured_output"}:
        raise ValueError("unsupported behavior task type")
    if (b.item_count < 1 or min(b.baseline_items_per_window, b.candidate_items_per_window) < 0
            or max(b.baseline_items_per_window, b.candidate_items_per_window) > b.item_count
            or min(b.minimum_samples_per_revision, b.minimum_paired_samples) < 1
            or b.max_age_logical_units < 0):
        raise ValueError("invalid behavior counts, sample limits or age")
    for backend in (config.baseline, config.candidate):
        if backend.behavior_profile not in {"correct", "classification_regression", "structured_output_regression"}:
            raise ValueError("unsupported behavior profile")
        if backend.regression_every_nth_item < 1:
            raise ValueError("regression_every_nth_item must be positive")
        if b.enabled and backend.behavior_profile != "correct" and backend.behavior_profile != b.task_type + "_regression":
            raise ValueError("behavior profile must match enabled task type")
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
