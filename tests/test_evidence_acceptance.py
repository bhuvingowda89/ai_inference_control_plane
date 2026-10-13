import json
from pathlib import Path
import subprocess
import sys
from dataclasses import replace
import pytest
from saferoll.config import load_config
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT=Path(__file__).parents[1]


def records(path):
    return [json.loads(line) for line in (path/'system_evidence.jsonl').read_text().splitlines()]


@pytest.mark.parametrize('name',['healthy','latency-regression','insufficient-evidence','error-regression'])
def test_cli_evidence_smokes(tmp_path,name):
    result=subprocess.run([sys.executable,'-m','saferoll.cli','run','--config',
                           str(ROOT/f'configs/phase4c-{name}.yaml'),'--output-root',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    path=Path(json.loads(result.stdout)['run_dir'])
    assert validate_run(path)=={'valid':True,'issues':[]}
    canary={r['backend_role']:r for r in records(path) if r['window_type']=='CANARY'}
    baseline,candidate=canary['baseline'],canary['candidate']
    assert baseline['success_rate']==1 and baseline['error_rate']==0
    assert baseline['latency_mean_ms']==baseline['latency_p95_ms']==10
    if name=='healthy':
        assert baseline['sufficient'] and candidate['sufficient']
        assert candidate['success_rate']==1 and candidate['error_rate']==0
        assert candidate['latency_mean_ms']==candidate['latency_p95_ms']==12
    elif name=='latency-regression':
        assert candidate['latency_mean_ms']==candidate['latency_p95_ms']==62
    elif name=='insufficient-evidence':
        assert candidate['sample_count']<3 and not candidate['sufficient']
    else:
        assert candidate['error_rate']==1 and candidate['success_rate']==0
        assert candidate['failure_count']==candidate['sample_count']
        assert candidate['latency_mean_ms']==12
    summary=json.loads((path/'summary.json').read_text())
    assert summary['final_outcome']=='PROMOTED' # Dummy policy stays evidence-independent.


def test_evidence_determinism_and_lifecycle_windows(tmp_path):
    config=load_config(ROOT/'configs/phase4b-hold-promote.yaml')
    paths=[run_experiment(config,tmp_path) for _ in range(2)]
    assert (paths[0]/'system_evidence.jsonl').read_bytes()==(paths[1]/'system_evidence.jsonl').read_bytes()
    evidence=records(paths[0])
    assert {r['window_type'] for r in evidence}=={'VALIDATION','CANARY','EVALUATION','HOLD','PROMOTION'}
    evaluation=[r for r in evidence if r['backend_role']=='candidate' and r['window_type']=='EVALUATION']
    assert len(evaluation)==2 and evaluation[0]['window_id']!=evaluation[1]['window_id']
    ids=[i for r in evidence for i in r['sample_request_ids']]
    assert len(ids)==len(set(ids))==config.experiment.request_count
    rollback=run_experiment(load_config(ROOT/'configs/phase4b-rollback.yaml'),tmp_path)
    recovery=[r for r in records(rollback) if r['window_type']=='RECOVERY']
    assert len(recovery)==2 and recovery[0]['sample_count']==5 and recovery[1]['sample_count']==0
    assert all(r['freshness_status']=='NO_SAMPLES' for r in recovery if r['sample_count']==0)


def test_zero_run_evidence(config,tmp_path):
    empty=replace(config,experiment=replace(config.experiment,request_count=0),
                  lifecycle=replace(config.lifecycle,canary_requests=0),
                  fault=replace(config.fault,start_request=0,end_request=0))
    path=run_experiment(empty,tmp_path)
    assert len(records(path))==8 # Four lifecycle batches, both revisions even when empty.
    assert all(r['sample_count']==0 and not r['sufficient'] for r in records(path))
