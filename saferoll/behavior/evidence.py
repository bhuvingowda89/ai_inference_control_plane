"""Per-window behavior facts and exact paired comparisons, independent of policy."""
from __future__ import annotations
from typing import Any
from ..config import Config
from .evaluator import evaluate_output
from .workload import BEHAVIOR_SCHEMA_VERSION


def aggregate_metrics(task_type: str, evaluated: list[dict[str, bool | None]]) -> dict[str, Any]:
    n = len(evaluated)
    classification = task_type == 'classification'
    correct = sum(r['correct'] is True for r in evaluated) if classification else None
    valid = sum(r['structure_valid'] is True for r in evaluated) if not classification else None
    exact = sum(r['exact_match'] is True for r in evaluated) if not classification else None
    return {'correct_count': correct, 'incorrect_count': n - correct if classification else None,
            'accuracy': correct / n if classification and n else None,
            'structure_valid_count': valid, 'structure_invalid_count': n - valid if not classification else None,
            'structure_valid_rate': valid / n if not classification and n else None,
            'exact_match_count': exact, 'exact_match_rate': exact / n if not classification and n else None}


def collect_behavior_evidence(config: Config, workload: dict[str, Any], probes: list[dict[str, Any]],
                              system_windows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not config.behavior_evidence.enabled:
        return [], []
    items = {i['item_id']: i for i in workload['items']}
    evidence, comparisons = [], []
    for w in system_windows[::2]:
        by_role = {}
        for role in ('baseline', 'candidate'):
            samples = [p for p in probes if p['window_id'] == w['window_id'] and p['backend_role'] == role]
            by_role[role] = samples
            evaluated = [evaluate_output(items[p['item_id']], p['observed_output']) for p in samples]
            metrics = aggregate_metrics(config.behavior_evidence.task_type, evaluated)
            latest = max((p['logical_time'] for p in samples), default=None)
            age = w['closed_at_logical_time'] - latest if latest is not None else None
            evidence.append({'schema_version': BEHAVIOR_SCHEMA_VERSION, 'window_id': w['window_id'],
                'rollout_id': config.rollout.rollout_id, 'epoch': config.rollout.epoch,
                'revision': getattr(config, role).revision, 'backend_role': role,
                'lifecycle_state': w['lifecycle_state'], 'transition_index': w['transition_index'],
                'window_type': w['window_type'], 'window_sequence': w['window_sequence'],
                'window_start_logical_time': w['window_start_logical_time'], 'window_end_logical_time': w['window_end_logical_time'],
                'workload_id': workload['workload_id'], 'task_type': workload['task_type'],
                'sample_request_ids': [p['request_id'] for p in samples], 'sample_item_ids': [p['item_id'] for p in samples],
                'sample_count': len(samples), 'minimum_required_samples': config.behavior_evidence.minimum_samples_per_revision,
                'sufficient': len(samples) >= config.behavior_evidence.minimum_samples_per_revision,
                'latest_sample_logical_time': latest, 'closed_at_logical_time': w['closed_at_logical_time'],
                'evaluated_at_logical_time': w['closed_at_logical_time'], 'age_at_evaluation': age,
                'freshness_status': 'NO_SAMPLES' if age is None else (
                    'FRESH' if age <= config.behavior_evidence.max_age_logical_units else 'STALE'), **metrics})
        baseline = {p['item_id']: p for p in by_role['baseline']}
        candidate = {p['item_id']: p for p in by_role['candidate']}
        paired_ids = [i['item_id'] for i in workload['items'] if i['item_id'] in baseline and i['item_id'] in candidate]
        agreement = sum(baseline[i]['observed_output'] == candidate[i]['observed_output'] for i in paired_ids)
        paired_metrics = {role: aggregate_metrics(workload['task_type'],
            [evaluate_output(items[i], source[i]['observed_output']) for i in paired_ids])
            for role, source in (('baseline', baseline), ('candidate', candidate))}
        record = {'schema_version': BEHAVIOR_SCHEMA_VERSION, 'window_id': w['window_id'],
            'rollout_id': config.rollout.rollout_id, 'epoch': config.rollout.epoch,
            'baseline_revision': config.baseline.revision, 'candidate_revision': config.candidate.revision,
            'lifecycle_state': w['lifecycle_state'], 'transition_index': w['transition_index'],
            'workload_id': workload['workload_id'], 'task_type': workload['task_type'],
            'paired_item_ids': paired_ids, 'baseline_request_ids': [baseline[i]['request_id'] for i in paired_ids],
            'candidate_request_ids': [candidate[i]['request_id'] for i in paired_ids],
            'paired_sample_count': len(paired_ids), 'minimum_required_paired_samples': config.behavior_evidence.minimum_paired_samples,
            'paired_sufficient': len(paired_ids) >= config.behavior_evidence.minimum_paired_samples,
            'agreement_count': agreement, 'disagreement_count': len(paired_ids) - agreement,
            'disagreement_rate': (len(paired_ids) - agreement) / len(paired_ids) if paired_ids else None}
        for name in ('accuracy', 'exact_match_rate'):
            a, b = paired_metrics['baseline'][name], paired_metrics['candidate'][name]
            record.update({f'baseline_{name}': a, f'candidate_{name}': b,
                           f'candidate_minus_baseline_{name}': b - a if a is not None and b is not None else None})
        comparisons.append(record)
    return evidence, comparisons
