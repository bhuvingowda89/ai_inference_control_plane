"""Three-plane, observation-reference and containment integrity checks."""
from __future__ import annotations
import json
import math
import random
from typing import Any
from .config import Config
from .lifecycle import RolloutState
from .observations import build_observations, build_fault_events


def validate_phase4e_artifacts(config: Config, routing: list[Any], observations: list[Any], faults: list[Any],
                              rows: list[dict[str,str]], events: list[Any], system: list[Any], behavior: list[Any],
                              summary: Any) -> list[str]:
    issues=[]
    def same(a: Any,b: Any) -> bool:
        return json.dumps(a,sort_keys=True,allow_nan=False)==json.dumps(b,sort_keys=True,allow_nan=False)
    try:
        mirror=[e['details'] for e in events if e['event_type']=='ROUTING_STATE']
        if not same(routing,mirror):
            raise ValueError('routing snapshots differ from event history')
        for index,r in enumerate(routing):
            if r['routing_state_index']!=index or r['rollout_id']!=config.rollout.rollout_id or r['epoch']!=config.rollout.epoch:
                raise ValueError('routing snapshot identity/index mismatch')
            for plane in ('desired','observed','effective'):
                RolloutState(r[plane+'_state'])
                a,b=r[plane+'_baseline_weight'],r[plane+'_candidate_weight']
                if (type(a) not in (int,float) or type(b) not in (int,float) or not all(math.isfinite(v) and 0<=v<=1 for v in (a,b))
                        or not math.isclose(a+b,1,rel_tol=0,abs_tol=1e-9)):
                    raise ValueError('invalid routing plane weights')
            for a,b in [('desired','observed'),('desired','effective'),('observed','effective')]:
                value=r[a+'_state']==r[b+'_state'] and r[a+'_candidate_weight']==r[b+'_candidate_weight']
                if not same(r[a+'_'+b+'_match'],value):
                    raise ValueError('routing consistency flag mismatch')
        rng=random.Random(config.experiment.seed)
        for row in rows:
            index=int(row['routing_state_index'])
            if not 0<=index<len(routing):
                raise ValueError('unknown request routing snapshot')
            r=routing[index]
            if r['capture_point']!='request' or r['request_index']!=int(row['request_index']) or r['transition_index']!=int(row['transition_index']):
                raise ValueError('request references wrong routing instant')
            role='candidate' if rng.random()<r['effective_candidate_weight'] else 'baseline'
            if row['backend_role']!=role or row['served_revision']!=getattr(config,role).revision:
                raise ValueError('request assignment disagrees with effective seeded routing')
        artifacts={'routing_states.jsonl':routing,'system_evidence.jsonl':system,'behavior_evidence.jsonl':behavior}
        ids=set()
        configured={f.id for group in (config.phase4e.routing_faults,config.phase4e.observation_faults,
                    config.phase4e.behavior_faults,config.phase4e.request_faults) for f in group}
        for index,o in enumerate(observations):
            if (o['observation_index']!=index or o['observation_id'] in ids
                    or o['rollout_id']!=config.rollout.rollout_id or o['epoch']!=config.rollout.epoch):
                raise ValueError('observation identity/index mismatch')
            ids.add(o['observation_id'])
            for field in ('ground_truth_reference','expected_ground_truth_reference'):
                ref=o[field]
                if ref['artifact'] not in artifacts or type(ref['record_index']) is not int or not 0<=ref['record_index']<len(artifacts[ref['artifact']]):
                    raise ValueError('unknown ground-truth observation reference')
                source=artifacts[ref['artifact']][ref['record_index']]
                if (ref['rollout_id']!=config.rollout.rollout_id or ref['epoch']!=config.rollout.epoch
                        or source['rollout_id']!=config.rollout.rollout_id or source['epoch']!=config.rollout.epoch
                        or ref['window_id']!=source.get('window_id') or ref['revision']!=source.get('revision')):
                    raise ValueError('cross-epoch/revision observation reference')
            if any(i not in configured for i in o['fault_ids']):
                raise ValueError('unknown observation fault ID')
            if o['observation_status'] in {'DELAYED','DROPPED','MISMATCHED'} and not o['fault_active']:
                raise ValueError('nonfresh observation lacks explicit configured fault')
            ref=o['ground_truth_reference']; source=artifacts[ref['artifact']][ref['record_index']]
            if o['observation_type']!='routing':
                latest=source['latest_sample_logical_time']
                expected_age=o['logical_time']-latest if latest is not None else None
                if not same(o['observed_age_logical_units'],expected_age):
                    raise ValueError('controller-visible evidence age mismatch')
                if o['source_window_id']!=source['window_id'] or o['source_revision']!=source['revision']:
                    raise ValueError('observation source history mismatch')
                if o['observation_status']=='DROPPED' and (o['observed_value'] is not None or o['available']):
                    raise ValueError('dropped observation contains an available payload')
        if not same(observations,build_observations(config,system,behavior,routing)):
            raise ValueError('controller observation fault transformations/order mismatch')
        if not same(faults,build_fault_events(config,routing,system,events)):
            raise ValueError('fault lifecycle activation/order mismatch')
        # Reconcile physical facts independently of the summary producer.
        requested=next((e['logical_time'] for e in events if e['event_type']=='STATE_TRANSITION' and e['details']['to_desired_state']=='ROLLING_BACK'),None)
        decision=next((e['logical_time'] for e in events if e['event_type']=='DECISION_MADE' and e['details']['decision']=='ROLLBACK'),None)
        completions={e['request_id']:e['logical_time'] for e in events if e['event_type']=='REQUEST_COMPLETED'}
        candidate_times=[completions[r['request_id']] for r in rows if r['backend_role']=='candidate']
        last=max(candidate_times,default=None)
        post=max((t for t in candidate_times if requested is not None and t>requested),default=None)
        zero=None
        if requested is not None:
            for r in reversed([r for r in routing if r['logical_time']>=requested]):
                if r['effective_candidate_weight']!=0:
                    break
                zero=r['logical_time']
        expected={'rollback_decision_time':decision,'rollback_requested_time':requested,'rollback_requested_logical_time':requested,
            'effective_zero_candidate_time':zero,'effective_candidate_zero_logical_time':zero,'last_candidate_served_time':last,
            'last_candidate_request_logical_time':last,'last_post_rollback_candidate_time':post,'effective_contained':zero is not None,
            'final_effective_candidate_weight':routing[-1]['effective_candidate_weight'],'final_effective_state':routing[-1]['effective_state']}
        for key,value in expected.items():
            if not same(summary.get(key),value):
                raise ValueError(f'containment raw fact mismatch: {key}')
    except (ValueError,TypeError,KeyError,IndexError,AttributeError) as error:
        issues.append(f'invalid phase4e artifacts: {error}')
    return issues
