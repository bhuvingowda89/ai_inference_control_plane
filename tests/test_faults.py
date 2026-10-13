from dataclasses import replace
from saferoll.faults.latency import LatencyFault
from saferoll.schemas import Response


def test_interval_target_output_and_clear(config):
    fault = LatencyFault(config.fault)
    template = Response(0, "id", "rollout", 1, "candidate-v2", "candidate-v2",
                        "candidate", True, 12, False, "unchanged")
    transitions = []
    for index in range(20):
        transitions += [(index, kind) for kind in fault.before_request(index)]
        response = fault.modify_response(replace(template, request_index=index))
        assert response.fault_active == (8 <= index <= 12)
        assert response.latency_ms == (62 if 8 <= index <= 12 else 12)
        assert response.output == "unchanged"
        baseline = replace(template, request_index=index, backend_role="baseline")
        assert fault.modify_response(baseline) == baseline
    assert transitions == [(8, "FAULT_ACTIVATED"), (13, "FAULT_CLEARED")]
    assert fault.finish() == []


def test_clear_when_interval_ends_with_run(config):
    fault = LatencyFault(replace(config.fault, start_request=0, end_request=0))
    assert fault.before_request(0) == ["FAULT_ACTIVATED"]
    assert fault.finish() == ["FAULT_CLEARED"]
    assert fault.finish() == []
