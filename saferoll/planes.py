"""Three-plane rollout/routing model; only effective routing serves requests."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any
from .config import Config
from .lifecycle import RolloutSnapshot


@dataclass(frozen=True)
class ThreePlaneSnapshot:
    desired_state: str
    observed_state: str
    effective_state: str
    desired_candidate_weight: float
    observed_candidate_weight: float
    effective_candidate_weight: float


class ServingPlanes:
    def __init__(self, config: Config) -> None:
        self.config=config
        self.snapshot=ThreePlaneSnapshot('STABLE','STABLE','STABLE',0.0,0.0,0.0)
        self.records: list[dict[str,Any]]=[]
        self.fault_events: list[dict[str,Any]]=[]
        self._started: dict[str,tuple[int,ThreePlaneSnapshot]]={}
        self._cleared: set[str]=set()

    def capture(self, command: RolloutSnapshot, request_index: int, logical_time: int, point: str) -> int:
        p=self.config.phase4e
        state=command.desired_state.value
        weight=command.desired_candidate_weight
        if p.enabled and p.evaluation_candidate_weight is not None and state in {'EVALUATING','HOLDING'}:
            weight=p.evaluation_candidate_weight
        previous=self.snapshot
        for f in p.routing_faults:
            if f.id not in self._started and state==f.start_state:
                if f.type == 'partial_rollout' and f.candidate_weight > weight:
                    raise ValueError('partial rollout cannot exceed desired candidate exposure')
                if f.type == 'partial_rollback' and f.candidate_weight > previous.effective_candidate_weight:
                    raise ValueError('partial rollback cannot exceed pre-rollback effective exposure')
                self._started[f.id]=(request_index,previous)
                self.fault_events.append({'event_type':'FAULT_ACTIVATED','fault_id':f.id,'fault_type':f.type,
                    'target':f.target,'domain':'routing','logical_time':logical_time,'request_index':request_index,'parameters':asdict(f)})
            if f.id in self._started and f.id not in self._cleared and request_index>=self._started[f.id][0]+f.duration_requests:
                self._cleared.add(f.id)
                self.fault_events.append({'event_type':'FAULT_CLEARED','fault_id':f.id,'fault_type':f.type,
                    'target':f.target,'domain':'routing','logical_time':logical_time,'request_index':request_index,'reason':'duration elapsed'})
        observed=effective=state
        ow=ew=weight
        active=[]
        for f in p.routing_faults:
            if f.id not in self._started or f.id in self._cleared:
                continue
            active.append(f)
            held=self._started[f.id][1]
            if f.type in {'routing_propagation_delay','rollback_propagation_delay'}:
                ew,effective=held.effective_candidate_weight,held.effective_state
            elif f.type in {'partial_rollout','partial_rollback'}:
                ew,effective=f.candidate_weight,f.reported_state or state
            elif f.type in {'stale_observed_routing','rollback_observation_mismatch'}:
                ow,observed=held.observed_candidate_weight,held.observed_state
            elif f.type=='incorrect_observed_routing':
                ow,observed=f.candidate_weight,f.reported_state or state
        self.snapshot=ThreePlaneSnapshot(state,observed,effective,weight,ow,ew)
        record={'routing_state_index':len(self.records),'logical_time':logical_time,
                'rollout_id':command.rollout_id,'epoch':command.epoch,
                'baseline_revision':command.baseline_revision,'candidate_revision':command.candidate_revision,
                'transition_index':command.transition_index,'request_index':request_index,'capture_point':point,
                **asdict(self.snapshot),'fault_active':bool(active),'fault_ids':[f.id for f in active],
                'fault_types':[f.type for f in active]}
        for plane in ('desired','observed','effective'):
            record[plane+'_baseline_weight']=1-record[plane+'_candidate_weight']
        record.update(desired_observed_match=state==observed and weight==ow,
                      desired_effective_match=state==effective and weight==ew,
                      observed_effective_match=observed==effective and ow==ew)
        self.records.append(record)
        return len(self.records)-1

    def finish_faults(self, logical_time: int, request_index: int) -> None:
        """End the experiment's fault lifecycle without asserting unobserved containment."""
        for f in self.config.phase4e.routing_faults:
            if f.id in self._started and f.id not in self._cleared:
                self._cleared.add(f.id)
                self.fault_events.append({'event_type':'FAULT_CLEARED','fault_id':f.id,'fault_type':f.type,
                    'target':f.target,'domain':'routing','logical_time':logical_time,'request_index':request_index,'reason':'run ended'})
