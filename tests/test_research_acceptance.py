from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from saferoll.config import load_config
from saferoll.research.matrix import run_policy_matrix,with_policy
from saferoll.research.configuration import POLICY_NAMES
from saferoll.runner import run_experiment
from saferoll.faults.phase4e_config import RoutingFault,Phase4EConfig,ObservationFault
from saferoll.validator import validate_run

ROOT=Path(__file__).parents[1]


@pytest.mark.parametrize('scenario,expected',[
    ('healthy',['PROMOTE']*5),
    ('silent-behavior-regression',['PROMOTE','PROMOTE','ROLLBACK','ROLLBACK','ROLLBACK']),
    ('latency-regression',['PROMOTE','ROLLBACK','PROMOTE','ROLLBACK','ROLLBACK']),
    ('insufficient-behavior',['PROMOTE','PROMOTE','HOLD','HOLD','HOLD']),
    ('stale-behavior',['PROMOTE','PROMOTE','HOLD','HOLD','HOLD']),
    ('routing-mismatch',['PROMOTE','PROMOTE','PROMOTE','PROMOTE','HOLD']),
    ('behavior-routing-compound',['PROMOTE','PROMOTE','ROLLBACK','ROLLBACK','ROLLBACK']),
    ('dropped-observation',['HOLD','HOLD','PROMOTE','HOLD','HOLD'])])
def test_policy_matrices(tmp_path,scenario,expected):
    c=load_config(ROOT/f'configs/phase4f-{scenario}.yaml')
    matrix=json.loads(run_policy_matrix(c,tmp_path).read_text())
    first=[r for r in matrix['rows'] if r['decision_index']==0]
    assert [r['policy'] for r in first]==list(POLICY_NAMES)
    assert [r['decision'] for r in first]==expected
    for row in first:
        assert validate_run(row['run_dir'])=={'valid':True,'issues':[]}
    if scenario in {'silent-behavior-regression','latency-regression','behavior-routing-compound'}:
        assert all(r['ground_truth_state']=='UNSAFE' for r in first)
        for row in first:
            assert row['critical_error']==(row['decision']=='PROMOTE')
    if scenario=='healthy':
        assert all(r['correctness_class']=='CORRECT_PROMOTION' for r in first)
    if scenario=='routing-mismatch':
        assert first[-1]['correctness_class']=='UNNECESSARY_HOLD' # Preserve conservative cost; oracle is not biased.
    if scenario=='stale-behavior':
        assert first[-1]['correctness_class']=='DELAYED_ROLLBACK'
    if scenario=='insufficient-behavior':
        p=Path(first[-1]['run_dir']);summary=json.loads((p/'summary.json').read_text())
        assert summary['final_lifecycle_state']=='FAILED' and summary['hold_count']==1
        assert summary['decision_metrics']['hold_count']==2
        assert summary['planned_requests']<=summary['request_budget']


