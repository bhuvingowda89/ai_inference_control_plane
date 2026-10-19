"""Post-hoc known-model oracle. Never imported by policy implementations or runtime."""
from __future__ import annotations
from dataclasses import replace
from typing import Any
import json
from ..config import Config
from ..backends.mock import MockBackend
from ..behavior.workload import build_workload
from ..behavior.evaluator import evaluate_output


def _rank(values: list[float],p: float) -> float:
    ordered=sorted(values); position=(len(ordered)-1)*p/100
    low=int(position); high=min(low+1,len(ordered)-1)
    return ordered[low]+(ordered[high]-ordered[low])*(position-low)


def _behavior_quality(config: Config,role: str,sequence: int) -> dict[str,float | None]:
    backend=getattr(config,role)
    for f in config.phase4e.behavior_faults:
        if f.target==role and f.start_window<=sequence<f.start_window+f.duration_windows:
            backend=replace(backend,behavior_profile=f.type,regression_every_nth_item=f.every_nth_item)
    items=build_workload(config)['items']
    if not items:
        return {'accuracy':None,'exact_match_rate':None,'structure_valid_rate':None}
    model=MockBackend(role,backend)
    correct=exact=valid=0
    for item in items:
        output=model.infer_behavior(item)
        if item['task_type']=='classification':
            correct+=output==item['expected_label']
        else:
            try:
                value=json.loads(output)
                structure=isinstance(value,dict) and set(value)=={'number','parity'} and type(value['number']) is int and type(value['parity']) is str
            except (ValueError,TypeError):
                structure=False;value=None
            valid+=structure; exact+=structure and value==item['expected_values']
    n=len(items)
    classification=config.behavior_evidence.task_type=='classification'
    return {'accuracy':correct/n if classification else None,'exact_match_rate':exact/n if not classification else None,
            'structure_valid_rate':valid/n if not classification else None}


