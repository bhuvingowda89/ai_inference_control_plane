from dataclasses import asdict,replace,fields,FrozenInstanceError
import json
from pathlib import Path
import pytest
from saferoll.config import load_config,config_from_dict
from saferoll.research.configuration import POLICY_NAMES,parse_policy
from saferoll.research.inputs import (PolicyInput,ReadinessInput,OpsInput,BehaviorInput,AIInput,SafeRollInput,
                                     input_from_dict)
from saferoll.research.policies import build_policy,Reason
from saferoll.research.projection import project_input
from saferoll.research.matrix import with_policy
from saferoll.runner import run_experiment,simulate

ROOT=Path(__file__).parents[1]


@pytest.fixture
def fixture_run(tmp_path):
    c=load_config(ROOT/'configs/phase4f-healthy.yaml')
    return c,run_experiment(c,tmp_path)


@pytest.fixture
def visible(fixture_run):
    _,path=fixture_run
    return input_from_dict(json.loads((path/'policy_inputs.jsonl').read_text().splitlines()[0]))


def capability(v,name):
    common={f.name:getattr(v,f.name) for f in fields(PolicyInput)};common['policy_name']=name
    if name=='readiness_gate':
        c=v.system[1]
        return ReadinessInput(**common,system_observation_id=c.observation_id,candidate_available=c.available,
            candidate_sample_count=c.sample_count,candidate_success_rate=c.success_rate)
    if name=='ops_gate':return OpsInput(**common,system=v.system)
    if name=='behavior_gate':return BehaviorInput(**common,behavior=v.behavior)
    if name=='ai_gate':return AIInput(**common,system=v.system,behavior=v.behavior)
    return replace(v,policy_name=name)


@pytest.mark.parametrize('name',POLICY_NAMES)
def test_healthy_all_policies(visible,name):
    cfg=replace(parse_policy({}),type=name)
    result=build_policy(cfg).decide(capability(visible,name))
    assert result.decision=='PROMOTE' and result.reason_codes==(Reason.ALL_REQUIRED_EVIDENCE_ACCEPTABLE,)


def test_capabilities_are_closed_and_immutable(visible):
    ops=capability(visible,'ops_gate');beh=capability(visible,'behavior_gate');ai=capability(visible,'ai_gate')
    assert not hasattr(ops,'behavior') and not hasattr(beh,'system')
    assert not hasattr(ai,'observed_candidate_weight')
    for value in (ops,beh,ai,visible):
        for hidden in ('config','fault_active','faults','effective_state','effective_candidate_weight','ground_truth','oracle','expected_decision'):
            assert not hasattr(value,hidden)
        with pytest.raises(FrozenInstanceError):value.epoch=999
    with pytest.raises(TypeError):
        build_policy(replace(parse_policy({}),type='ops_gate')).decide(visible)
    with pytest.raises(TypeError):
        build_policy(replace(parse_policy({}),type='ai_gate')).decide(visible)
    data=asdict(ops);data['behavior']=asdict(visible)['behavior']
    with pytest.raises(ValueError,match='boundary'):input_from_dict(data)
    data=asdict(visible);data['system'][0]['fault_active']=True
    with pytest.raises(ValueError,match='boundary'):input_from_dict(data)


@pytest.mark.parametrize('value,action',[(.95,'PROMOTE'),(.9499999,'ROLLBACK')])
def test_accuracy_boundary(visible,value,action):
    candidate=replace(visible.behavior[1],accuracy=value)
    v=replace(visible,behavior=(visible.behavior[0],candidate))
    assert build_policy(parse_policy({'type':'saferoll'})).decide(v).decision==action


@pytest.mark.parametrize('value,action',[(12,'PROMOTE'),(12.000001,'ROLLBACK')])
def test_latency_boundary(visible,value,action):
    candidate=replace(visible.system[1],latency_p95_ms=value)
    v=replace(visible,system=(visible.system[0],candidate))
    assert build_policy(parse_policy({'type':'saferoll'})).decide(v).decision==action


@pytest.mark.parametrize('value,action',[(.05,'PROMOTE'),(.0500001,'ROLLBACK')])
def test_error_rate_boundary(visible,value,action):
    candidate=replace(visible.system[1],error_rate=value)
    v=replace(visible,system=(visible.system[0],candidate))
    assert build_policy(parse_policy({'type':'saferoll'})).decide(v).decision==action