def test_research_cli_matrix_and_policy_override(tmp_path):
    result=subprocess.run([sys.executable,'-m','saferoll.cli','policy-matrix','--config',
        str(ROOT/'configs/phase4f-healthy.yaml'),'--output-root',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert Path(json.loads(result.stdout)['matrix_file']).exists()
    result=subprocess.run([sys.executable,'-m','saferoll.cli','run','--config',
        str(ROOT/'configs/phase4f-silent-behavior-regression.yaml'),'--policy','ops_gate','--output-root',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    path=Path(json.loads(result.stdout)['run_dir'])
    decision=json.loads((path/'decisions.jsonl').read_text().splitlines()[0])
    assert decision['policy_name']=='ops_gate' and decision['decision']=='PROMOTE'


def test_policy_bytes_and_hash_seed_independence(tmp_path):
    paths=[]
    for hash_seed in ('1','999'):
        result=subprocess.run([sys.executable,'-m','saferoll.cli','run','--config',
            str(ROOT/'configs/phase4f-stale-behavior.yaml'),'--output-root',str(tmp_path)],
            env={**os.environ,'PYTHONHASHSEED':hash_seed},capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        paths.append(Path(json.loads(result.stdout)['run_dir']))
    for name in ('policy_inputs.jsonl','decisions.jsonl','decision_evaluations.jsonl','summary.json','requests.csv','observations.jsonl'):
        assert (paths[0]/name).read_bytes()==(paths[1]/name).read_bytes()


def test_hidden_effective_mismatch_is_not_visible_to_saferoll(tmp_path):
    c=load_config(ROOT/'configs/phase4f-routing-mismatch.yaml')
    c=replace(c,phase4e=replace(c.phase4e,routing_faults=(RoutingFault('hidden-delay','routing_propagation_delay','EVALUATING',99),)))
    path=run_experiment(c,tmp_path)
    decision=json.loads((path/'decisions.jsonl').read_text().splitlines()[0])
    evaluation=json.loads((path/'decision_evaluations.jsonl').read_text().splitlines()[0])
    assert decision['decision']=='PROMOTE'
    assert evaluation['ground_truth_state']=='INDETERMINATE'
    assert evaluation['correctness_class']=='UNSUPPORTED_PROMOTION' and not evaluation['critical_error']
    visible=json.loads((path/'policy_inputs.jsonl').read_text().splitlines()[0])
    assert visible['desired_candidate_weight']==visible['observed_candidate_weight']==.5
    assert 'effective_candidate_weight' not in visible


def test_identity_uncertainty_and_oracle_known_safe(tmp_path):
    c=load_config(ROOT/'configs/phase4f-healthy.yaml')
    c=replace(c,phase4e=Phase4EConfig(True,observation_faults=(ObservationFault('mislabel','misattribution','behavior','candidate',2,1,reported_epoch=99),)))
    path=run_experiment(c,tmp_path)
    first=json.loads((path/'decisions.jsonl').read_text().splitlines()[0])
    evaluated=json.loads((path/'decision_evaluations.jsonl').read_text().splitlines()[0])
    assert first['decision']=='HOLD' and first['reason_codes']==['IDENTITY_UNCERTAINTY']
    assert evaluated['ground_truth_state']=='SAFE' and evaluated['correctness_class']=='UNNECESSARY_HOLD'


def test_structured_policy_and_oracle(tmp_path):
    c=with_policy(load_config(ROOT/'configs/phase4d-structured-regression.yaml'),'saferoll')
    path=run_experiment(c,tmp_path)
    first=json.loads((path/'decisions.jsonl').read_text().splitlines()[0])
    truth=json.loads((path/'decision_evaluations.jsonl').read_text().splitlines()[0])
    assert first['decision']=='ROLLBACK' and 'BEHAVIOR_REGRESSION' in first['reason_codes']
    assert truth['correctness_class']=='CORRECT_ROLLBACK'


def test_unnecessary_rollback_oracle_not_based_on_policy_reason(tmp_path):
    from saferoll.research.oracle import evaluate_decisions
    c=load_config(ROOT/'configs/phase4f-healthy.yaml');path=run_experiment(c,tmp_path)
    def read(name):return [json.loads(line) for line in (path/name).read_text().splitlines()]
    inputs,decisions=read('policy_inputs.jsonl'),read('decisions.jsonl')
    decisions[0]['decision']='ROLLBACK'
    import csv
    with (path/'requests.csv').open(newline='') as stream:requests=list(csv.DictReader(stream))
    evaluated=evaluate_decisions(c,inputs,decisions,read('system_evidence.jsonl'),read('routing_states.jsonl'),requests)
    assert evaluated[0]['correctness_class']=='UNNECESSARY_ROLLBACK'


def test_policy_and_oracle_agree_on_exact_allowed_drop(tmp_path):
    c=load_config(ROOT/'configs/phase4f-silent-behavior-regression.yaml')
    c=replace(c,policy=replace(c.policy,numeric_tolerance=0),candidate=replace(c.candidate,regression_every_nth_item=20))
    path=run_experiment(c,tmp_path)
    decision=json.loads((path/'decisions.jsonl').read_text().splitlines()[0])
    evaluation=json.loads((path/'decision_evaluations.jsonl').read_text().splitlines()[0])
    assert decision['decision']=='PROMOTE' and evaluation['ground_truth_state']=='SAFE'
