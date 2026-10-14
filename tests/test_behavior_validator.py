from dataclasses import replace
import json
from pathlib import Path
import pytest
from saferoll.config import load_config
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT=Path(__file__).parents[1]


@pytest.fixture
def behavior_run(tmp_path):
    return run_experiment(load_config(ROOT/'configs/phase4d-classification-regression.yaml'),tmp_path)


def read(path,name):
    return [json.loads(line) for line in (path/name).read_text().splitlines()]


def write(path,name,records):
    (path/name).write_text(''.join(json.dumps(r)+'\n' for r in records))


def mutate_probes(path,modify):
    probes=read(path,'behavior_probes.jsonl')
    modify(probes)
    write(path,'behavior_probes.jsonl',probes)
    events=read(path,'events.jsonl')
    for event,probe in zip((e for e in events if e['event_type']=='BEHAVIOR_PROBE_COMPLETED'),probes):
        event['details']=probe
    write(path,'events.jsonl',events)


@pytest.mark.parametrize('field,value',[('expected_label','wrong'),('predicted_label','wrong'),('observed_output','wrong'),
    ('revision','unknown'),('served_revision','unknown'),('epoch',2),('item_id','unknown'),('task_type','structured_output'),
    ('workload_id','other'),('request_id','unknown'),('window_id','other'),('lifecycle_state','HOLDING'),
    ('transition_index',999),('logical_time',0),('input',{'number':0})])
def test_corrupted_probe_records_rejected(behavior_run,field,value):
    mutate_probes(behavior_run,lambda records:records[0].update({field:value}))
    result=validate_run(behavior_run)
    assert not result['valid']
    assert any('behavior' in i for i in result['issues'])
    assert json.loads((behavior_run/'validation.json').read_text())==result


@pytest.mark.parametrize('field,value',[('accuracy',0),('correct_count',0),('correct_count',True),('sufficient',False),
    ('age_at_evaluation',999),('freshness_status','STALE'),('sample_request_ids',['unknown']),
    ('sample_item_ids',['unknown']),('epoch',2),('revision','unknown'),('task_type','structured_output'),
    ('sample_count',999),('minimum_required_samples',999),('closed_at_logical_time',0)])
def test_corrupted_evidence_rejected(behavior_run,field,value):
    records=read(behavior_run,'behavior_evidence.jsonl');records[0][field]=value
    write(behavior_run,'behavior_evidence.jsonl',records)
    assert not validate_run(behavior_run)['valid']


@pytest.mark.parametrize('field,value',[('disagreement_count',999),('agreement_count',999),('disagreement_rate',1),
    ('paired_sufficient',False),('paired_sample_count',99),('paired_item_ids',['unknown']),
    ('baseline_request_ids',['unknown']),('candidate_request_ids',['unknown']),('candidate_accuracy',1),
    ('candidate_minus_baseline_accuracy',0)])
def test_corrupted_comparison_rejected(behavior_run,field,value):
    records=read(behavior_run,'behavior_comparisons.jsonl');records[0][field]=value
    write(behavior_run,'behavior_comparisons.jsonl',records)
    assert not validate_run(behavior_run)['valid']


@pytest.mark.parametrize('field,value',[('expected_label','wrong'),('item_id','wrong'),('task_type','wrong'),
    ('expected_output','wrong'),('input',{'number':0})])
def test_ground_truth_corruption_rejected(behavior_run,field,value):
    file=behavior_run/'behavior_workload.json';workload=json.loads(file.read_text())
    workload['items'][0][field]=value
    file.write_text(json.dumps(workload))
    result=validate_run(behavior_run)
    assert not result['valid'] and any('ground-truth' in i for i in result['issues'])


@pytest.mark.parametrize('artifact',['behavior_workload.json','behavior_probes.jsonl','behavior_evidence.jsonl','behavior_comparisons.jsonl'])
@pytest.mark.parametrize('mode',['missing','malformed'])
def test_required_artifacts(behavior_run,artifact,mode):
    file=behavior_run/artifact
    if mode=='missing':
        file.unlink()
    else:
        file.write_text('{bad\n')
    assert not validate_run(behavior_run)['valid']


def test_duplicate_probes_and_unknown_references(behavior_run):
    mutate_probes(behavior_run,lambda records:records.__setitem__(2,dict(records[0])))
    assert not validate_run(behavior_run)['valid']


def test_cross_epoch_probe_and_evidence_contamination(tmp_path):
    config=load_config(ROOT/'configs/phase4d-classification-regression.yaml')
    one=run_experiment(config,tmp_path)
    two=run_experiment(replace(config,rollout=replace(config.rollout,epoch=2)),tmp_path)
    probes1,probes2=read(one,'behavior_probes.jsonl'),read(two,'behavior_probes.jsonl')
    assert {p['request_id'] for p in probes1}.isdisjoint(p['request_id'] for p in probes2)
    assert probes1[0]['item_id']==probes2[0]['item_id'] # Ground truth reused, request identities isolated.
    mutate_probes(two,lambda records:records.__setitem__(0,probes1[0]))
    assert not validate_run(two)['valid']
    three=run_experiment(replace(config,rollout=replace(config.rollout,epoch=2)),tmp_path)
    records=read(three,'behavior_evidence.jsonl');records[0]=read(one,'behavior_evidence.jsonl')[0]
    write(three,'behavior_evidence.jsonl',records)
    assert not validate_run(three)['valid']


@pytest.mark.parametrize('field',['structure_valid_count','exact_match_count','structure_valid_rate','exact_match_rate'])
def test_corrupted_structured_metrics(tmp_path,field):
    path=run_experiment(load_config(ROOT/'configs/phase4d-structured-regression.yaml'),tmp_path)
    records=read(path,'behavior_evidence.jsonl');records[1][field]=0
    write(path,'behavior_evidence.jsonl',records)
    assert not validate_run(path)['valid']


def test_structured_expected_values_corruption(tmp_path):
    path=run_experiment(load_config(ROOT/'configs/phase4d-structured-regression.yaml'),tmp_path)
    file=path/'behavior_workload.json';workload=json.loads(file.read_text())
    workload['items'][0]['expected_values']['number']=999
    file.write_text(json.dumps(workload))
    assert not validate_run(path)['valid']


def test_evaluator_independence(monkeypatch,tmp_path):
    from saferoll.behavior import evidence
    monkeypatch.setattr(evidence,'evaluate_output',lambda item,output:{'correct':True,'structure_valid':None,'exact_match':None})
    with pytest.raises(ValueError,match='behavior evidence'):
        run_experiment(load_config(ROOT/'configs/phase4d-classification-regression.yaml'),tmp_path)


def test_structured_evaluator_independence(monkeypatch,tmp_path):
    from saferoll.behavior import evidence
    monkeypatch.setattr(evidence,'evaluate_output',lambda item,output:{'correct':None,'structure_valid':True,'exact_match':True})
    with pytest.raises(ValueError,match='behavior evidence'):
        run_experiment(load_config(ROOT/'configs/phase4d-structured-regression.yaml'),tmp_path)
