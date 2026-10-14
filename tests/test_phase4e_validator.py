from dataclasses import replace
import csv
import json
from pathlib import Path
import pytest
from saferoll.config import load_config
from saferoll.faults.phase4e_config import Phase4EConfig, ObservationFault
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT=Path(__file__).parents[1]


@pytest.fixture
def run(tmp_path):
    return run_experiment(load_config(ROOT/'configs/phase4e-rollback-delay.yaml'),tmp_path)


def read(path,name):
    return [json.loads(line) for line in (path/name).read_text().splitlines()]


def write(path,name,data):
    (path/name).write_text(''.join(json.dumps(r)+'\n' for r in data))


@pytest.mark.parametrize('artifact',['routing_states.jsonl','observations.jsonl','fault_events.jsonl'])
@pytest.mark.parametrize('mode',['missing','malformed'])
def test_missing_and_malformed(run,artifact,mode):
    if mode=='missing':
        (run/artifact).unlink()
    else:
        (run/artifact).write_text('{bad\n')
    assert not validate_run(run)['valid']


@pytest.mark.parametrize('field,value',[('effective_candidate_weight',.9),('desired_baseline_weight',.99),
    ('epoch',2),('effective_state','UNKNOWN'),('desired_effective_match',False),('routing_state_index',99)])
def test_routing_corruption(run,field,value):
    records=read(run,'routing_states.jsonl');records[0][field]=value
    write(run,'routing_states.jsonl',records)
    # Synchronize both views to exercise actual semantic checks.
    events=read(run,'events.jsonl')
    next(e for e in events if e['event_type']=='ROUTING_STATE')['details']=records[0]
    write(run,'events.jsonl',events)
    assert not validate_run(run)['valid']


@pytest.mark.parametrize('field,value',[('epoch',2),('observation_index',999),('observation_id','unknown'),
    ('observed_age_logical_units',999),('fault_ids',['unknown']),('source_revision','unknown'),
    ('observation_status','DROPPED'),('observed_value',{'tampered':True})])
def test_observation_corruption(run,field,value):
    records=read(run,'observations.jsonl');records[0][field]=value
    write(run,'observations.jsonl',records)
    assert not validate_run(run)['valid']


@pytest.mark.parametrize('key,value',[('artifact','unknown.jsonl'),('record_index',999),('epoch',2),('revision','unknown')])
def test_bad_observation_references(run,key,value):
    records=read(run,'observations.jsonl');records[0]['ground_truth_reference'][key]=value
    write(run,'observations.jsonl',records)
    assert not validate_run(run)['valid']


@pytest.mark.parametrize('field',['rollback_requested_time','last_candidate_served_time','effective_zero_candidate_time','effective_contained'])
def test_bad_containment_facts(run,field):
    file=run/'summary.json';summary=json.loads(file.read_text())
    summary[field]='corrupt'
    file.write_text(json.dumps(summary))
    assert not validate_run(run)['valid']


def test_fault_event_order_and_ids(run):
    records=read(run,'fault_events.jsonl');records[0]['fault_id']='unknown'
    write(run,'fault_events.jsonl',records)
    assert not validate_run(run)['valid']


def test_effective_routing_request_reference(run):
    file=run/'requests.csv'
    with file.open(newline='') as stream:
        reader=csv.DictReader(stream);columns=reader.fieldnames;rows=list(reader)
    rows[0]['routing_state_index']='999'
    with file.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,columns);writer.writeheader();writer.writerows(rows)
    assert not validate_run(run)['valid']


def test_valid_misleading_envelope_invalid_ground_truth(tmp_path):
    c=load_config(ROOT/'configs/phase4d-behavior-healthy.yaml')
    c=replace(c,phase4e=Phase4EConfig(True,observation_faults=(ObservationFault('wrong-1','misattribution','behavior','candidate',1,1,reported_epoch=9),)))
    path=run_experiment(c,tmp_path)
    assert validate_run(path)['valid']
    observations=read(path,'observations.jsonl')
    altered=next(o for o in observations if o['observation_status']=='MISMATCHED' and o['observation_type']=='behavior')
    assert altered['epoch']==1 and altered['observed_value']['epoch']==9
    assert altered['ground_truth_reference']['epoch']==1
    evidence=read(path,'behavior_evidence.jsonl');evidence[3]['revision']='unknown'
    write(path,'behavior_evidence.jsonl',evidence)
    assert not validate_run(path)['valid']


def test_cross_epoch_observation_contamination(tmp_path):
    c=load_config(ROOT/'configs/phase4e-stale-behavior-observation.yaml')
    one=run_experiment(c,tmp_path)
    two=run_experiment(replace(c,rollout=replace(c.rollout,epoch=2)),tmp_path)
    a,b=read(one,'observations.jsonl'),read(two,'observations.jsonl')
    assert {o['observation_id'] for o in a}.isdisjoint(o['observation_id'] for o in b)
    b[0]=a[0];write(two,'observations.jsonl',b)
    assert not validate_run(two)['valid']
