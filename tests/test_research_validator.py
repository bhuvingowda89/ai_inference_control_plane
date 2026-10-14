import json
from pathlib import Path
from dataclasses import replace
import pytest
from saferoll.config import load_config
from saferoll.runner import run_experiment
from saferoll.validator import validate_run
from saferoll.research.inputs import snapshot_id
from saferoll.research.matrix import with_policy

ROOT=Path(__file__).parents[1]


@pytest.fixture
def policy_run(tmp_path):
    return run_experiment(load_config(ROOT/'configs/phase4f-silent-behavior-regression.yaml'),tmp_path)


def read(path,name):return [json.loads(line) for line in (path/name).read_text().splitlines()]


def write(path,name,records):
    (path/name).write_text(''.join(json.dumps(r)+'\n' for r in records))


def mutate_input(path,modify):
    inputs=read(path,'policy_inputs.jsonl');decisions=read(path,'decisions.jsonl');events=read(path,'events.jsonl')
    modify(inputs[0])
    inputs[0]['input_snapshot_id']=snapshot_id(inputs[0]);decisions[0]['input_snapshot_id']=inputs[0]['input_snapshot_id']
    event=next(e for e in events if e['event_type']=='DECISION_MADE')
    event['details'].update(policy_input=inputs[0],policy_result=decisions[0])
    write(path,'policy_inputs.jsonl',inputs);write(path,'decisions.jsonl',decisions);write(path,'events.jsonl',events)


@pytest.mark.parametrize('key',['effective_candidate_weight','fault_active','fault_configuration','ground_truth_state','oracle','expected_decision'])
def test_hidden_fields_rejected_even_with_updated_hash(policy_run,key):
    mutate_input(policy_run,lambda p:p.update({key:'hidden'}))
    result=validate_run(policy_run)
    assert not result['valid'] and any('boundary' in i for i in result['issues'])


def test_hidden_nested_field_rejected(policy_run):
    mutate_input(policy_run,lambda p:p['system'][0].update(effective_state='CANARY'))
    assert not validate_run(policy_run)['valid']


@pytest.mark.parametrize('field,value',[('epoch',2),('rollout_id','other'),('policy_name','unknown'),
    ('logical_time',0),('expected_window_id','unknown'),('transition_index',999)])
def test_snapshot_corruption(policy_run,field,value):
    mutate_input(policy_run,lambda p:p.update({field:value}))
    assert not validate_run(policy_run)['valid']


def test_unknown_and_future_observation_references(policy_run):
    mutate_input(policy_run,lambda p:p['system'][0].update(observation_id='unknown'))
    result=validate_run(policy_run)
    assert not result['valid'] and any('unknown or future' in i for i in result['issues'])


def test_future_reference_rejected(policy_run):
    observations=read(policy_run,'observations.jsonl')
    decision=read(policy_run,'decisions.jsonl')[0]
    future=next(o for o in observations if o['logical_time']>decision['logical_time'])
    mutate_input(policy_run,lambda p:p['system'][0].update(observation_id=future['observation_id']))
    result=validate_run(policy_run)
    assert not result['valid'] and any('future observation' in i for i in result['issues'])


@pytest.mark.parametrize('field,value',[('decision','PROMOTE'),('input_snapshot_id','unknown'),('policy_version','999'),
    ('policy_name','ops_gate'),('reason_codes',['UNKNOWN']),('reason_details',['fabricated']),('decision_id','unknown'),('epoch',2)])
def test_decision_corruption(policy_run,field,value):
    records=read(policy_run,'decisions.jsonl');records[0][field]=value
    events=read(policy_run,'events.jsonl')
    next(e for e in events if e['event_type']=='DECISION_MADE')['details']['policy_result']=records[0]
    write(policy_run,'decisions.jsonl',records);write(policy_run,'events.jsonl',events)
    assert not validate_run(policy_run)['valid']


@pytest.mark.parametrize('field,value',[('ground_truth_state','SAFE'),('correctness_class','CORRECT_PROMOTION'),('critical_error',True),
    ('decision_id','unknown'),('oracle_version','999'),('decision','PROMOTE')])
def test_oracle_corruption(policy_run,field,value):
    records=read(policy_run,'decision_evaluations.jsonl');records[0][field]=value
    write(policy_run,'decision_evaluations.jsonl',records)
    assert not validate_run(policy_run)['valid']


@pytest.mark.parametrize('artifact',['policy_inputs.jsonl','decisions.jsonl','decision_evaluations.jsonl'])
@pytest.mark.parametrize('mode',['missing','malformed'])
def test_required_artifacts(policy_run,artifact,mode):
    if mode=='missing':(policy_run/artifact).unlink()
    else:(policy_run/artifact).write_text('{bad\n')
    assert not validate_run(policy_run)['valid']


def test_metrics_corruption(policy_run):
    file=policy_run/'summary.json';summary=json.loads(file.read_text())
    summary['decision_metrics']['rollback_count']=999
    file.write_text(json.dumps(summary))
    assert not validate_run(policy_run)['valid']


def test_cross_epoch_decision_rejection(tmp_path):
    c=load_config(ROOT/'configs/phase4f-healthy.yaml')
    one=run_experiment(c,tmp_path)
    two=run_experiment(replace(c,rollout=replace(c.rollout,epoch=2)),tmp_path)
    old,new=read(one,'decisions.jsonl'),read(two,'decisions.jsonl')
    assert {d['decision_id'] for d in old}.isdisjoint(d['decision_id'] for d in new)
    write(two,'decisions.jsonl',old)
    assert not validate_run(two)['valid']


def test_no_behavior_ground_truth_is_explicitly_indeterminate(tmp_path):
    c=with_policy(load_config(ROOT/'configs/phase4a-smoke.yaml'),'saferoll')
    path=run_experiment(c,tmp_path)
    evaluated=read(path,'decision_evaluations.jsonl')
    assert all(e['ground_truth_state']=='INDETERMINATE' and e['correctness_class']=='CORRECT_HOLD' for e in evaluated)
