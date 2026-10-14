"""Closed, frozen capabilities: policies receive only projected controller-visible data."""
from __future__ import annotations
from dataclasses import dataclass, asdict, fields
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class VisibleEvidence:
    observation_id: str | None
    available: bool
    scope_role: str
    rollout_id: str | None
    epoch: int | None
    revision: str | None
    backend_role: str | None
    window_id: str | None
    lifecycle_state: str | None
    transition_index: int | None
    sample_count: int
    sufficient: bool
    fresh: bool
    age_logical_units: int | None


@dataclass(frozen=True)
class SystemEvidence(VisibleEvidence):
    error_rate: float | None
    success_rate: float | None
    latency_p95_ms: float | None


@dataclass(frozen=True)
class BehaviorEvidence(VisibleEvidence):
    workload_id: str | None
    task_type: str | None
    sample_item_ids: tuple[str,...]
    accuracy: float | None
    exact_match_rate: float | None
    structure_valid_rate: float | None


@dataclass(frozen=True)
class PolicyInput:
    input_snapshot_id: str
    policy_name: str
    rollout_id: str
    epoch: int
    baseline_revision: str
    candidate_revision: str
    lifecycle_state: str
    transition_index: int
    expected_window_id: str
    logical_time: int


@dataclass(frozen=True)
class ReadinessInput(PolicyInput):
    system_observation_id: str | None
    candidate_available: bool
    candidate_sample_count: int
    candidate_success_rate: float | None


@dataclass(frozen=True)
class OpsInput(PolicyInput):
    system: tuple[SystemEvidence,...]


@dataclass(frozen=True)
class BehaviorInput(PolicyInput):
    behavior: tuple[BehaviorEvidence,...]


@dataclass(frozen=True)
class AIInput(PolicyInput):
    system: tuple[SystemEvidence,...]
    behavior: tuple[BehaviorEvidence,...]


@dataclass(frozen=True)
class SafeRollInput(AIInput):
    routing_observation_id: str | None
    desired_state: str
    observed_state: str | None
    desired_candidate_weight: float
    observed_candidate_weight: float | None


INPUT_TYPES={'readiness_gate':ReadinessInput,'ops_gate':OpsInput,'behavior_gate':BehaviorInput,
             'ai_gate':AIInput,'saferoll':SafeRollInput}


def snapshot_id(payload: dict[str,Any]) -> str:
    data={k:v for k,v in payload.items() if k!='input_snapshot_id'}
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def input_from_dict(payload: Any) -> PolicyInput:
    """Reject extra fields instead of allowing a ground-truth dictionary into a policy."""
    if not isinstance(payload,dict) or payload.get('policy_name') not in INPUT_TYPES:
        raise ValueError('invalid policy input type')
    cls=INPUT_TYPES[payload['policy_name']]
    if set(payload)!={f.name for f in fields(cls)}:
        raise ValueError('policy input fields violate the information boundary')
    values=dict(payload)
    for key,record_cls in [('system',SystemEvidence),('behavior',BehaviorEvidence)]:
        if key in values:
            records=[]
            for record in values[key]:
                if not isinstance(record,dict) or set(record)!={f.name for f in fields(record_cls)}:
                    raise ValueError('visible evidence fields violate the information boundary')
                record=dict(record)
                if key=='behavior':
                    record['sample_item_ids']=tuple(record['sample_item_ids'])
                records.append(record_cls(**record))
            values[key]=tuple(records)
    return cls(**values)
