from dataclasses import replace
import csv
import json
from pathlib import Path
import random
import subprocess
import sys
import pytest
from saferoll.config import load_config, config_from_dict
from saferoll.faults.phase4e_config import Phase4EConfig, RoutingFault, ObservationFault, RequestFault
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT=Path(__file__).parents[1]


def read(path,name):
    return [json.loads(line) for line in (path/name).read_text().splitlines()]


def rows(path):
    with (path/'requests.csv').open(newline='') as stream:
        return list(csv.DictReader(stream))


def base():
    return load_config(ROOT/'configs/phase4d-behavior-healthy.yaml')


def with_routing(kind,weight=.25,duration=5,state='CANARY'):
    c=base()
    return replace(c,phase4e=Phase4EConfig(True,routing_faults=(RoutingFault('route-1',kind,state,duration,candidate_weight=weight),)))


@pytest.mark.parametrize('kind',['routing_propagation_delay','stale_observed_routing','incorrect_observed_routing','partial_rollout'])
def test_three_planes(tmp_path,kind):
    c=with_routing(kind,weight=.73 if kind=='incorrect_observed_routing' else .25)
    path=run_experiment(c,tmp_path)
    active=[r for r in read(path,'routing_states.jsonl') if r['fault_active'] and r['capture_point']=='request']
    assert len(active)==5
    assert all(r['desired_candidate_weight']==.5 for r in active)
    if kind=='routing_propagation_delay':
        assert all(r['effective_candidate_weight']==0 and r['effective_state']=='VALIDATING' for r in active)
    elif kind=='stale_observed_routing':
        assert all(r['observed_candidate_weight']==0 and r['effective_candidate_weight']==.5 for r in active)
    elif kind=='incorrect_observed_routing':
        assert all(r['observed_candidate_weight']==.73 and r['effective_candidate_weight']==.5 for r in active)
        assert .73 not in {r['effective_candidate_weight'] for r in read(path,'routing_states.jsonl')}
    else:
        assert all(r['effective_candidate_weight']==.25 and r['observed_candidate_weight']==.5 for r in active)
    assert validate_run(path)['valid']


def test_effective_point_two_not_desired_point_five(tmp_path):
    c=load_config(ROOT/'configs/phase4e-routing-delay.yaml')
    path=run_experiment(c,tmp_path)
    routing=read(path,'routing_states.jsonl')
    active=[r for r in routing if r['capture_point']=='request' and r['fault_active']]
    assert len(active)==10
    assert all(r['desired_candidate_weight']==r['observed_candidate_weight']==.5 and
               r['effective_candidate_weight']==.2 for r in active)
    rng=random.Random(c.experiment.seed)
    desired_differences=0
    for row in rows(path):
        r=routing[int(row['routing_state_index'])]
        u=rng.random()
        assert row['backend_role']==('candidate' if u<r['effective_candidate_weight'] else 'baseline')
        if r['fault_active'] and .2<=u<.5:
            desired_differences+=1
            assert row['backend_role']=='baseline'
    assert desired_differences>0


@pytest.mark.parametrize('kind',['rollback_propagation_delay','partial_rollback','rollback_observation_mismatch'])
def test_recovery_faults(tmp_path,kind):
    c=load_config(ROOT/'configs/phase4e-rollback-delay.yaml')
    c=replace(c,phase4e=Phase4EConfig(True,routing_faults=(RoutingFault('recover-1',kind,'ROLLING_BACK',12,candidate_weight=.05),)))
    path=run_experiment(c,tmp_path)
    active=[r for r in read(path,'routing_states.jsonl') if r['fault_active'] and r['capture_point']=='request']
    assert len(active)==12 and all(r['desired_candidate_weight']==0 for r in active)
    if kind=='rollback_observation_mismatch':
        assert all(r['effective_candidate_weight']==0 and r['observed_candidate_weight']==.2 for r in active)
    else:
        assert all(r['effective_candidate_weight']==(.2 if kind=='rollback_propagation_delay' else .05) for r in active)
    summary=json.loads((path/'summary.json').read_text())
    assert summary['effective_contained'] and summary['final_effective_candidate_weight']==0


