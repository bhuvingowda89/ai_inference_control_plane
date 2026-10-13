from dataclasses import FrozenInstanceError, replace
import pytest
from saferoll.config import EvidenceConfig, config_from_dict
from saferoll.evidence import EvidenceCollector, percentile
from saferoll.ids import request_id
from saferoll.schemas import Response


def sample(config, index, role='candidate', latency=12, success=True, state='CANARY', transition=3):
    revision = getattr(config, role).revision
    return Response(index, request_id(config.identity, index), config.rollout.rollout_id, config.rollout.epoch,
                    revision, revision, role, success, latency, False, 'output', state, state, transition)


@pytest.mark.parametrize('values,p,expected', [
    ([],50,None), ([12],95,12), ([10,20],50,15), ([10,20],95,19.5),
    ([30,0,20,10],50,15), ([30,0,20,10],95,28.5), ([0,10,20,30],0,0),
    ([0,10,20,30],100,30), ([7,7,7],95,7),
])
def test_percentile_definition(values,p,expected):
    assert percentile(values,p) == expected


@pytest.mark.parametrize('values,p', [([1],-1), ([1],101), ([1],float('nan')), ([-1],50), ([float('inf')],50)])
def test_invalid_percentiles(values,p):
    with pytest.raises(ValueError):
        percentile(values,p)


def test_aggregation_per_revision_and_freshness(config):
    collector=EvidenceCollector(config.identity,EvidenceConfig(3,10))
    collector.open_window('CANARY',3,0,7)
    for index, latency in enumerate([0,10,20,30]):
        collector.add_request(sample(config,index,latency=latency,success=index!=0),index+1)
    for index, latency in enumerate([1,2,3],4):
        collector.add_request(sample(config,index,role='baseline',latency=latency),index+1)
    baseline,candidate=collector.close_window(8)
    assert baseline.revision==config.baseline.revision and candidate.revision==config.candidate.revision
    assert baseline.latency_mean_ms==2 and candidate.latency_mean_ms==15
    assert candidate.latency_p50_ms==15 and candidate.latency_p95_ms==28.5
    assert candidate.request_count==candidate.sample_count==4
    assert candidate.success_count==3 and candidate.failure_count==1
    assert candidate.success_rate==.75 and candidate.error_rate==.25
    assert candidate.age_at_evaluation==4 and baseline.age_at_evaluation==1
    assert candidate.sufficient and baseline.sufficient
    assert candidate.freshness_status=='FRESH'
    with pytest.raises(FrozenInstanceError):
        candidate.sufficient=False
    old=collector.get_evidence()
    later=collector.get_evidence(18)
    assert later[1].age_at_evaluation==14 and later[1].freshness_status=='STALE'
    assert collector.get_evidence()==old
    assert collector.get_evidence(14)[1].freshness_status=='FRESH' # inclusive maximum age
    with pytest.raises(ValueError):
        collector.get_evidence(7)


def test_zero_and_insufficient_samples(config):
    collector=EvidenceCollector(config.identity,EvidenceConfig())
    collector.open_window('CANARY',3,0,1)
    collector.add_request(sample(config,0),1)
    baseline,candidate=collector.close_window(2)
    assert candidate.sample_count==1 and not candidate.sufficient
    assert baseline.sample_count==0 and not baseline.sufficient
    assert baseline.latest_sample_logical_time is None and baseline.age_at_evaluation is None
    assert baseline.freshness_status=='NO_SAMPLES'
    for name in ['success_rate','error_rate','latency_mean_ms','latency_min_ms','latency_max_ms','latency_p50_ms','latency_p95_ms']:
        assert getattr(baseline,name) is None
    collector.open_window('EVALUATING',4,3,0)
    assert all(r.sample_count==0 for r in collector.close_window(4))


@pytest.mark.parametrize('field,value', [('epoch',2), ('rollout_id','other'), ('served_revision','other'),
    ('backend_role','baseline'), ('observed_rollout_state','HOLDING'), ('transition_index',4),
    ('latency_ms',float('nan')), ('success','True')])
def test_contaminated_samples_rejected(config,field,value):
    collector=EvidenceCollector(config.identity,EvidenceConfig())
    collector.open_window('CANARY',3,0,1)
    with pytest.raises(ValueError):
        collector.add_request(replace(sample(config,0),**{field:value}),1)


def test_boundaries_and_duplicate_samples(config):
    collector=EvidenceCollector(config.identity,EvidenceConfig())
    with pytest.raises(ValueError):
        collector.add_request(sample(config,0),1)
    collector.open_window('CANARY',3,0,1)
    with pytest.raises(ValueError):
        collector.open_window('CANARY',3,0,1)
    with pytest.raises(ValueError):
        collector.close_window(1)
    collector.add_request(sample(config,0),1)
    with pytest.raises(ValueError):
        collector.add_request(sample(config,1),2)
    with pytest.raises(ValueError):
        collector.close_window(1)
    collector.close_window(2)
    collector.open_window('EVALUATING',4,3,1)
    with pytest.raises(ValueError):
        collector.add_request(sample(config,0,state='EVALUATING',transition=4),4)
    with pytest.raises(ValueError):
        collector.add_request(sample(config,1,state='EVALUATING',transition=4),3)


@pytest.mark.parametrize('key,value', [('minimum_samples_per_revision',0), ('minimum_samples_per_revision',-1),
    ('minimum_samples_per_revision',True), ('max_age_logical_units',-1), ('max_age_logical_units',1.5),
    ('max_age_logical_units',True)])
def test_config_invalid(config,key,value):
    data=config.to_dict()
    data['evidence'][key]=value
    with pytest.raises(ValueError):
        config_from_dict(data)


def test_config_null_and_unknown_fields(config):
    for evidence in [None, {}, {'minimum_samples_per_revision':3,'max_age_logical_units':10,'max_p95':50}]:
        data=config.to_dict(); data['evidence']=evidence
        with pytest.raises(ValueError):
            config_from_dict(data)


def test_delayed_closure_view_and_invalid_evaluation(config):
    collector=EvidenceCollector(config.identity,EvidenceConfig(1,10))
    collector.open_window('CANARY',3,0,1)
    collector.add_request(sample(config,0),1)
    with pytest.raises(ValueError):
        collector.close_window(2,1)
    _,candidate=collector.close_window(2,15)
    assert candidate.closed_at_logical_time==2 and candidate.evaluated_at_logical_time==15
    assert candidate.latest_sample_logical_time==1 and candidate.age_at_evaluation==14
    assert candidate.sufficient and candidate.freshness_status=='STALE'