def evaluate_decisions(config: Config, inputs: list[dict[str,Any]], decisions: list[dict[str,Any]],
                       system: list[dict[str,Any]], routing: list[dict[str,Any]], requests: list[dict[str,Any]],
                       probes: list[dict[str,Any]] | None = None) -> list[dict[str,Any]]:
    """Oracle uses both channels for every policy, without looking at policy explanations."""
    results=[]
    c=config.policy
    for visible,decision in zip(inputs,decisions):
        window=visible['expected_window_id']; time=decision['logical_time']
        s=[r for r in system if r['window_id']==window]
        sequence=s[0]['window_sequence']
        row_set=[r for r in requests if int(r['transition_index'])==visible['transition_index']]
        unsafe=[]
        if any(r['served_revision'] not in (config.baseline.revision,config.candidate.revision) for r in row_set):
            unsafe.append('INVALID_SERVING_REVISION')
        operational=[]
        bases=[]
        real=config.baseline.backend_type=='llama_cpp'
        missing_operational=False
        for role in ('baseline','candidate'):
            raw=[r for r in row_set if r['backend_role']==role]
            if raw:
                rate=sum(r['success'] in (False,'False') for r in raw)/len(raw)
                latencies=[float(r['latency_ms']) for r in raw]
                bases.append('ACTUAL_SERVING_RECORDS')
            else:
                if real:
                    missing_operational=True
                    operational.append({'error_rate':None,'p95_ms':None})
                    bases.append('NO_REAL_SERVING_SAMPLES')
                    continue
                # Known deterministic backend: explicit modeled counterfactual, never a fabricated request.
                latencies=[];failures=0
                for row in row_set or [{'request_index':-1}]:
                    latency=getattr(config,role).fixed_latency_ms;failed=False
                    if role=='candidate':
                        for fault in (config.fault,*config.phase4e.request_faults):
                            if fault.start_request<=int(row['request_index'])<=fault.end_request:
                                if fault.type=='latency': latency+=fault.added_latency_ms
                                else: failed=True
                    latencies.append(latency);failures+=failed
                rate=failures/len(latencies)
                bases.append('CONFIGURED_BACKEND_COUNTERFACTUAL')
            operational.append({'error_rate':rate,'p95_ms':_rank(latencies,95)})
        a,b=operational
        if not missing_operational and (b['error_rate']>c.system.max_error_rate+c.numeric_tolerance or b['p95_ms']>a['p95_ms']*(1+c.system.max_latency_regression_fraction)+c.numeric_tolerance):
            unsafe.append('SYSTEM_REGRESSION')
        if real:
            items={i['item_id']:i for i in build_workload(config)['items']}
            qualities=[]
            for role in ('baseline','candidate'):
                samples=[p for p in (probes or []) if p['window_id']==window and p['backend_role']==role]
                evaluated=[evaluate_output(items[p['item_id']],p['observed_output']) for p in samples]
                n=len(evaluated)
                classification=config.behavior_evidence.task_type=='classification'
                qualities.append({'accuracy':sum(x['correct'] is True for x in evaluated)/n if classification and n else None,
                    'exact_match_rate':sum(x['exact_match'] is True for x in evaluated)/n if not classification and n else None,
                    'structure_valid_rate':sum(x['structure_valid'] is True for x in evaluated)/n if not classification and n else None})
        else:
            qualities=[_behavior_quality(config,role,sequence) for role in ('baseline','candidate')]
        a,b=qualities
        unknown_behavior=(b['accuracy'] is None and b['exact_match_rate'] is None) or (a['accuracy'] is None and a['exact_match_rate'] is None)
        if not unknown_behavior and b['accuracy'] is not None:
            if b['accuracy']<a['accuracy']-c.behavior.max_accuracy_drop-c.numeric_tolerance:
                unsafe.append('BEHAVIOR_REGRESSION')
        elif not unknown_behavior and b['exact_match_rate'] is not None:
            if (b['exact_match_rate']<a['exact_match_rate']-c.behavior.max_exact_match_drop-c.numeric_tolerance
                    or b['structure_valid_rate']<c.behavior.min_structure_valid_rate-c.numeric_tolerance):
                unsafe.append('BEHAVIOR_REGRESSION')
        physical=[r for r in routing if r['logical_time']<=time][-1]
        uncertain=unknown_behavior or missing_operational or not physical['desired_effective_match']
        truth='UNSAFE' if unsafe else 'INDETERMINATE' if uncertain else 'SAFE'
        action=decision['decision']
        classes={'SAFE':{'PROMOTE':'CORRECT_PROMOTION','HOLD':'UNNECESSARY_HOLD','ROLLBACK':'UNNECESSARY_ROLLBACK'},
                 'UNSAFE':{'PROMOTE':'UNSAFE_PROMOTION','HOLD':'DELAYED_ROLLBACK','ROLLBACK':'CORRECT_ROLLBACK'},
                 'INDETERMINATE':{'PROMOTE':'UNSUPPORTED_PROMOTION','HOLD':'CORRECT_HOLD','ROLLBACK':'UNSUPPORTED_ROLLBACK'}}
        results.append({'decision_id':decision['decision_id'],'rollout_id':config.rollout.rollout_id,'epoch':config.rollout.epoch,
            'logical_time':time,'source_window_id':window,'ground_truth_state':truth,'decision':action,
            'correctness_class':classes[truth][action],'critical_error':truth=='UNSAFE' and action=='PROMOTE',
            'oracle_version':'1.0','truth_reasons':unsafe+(['UNREALIZED_DEPLOYMENT_OR_UNDEFINED_BEHAVIOR'] if uncertain and not unsafe else []),
            'oracle_basis':{'system':bases,'behavior':'ACTUAL_REAL_PROBES' if real else 'CONFIGURED_FULL_DETERMINISTIC_WORKLOAD',
                            'operational_metrics':operational,'behavior_metrics':qualities}})
    return results


def decision_metrics(decisions: list[dict[str,Any]], evaluations: list[dict[str,Any]]) -> dict[str,int]:
    return {'decision_count':len(decisions),'promote_count':sum(d['decision']=='PROMOTE' for d in decisions),
        'hold_count':sum(d['decision']=='HOLD' for d in decisions),'rollback_count':sum(d['decision']=='ROLLBACK' for d in decisions),
        'unsafe_promotion_count':sum(e['correctness_class']=='UNSAFE_PROMOTION' for e in evaluations),
        'unnecessary_rollback_count':sum(e['correctness_class']=='UNNECESSARY_ROLLBACK' for e in evaluations),
        'unnecessary_hold_count':sum(e['correctness_class']=='UNNECESSARY_HOLD' for e in evaluations)}