def test_critical_containment_timing(tmp_path):
    c=load_config(ROOT/'configs/phase4e-rollback-delay.yaml')
    path=run_experiment(c,tmp_path)
    summary=json.loads((path/'summary.json').read_text())
    assert summary['rollback_decision_time']<summary['rollback_requested_time']
    assert summary['rollback_requested_time']<summary['last_post_rollback_candidate_time']<=summary['effective_zero_candidate_time']
    assert summary['last_post_rollback_candidate_time']==summary['last_candidate_served_time']
    assert summary['final_outcome']=='ROLLED_BACK'
    assert summary['effective_contained']
    candidate=[e['logical_time'] for e in read(path,'events.jsonl') if e['event_type']=='REQUEST_COMPLETED' and e['served_revision']==c.candidate.revision]
    assert summary['last_candidate_served_time']==max(candidate)


def test_uncontained_rollback_is_not_false_success(tmp_path):
    c=load_config(ROOT/'configs/phase4e-rollback-delay.yaml')
    c=replace(c,phase4e=Phase4EConfig(True,routing_faults=(RoutingFault('partial-1','partial_rollback','ROLLING_BACK',100,candidate_weight=.05),)))
    path=run_experiment(c,tmp_path)
    summary=json.loads((path/'summary.json').read_text())
    assert summary['final_outcome']=='ROLLED_BACK' # command lifecycle only
    assert not summary['effective_contained'] and summary['effective_zero_candidate_time'] is None
    assert summary['final_effective_candidate_weight']==.05
    assert validate_run(path)['valid']


@pytest.mark.parametrize('kind',['observation_delay','observation_drop','misattribution'])
def test_observation_faults_and_ground_truth_immutability(tmp_path,kind):
    c=base()
    fault=ObservationFault('observer-1',kind,'system','candidate',1,1,delay_logical_units=9,reported_epoch=99 if kind=='misattribution' else None)
    c=replace(c,phase4e=Phase4EConfig(True,observation_faults=(fault,)))
    one=run_experiment(c,tmp_path)
    two=run_experiment(replace(c,phase4e=Phase4EConfig(True)),tmp_path)
    for name in ('system_evidence.jsonl','behavior_evidence.jsonl','behavior_comparisons.jsonl','behavior_probes.jsonl','requests.csv','events.jsonl'):
        assert (one/name).read_bytes()==(two/name).read_bytes()
    o=next(r for r in read(one,'observations.jsonl') if r['fault_active'] and r['observation_type']=='system')
    assert o['ground_truth_reference']['epoch']==c.rollout.epoch
    if kind=='observation_delay':
        assert o['observation_status']=='DELAYED'
        assert o['observed_age_logical_units']==o['ground_truth_age_at_evaluation']+9
    elif kind=='observation_drop':
        assert o['observation_status']=='DROPPED' and not o['available'] and o['observed_value'] is None
    else:
        assert o['observation_status']=='MISMATCHED' and o['observed_value']['epoch']==99
    assert validate_run(one)['valid']


def test_stale_healthy_view_over_regressed_truth(tmp_path):
    path=run_experiment(load_config(ROOT/'configs/phase4e-compound-behavior-stale.yaml'),tmp_path)
    current=next(r for r in read(path,'behavior_evidence.jsonl') if r['backend_role']=='candidate' and r['window_sequence']==2)
    observed=next(r for r in read(path,'observations.jsonl') if r['observation_type']=='behavior' and r['observation_status']=='STALE')
    assert current['accuracy']==.75 and observed['observed_value']['accuracy']==1
    assert observed['source_window_id']!=current['window_id']
    assert observed['observed_age_logical_units']>observed['ground_truth_age_at_evaluation']
    assert observed['logical_time']>observed['observed_value']['closed_at_logical_time']


