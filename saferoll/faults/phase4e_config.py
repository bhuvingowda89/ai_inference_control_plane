"""Strict compound fault configuration; scopes use serving requests or window sequence."""
from __future__ import annotations
from dataclasses import dataclass, asdict, fields
import math
from typing import Any

ROUTING_TYPES = {'routing_propagation_delay','stale_observed_routing','incorrect_observed_routing','partial_rollout',
                 'rollback_propagation_delay','partial_rollback','rollback_observation_mismatch'}
OBSERVATION_TYPES = {'observation_delay','observation_drop','stale_evidence','misattribution',
                     'misattribution_replay','historical_evidence_replay'}


@dataclass(frozen=True)
class RoutingFault:
    id: str
    type: str
    start_state: str
    duration_requests: int
    target: str = 'candidate'
    candidate_weight: float = 0.2
    reported_state: str | None = None


@dataclass(frozen=True)
class ObservationFault:
    id: str
    type: str
    source: str
    target: str
    start_window: int
    duration_windows: int
    delay_logical_units: int = 0
    source_window: int = 0
    drop_every_nth_record: int = 1
    reported_revision: str | None = None
    reported_epoch: int | None = None
    historical_index_path: str | None = None
    historical_index_sha256: str | None = None


@dataclass(frozen=True)
class BehaviorFault:
    id: str
    type: str
    target: str
    start_window: int
    duration_windows: int
    every_nth_item: int = 4


@dataclass(frozen=True)
class RequestFault:
    id: str
    type: str
    start_request: int
    end_request: int
    target: str = 'candidate'
    added_latency_ms: float = 0.0
    every_nth_candidate: int = 1


@dataclass(frozen=True)
class Phase4EConfig:
    enabled: bool = False
    evaluation_candidate_weight: float | None = None
    routing_faults: tuple[RoutingFault, ...] = ()
    observation_faults: tuple[ObservationFault, ...] = ()
    behavior_faults: tuple[BehaviorFault, ...] = ()
    request_faults: tuple[RequestFault, ...] = ()


def _record(cls: Any, data: Any) -> Any:
    if not isinstance(data,dict) or set(data)-{f.name for f in fields(cls)}:
        raise ValueError('unknown fault fields or non-object fault')
    try:
        obj=cls(**data)
    except TypeError as error:
        raise ValueError(f'missing fault fields: {error}') from error
    for key,value in asdict(obj).items():
        if key in {'reported_state','reported_revision','reported_epoch','historical_index_path',
                   'historical_index_sha256'} and value is None:
            continue
        if key in {'duration_requests','start_window','duration_windows','delay_logical_units','source_window',
                   'drop_every_nth_record','every_nth_item','every_nth_candidate',
                   'start_request','end_request','reported_epoch'}:
            if type(value) is not int or value<0:
                raise ValueError(f'{key} must be a nonnegative integer')
        elif key in {'candidate_weight','added_latency_ms'}:
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:
                raise ValueError(f'{key} must be finite and nonnegative')
        elif not isinstance(value,str) or not value.strip():
            raise ValueError(f'{key} must be a nonempty string')
    return obj


def parse_phase4e(data: Any) -> Phase4EConfig:
    if not isinstance(data,dict) or set(data)-{f.name for f in fields(Phase4EConfig)}:
        raise ValueError('invalid phase4e section')
    enabled=data.get('enabled',False)
    weight=data.get('evaluation_candidate_weight')
    if type(enabled) is not bool or (weight is not None and
            (type(weight) not in (int,float) or not math.isfinite(weight) or not 0<=weight<=1)):
        raise ValueError('invalid phase4e enable flag or evaluation weight')
    groups={}
    for name,cls in [('routing_faults',RoutingFault),('observation_faults',ObservationFault),
                     ('behavior_faults',BehaviorFault),('request_faults',RequestFault)]:
        values=data.get(name,[])
        if not isinstance(values,(list,tuple)):
            raise ValueError(f'{name} must be a list')
        groups[name]=tuple(sorted((_record(cls,v) for v in values),key=lambda f:f.id))
    config=Phase4EConfig(enabled,weight,**groups)
    all_faults=[f for group in groups.values() for f in group]
    ids=[f.id for f in all_faults]
    if len(ids)!=len(set(ids)) or 'legacy-request-fault' in ids:
        raise ValueError('fault IDs must be globally unique and cannot use legacy-request-fault')
    if not enabled and (all_faults or weight is not None):
        raise ValueError('phase4e must be enabled when controls/faults are configured')
    return config


def validate_phase4e(config: Any) -> None:
    from ..lifecycle import RolloutState
    p=config.phase4e
    for f in p.routing_faults:
        if (f.type not in ROUTING_TYPES or f.target!='candidate' or f.duration_requests<1
                or f.start_state not in {s.value for s in RolloutState if s.value not in {'COMPLETED','FAILED'}}
                or f.candidate_weight>1 or (f.reported_state is not None and f.reported_state not in {s.value for s in RolloutState})):
            raise ValueError('invalid routing fault')
        if f.type.startswith('rollback_') or f.type=='partial_rollback':
            if f.start_state!='ROLLING_BACK':
                raise ValueError('recovery faults must start at ROLLING_BACK')
    policy=config.decision_policy
    requested_holds=config.lifecycle.max_holds+1 if policy.type=='always_hold' else (
        policy.holds_before_decision if policy.type=='hold_then_promote' else 0)
    windows=3+2*min(requested_holds,config.lifecycle.max_holds)+(requested_holds<=config.lifecycle.max_holds)
    if config.policy.type != "dummy":
        windows=4+2*config.lifecycle.max_holds
    for f in p.observation_faults:
        if (f.type not in OBSERVATION_TYPES or f.source not in {'system','behavior'}
                or f.target not in {'baseline','candidate'} or f.duration_windows<1
                or f.start_window>=windows or f.drop_every_nth_record<1 or (f.source=='behavior' and not config.behavior_evidence.enabled)
                or (f.type=='observation_delay' and f.delay_logical_units<1)
                or (f.type in {'stale_evidence','misattribution_replay'} and f.source_window>=f.start_window)
                or (f.type=='misattribution_replay' and f.reported_revision is None and f.reported_epoch is None)
                or (f.type=='historical_evidence_replay' and (f.source!='behavior' or not f.historical_index_path
                    or not f.historical_index_sha256 or len(f.historical_index_sha256)!=64))
                or (f.type!='historical_evidence_replay' and (f.historical_index_path is not None
                    or f.historical_index_sha256 is not None))
                or (f.reported_epoch is not None and f.reported_epoch<1)):
            raise ValueError('invalid observation fault')
    for f in p.behavior_faults:
        if (not config.behavior_evidence.enabled or f.type!=config.behavior_evidence.task_type+'_regression'
                or f.target not in {'baseline','candidate'} or f.start_window>=windows or f.duration_windows<1 or f.every_nth_item<1):
            raise ValueError('invalid window-scoped behavior fault')
    for f in p.request_faults:
        if (f.type not in {'latency','candidate_request_failure'} or f.target!='candidate'
                or not 0<=f.start_request<=f.end_request<config.experiment.request_count
                or f.every_nth_candidate<1
                or (f.type=='latency' and f.every_nth_candidate!=1)
                or (f.type=='candidate_request_failure' and f.added_latency_ms!=0)):
            raise ValueError('invalid additional request fault')
    if not math.isfinite(config.candidate.fixed_latency_ms+config.fault.added_latency_ms+
                         sum(f.added_latency_ms for f in p.request_faults)):
        raise ValueError('compound latency must remain finite')
