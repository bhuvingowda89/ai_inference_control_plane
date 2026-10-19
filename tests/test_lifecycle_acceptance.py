import csv
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import pytest
from saferoll.config import config_from_dict, load_config
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT = Path(__file__).parents[1]


def read_rows(path):
    with (path / 'requests.csv').open(newline='') as stream:
        return list(csv.DictReader(stream))


def read_transitions(path):
    return [json.loads(line) for line in (path / 'rollout_transitions.jsonl').read_text().splitlines()]


@pytest.mark.parametrize('scenario,outcome,path_states,holds', [
    ('promote', 'PROMOTED', ['PROMOTING', 'COMPLETED'], 0),
    ('rollback', 'ROLLED_BACK', ['ROLLING_BACK', 'RECOVERING', 'COMPLETED'], 0),
    ('hold-promote', 'PROMOTED', ['HOLDING', 'EVALUATING', 'PROMOTING', 'COMPLETED'], 1),
])
def test_scenarios_and_determinism(tmp_path, scenario, outcome, path_states, holds):
    config = load_config(ROOT / f'configs/phase4b-{scenario}.yaml')
    one = run_experiment(config, tmp_path)
    two = run_experiment(config, tmp_path)
    assert validate_run(one)['valid']
    transitions = read_transitions(one)
    assert [t['to_desired_state'] for t in transitions] == [
        'STABLE', 'CANDIDATE_CREATED', 'VALIDATING', 'CANARY', 'EVALUATING', *path_states]
    summary = json.loads((one / 'summary.json').read_text())
    assert summary['final_outcome'] == outcome
    assert summary['final_lifecycle_state'] == 'COMPLETED'
    assert summary['hold_count'] == holds
    assert summary['decision_count'] == 1 + holds
    assert summary['transition_count'] == len(transitions)
    assert summary['completed_requests'] == config.experiment.request_count
    manifest = json.loads((one / 'manifest.json').read_text())
    assert manifest['schema_version'] == '7.0'
    assert manifest['state_machine_version'] == '1.0'
    assert manifest['initial_state'] == 'STABLE'
    assert manifest['decision_policy_type'] == config.decision_policy.type
    rows = read_rows(one)
    states = [r['observed_rollout_state'] for r in rows]
    assert states.count('VALIDATING') == 5
    assert states.count('CANARY') == 20
    assert states.count('EVALUATING') == 10 * (1 + holds)
    assert states.count('HOLDING') == 3 * holds
    for row in rows:
        t = transitions[int(row['transition_index'])]
        assert row['desired_rollout_state'] == t['to_desired_state']
        assert row['observed_rollout_state'] == t['to_observed_state']
        if row['observed_rollout_state'] in {'VALIDATING', 'RECOVERING'}:
            assert row['backend_role'] == 'baseline'
        if row['observed_rollout_state'] == 'PROMOTING':
            assert row['backend_role'] == 'candidate'
        if row['observed_rollout_state'] == 'HOLDING':
            assert t['to_observed_candidate_weight'] == config.routing.candidate_weight
    assert states.count('RECOVERING' if scenario == 'rollback' else 'PROMOTING') == 5
    for name in ('config.resolved.json', 'requests.csv', 'events.jsonl', 'rollout_transitions.jsonl', 'summary.json', 'validation.json', 'system_evidence.jsonl'):
        assert (one / name).read_bytes() == (two / name).read_bytes()
    events = [json.loads(line) for line in (one / 'events.jsonl').read_text().splitlines()]
    assert [e['logical_time'] for e in events] == list(range(len(events)))
    assert [t['logical_time'] for t in transitions] == sorted({t['logical_time'] for t in transitions})
    assert events[0]['event_type'] == 'RUN_STARTED' and events[-1]['event_type'] == 'RUN_COMPLETED'