@pytest.mark.parametrize('field,value,action',[('structure_valid_rate',.95,'PROMOTE'),('structure_valid_rate',.9499999,'ROLLBACK'),
    ('exact_match_rate',.95,'PROMOTE'),('exact_match_rate',.9499999,'ROLLBACK')])
def test_structured_boundaries(visible,field,value,action):
    baseline=replace(visible.behavior[0],task_type='structured_output',accuracy=None,exact_match_rate=1,structure_valid_rate=1)
    candidate=replace(visible.behavior[1],task_type='structured_output',accuracy=None,exact_match_rate=1,structure_valid_rate=1)
    candidate=replace(candidate,**{field:value})
    v=replace(visible,behavior=(baseline,candidate))
    assert build_policy(parse_policy({'type':'saferoll'})).decide(v).decision==action


def test_reliable_harm_dominates_mismatch_but_stale_harm_does_not(visible):
    candidate=replace(visible.behavior[1],accuracy=.75)
    v=replace(visible,behavior=(visible.behavior[0],candidate),observed_candidate_weight=.2)
    policy=build_policy(parse_policy({'type':'saferoll'}))
    result=policy.decide(v)
    assert result.decision=='ROLLBACK' and result.reason_codes[0]==Reason.BEHAVIOR_REGRESSION
    assert Reason.ROUTING_STATE_MISMATCH in result.reason_codes
    v=replace(v,behavior=(v.behavior[0],replace(candidate,fresh=False)))
    result=policy.decide(v)
    assert result.decision=='HOLD' and Reason.STALE_BEHAVIOR_EVIDENCE in result.reason_codes


def test_identity_precedes_harm_and_other_channel_uncertainty(visible):
    v=replace(visible,behavior=(visible.behavior[0],replace(visible.behavior[1],accuracy=.75,epoch=99)))
    policy=build_policy(parse_policy({'type':'saferoll'}))
    assert policy.decide(v).reason_codes==(Reason.IDENTITY_UNCERTAINTY,)
    v=replace(visible,behavior=(visible.behavior[0],replace(visible.behavior[1],accuracy=.75)),
              system=(visible.system[0],replace(visible.system[1],available=False)))
    assert policy.decide(v).decision=='ROLLBACK'
    assert Reason.MISSING_SYSTEM_OBSERVATION in policy.decide(v).reason_codes


@pytest.mark.parametrize('kind,reason',[('missing',Reason.MISSING_BEHAVIOR_OBSERVATION),('insufficient',Reason.INSUFFICIENT_BEHAVIOR_EVIDENCE),('stale',Reason.STALE_BEHAVIOR_EVIDENCE)])
def test_uncertainty_is_hold(visible,kind,reason):
    changes={'missing':{'available':False},'insufficient':{'sufficient':False,'sample_count':2},'stale':{'fresh':False}}
    v=replace(visible,behavior=(visible.behavior[0],replace(visible.behavior[1],**changes[kind])))
    for name in ('ai_gate','saferoll'):
        result=build_policy(parse_policy({'type':name})).decide(capability(v,name))
        assert result.decision=='HOLD' and reason in result.reason_codes


def test_readiness_genuine_catastrophic_gate(visible):
    v=capability(visible,'readiness_gate')
    policy=build_policy(parse_policy({'type':'readiness_gate'}))
    assert policy.decide(replace(v,candidate_success_rate=.4)).decision=='ROLLBACK'
    assert policy.decide(replace(v,candidate_success_rate=.5)).decision=='PROMOTE'
    assert policy.decide(replace(v,candidate_available=False)).decision=='HOLD'


def test_policy_overrides_ignore_excluded_channels(visible):
    altered=replace(visible,behavior=(visible.behavior[0],replace(visible.behavior[1],accuracy=0)),
                    system=(visible.system[0],replace(visible.system[1],error_rate=1)))
    assert capability(visible,'ops_gate')==capability(replace(visible,behavior=altered.behavior),'ops_gate')
    assert capability(visible,'behavior_gate')==capability(replace(visible,system=altered.system),'behavior_gate')
    assert build_policy(parse_policy({'type':'ops_gate'})).decide(capability(replace(visible,behavior=altered.behavior),'ops_gate')).decision=='PROMOTE'
    assert build_policy(parse_policy({'type':'behavior_gate'})).decide(capability(replace(visible,system=altered.system),'behavior_gate')).decision=='PROMOTE'


