"""Original parity classification and fixed integer/parity extraction tasks."""
from __future__ import annotations
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from ..config import Config

BEHAVIOR_SCHEMA_VERSION = "1.0"


def item_id(workload_id: str, seed: int, task_type: str, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps([workload_id, seed, task_type, index],
                                             ensure_ascii=False, separators=(",", ":"))))


def probe_id(config: Config, window_id: str, role: str, item: str) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps([config.rollout.rollout_id, config.rollout.epoch,
                  "behavior-probe", window_id, role, item], ensure_ascii=False, separators=(",", ":"))))


def build_workload(config: Config) -> dict[str, Any]:
    b = config.behavior_evidence
    items = []
    for index in range(b.item_count if b.enabled else 0):
        number = config.experiment.seed + index
        label = "even" if number % 2 == 0 else "odd"
        structured = b.task_type == "structured_output"
        values = {"number": number, "parity": label}
        real = config.baseline.backend_type == "llama_cpp"
        item_input = {"number": number, "text": f"Record {number} has parity {label}."} if real else {"number": number}
        items.append({"workload_id": b.workload_id, "item_id": item_id(b.workload_id, config.experiment.seed, b.task_type, index),
                      "item_index": index, "task_type": b.task_type, "input": item_input,
                      "expected_output": values if structured else label,
                      "expected_label": None if structured else label,
                      "expected_structure": {"number": "integer", "parity": "string"} if structured else None,
                      "expected_values": values if structured else None})
    return {"schema_version": BEHAVIOR_SCHEMA_VERSION, "rollout_id": config.rollout.rollout_id,
            "epoch": config.rollout.epoch, "workload_id": b.workload_id, "seed": config.experiment.seed,
            "task_type": b.task_type, "items": items}
