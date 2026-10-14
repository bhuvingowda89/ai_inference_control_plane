"""Independent workload, probe, metric and pairing validation from persisted data."""
from __future__ import annotations
import json
from typing import Any
from ..config import Config
from .workload import BEHAVIOR_SCHEMA_VERSION, item_id, probe_id


def _metrics(task: str, samples: list[dict[str, Any]], items: dict[str, Any]) -> dict[str, Any]:
    """Independent of the runtime evaluator and aggregator."""
    correct, structures, exact = 0, 0, 0
    for sample in samples:
        item, output = items[sample['item_id']], sample['observed_output']
        if task == 'classification':
            correct += int(output == item['expected_label'])
        else:
            try:
                # Preserve key pairs to reject duplicate keys independently.
                parsed = json.loads(output, object_pairs_hook=lambda pairs: pairs)
                valid = (isinstance(parsed, list) and len(parsed) == 2
                         and all(isinstance(p, tuple) and len(p) == 2 for p in parsed))
                value = dict(parsed) if valid else {}
                valid = (valid and len(value) == 2 and set(value) == {'number', 'parity'}
                         and type(value['number']) is int and type(value['parity']) is str)
            except (ValueError, TypeError, KeyError):
                valid, value = False, {}
            structures += int(valid)
            exact += int(valid and value == item['expected_values'])
    n = len(samples)
    classification = task == 'classification'
    return {'correct_count': correct if classification else None, 'incorrect_count': n-correct if classification else None,
            'accuracy': correct/n if n and classification else None,
            'structure_valid_count': structures if not classification else None,
            'structure_invalid_count': n-structures if not classification else None,
            'structure_valid_rate': structures/n if n and not classification else None,
            'exact_match_count': exact if not classification else None,
            'exact_match_rate': exact/n if n and not classification else None}


def _same(actual: Any, expected: Any) -> bool:
    return json.dumps(actual,sort_keys=True,allow_nan=False,ensure_ascii=False)==json.dumps(
        expected,sort_keys=True,allow_nan=False,ensure_ascii=False)


