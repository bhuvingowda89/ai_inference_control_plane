"""Frozen request, response, event and run-state schemas."""
from __future__ import annotations
from dataclasses import dataclass, fields
from typing import Any

SCHEMA_VERSION = "1.0"
EVENT_TYPES = {"RUN_STARTED", "REQUEST_CREATED", "REQUEST_ROUTED", "FAULT_ACTIVATED",
               "FAULT_CLEARED", "REQUEST_COMPLETED", "RUN_COMPLETED"}


@dataclass(frozen=True)
class Request:
    request_index: int
    request_id: str
    rollout_id: str
    epoch: int
    intended_revision: str
    served_revision: str | None
    backend_role: str
    payload: str


@dataclass(frozen=True)
class Response:
    request_index: int
    request_id: str
    rollout_id: str
    epoch: int
    intended_revision: str
    served_revision: str
    backend_role: str
    success: bool
    latency_ms: float
    fault_active: bool
    output: str


@dataclass(frozen=True)
class Event:
    event_index: int
    event_type: str
    rollout_id: str
    epoch: int
    baseline_revision: str
    candidate_revision: str
    request_id: str | None
    intended_revision: str | None
    served_revision: str | None
    logical_time: int
    details: dict[str, Any]


@dataclass(frozen=True)
class RunState:
    rollout_id: str
    epoch: int
    baseline_revision: str
    candidate_revision: str
    completed_requests: int


REQUEST_COLUMNS = [field.name for field in fields(Response)]