def test_oracle_not_called_during_execution(monkeypatch):
    from saferoll.research import oracle
    def forbidden(*args,**kwargs):raise AssertionError('oracle accessed during execution')
    monkeypatch.setattr(oracle,'evaluate_decisions',forbidden)
    c=load_config(ROOT/'configs/phase4f-silent-behavior-regression.yaml')
    _,events,_=simulate(c)
    assert next(e.details['decision'] for e in events if e.event_type=='DECISION_MADE')=='ROLLBACK'


@pytest.mark.parametrize('data',[{'type':'unknown'},{'system':{'max_error_rate':-1}}, {'system':{'max_error_rate':True}},
    {'behavior':{'max_accuracy_drop':1.1}}, {'behavior':{'max_accuracy_drop':float('nan')}},
    {'evidence':{'require_fresh_behavior':'true'}}, {'consistency':{'require_observed_routing_match':1}},
    {'numeric_tolerance':1}, {'behavior':{'learn_threshold':True}}])
def test_invalid_policy_config(data):
    with pytest.raises(ValueError):parse_policy(data)


def test_policy_import_boundary_and_full_config_rejected():
    import ast,inspect
    from saferoll.research import policies
    tree=ast.parse(inspect.getsource(policies))
    imports={node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)}
    assert not imports.intersection({'oracle','config','runner','planes','observations','artifacts'})
    c=load_config(ROOT/'configs/phase4f-healthy.yaml')
    with pytest.raises(TypeError):policies.SafeRoll(c)


def test_projection_never_uses_hidden_flags_or_future_payloads(fixture_run):
    from copy import deepcopy
    c,path=fixture_run
    observations=[json.loads(line) for line in (path/'observations.jsonl').read_text().splitlines()]
    p=json.loads((path/'policy_inputs.jsonl').read_text().splitlines()[0])
    routing=[json.loads(line) for line in (path/'routing_states.jsonl').read_text().splitlines()]
    desired=[r for r in routing if r['logical_time']<=p['logical_time']][-1]
    def project(data):
        return project_input(c.identity,'saferoll',p['logical_time'],p['expected_window_id'],p['transition_index'],data,
            desired['desired_state'],desired['desired_candidate_weight'],c.evidence.max_age_logical_units,c.behavior_evidence.max_age_logical_units)
    changed=deepcopy(observations)
    for o in changed:
        o['fault_active']=True;o['fault_ids']=['secret'];o['observation_status']='MISMATCHED'
        o['ground_truth_reference']={'secret':'hidden'}
    assert project(changed)==project(observations)
    for o in changed:
        o['logical_time']+=10000
    future=project(changed)
    assert not any(r.available for r in future.system+future.behavior)
    assert future.observed_candidate_weight is None


def test_tolerance_band_and_zero_latency_baseline(visible):
    cfg=parse_policy({'type':'saferoll'})
    candidate=replace(visible.behavior[1],accuracy=.95-5e-10)
    assert build_policy(cfg).decide(replace(visible,behavior=(visible.behavior[0],candidate))).decision=='PROMOTE'
    baseline=replace(visible.system[0],latency_p95_ms=0)
    candidate=replace(visible.system[1],latency_p95_ms=0)
    v=replace(visible,system=(baseline,candidate))
    assert build_policy(cfg).decide(v).decision=='PROMOTE'
    assert build_policy(cfg).decide(replace(v,system=(baseline,replace(candidate,latency_p95_ms=1e-8)))).decision=='ROLLBACK'


def test_threshold_changes_have_distinct_decision_identity(visible):
    a=build_policy(parse_policy({'type':'saferoll'})).decide(visible)
    b=build_policy(parse_policy({'type':'saferoll','system':{'max_latency_regression_fraction':.1}})).decide(visible)
    assert a.input_snapshot_id==b.input_snapshot_id
    assert a.thresholds_sha256!=b.thresholds_sha256 and a.decision_id!=b.decision_id
    assert a.decision=='PROMOTE' and b.decision=='ROLLBACK'


def test_accuracy_equality_with_zero_tolerance(visible):
    c=parse_policy({'type':'saferoll','numeric_tolerance':0})
    v=replace(visible,behavior=(visible.behavior[0],replace(visible.behavior[1],accuracy=.95)))
    assert build_policy(c).decide(v).decision=='PROMOTE'
