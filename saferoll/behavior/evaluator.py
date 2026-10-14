"""Ground-truth evaluation only: no profiles, thresholds or rollout actions."""
from __future__ import annotations
import json
from typing import Any


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def evaluate_output(item: dict[str, Any], output: str) -> dict[str, bool | None]:
    if item["task_type"] == "classification":
        return {"correct": output == item["expected_label"], "structure_valid": None, "exact_match": None}
    if item["task_type"] != "structured_output":
        raise ValueError("unsupported behavior task")
    try:
        value = json.loads(output, object_pairs_hook=_unique_object)
        valid = (isinstance(value, dict) and set(value) == set(item["expected_structure"])
                 and type(value["number"]) is int and type(value["parity"]) is str)
    except (ValueError, TypeError, KeyError):
        valid = False
        value = None
    return {"correct": None, "structure_valid": valid,
            "exact_match": valid and value == item["expected_values"]}
