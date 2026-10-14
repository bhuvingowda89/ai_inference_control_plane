from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from saferoll.config import load_config
from saferoll.runner import run_experiment

ROOT=Path(__file__).parents[1]


def read(path,name):
    return [json.loads(line) for line in (path/name).read_text().splitlines()]


@pytest.mark.parametrize('scenario',['behavior-healthy','classification-regression','structured-regression',
    'insufficient-behavior','system-healthy-behavior-regressed'])
def test_behavior_cli_scenarios(tmp_path,scenario):
    result=subprocess.run([sys.executable,'-m','saferoll.cli','run','--config',
        str(ROOT/f'configs/phase4d-{scenario}.yaml'),'--output-root',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    path=Path(json.loads(result.stdout)['run_dir'])
    assert json.loads((path/'validation.json').read_text())=={'valid':True,'issues':[]}
    evidence={r['backend_role']:r for r in read(path,'behavior_evidence.jsonl') if r['window_type']=='CANARY'}
    comparisons=[r for r in read(path,'behavior_comparisons.jsonl') if r['lifecycle_state']=='CANARY']
    baseline,candidate=evidence['baseline'],evidence['candidate']
    comparison=comparisons[0]
    if scenario=='structured-regression':
        assert baseline['structure_valid_rate']==baseline['exact_match_rate']==1
        assert candidate['structure_valid_rate']==.85 and candidate['exact_match_rate']==.75
        assert candidate['accuracy'] is None and comparison['disagreement_count']==5
    else:
        assert baseline['accuracy']==1
        assert candidate['accuracy']==(.75 if 'regress' in scenario else 1)
        assert candidate['exact_match_count'] is None
    if scenario=='insufficient-behavior':
        assert candidate['sample_count']==2 and not candidate['sufficient']
        assert baseline['sufficient']
        assert comparison['paired_sample_count']==2 and not comparison['paired_sufficient']
    else:
        assert comparison['paired_sample_count']==20 and comparison['paired_sufficient']
        assert comparison['disagreement_count']==(0 if scenario=='behavior-healthy' else 5)
        assert comparison['agreement_count']==(20 if scenario=='behavior-healthy' else 15)
        assert comparison['disagreement_rate']==(0 if scenario=='behavior-healthy' else .25)
    system=[r for r in read(path,'system_evidence.jsonl') if r['window_type']=='CANARY']
    assert all(r['success_rate']==1 and r['error_rate']==0 for r in system)
    if scenario=='system-healthy-behavior-regressed':
        assert system[0]['latency_mean_ms']==12 and system[1]['latency_mean_ms']==10
        assert baseline['accuracy']==1 and candidate['accuracy']==.75
    summary=json.loads((path/'summary.json').read_text())
    assert summary['completed_requests']==70 and summary['final_outcome']=='PROMOTED'
    assert summary['behavior_windows']==4
    assert summary['behavior_samples']==(88 if scenario=='insufficient-behavior' else 160)
    assert summary['paired_behavior_samples']==(8 if scenario=='insufficient-behavior' else 80)


def test_byte_determinism_and_hash_randomization(tmp_path):
    paths=[]
    for hash_seed in ('1','999'):
        result=subprocess.run([sys.executable,'-m','saferoll.cli','run','--config',
            str(ROOT/'configs/phase4d-classification-regression.yaml'),'--output-root',str(tmp_path)],
            env={**os.environ,'PYTHONHASHSEED':hash_seed},capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        paths.append(Path(json.loads(result.stdout)['run_dir']))
    for name in ['behavior_workload.json','behavior_probes.jsonl','behavior_evidence.jsonl','behavior_comparisons.jsonl',
                 'events.jsonl','requests.csv','system_evidence.jsonl','summary.json']:
        assert (paths[0]/name).read_bytes()==(paths[1]/name).read_bytes()


def test_healthy_structured_and_same_wrong_outputs(tmp_path):
    c=load_config(ROOT/'configs/phase4d-structured-regression.yaml')
    c=replace(c,candidate=replace(c.candidate,behavior_profile='correct'))
    path=run_experiment(c,tmp_path)
    assert all(r['exact_match_rate']==r['structure_valid_rate']==1 for r in read(path,'behavior_evidence.jsonl'))
    assert all(r['disagreement_rate']==0 for r in read(path,'behavior_comparisons.jsonl'))
    c=load_config(ROOT/'configs/phase4d-classification-regression.yaml')
    c=replace(c,baseline=replace(c.baseline,behavior_profile='classification_regression'))
    path=run_experiment(c,tmp_path)
    assert all(r['accuracy']==.75 for r in read(path,'behavior_evidence.jsonl'))
    assert all(r['disagreement_rate']==0 for r in read(path,'behavior_comparisons.jsonl'))


def test_pair_accuracy_only_uses_intersection(tmp_path):
    c=load_config(ROOT/'configs/phase4d-insufficient-behavior.yaml')
    c=replace(c,baseline=replace(c.baseline,behavior_profile='classification_regression'))
    path=run_experiment(c,tmp_path)
    assert read(path,'behavior_evidence.jsonl')[0]['accuracy']==.75
    assert read(path,'behavior_comparisons.jsonl')[0]['baseline_accuracy']==1
    assert read(path,'behavior_comparisons.jsonl')[0]['disagreement_count']==0


def test_empty_samples_and_freshness(tmp_path):
    c=load_config(ROOT/'configs/phase4d-behavior-healthy.yaml')
    c=replace(c,behavior_evidence=replace(c.behavior_evidence,baseline_items_per_window=0,candidate_items_per_window=0))
    path=run_experiment(c,tmp_path)
    assert all(r['sample_count']==0 and not r['sufficient'] and r['accuracy'] is None and
               r['freshness_status']=='NO_SAMPLES' for r in read(path,'behavior_evidence.jsonl'))
    assert all(r['paired_sample_count']==0 and r['disagreement_rate'] is None for r in read(path,'behavior_comparisons.jsonl'))
    c=load_config(ROOT/'configs/phase4d-behavior-healthy.yaml')
    c=replace(c,behavior_evidence=replace(c.behavior_evidence,max_age_logical_units=0))
    path=run_experiment(c,tmp_path)
    assert all(r['freshness_status']=='STALE' for r in read(path,'behavior_evidence.jsonl'))


def test_hold_and_recovery_window_isolation(tmp_path):
    for filename in ('phase4b-hold-promote.yaml','phase4b-rollback.yaml'):
        c=load_config(ROOT/'configs'/filename)
        c=replace(c,behavior_evidence=replace(c.behavior_evidence,enabled=True))
        path=run_experiment(c,tmp_path)
        records=read(path,'behavior_evidence.jsonl')
        expected='HOLD' if 'hold' in filename else 'RECOVERY'
        assert expected in {r['window_type'] for r in records}
        ids=[i for r in records for i in r['sample_request_ids']]
        assert len(ids)==len(set(ids))
        if expected=='HOLD':
            evals=[r for r in records if r['backend_role']=='candidate' and r['window_type']=='EVALUATION']
            assert len(evals)==2 and evals[0]['window_id']!=evals[1]['window_id']