def validate_behavior(config: Config, workload: Any, probes: list[Any], evidence: list[Any],
                      comparisons: list[Any], windows: list[Any], events: list[Any], summary: Any) -> list[str]:
    issues = []
    try:
        b = config.behavior_evidence
        expected_items = []
        for index in range(b.item_count if b.enabled else 0):
            number = config.experiment.seed + index
            label = 'odd' if number % 2 else 'even'
            values = {'number': number, 'parity': label}
            structured = b.task_type == 'structured_output'
            expected_items.append({'workload_id': b.workload_id, 'item_id': item_id(b.workload_id,config.experiment.seed,b.task_type,index),
                'item_index': index, 'task_type': b.task_type, 'input': {'number': number},
                'expected_output': values if structured else label, 'expected_label': None if structured else label,
                'expected_structure': {'number':'integer','parity':'string'} if structured else None,
                'expected_values': values if structured else None})
        expected_workload = {'schema_version':BEHAVIOR_SCHEMA_VERSION,'rollout_id':config.rollout.rollout_id,
            'epoch':config.rollout.epoch,'workload_id':b.workload_id,'seed':config.experiment.seed,
            'task_type':b.task_type,'items':expected_items}
        if not _same(workload,expected_workload):
            raise ValueError('workload identity/item/ground-truth mismatch')
        event_probes = [e['details'] for e in events if e['event_type']=='BEHAVIOR_PROBE_COMPLETED']
        if not _same(probes,event_probes):
            raise ValueError('persisted behavior probes differ from event request/response records')
        items = {i['item_id']:i for i in expected_items}
        source_windows = windows[::2] if b.enabled else []
        if len(evidence)!=2*len(source_windows) or len(comparisons)!=len(source_windows):
            raise ValueError('behavior window evidence/comparison count mismatch')
        expected_order, expected_evidence, expected_comparisons = [], [], []
        for w in source_windows:
            sources = {}
            for role, limit in (('baseline',b.baseline_items_per_window),('candidate',b.candidate_items_per_window)):
                samples = [p for p in probes if p['window_id']==w['window_id'] and p['backend_role']==role]
                sources[role] = {p['item_id']:p for p in samples}
                if [p['item_id'] for p in samples]!=[i['item_id'] for i in expected_items[:limit]]:
                    raise ValueError('unknown/duplicate/unexpected behavior item membership')
                for p in samples:
                    item = items[p['item_id']]
                    expected = {'schema_version':BEHAVIOR_SCHEMA_VERSION,'rollout_id':config.rollout.rollout_id,
                        'epoch':config.rollout.epoch,'request_id':probe_id(config,w['window_id'],role,item['item_id']),
                        'request_kind':'behavior_probe','revision':getattr(config,role).revision,
                        'intended_revision':getattr(config,role).revision,'served_revision':getattr(config,role).revision,
                        'backend_role':role,'lifecycle_state':w['lifecycle_state'],'transition_index':w['transition_index'],
                        'window_id':w['window_id'],'window_sequence':w['window_sequence'],**item,
                        'observed_output':p['observed_output'],
                        'predicted_label':p['observed_output'] if b.task_type=='classification' else None,
                        'success':True,'latency_ms':getattr(config,role).fixed_latency_ms,'logical_time':p['logical_time']}
                    if not isinstance(p['observed_output'],str) or not _same(p,expected):
                        raise ValueError('behavior probe identity/task/expected/observed result mismatch')
                    if type(p['logical_time']) is not int or not w['window_start_logical_time']<p['logical_time']<w['closed_at_logical_time']:
                        raise ValueError('behavior probe outside lifecycle window')
                latest = max((p['logical_time'] for p in samples),default=None)
                age = w['closed_at_logical_time']-latest if latest is not None else None
                record = {'schema_version':BEHAVIOR_SCHEMA_VERSION,'window_id':w['window_id'],
                    'rollout_id':config.rollout.rollout_id,'epoch':config.rollout.epoch,
                    'revision':getattr(config,role).revision,'backend_role':role,'lifecycle_state':w['lifecycle_state'],
                    'transition_index':w['transition_index'],'window_type':w['window_type'],'window_sequence':w['window_sequence'],
                    'window_start_logical_time':w['window_start_logical_time'],'window_end_logical_time':w['window_end_logical_time'],
                    'workload_id':b.workload_id,'task_type':b.task_type,'sample_request_ids':[p['request_id'] for p in samples],
                    'sample_item_ids':[p['item_id'] for p in samples],'sample_count':len(samples),
                    'minimum_required_samples':b.minimum_samples_per_revision,'sufficient':len(samples)>=b.minimum_samples_per_revision,
                    'latest_sample_logical_time':latest,'closed_at_logical_time':w['closed_at_logical_time'],
                    'evaluated_at_logical_time':w['closed_at_logical_time'],'age_at_evaluation':age,
                    'freshness_status':'NO_SAMPLES' if age is None else ('FRESH' if age<=b.max_age_logical_units else 'STALE'),
                    **_metrics(b.task_type,samples,items)}
                expected_evidence.append(record)
            for index,item in enumerate(expected_items):
                for role,limit in (('baseline',b.baseline_items_per_window),('candidate',b.candidate_items_per_window)):
                    if index<limit:
                        expected_order.append(sources[role][item['item_id']]['request_id'])
            paired = [i['item_id'] for i in expected_items if i['item_id'] in sources['baseline'] and i['item_id'] in sources['candidate']]
            bm,cm = [_metrics(b.task_type,[sources[role][i] for i in paired],items) for role in ('baseline','candidate')]
            disagreements = sum(sources['baseline'][i]['observed_output']!=sources['candidate'][i]['observed_output'] for i in paired)
            comparison = {'schema_version':BEHAVIOR_SCHEMA_VERSION,'window_id':w['window_id'],
                'rollout_id':config.rollout.rollout_id,'epoch':config.rollout.epoch,'baseline_revision':config.baseline.revision,
                'candidate_revision':config.candidate.revision,'lifecycle_state':w['lifecycle_state'],'transition_index':w['transition_index'],
                'workload_id':b.workload_id,'task_type':b.task_type,'paired_item_ids':paired,
                'baseline_request_ids':[sources['baseline'][i]['request_id'] for i in paired],
                'candidate_request_ids':[sources['candidate'][i]['request_id'] for i in paired],
                'paired_sample_count':len(paired),'minimum_required_paired_samples':b.minimum_paired_samples,
                'paired_sufficient':len(paired)>=b.minimum_paired_samples,'agreement_count':len(paired)-disagreements,
                'disagreement_count':disagreements,'disagreement_rate':disagreements/len(paired) if paired else None}
            for name in ('accuracy','exact_match_rate'):
                a,c = bm[name],cm[name]
                comparison.update({f'baseline_{name}':a,f'candidate_{name}':c,
                                   f'candidate_minus_baseline_{name}':c-a if a is not None and c is not None else None})
            expected_comparisons.append(comparison)
        if [p['request_id'] for p in probes]!=expected_order or len(expected_order)!=len(set(expected_order)):
            raise ValueError('unknown/duplicate/misordered/cross-window behavior request references')
        if not _same(evidence,expected_evidence):
            issues.append('behavior evidence attribution/metrics/sufficiency/freshness mismatch')
        if not _same(comparisons,expected_comparisons):
            issues.append('behavior paired membership/accuracy/agreement/disagreement mismatch')
        counts = {'behavior_windows':len(source_windows),'behavior_samples':len(probes),
                  'paired_behavior_samples':sum(r['paired_sample_count'] for r in expected_comparisons)}
        for key,value in counts.items():
            if not _same(summary.get(key),value):
                issues.append(f'behavior summary mismatch: {key}')
    except (ValueError,TypeError,KeyError,IndexError,AttributeError) as error:
        issues.append(f'invalid behavior artifacts: {error}')
    return issues
