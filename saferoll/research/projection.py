"""Trusted adapter: whitelist visible payload facts and controller-owned query context."""
from __future__ import annotations
from dataclasses import asdict, replace
from typing import Any
from ..ids import RolloutIdentity
from .inputs import (PolicyInput,ReadinessInput,OpsInput,BehaviorInput,AIInput,SafeRollInput,
                     SystemEvidence,BehaviorEvidence,snapshot_id)


def project_input(identity: RolloutIdentity, policy_name: str, logical_time: int, window_id: str,
                  transition_index: int, observations: list[dict[str,Any]], desired_state: str,
                  desired_candidate_weight: float, system_max_age: int, behavior_max_age: int) -> PolicyInput:
    """No hidden-reference/fault/status/effective-state fields enter a policy capability."""
    delivered=[o for o in observations if o['logical_time']<=logical_time]
    def evidence(channel: str,role: str) -> Any:
        revision=identity.baseline_revision if role=='baseline' else identity.candidate_revision
        matches=[o for o in delivered if o['observation_type']==channel
                 and o['expected_ground_truth_reference']['window_id']==window_id
                 and o['expected_ground_truth_reference']['revision']==revision]
        o=matches[-1] if matches else None
        p=o['observed_value'] if o is not None else None
        age=logical_time-p['latest_sample_logical_time'] if p is not None and p['latest_sample_logical_time'] is not None else None
        scope_ok=p is not None and p['window_id']==window_id and p['transition_index']==transition_index and p['lifecycle_state']=='EVALUATING'
        values=dict(observation_id=o['observation_id'] if o else None,available=p is not None,scope_role=role,
            rollout_id=p['rollout_id'] if p else None,epoch=p['epoch'] if p else None,revision=p['revision'] if p else None,
            backend_role=p['backend_role'] if p else None,window_id=p['window_id'] if p else None,
            lifecycle_state=p['lifecycle_state'] if p else None,transition_index=p['transition_index'] if p else None,
            sample_count=p['sample_count'] if p else 0,sufficient=p['sufficient'] if p else False,
            fresh=bool(scope_ok and age is not None and age<=(system_max_age if channel=='system' else behavior_max_age)),age_logical_units=age)
        if channel=='system':
            return SystemEvidence(**values,error_rate=p['error_rate'] if p else None,success_rate=p['success_rate'] if p else None,
                                  latency_p95_ms=p['latency_p95_ms'] if p else None)
        return BehaviorEvidence(**values,workload_id=p['workload_id'] if p else None,task_type=p['task_type'] if p else None,
            sample_item_ids=tuple(p['sample_item_ids']) if p else (),accuracy=p['accuracy'] if p else None,
            exact_match_rate=p['exact_match_rate'] if p else None,structure_valid_rate=p['structure_valid_rate'] if p else None)
    common=dict(input_snapshot_id='',policy_name=policy_name,rollout_id=identity.rollout_id,epoch=identity.epoch,
        baseline_revision=identity.baseline_revision,candidate_revision=identity.candidate_revision,lifecycle_state=desired_state,
        transition_index=transition_index,expected_window_id=window_id,logical_time=logical_time)
    if policy_name=='readiness_gate':
        c=evidence('system','candidate')
        result=ReadinessInput(**common,system_observation_id=c.observation_id,candidate_available=c.available,
                             candidate_sample_count=c.sample_count,candidate_success_rate=c.success_rate)
    elif policy_name=='ops_gate':
        result=OpsInput(**common,system=tuple(evidence('system',r) for r in ('baseline','candidate')))
    elif policy_name=='behavior_gate':
        result=BehaviorInput(**common,behavior=tuple(evidence('behavior',r) for r in ('baseline','candidate')))
    elif policy_name=='ai_gate':
        result=AIInput(**common,system=tuple(evidence('system',r) for r in ('baseline','candidate')),
                       behavior=tuple(evidence('behavior',r) for r in ('baseline','candidate')))
    elif policy_name=='saferoll':
        routing=[o for o in delivered if o['observation_type']=='routing']
        o=routing[-1] if routing else None
        p=o['observed_value'] if o else None
        result=SafeRollInput(**common,system=tuple(evidence('system',r) for r in ('baseline','candidate')),
            behavior=tuple(evidence('behavior',r) for r in ('baseline','candidate')),
            routing_observation_id=o['observation_id'] if o else None,desired_state=desired_state,
            observed_state=p['observed_state'] if p else None,desired_candidate_weight=desired_candidate_weight,
            observed_candidate_weight=p['observed_candidate_weight'] if p else None)
    else:
        raise ValueError('unsupported research policy')
    return replace(result,input_snapshot_id=snapshot_id(asdict(result)))
