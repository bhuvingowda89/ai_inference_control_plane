import csv
from dataclasses import replace
import json
from pathlib import Path
import pytest
from saferoll.config import load_config
from saferoll.evidence_validation import validate_system_evidence
from saferoll.runner import run_experiment
from saferoll.validator import validate_run


@pytest.fixture
def evidence_run(tmp_path):
    config=load_config(Path(__file__).parents[1]/'configs/phase4c-healthy.yaml')
    return run_experiment(config,tmp_path)


def read_evidence(path):
    return [json.loads(line) for line in (path/'system_evidence.jsonl').read_text().splitlines()]


def write_evidence(path,records):
    (path/'system_evidence.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))


@pytest.mark.parametrize('field,value', [
    ('rollout_id','other'), ('epoch',2), ('revision','unknown'), ('backend_role','candidate'),
    ('lifecycle_state','HOLDING'), ('transition_index',99), ('window_id','other'),
    ('sample_count',99), ('request_count',99), ('success_count',0), ('failure_count',1),
    ('success_rate',0), ('error_rate',1), ('latency_mean_ms',99), ('latency_min_ms',99),
    ('latency_max_ms',99), ('latency_p50_ms',99), ('latency_p95_ms',99),
    ('sufficient',False), ('minimum_required_samples',99), ('freshness_status','STALE'),
    ('latest_sample_logical_time',0), ('age_at_evaluation',99), ('closed_at_logical_time',0),
    ('evaluated_at_logical_time',999), ('window_start_logical_time',0), ('window_end_logical_time',0),
    ('window_type','RECOVERY'), ('window_sequence',99), ('planned_window_request_count',99),
    ('schema_version','unknown'), ('sample_count',True), ('latency_mean_ms',float('nan')),
])
def test_corrupted_evidence_fields(evidence_run,field,value):
    records=read_evidence(evidence_run)
    # Nonempty baseline validation record, sufficient and fresh.
    records[0][field]=value
    write_evidence(evidence_run,records)
    result=validate_run(evidence_run)
    assert not result['valid'] and result['issues']
    assert json.loads((evidence_run/'validation.json').read_text())==result


@pytest.mark.parametrize('corruption',['missing','malformed','schema','duplicate_record','missing_record',
    'unknown_id','duplicate_id','mixed_revision_ids','cross_window_ids','zero_as_zero'])
def test_evidence_corruption(evidence_run,corruption):
    file=evidence_run/'system_evidence.jsonl'
    records=read_evidence(evidence_run)
    if corruption=='missing':
        file.unlink()
    elif corruption=='malformed':
        file.write_text('{bad\n')
    else:
        if corruption=='schema':
            del records[0]['error_rate']
        elif corruption=='duplicate_record':
            records[2]=records[0]
        elif corruption=='missing_record':
            records.pop()
        elif corruption=='unknown_id':
            records[0]['sample_request_ids'][0]='unknown'
        elif corruption=='duplicate_id':
            records[0]['sample_request_ids'][1]=records[0]['sample_request_ids'][0]
        elif corruption=='mixed_revision_ids':
            records[3]['sample_request_ids'][0]=records[2]['sample_request_ids'][0]
        elif corruption=='cross_window_ids':
            records[2]['sample_request_ids'][0]=records[0]['sample_request_ids'][0]
        else:
            records[1]['latency_p95_ms']=0 # Empty candidate validation metrics must remain null.
        write_evidence(evidence_run,records)
    assert not validate_run(evidence_run)['valid']


def test_cross_epoch_request_and_record_corruption(tmp_path):
    config=load_config(Path(__file__).parents[1]/'configs/phase4c-healthy.yaml')
    one=run_experiment(config,tmp_path)
    two=run_experiment(replace(config,rollout=replace(config.rollout,epoch=2)),tmp_path)
    prior=read_evidence(one)
    current=read_evidence(two)
    assert {r["window_id"] for r in prior}.isdisjoint(r["window_id"] for r in current)
    current[0]['sample_request_ids'][0]=prior[0]['sample_request_ids'][0]
    write_evidence(two,current)
    assert not validate_run(two)['valid']
    current=read_evidence(two)
    current[0]=prior[0]
    write_evidence(two,current)
    assert not validate_run(two)['valid']


def test_wrong_known_revision_preserving_ids(evidence_run):
    records=read_evidence(evidence_run)
    records[3]['revision']=records[2]['revision']
    write_evidence(evidence_run,records)
    assert not validate_run(evidence_run)['valid']


def test_validator_does_not_share_collector_percentile_implementation(monkeypatch,tmp_path):
    from saferoll import evidence
    monkeypatch.setattr(evidence,'percentile',lambda values,p: 999 if values else None)
    config=load_config(Path(__file__).parents[1]/'configs/phase4c-healthy.yaml')
    with pytest.raises(ValueError,match='latency_p50_ms'):
        run_experiment(config,tmp_path)


def test_independent_recomputation_uses_raw_latency_vectors(config,tmp_path):
    config=replace(config,experiment=replace(config.experiment,request_count=4),
                   lifecycle=replace(config.lifecycle,canary_requests=4),
                   routing=replace(config.routing,candidate_weight=1,baseline_weight=0),
                   fault=replace(config.fault,start_request=0,end_request=0,added_latency_ms=0))
    path=run_experiment(config,tmp_path)
    with (path/'requests.csv').open(newline='') as stream:
        rows=list(csv.DictReader(stream))
    for row,value in zip(rows,[0,10,20,30]):
        row['latency_ms']=str(value)
    records=read_evidence(path)
    candidate=next(r for r in records if r['window_type']=='CANARY' and r['backend_role']=='candidate')
    candidate.update(latency_mean_ms=15,latency_min_ms=0,latency_max_ms=30,latency_p50_ms=15,latency_p95_ms=28.5)
    events=[json.loads(line) for line in (path/'events.jsonl').read_text().splitlines()]
    assert validate_system_evidence(config,records,rows,events)==[]
    candidate['latency_p95_ms']=30
    assert any('latency_p95_ms' in issue for issue in validate_system_evidence(config,records,rows,events))


def test_freshness_validator_recomputes_zero_max_age(config,tmp_path):
    config=replace(config,evidence=replace(config.evidence,max_age_logical_units=0))
    path=run_experiment(config,tmp_path)
    assert all(r['freshness_status']=='STALE' for r in read_evidence(path) if r['sample_count'])
    assert validate_run(path)['valid']
