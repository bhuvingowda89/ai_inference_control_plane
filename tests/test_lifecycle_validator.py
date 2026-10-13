import csv
from dataclasses import replace
import json
from pathlib import Path
import pytest
from saferoll.config import load_config
from saferoll.runner import run_experiment
from saferoll.validator import validate_run


@pytest.fixture
def lifecycle_run(tmp_path):
    return run_experiment(load_config(Path(__file__).parents[1] / 'configs/phase4b-hold-promote.yaml'), tmp_path)


def mutate_transition(path, modify, synchronize=False):
    file = path / 'rollout_transitions.jsonl'
    records = [json.loads(line) for line in file.read_text().splitlines()]
    modify(records)
    file.write_text(''.join(json.dumps(r) + '\n' for r in records))
    if synchronize:
        events_file = path / 'events.jsonl'
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        for event, record in zip((e for e in events if e['event_type'] == 'STATE_TRANSITION'), records):
            event['details'] = record
        events_file.write_text(''.join(json.dumps(e) + '\n' for e in events))


@pytest.mark.parametrize('corruption', ['missing_file', 'malformed', 'index', 'initial', 'forbidden',
    'desired_enum', 'observed_enum', 'decision', 'hold', 'epoch', 'revision', 'time', 'missing_field',
    'missing_final', 'routing', 'request_index', 'request_state', 'outcome', 'failed_success'])
def test_lifecycle_corruption_rejected(lifecycle_run, corruption):
    path = lifecycle_run
    if corruption == 'missing_file':
        (path / 'rollout_transitions.jsonl').unlink()
    elif corruption == 'malformed':
        (path / 'rollout_transitions.jsonl').write_text('{bad\n')
    elif corruption in {'request_index', 'request_state'}:
        file = path / 'requests.csv'
        with file.open(newline='') as stream:
            reader = csv.DictReader(stream)
            names, rows = reader.fieldnames, list(reader)
        rows[0]['transition_index' if corruption == 'request_index' else 'observed_rollout_state'] = (
            '999' if corruption == 'request_index' else 'COMPLETED')
        with file.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, names)
            writer.writeheader()
            writer.writerows(rows)
    elif corruption == 'outcome':
        file = path / 'summary.json'
        summary = json.loads(file.read_text())
        summary['final_outcome'] = 'ROLLED_BACK'
        file.write_text(json.dumps(summary))
    else:
        def modify(records):
            if corruption == 'missing_field':
                del records[2]['reason']
            elif corruption == 'missing_final':
                records.pop()
            else:
                index, key, value = {
                    'index': (2, 'transition_index', 99), 'initial': (0, 'to_desired_state', 'CANARY'),
                    'forbidden': (3, 'to_desired_state', 'COMPLETED'),
                    'desired_enum': (2, 'to_desired_state', 'UNKNOWN'),
                    'observed_enum': (2, 'to_observed_state', 'UNKNOWN'),
                    'decision': (3, 'decision', 'PROMOTE'), 'hold': (5, 'decision', 'ROLLBACK'),
                    'epoch': (2, 'epoch', 2), 'revision': (2, 'candidate_revision', 'other'),
                    'time': (2, 'logical_time', 0), 'routing': (3, 'to_observed_candidate_weight', 1),
                    'failed_success': (-1, 'to_desired_state', 'FAILED'),
                }[corruption]
                records[index][key] = value
        mutate_transition(path, modify, synchronize=True)
    result = validate_run(path)
    assert not result['valid'] and result['issues']
    assert json.loads((path / 'validation.json').read_text()) == result


def test_cross_epoch_lifecycle_rejected(config, tmp_path):
    one = run_experiment(config, tmp_path)
    two = run_experiment(replace(config, rollout=replace(config.rollout, epoch=2)), tmp_path)
    old = json.loads((one / 'rollout_transitions.jsonl').read_text().splitlines()[2])
    mutate_transition(two, lambda records: records.__setitem__(2, old), synchronize=True)
    result = validate_run(two)
    assert not result['valid']
    assert any('identity mismatch: epoch' in issue for issue in result['issues'])


def test_validator_rejects_forbidden_edge_independently(lifecycle_run):
    def corrupt(records):
        # An apparently converged VALIDATING -> COMPLETED transition.
        records[3]['to_desired_state'] = records[3]['to_observed_state'] = 'COMPLETED'
    mutate_transition(lifecycle_run, corrupt, synchronize=True)
    result = validate_run(lifecycle_run)
    assert any('forbidden lifecycle transition: VALIDATING -> COMPLETED' in issue for issue in result['issues'])


def test_failed_run_cannot_claim_promotion(config, tmp_path):
    c = replace(config, decision_policy=replace(config.decision_policy, type='always_hold'))
    # Legacy migration has zero-size evaluation/hold/completion batches.
    path = run_experiment(c, tmp_path)
    file = path / 'summary.json'
    summary = json.loads(file.read_text())
    assert summary['final_outcome'] == 'FAILED'
    summary['final_outcome'] = 'PROMOTED'
    file.write_text(json.dumps(summary))
    result = validate_run(path)
    assert not result['valid']
    assert any('lifecycle summary mismatch: final_outcome' in issue for issue in result['issues'])
