"""CPU-only inference with simulated latency and deterministic text."""
from __future__ import annotations
from ..config import BackendConfig
from ..schemas import Request, Response


class MockBackend:
    def __init__(self, role: str, config: BackendConfig) -> None:
        if role not in {"baseline", "candidate"}:
            raise ValueError("unknown backend role")
        self.role = role
        self.config = config

    def infer(self, request: Request) -> Response:
        if request.backend_role != self.role or request.intended_revision != self.config.revision:
            raise ValueError("request does not match backend")
        return Response(request.request_index, request.request_id, request.rollout_id,
                        request.epoch, request.intended_revision, self.config.revision,
                        self.role, True, self.config.fixed_latency_ms, False,
                        f"{self.config.response_prefix}:{request.payload}", request.desired_rollout_state,
                        request.observed_rollout_state, request.transition_index)