@pytest.mark.parametrize('max_holds', [0, 1, 2])
@pytest.mark.parametrize('policy_type', ['always_hold', 'hold_then_promote'])
def test_hold_exhaustion_is_valid_failed_run(tmp_path, max_holds, policy_type):
    config = load_config(ROOT / 'configs/phase4b-promote.yaml')
    config = replace(config, lifecycle=replace(config.lifecycle, max_holds=max_holds),
                     decision_policy=replace(config.decision_policy, type=policy_type,
                                             holds_before_decision=max_holds + 1 if policy_type == 'hold_then_promote' else 0))
    config = replace(config, experiment=replace(config.experiment, request_count=config.planned_requests))
    path = run_experiment(config, tmp_path)
    summary = json.loads((path / 'summary.json').read_text())
    assert summary['final_lifecycle_state'] == summary['final_outcome'] == 'FAILED'
    assert summary['hold_count'] == max_holds
    assert summary['decision_count'] == max_holds + 1
    assert summary['completed_requests'] == summary['planned_requests']
    assert read_transitions(path)[-1]['reason'] == 'hold bound exceeded'
    assert validate_run(path)['valid']


@pytest.mark.parametrize('scenario', ['promote', 'rollback', 'hold-promote'])
def test_cli_lifecycle_scenarios(tmp_path, scenario):
    result = subprocess.run([sys.executable, '-m', 'saferoll.cli', 'run', '--config',
                             str(ROOT / f'configs/phase4b-{scenario}.yaml'), '--output-root', str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    path = Path(json.loads(result.stdout)['run_dir'])
    result = subprocess.run([sys.executable, '-m', 'saferoll.cli', 'validate', '--run-dir', str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)['valid']


@pytest.mark.parametrize('section,field,value', [
    ('lifecycle', 'validation_requests', -1), ('lifecycle', 'canary_requests', True),
    ('lifecycle', 'evaluation_requests', -1), ('lifecycle', 'hold_requests', -1),
    ('lifecycle', 'completion_requests', -1), ('lifecycle', 'max_holds', -1),
    ('lifecycle', 'max_holds', 1.5), ('decision_policy', 'type', 'saferoll'),
    ('decision_policy', 'holds_before_decision', -1), ('decision_policy', 'holds_before_decision', 1),
    ('experiment', 'request_count', 41),
])
def test_lifecycle_config_invalid(section, field, value):
    config = load_config(ROOT / 'configs/phase4b-promote.yaml').to_dict()
    config[section][field] = value
    with pytest.raises(ValueError):
        config_from_dict(config)


def test_partial_lifecycle_config_rejected(config):
    data = config.to_dict()
    del data['decision_policy']
    with pytest.raises(ValueError):
        config_from_dict(data)


def test_legacy_migration_preserves_phase4a_behavior(config, tmp_path):
    import random
    from saferoll.ids import request_id
    path = run_experiment(config, tmp_path)
    rng = random.Random(config.experiment.seed)
    for index, row in enumerate(read_rows(path)):
        role = 'candidate' if rng.random() < config.routing.candidate_weight else 'baseline'
        backend = getattr(config, role)
        affected = role == 'candidate' and 8 <= index <= 12
        assert row['request_id'] == request_id(config.identity, index)
        assert row['backend_role'] == role
        assert row['intended_revision'] == row['served_revision'] == backend.revision
        assert row['output'] == f'{backend.response_prefix}:request-{index:06d}'
        assert float(row['latency_ms']) == backend.fixed_latency_ms + (50 if affected else 0)
        assert row['fault_active'] == str(affected)
        assert row['desired_rollout_state'] == row['observed_rollout_state'] == 'CANARY'


def test_hold_loop_terminates_without_any_requests(config, tmp_path):
    c = replace(config, lifecycle=replace(config.lifecycle, canary_requests=0, max_holds=2),
                decision_policy=replace(config.decision_policy, type='always_hold'),
                experiment=replace(config.experiment, request_count=0),
                fault=replace(config.fault, start_request=0, end_request=0))
    path = run_experiment(c, tmp_path)
    summary = json.loads((path / 'summary.json').read_text())
    assert summary['final_outcome'] == 'FAILED'
    assert summary['decision_count'] == 3 and summary['hold_count'] == 2
    assert read_rows(path) == []
