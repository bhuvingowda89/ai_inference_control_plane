"""Recompute decisions from closed visible capabilities; evaluate oracle only post-hoc."""
from __future__ import annotations
from dataclasses import asdict
import json
from typing import Any
from .inputs import input_from_dict,snapshot_id
from .projection import project_input
from .policies import build_policy,Reason
from .configuration import POLICY_VERSION
from .oracle import evaluate_decisions,decision_metrics


def validate_policy_artifacts(config: Any, inputs: list[Any], decisions: list[Any], evaluations: list[Any],
                              observations: list[Any], routing: list[Any], system: list[Any],
                              requests: list[Any], events: list[Any], summary: Any) -> list[str]:
    issues=[]
    def same(a: Any,b: Any) -> bool:
        return json.dumps(a,sort_keys=True,ensure_ascii=False,allow_nan=False)==json.dumps(b,sort_keys=True,ensure_ascii=False,allow_nan=False)
    try:
        live=[e for e in events if e['event_type']=='DECISION_MADE' and 'policy_input' in e['details']]
        if not same(inputs,[e['details']['policy_input'] for e in live]) or not same(decisions,[e['details']['policy_result'] for e in live]):
            raise ValueError('policy artifacts differ from decision event history')
        if len(inputs)!=len(decisions) or (config.policy.type!='dummy' and not decisions):
            raise ValueError('policy input/decision count mismatch')
        if config.policy.type=='dummy' and (inputs or decisions or evaluations):
            raise ValueError('dummy run contains research decisions')
        observation_ids={o['observation_id']:o for o in observations}
        ids=set()
        for payload,decision,event in zip(inputs,decisions,live):
            visible=input_from_dict(payload)
            if (visible.rollout_id!=config.rollout.rollout_id or visible.epoch!=config.rollout.epoch
                    or visible.policy_name!=config.policy.type or visible.logical_time!=event['logical_time']
                    or visible.input_snapshot_id!=snapshot_id(payload) or visible.input_snapshot_id in ids):
                raise ValueError('policy snapshot identity/hash mismatch')
            scope=[r for r in system if r['transition_index']==event['details']['transition_index']
                   and r['closed_at_logical_time']==event['logical_time']]
            if not scope or visible.expected_window_id!=scope[0]['window_id'] or visible.transition_index!=event['details']['transition_index']:
                raise ValueError('policy snapshot references a different lifecycle evidence window')
            ids.add(visible.input_snapshot_id)
            refs=[]
            for channel in ('system','behavior'):
                refs += [r['observation_id'] for r in payload.get(channel,[]) if r['observation_id'] is not None]
            refs += [payload[k] for k in ('routing_observation_id','system_observation_id') if payload.get(k) is not None]
            if any(ref not in observation_ids or observation_ids[ref]['logical_time']>visible.logical_time for ref in refs):
                raise ValueError('unknown or future observation referenced by policy')
            current=[r for r in routing if r['logical_time']<=visible.logical_time][-1]
            expected=project_input(config.identity,config.policy.type,visible.logical_time,visible.expected_window_id,
                event['details']['transition_index'],observations,current['desired_state'],current['desired_candidate_weight'],
                config.evidence.max_age_logical_units,config.behavior_evidence.max_age_logical_units)
            if not same(payload,asdict(expected)):
                raise ValueError('policy input is not the exact visible projection')
            if (decision['input_snapshot_id']!=visible.input_snapshot_id or decision['rollout_id']!=visible.rollout_id
                    or decision['epoch']!=visible.epoch or decision['policy_version']!=POLICY_VERSION):
                raise ValueError('decision snapshot/epoch/version reference mismatch')
            for reason in decision['reason_codes']:
                Reason(reason)
            result=asdict(build_policy(config.policy).decide(visible))
            if not same(decision,result):
                raise ValueError('policy decision/explanation recomputation mismatch')
        expected_evaluations=evaluate_decisions(config,inputs,decisions,system,routing,requests)
        if not same(evaluations,expected_evaluations):
            raise ValueError('oracle/correctness recomputation mismatch')
        if config.policy.type!='dummy':
            expected_metrics=decision_metrics(decisions,expected_evaluations)
            if not same(summary.get('decision_metrics'),expected_metrics):
                raise ValueError('post-hoc decision count summary mismatch')
            for key in ('unsafe_promotion_count','unnecessary_rollback_count','unnecessary_hold_count'):
                if summary.get(key)!=expected_metrics[key]:
                    raise ValueError('post-hoc correctness summary mismatch')
    except (ValueError,TypeError,KeyError,IndexError,AttributeError) as error:
        issues.append(f'invalid policy artifacts: {error}')
    return issues
