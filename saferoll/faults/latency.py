from __future__ import annotations
from dataclasses import replace
from ..config import FaultConfig
from ..schemas import Response


class LatencyFault:
    """Inclusive zero-based interval; clear before end+1, or at run end."""
    def __init__(self, config: FaultConfig) -> None:
        self.config = config
        self.active = False

    def before_request(self, request_index: int) -> list[str]:
        if request_index == self.config.start_request:
            self.active = True
            return ["FAULT_ACTIVATED"]
        if self.active and request_index > self.config.end_request:
            self.active = False
            return ["FAULT_CLEARED"]
        return []

    def modify_response(self, response: Response) -> Response:
        affected = (self.active and response.backend_role == self.config.target
                    and self.config.start_request <= response.request_index <= self.config.end_request)
        return replace(response, latency_ms=response.latency_ms + self.config.added_latency_ms,
                       fault_active=True) if affected else response

    def finish(self) -> list[str]:
        if self.active:
            self.active = False
            return ["FAULT_CLEARED"]
        return []
