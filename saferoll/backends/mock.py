"""CPU-only inference with simulated latency and deterministic text."""
from __future__ import annotations
import json
from typing import Any
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
                        request.observed_rollout_state, request.transition_index, request.routing_state_index)

    def infer_behavior(self, item: dict[str, Any]) -> str:
        """Explicit profiles alter only behavior probes; ordinary serving is unchanged."""
        structured = item['task_type'] == 'structured_output'
        expected = item['expected_output']
        degraded = (self.config.behavior_profile == item['task_type'] + '_regression'
                    and (item['item_index'] + 1) % self.config.regression_every_nth_item == 0)
        if not structured:
            return ('odd' if expected == 'even' else 'even') if degraded else expected
        value = dict(expected)
        if degraded:
            mode = ((item['item_index'] + 1) // self.config.regression_every_nth_item - 1) % 3
            if mode == 0:
                del value['parity']
            elif mode == 1:
                value['number'] += 1
            else:
                return '{malformed'
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
