"""Independent recomputation of operational evidence from persisted requests."""
from __future__ import annotations
from dataclasses import fields
import math
from typing import Any
from .config import Config
from .evidence import EVIDENCE_SCHEMA_VERSION, SystemEvidence, window_id

# Deliberately independent of the collector's aggregation and percentile functions.
PHASES = {"VALIDATING": ("VALIDATION", "validation_requests"), "CANARY": ("CANARY", "canary_requests"),
          "EVALUATING": ("EVALUATION", "evaluation_requests"), "HOLDING": ("HOLD", "hold_requests"),
          "PROMOTING": ("PROMOTION", "completion_requests"), "RECOVERING": ("RECOVERY", "completion_requests")}


def validate_system_evidence(config: Config, records: list[Any], rows: list[dict[str, str]],
                             events: list[Any]) -> list[str]:
    """Require exact sample attribution and independently check every derived field."""
    issues: list[str] = []
    try:
        windows = []
        active = None
        completion_times = {}
        for event in events:
            kind = event['event_type']
            if kind in {'STATE_TRANSITION', 'DECISION_MADE'} and active is not None:
                active['end'] = event['logical_time']
                windows.append(active)
                active = None
            if kind == 'STATE_TRANSITION':
                transition = event['details']
                state = transition['to_observed_state']
                if state in PHASES:
                    active = {'state': state, 'transition': transition['transition_index'],
                              'start': event['logical_time']}
            elif kind == 'REQUEST_COMPLETED':
                if active is None or event['request_id'] in completion_times:
                    raise ValueError('duplicate completion or request outside a lifecycle window')
                completion_times[event['request_id']] = event['logical_time']
        if active is not None:
            raise ValueError('unclosed evidence lifecycle interval')
        if len(records) != 2 * len(windows):
            raise ValueError('expected exactly baseline and candidate evidence for each window')
        required = {f.name for f in fields(SystemEvidence)}
        raw_ids = [r['request_id'] for r in rows]
        if len(raw_ids) != len(set(raw_ids)) or set(raw_ids) != set(completion_times):
            raise ValueError('raw request/completion identities do not reconcile')
        seen_samples = set()
        seen_pairs = set()
        for sequence, w in enumerate(windows):
            state, ti = w['state'], w['transition']
            samples = [r for r in rows if int(r['transition_index']) == ti]
            expected_count = getattr(config.lifecycle, PHASES[state][1])
            if len(samples) != expected_count:
                raise ValueError('lifecycle window request-count boundary mismatch')
            for row in samples:
                if (row['rollout_id'] != config.rollout.rollout_id or int(row['epoch']) != config.rollout.epoch
                        or row['observed_rollout_state'] != state
                        or not w['start'] < completion_times[row['request_id']] < w['end']):
                    raise ValueError('cross-epoch/lifecycle/time-contaminated raw sample')
            for offset, role in enumerate(('baseline', 'candidate')):
                record = records[2 * sequence + offset]
                if not isinstance(record, dict) or set(record) != required:
                    raise ValueError('system evidence schema mismatch')
                revision = getattr(config, role).revision
                selected = [r for r in samples if r['backend_role'] == role]
                if any(r['served_revision'] != revision for r in selected):
                    raise ValueError('sample backend role disagrees with served revision')
                ids = [r['request_id'] for r in selected]
                pair = (record['window_id'], record['revision'])
                if pair in seen_pairs:
                    raise ValueError('duplicate window/revision evidence')
                seen_pairs.add(pair)
                supplied = record['sample_request_ids']
                if not isinstance(supplied, list) or any(not isinstance(i, str) for i in supplied):
                    raise ValueError('sample_request_ids must be a list of strings')
                if supplied != ids or any(i not in completion_times or i in seen_samples for i in supplied):
                    raise ValueError('unknown, duplicated, mixed or wrongly attributed evidence sample IDs')
                seen_samples.update(supplied)
                latest = max((completion_times[i] for i in ids), default=None)
                # Current runs evaluate on closure. Later delays can be explicitly modeled.
                age = w['end'] - latest if latest is not None else None
                count = len(selected)
                if any(r['success'] not in {'True', 'False'} for r in selected):
                    raise ValueError('request success must be an explicit boolean')
                successes = sum(r['success'] == 'True' for r in selected)
                latencies = sorted(float(r['latency_ms']) for r in selected)
                if any(not math.isfinite(v) or v < 0 for v in latencies):
                    raise ValueError('latency sample must be finite and nonnegative')
                expected = {
                    'schema_version': EVIDENCE_SCHEMA_VERSION,
                    'window_id': window_id(config.identity, ti, sequence),
                    'rollout_id': config.rollout.rollout_id, 'epoch': config.rollout.epoch,
                    'revision': revision, 'backend_role': role, 'lifecycle_state': state,
                    'transition_index': ti, 'window_sequence': sequence, 'window_type': PHASES[state][0],
                    'window_start_logical_time': w['start'], 'window_end_logical_time': w['end'],
                    'closed_at_logical_time': w['end'], 'evaluated_at_logical_time': w['end'],
                    'planned_window_request_count': expected_count, 'sample_count': count,
                    'minimum_required_samples': config.evidence.minimum_samples_per_revision,
                    'sufficient': count >= config.evidence.minimum_samples_per_revision,
                    'latest_sample_logical_time': latest, 'age_at_evaluation': age,
                    'freshness_status': 'NO_SAMPLES' if age is None else (
                        'FRESH' if age <= config.evidence.max_age_logical_units else 'STALE'),
                    'request_count': count, 'success_count': successes, 'failure_count': count - successes,
                    'success_rate': successes / count if count else None,
                    'error_rate': 1 - successes / count if count else None,
                    'latency_mean_ms': sum(v / count for v in latencies) if count else None,
                    'latency_min_ms': latencies[0] if count else None,
                    'latency_max_ms': latencies[-1] if count else None,
                }
                for p in (50, 95):
                    result = None
                    if latencies:
                        rank = (count - 1) * p / 100
                        lower = int(rank)
                        fraction = rank - lower
                        upper = min(lower + 1, count - 1)
                        result = (1 - fraction) * latencies[lower] + fraction * latencies[upper]
                    expected[f'latency_p{p}_ms'] = result
                numeric_metrics = {'success_rate', 'error_rate', 'latency_mean_ms', 'latency_min_ms',
                                   'latency_max_ms', 'latency_p50_ms', 'latency_p95_ms'}
                for key, value in expected.items():
                    actual = record[key]
                    if key in numeric_metrics and value is not None:
                        match = (type(actual) in (int, float) and math.isfinite(actual)
                                 and math.isclose(actual, value, rel_tol=1e-12, abs_tol=1e-12))
                    else:
                        match = type(actual) is type(value) and actual == value
                    if not match:
                        issues.append(f'evidence window {sequence}/{role}: mismatch: {key}')
        if seen_samples != set(raw_ids):
            raise ValueError('requests missing from evidence or included in overlapping windows')
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as error:
        issues.append(f'invalid system evidence: {error}')
    return issues
