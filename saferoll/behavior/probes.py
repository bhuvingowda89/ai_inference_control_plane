"""Separate paired probes bypass serving routing and serving-index faults."""
from __future__ import annotations
from typing import Any
from ..backends.mock import MockBackend
from ..config import Config
from ..evidence import window_id
from ..lifecycle import RolloutSnapshot
from .workload import BEHAVIOR_SCHEMA_VERSION, probe_id


def generate_probe(config: Config, snapshot: RolloutSnapshot, window_sequence: int,
                   item: dict[str, Any], role: str, logical_time: int) -> dict[str, Any]:
    backend = MockBackend(role, getattr(config, role))
    window = window_id(config.identity, snapshot.transition_index, window_sequence)
    output = backend.infer_behavior(item)
    return {"schema_version": BEHAVIOR_SCHEMA_VERSION, "rollout_id": snapshot.rollout_id, "epoch": snapshot.epoch,
            "request_id": probe_id(config, window, role, item["item_id"]), "request_kind": "behavior_probe",
            "revision": backend.config.revision, "intended_revision": backend.config.revision,
            "served_revision": backend.config.revision, "backend_role": role,
            "lifecycle_state": snapshot.observed_state.value, "transition_index": snapshot.transition_index,
            "window_id": window, "window_sequence": window_sequence, **item,
            "observed_output": output, "predicted_label": output if item["task_type"] == "classification" else None,
            "success": True, "latency_ms": backend.config.fixed_latency_ms, "logical_time": logical_time}
