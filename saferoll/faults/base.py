"""Minimal extension interface for deterministic faults."""
from __future__ import annotations
from typing import Protocol
from ..schemas import Response


class FaultInjector(Protocol):
    def before_request(self, request_index: int) -> list[str]: ...
    def modify_response(self, response: Response) -> Response: ...
    def finish(self) -> list[str]: ...
