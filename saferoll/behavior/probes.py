"""Separate paired probes bypass serving routing and serving-index faults."""
from __future__ import annotations
from typing import Any
from ..backends.mock import MockBackend
from ..config import Config
from ..evidence import window_id
from ..lifecycle import RolloutSnapshot
from .workload import BEHAVIOR_SCHEMA_VERSION, probe_id


def generate_probe(config: Config, snapshot: RolloutSnapshot, window_sequence: int,
                   item: dict[str, Any], role: str, logical_time: int,
                   backend: Any | None = None) -> dict[str, Any]:
    backend = backend or MockBackend(role, getattr(config, role))
    window = window_id(config.identity, snapshot.transition_index, window_sequence)
    real = config.baseline.backend_type == "llama_cpp"
    if real:
        from .real import normalize_output, prompt_for_item, PROMPT_TEMPLATE_VERSION
        from .evaluator import evaluate_output
        raw, success, start, end, latency, usage = backend.infer_behavior(item)
        output = normalize_output(item["task_type"], raw)
        extras = {"raw_output": raw, "normalized_output": output,
                  "evaluation_result": evaluate_output(item, output),
                  "user_prompt": prompt_for_item(item, getattr(config, role).prompt_variant),
                  "prompt_template_version": PROMPT_TEMPLATE_VERSION,
                  "timing_mode": "MEASURED", "request_start_monotonic": start,
                  "request_end_monotonic": end, "runtime_usage": usage}
    else:
        output = backend.infer_behavior(item)
        success = True
        latency = backend.config.fixed_latency_ms
        extras = {}
    return {"schema_version": BEHAVIOR_SCHEMA_VERSION, "rollout_id": snapshot.rollout_id, "epoch": snapshot.epoch,
            "request_id": probe_id(config, window, role, item["item_id"]), "request_kind": "behavior_probe",
            "revision": backend.config.revision, "intended_revision": backend.config.revision,
            "served_revision": backend.config.revision, "backend_role": role,
            "lifecycle_state": snapshot.observed_state.value, "transition_index": snapshot.transition_index,
            "window_id": window, "window_sequence": window_sequence, **item,
            "observed_output": output, "predicted_label": output if item["task_type"] == "classification" else None,
            "success": success, "latency_ms": latency, "logical_time": logical_time, **extras}