def test_compound_request_faults_and_canonical_order(tmp_path):
    c=base()
    request_faults=(RequestFault('z-latency','latency',5,44,added_latency_ms=50),
                    RequestFault('a-failure','candidate_request_failure',5,44))
    c=replace(c,phase4e=Phase4EConfig(True,request_faults=request_faults))
    a=run_experiment(c,tmp_path)
    b=run_experiment(replace(c,phase4e=replace(c.phase4e,request_faults=tuple(reversed(request_faults)))),tmp_path)
    for name in ('requests.csv','events.jsonl','fault_events.jsonl','observations.jsonl','routing_states.jsonl','system_evidence.jsonl'):
        assert (a/name).read_bytes()==(b/name).read_bytes()
    candidates=[r for r in read(a,'system_evidence.jsonl') if r['backend_role']=='candidate' and r['window_type']=='CANARY']
    assert candidates[0]['error_rate']==1 and candidates[0]['latency_mean_ms']==62
    faults=read(a,'fault_events.jsonl')
    ids={r['fault_id'] for r in faults}
    assert ids=={'a-failure','z-latency','legacy-request-fault'}


@pytest.mark.parametrize('name',['routing-delay','stale-behavior-observation','dropped-system-observation','rollback-delay',
    'compound-behavior-stale','compound-routing-latency','compound-behavior-rollback-delay'])
