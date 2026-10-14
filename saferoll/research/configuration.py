"""Externally configurable frozen research thresholds; no experimental fault inputs."""
from __future__ import annotations
from dataclasses import dataclass, fields
import math
from typing import Any

POLICY_NAMES=('readiness_gate','ops_gate','behavior_gate','ai_gate','saferoll')
POLICY_VERSION='1.0'


@dataclass(frozen=True)
class SystemThresholds:
    max_error_rate: float = .05
    max_latency_regression_fraction: float = .20
    min_readiness_success_rate: float = .50


@dataclass(frozen=True)
class BehaviorThresholds:
    max_accuracy_drop: float = .05
    max_exact_match_drop: float = .05
    min_structure_valid_rate: float = .95


@dataclass(frozen=True)
class EvidenceRequirements:
    require_system_sufficient: bool = True
    require_behavior_sufficient: bool = True
    require_fresh_system: bool = True
    require_fresh_behavior: bool = True


@dataclass(frozen=True)
class ConsistencyRequirements:
    require_observed_routing_match: bool = True


@dataclass(frozen=True)
class PolicyConfig:
    type: str = 'dummy'
    numeric_tolerance: float = 1e-9
    system: SystemThresholds = SystemThresholds()
    behavior: BehaviorThresholds = BehaviorThresholds()
    evidence: EvidenceRequirements = EvidenceRequirements()
    consistency: ConsistencyRequirements = ConsistencyRequirements()


def parse_policy(data: Any) -> PolicyConfig:
    if not isinstance(data,dict) or set(data)-{f.name for f in fields(PolicyConfig)}:
        raise ValueError('invalid policy section or unknown thresholds')
    name=data.get('type','dummy'); tolerance=data.get('numeric_tolerance',1e-9)
    if name not in ('dummy',*POLICY_NAMES) or type(tolerance) not in (int,float) or not math.isfinite(tolerance) or not 0<=tolerance<=1e-6:
        raise ValueError('invalid policy type or numeric tolerance')
    groups={}
    for key,cls in [('system',SystemThresholds),('behavior',BehaviorThresholds),('evidence',EvidenceRequirements),('consistency',ConsistencyRequirements)]:
        values=data.get(key,{})
        if not isinstance(values,dict) or set(values)-{f.name for f in fields(cls)}:
            raise ValueError(f'invalid policy.{key} fields')
        normalized={}
        for field,value in values.items():
            if key in {'evidence','consistency'}:
                if type(value) is not bool:
                    raise ValueError('evidence/consistency switches must be booleans')
            else:
                if type(value) not in (int,float) or not math.isfinite(value) or value<0 or (field!='max_latency_regression_fraction' and value>1):
                    raise ValueError('policy thresholds must be finite nonnegative values; rates/drops <=1')
                value=float(value)
            normalized[field]=value
        groups[key]=cls(**normalized)
    return PolicyConfig(name,float(tolerance),**groups)
