from dataclasses import replace
import pytest
from saferoll.backends.mock import MockBackend
from saferoll.schemas import Request


def test_deterministic_mock(config):
    request = Request(4, "id", config.rollout.rollout_id, 1, "candidate-v2", None,
                      "candidate", "request-000004")
    backend = MockBackend("candidate", config.candidate)
    response = backend.infer(request)
    assert response == backend.infer(request)
    assert response.output == "candidate:request-000004"
    assert response.latency_ms == 12
    assert response.success and response.served_revision == request.intended_revision
    with pytest.raises(ValueError):
        backend.infer(replace(request, intended_revision="other"))
    with pytest.raises(ValueError):
        MockBackend("unknown", config.candidate)