def test_cli_smokes(tmp_path,name):
    result=subprocess.run([sys.executable,'-m','saferoll.cli','run','--config',str(ROOT/f'configs/phase4e-{name}.yaml'),
                           '--output-root',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    path=Path(json.loads(result.stdout)['run_dir'])
    assert validate_run(path)=={'valid':True,'issues':[]}
    c=load_config(ROOT/f'configs/phase4e-{name}.yaml')
    rerun=run_experiment(c,tmp_path)
    for artifact in ('requests.csv','events.jsonl','routing_states.jsonl','observations.jsonl','fault_events.jsonl',
                     'system_evidence.jsonl','behavior_evidence.jsonl','summary.json'):
        assert (path/artifact).read_bytes()==(rerun/artifact).read_bytes()


@pytest.mark.parametrize('group,fault',[('routing_faults',{'id':'x','type':'unknown','start_state':'CANARY','duration_requests':1}),
    ('routing_faults',{'id':'x','type':'partial_rollout','start_state':'CANARY','duration_requests':0}),
    ('routing_faults',{'id':'x','type':'partial_rollout','start_state':'CANARY','duration_requests':1,'candidate_weight':1.2}),
    ('routing_faults',{'id':'x','type':'partial_rollout','start_state':'CANARY','duration_requests':True}),
    ('routing_faults',{'id':'x','type':'rollback_propagation_delay','start_state':'CANARY','duration_requests':1}),
    ('observation_faults',{'id':'x','type':'stale_evidence','source':'system','target':'candidate','start_window':0,'duration_windows':1}),
    ('observation_faults',{'id':'x','type':'observation_delay','source':'system','target':'candidate','start_window':1,'duration_windows':1}),
    ('observation_faults',{'id':'x','type':'observation_drop','source':'unknown','target':'candidate','start_window':1,'duration_windows':1}),
    ('observation_faults',{'id':'x','type':'observation_drop','source':'system','target':'candidate','start_window':99,'duration_windows':1}),
    ('request_faults',{'id':'x','type':'latency','start_request':0,'end_request':999}),
    ('behavior_faults',{'id':'x','type':'unknown','target':'candidate','start_window':1,'duration_windows':1})])
def test_invalid_fault_config(group,fault):
    data=base().to_dict();data['phase4e']={'enabled':True,group:[fault]}
    with pytest.raises(ValueError):
        config_from_dict(data)


def test_unique_ids_and_disabled_controls():
    fault={'id':'same','type':'partial_rollout','start_state':'CANARY','duration_requests':1}
    data=base().to_dict();data['phase4e']={'enabled':True,'routing_faults':[fault,fault]}
    with pytest.raises(ValueError):
        config_from_dict(data)
    data['phase4e']={'enabled':False,'routing_faults':[fault]}
    with pytest.raises(ValueError):
        config_from_dict(data)


def test_controller_delivery_boundary_and_drop_fraction(tmp_path):
    from saferoll.observations import controller_view_at
    c=base()
    faults=(ObservationFault('a-delay','observation_delay','system','candidate',1,1,delay_logical_units=9),
            ObservationFault('z-drop','observation_drop','behavior','candidate',0,4,drop_every_nth_record=2))
    c=replace(c,phase4e=Phase4EConfig(True,observation_faults=faults))
    path=run_experiment(c,tmp_path)
    observations=read(path,'observations.jsonl')
    delayed=next(o for o in observations if o['observation_status']=='DELAYED')
    delivery=delayed['logical_time']
    assert delayed['observation_id'] not in {o['observation_id'] for o in controller_view_at(observations,delivery-1)}
    assert delayed['observation_id'] in {o['observation_id'] for o in controller_view_at(observations,delivery)}
    visible=controller_view_at(observations,delivery)
    visible[0]['observed_value']={'tampered':True}
    assert observations[0]['observed_value']!={'tampered':True}
    dropped=[o for o in observations if o['observation_type']=='behavior' and o['observation_status']=='DROPPED']
    assert len(dropped)==2 and all(o['observed_value'] is None for o in dropped)


def test_compound_behavior_rollback_and_latency_missing(tmp_path):
    path=run_experiment(load_config(ROOT/'configs/phase4e-compound-behavior-rollback-delay.yaml'),tmp_path)
    evidence=read(path,'behavior_evidence.jsonl')
    assert all(r['accuracy']==.75 for r in evidence if r['backend_role']=='candidate')
    summary=json.loads((path/'summary.json').read_text())
    assert summary['rollback_requested_time']<summary['last_post_rollback_candidate_time']<=summary['effective_zero_candidate_time']
    path=run_experiment(load_config(ROOT/'configs/phase4e-dropped-system-observation.yaml'),tmp_path)
    truth=next(r for r in read(path,'system_evidence.jsonl') if r['window_sequence']==1 and r['backend_role']=='candidate')
    assert truth['latency_p95_ms']==62
    view=next(o for o in read(path,'observations.jsonl') if o['observation_type']=='system' and o['observation_status']=='DROPPED')
    assert view['ground_truth_reference']['window_id']==truth['window_id'] and not view['available']


def test_dropped_observation_is_absorbing_in_compound_view(tmp_path):
    c=load_config(ROOT/'configs/phase4e-compound-behavior-stale.yaml')
    faults=(ObservationFault('a-drop','observation_drop','behavior','candidate',2,1),
            ObservationFault('z-stale','stale_evidence','behavior','candidate',2,1,source_window=0))
    c=replace(c,phase4e=replace(c.phase4e,observation_faults=faults))
    path=run_experiment(c,tmp_path)
    observation=next(o for o in read(path,'observations.jsonl') if 'a-drop' in o['fault_ids'])
    assert observation['observation_status']=='DROPPED' and observation['observed_value'] is None
    assert observation['fault_ids']==['a-drop','z-stale']


def test_partial_faults_cannot_increase_exposure(tmp_path):
    c=with_routing('partial_rollout',weight=.9)
    with pytest.raises(ValueError,match='partial rollout cannot exceed'):
        run_experiment(c,tmp_path)
    c=load_config(ROOT/'configs/phase4e-rollback-delay.yaml')
    c=replace(c,phase4e=Phase4EConfig(True,routing_faults=(RoutingFault('partial-1','partial_rollback','ROLLING_BACK',5,candidate_weight=.9),)))
    with pytest.raises(ValueError,match='partial rollback cannot exceed'):
        run_experiment(c,tmp_path)


def test_no_fault_three_plane_convergence(tmp_path):
    path=run_experiment(base(),tmp_path)
    for r in read(path,'routing_states.jsonl'):
        assert r['desired_state']==r['observed_state']==r['effective_state']
        assert r['desired_candidate_weight']==r['observed_candidate_weight']==r['effective_candidate_weight']
        assert r['desired_observed_match'] and r['desired_effective_match'] and r['observed_effective_match']
