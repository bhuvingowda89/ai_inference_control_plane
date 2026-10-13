"""Minimal candidate failure injection using the existing interval lifecycle."""
from __future__ import annotations
from dataclasses import replace
from .latency import LatencyFault
from ..schemas import Response


class CandidateRequestFailure(LatencyFault):
    """Mark candidate requests unsuccessful; preserve output and simulated latency."""
    def modify_response(self, response: Response) -> Response:
        affected = (self.active and response.backend_role == self.config.target
                    and self.config.start_request <= response.request_index <= self.config.end_request)
        return replace(response, success=False, fault_active=True) if affected else response
