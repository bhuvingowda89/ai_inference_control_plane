from dataclasses import replace
import pytest
from saferoll.config import config_from_dict
from saferoll.behavior.workload import build_workload
from saferoll.behavior.evaluator import evaluate_output
from saferoll.backends.mock import MockBackend


def enabled(config,task='classification'):
    return replace(config,behavior_evidence=replace(config.behavior_evidence,enabled=True,task_type=task))


def test_workload_identity_ground_truth_and_seed(config):
    c=enabled(config)
    a,b=build_workload(c),build_workload(c)
    assert a==b and len(a['items'])==20
    assert len({i['item_id'] for i in a['items']})==20
    assert a['items'][0]['input']=={'number':101}
    assert a['items'][0]['expected_label']=='odd'
    assert a['items'][1]['expected_label']=='even'
    other=build_workload(replace(c,experiment=replace(c.experiment,seed=102)))
    assert other['items'][0]['item_id']!=a['items'][0]['item_id']
    assert build_workload(config)['items']==[]


def test_classification_and_profiles(config):
    c=enabled(config)
    items=build_workload(c)['items']
    correct=MockBackend('baseline',c.baseline)
    regressed=MockBackend('candidate',replace(c.candidate,behavior_profile='classification_regression'))
    assert all(evaluate_output(i,correct.infer_behavior(i))['correct'] for i in items)
    outputs=[regressed.infer_behavior(i) for i in items]
    assert sum(evaluate_output(i,o)['correct'] for i,o in zip(items,outputs))==15
    assert evaluate_output(items[0],' odd ')['correct'] is False # exact labels, no normalization
    assert outputs==[regressed.infer_behavior(i) for i in items]


@pytest.mark.parametrize('output,valid,exact', [
    ('{"number":101,"parity":"odd"}',True,True), ('{ "parity": "odd", "number": 101 }',True,True),
    ('{"number":102,"parity":"odd"}',True,False), ('{"number":101}',False,False),
    ('{bad',False,False), ('{"number":true,"parity":"odd"}',False,False),
    ('{"number":101,"parity":"odd","extra":0}',False,False),
    ('{"number":101,"number":101,"parity":"odd"}',False,False),
    ('[101,"odd"]',False,False), ('null',False,False),
    ('{"number":101.0,"parity":"odd"}',False,False),
    ('{"number":NaN,"parity":"odd"}',False,False),
])
def test_structured_validation(config,output,valid,exact):
    item=build_workload(enabled(config,'structured_output'))['items'][0]
    assert evaluate_output(item,output)=={'correct':None,'structure_valid':valid,'exact_match':exact}


def test_structured_profile_modes(config):
    c=enabled(config,'structured_output')
    backend=MockBackend('candidate',replace(c.candidate,behavior_profile='structured_output_regression'))
    items=build_workload(c)['items']
    results=[evaluate_output(i,backend.infer_behavior(i)) for i in items]
    assert sum(r['structure_valid'] for r in results)==17
    assert sum(r['exact_match'] for r in results)==15
    assert backend.infer_behavior(items[11])=='{malformed'


@pytest.mark.parametrize('field,value', [('enabled','true'),('enabled',1),('task_type','freeform'),('workload_id',''),
    ('item_count',0),('item_count',True),('baseline_items_per_window',-1),('candidate_items_per_window',21),
    ('minimum_samples_per_revision',0),('minimum_paired_samples',0),('max_age_logical_units',-1)])
def test_behavior_config_validation(config,field,value):
    data=config.to_dict()
    data['behavior_evidence'][field]=value
    with pytest.raises(ValueError):
        config_from_dict(data)


@pytest.mark.parametrize('field,value',[('behavior_profile','unknown'),('regression_every_nth_item',0),
    ('regression_every_nth_item',True)])
def test_profile_config_validation(config,field,value):
    data=config.to_dict();data['candidate'][field]=value
    with pytest.raises(ValueError):
        config_from_dict(data)


def test_profile_task_mismatch_and_partial_config(config):
    data=config.to_dict()
    data['behavior_evidence']['enabled']=True
    data['candidate']['behavior_profile']='structured_output_regression'
    with pytest.raises(ValueError):
        config_from_dict(data)
    data=config.to_dict();data['behavior_evidence']={'enabled':True}
    with pytest.raises(ValueError):
        config_from_dict(data)
    data=config.to_dict();data['behavior_evidence']=None
    with pytest.raises(ValueError):
        config_from_dict(data)
