from dataclasses import replace
import pytest
from saferoll.config import config_from_dict
from saferoll.faults.failure import CandidateRequestFailure
from saferoll.schemas import Response


def test_failure_interval_target_and_output(config):
    fault=CandidateRequestFailure(replace(config.fault,type='candidate_request_failure',added_latency_ms=0))
    template=Response(0,'id','rollout',1,'candidate-v2','candidate-v2','candidate',True,12,False,'unchanged')
    transitions=[]
    for index in range(20):
        transitions.extend((index,t) for t in fault.before_request(index))
        response=fault.modify_response(replace(template,request_index=index))
        assert response.success == (not 8<=index<=12)
        assert response.fault_active == (8<=index<=12)
        assert response.latency_ms==12 and response.output=='unchanged'
        baseline=replace(template,request_index=index,backend_role='baseline')
        assert fault.modify_response(baseline)==baseline
    assert transitions==[(8,'FAULT_ACTIVATED'),(13,'FAULT_CLEARED')]
    assert fault.finish()==[]


def test_failure_config_validation(config):
    data=config.to_dict()
    data['fault']['type']='candidate_request_failure'
    with pytest.raises(ValueError,match='added_latency_ms'):
        config_from_dict(data)
    data['fault']['added_latency_ms']=0
    assert config_from_dict(data).fault.type=='candidate_request_failure'
